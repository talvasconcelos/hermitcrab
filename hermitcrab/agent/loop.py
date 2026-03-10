"""Agent loop: the core processing engine.

Phase Separation:
- Phase A: Input handling + session retrieval (Tier 0, deterministic)
- Phase B: Interactive response (LLM allowed, policy gated)
- Phase C: Deterministic session save (Tier 0)
- Phase D: Session end detection (explicit reset or inactivity timeout)
- Phase E: Deferred journal + background cognition (non-blocking, optional)

Job Classes (LLM routing):
- interactive_response: Latency-sensitive, user-facing (uses configured model)
- journal_synthesis: Narrative summary, prefer weak local (1-3B)
- distillation: Atomic extraction, local only, skip if unavailable
- reflection: Meta-analysis, local preferred

Core Principles:
- Journal is narrative, lossy, non-authoritative
- Journal runs only on session end / inactivity (30 min)
- Distillation and reflection are async and optional
- Memory correctness independent of LLMs
- External models never affect correctness
- Background cognition failures never block main loop
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections import defaultdict
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import json_repair
from loguru import logger

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.agent.distillation import AtomicCandidate, CandidateType
from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.knowledge import KnowledgeStore
from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.reflection import ReflectionService
from hermitcrab.agent.subagent import SubagentManager
from hermitcrab.agent.tools.cron import CronTool
from hermitcrab.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from hermitcrab.agent.tools.knowledge import (
    KnowledgeIngestTool,
    KnowledgeIngestURLTool,
    KnowledgeListTool,
    KnowledgeSearchTool,
    KnowledgeStatsTool,
)
from hermitcrab.agent.tools.mcp import connect_mcp_servers
from hermitcrab.agent.tools.memory import (
    ReadMemoryTool,
    SearchMemoryTool,
    WriteDecisionTool,
    WriteFactTool,
    WriteGoalTool,
    WriteReflectionTool,
    WriteTaskTool,
)
from hermitcrab.agent.tools.message import MessageTool
from hermitcrab.agent.tools.registry import ToolRegistry
from hermitcrab.agent.tools.shell import ExecTool
from hermitcrab.agent.tools.spawn import SpawnTool
from hermitcrab.agent.tools.web import WebFetchTool, WebSearchTool
from hermitcrab.bus.events import InboundMessage, OutboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.config.schema import ExecToolConfig
from hermitcrab.providers.base import LLMProvider, ToolCallRequest
from hermitcrab.session.manager import Session, SessionManager
from hermitcrab.utils.helpers import ensure_dir, safe_filename

if TYPE_CHECKING:
    from hermitcrab.config.schema import ChannelsConfig
    from hermitcrab.cron.service import CronService


class JobClass(str, Enum):
    """
    Job class for LLM routing.

    Each job class has different:
    - Latency requirements
    - Model preferences (local vs external)
    - Trust levels
    - Cost constraints
    """

    INTERACTIVE_RESPONSE = "interactive_response"  # User-facing, latency sensitive
    JOURNAL_SYNTHESIS = "journal_synthesis"  # Narrative, prefer weak local
    DISTILLATION = "distillation"  # Atomic extraction, local only
    REFLECTION = "reflection"  # Meta-analysis, local preferred
    SUMMARISATION = "summarisation"  # Content summarization, flexible
    SUBAGENT = "subagent"  # Background subagent tasks


# Session inactivity timeout (30 minutes)
INACTIVITY_TIMEOUT_S = 30 * 60


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM (job-class routed)
    4. Executes tool calls
    5. Sends responses back
    6. Manages session lifecycle (timeout detection)
    7. Triggers background cognition (journal, distillation, reflection)

    Session Lifecycle:
    - Sessions tracked via last activity timestamp
    - Inactivity timeout: 30 minutes (configurable)
    - Session end triggers journal synthesis (non-blocking)
    - Background cognition never blocks main loop
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        # Optional: override job models (usually loaded from config)
        job_models: dict[JobClass, str | None] | None = None,
        # Optional: reflection promotion config
        reflection_config: dict[str, Any] | None = None,
        distillation_enabled: bool = False,
        # Optional: model aliases (friendly names like "qwen", "local")
        model_aliases: dict[str, str] | None = None,
        # Optional: reasoning effort config (loaded from config)
        reasoning_effort_config: dict[str, Any] | None = None,
        inactivity_timeout_s: int = INACTIVITY_TIMEOUT_S,
        llm_max_retries: int = 2,
        llm_retry_base_delay_s: float = 0.5,
        max_loop_seconds: int = 300,
        max_identical_tool_cycles: int = 3,
        memory_context_max_chars: int = 12000,
        memory_context_max_items_per_category: int = 25,
        memory_context_max_item_chars: int = 600,
    ):
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.inactivity_timeout_s = max(1, inactivity_timeout_s)
        self.llm_max_retries = max(0, llm_max_retries)
        self.llm_retry_base_delay_s = max(0.0, llm_retry_base_delay_s)
        self.max_loop_seconds = max(1, max_loop_seconds)
        self.max_identical_tool_cycles = max(2, max_identical_tool_cycles)
        self.distillation_enabled = distillation_enabled
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        # Model routing by job class
        # If job_models dict provided, use it; otherwise build from defaults
        if job_models is not None:
            self._job_models: dict[JobClass, str | None] = job_models
        else:
            # Default: all jobs use primary model (distillation skips if None)
            self._job_models = {
                JobClass.INTERACTIVE_RESPONSE: self.model,
                JobClass.JOURNAL_SYNTHESIS: self.model,
                JobClass.DISTILLATION: None,  # Skip by default
                JobClass.REFLECTION: self.model,
                JobClass.SUMMARISATION: self.model,
                JobClass.SUBAGENT: self.model,
            }
        self.model_aliases = model_aliases or {}

        # Reasoning effort configuration (default: "medium")
        self._reasoning_effort = (
            reasoning_effort_config.get("reasoning_effort", "medium")
            if reasoning_effort_config
            else "medium"
        )

        self.context = ContextBuilder(
            workspace,
            memory_max_chars=memory_context_max_chars,
            memory_max_items_per_category=memory_context_max_items_per_category,
            memory_max_item_chars=memory_context_max_item_chars,
            model_aliases=self.model_aliases,
        )
        self.sessions = session_manager or SessionManager(workspace)
        self.journal = JournalStore(workspace)
        self.memory = MemoryStore(workspace)
        self.knowledge = KnowledgeStore(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self._job_models.get(JobClass.SUBAGENT) or self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            model_aliases=self.model_aliases,
        )

        # Initialize reflection service
        reflection_model = self._get_model_for_job(JobClass.REFLECTION) or self.model
        self._reflection_service = ReflectionService(
            memory=self.memory,
            provider=provider,
            model=reflection_model,
        )
        self._reflection_auto_promote = reflection_config.get("auto_promote", True) if reflection_config else False
        self._reflection_notify = reflection_config.get("notify_user", True) if reflection_config else True

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._scratchpad_dir = ensure_dir(workspace / "scratchpads")
        # Track background tasks for cleanup (fire-and-forget, but track for shutdown)
        self._background_tasks: set[asyncio.Task] = set()
        # Session timeout tracking (checked on each message)
        self._session_timers: dict[str, datetime] = {}
        # Per-session lock to prevent concurrent turn processing races
        self._session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
            )
        )
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))

        self.tools.register(ReadMemoryTool(self.memory))
        self.tools.register(SearchMemoryTool(self.memory))
        self.tools.register(WriteFactTool(self.memory))
        self.tools.register(WriteDecisionTool(self.memory))
        self.tools.register(WriteGoalTool(self.memory))
        self.tools.register(WriteTaskTool(self.memory))
        self.tools.register(WriteReflectionTool(self.memory))

        # Knowledge base tools (explicit retrieval only, never auto-loaded)
        self.tools.register(KnowledgeSearchTool(self.knowledge))
        self.tools.register(KnowledgeIngestTool(self.knowledge))
        self.tools.register(KnowledgeIngestURLTool(self.knowledge))
        self.tools.register(KnowledgeListTool(self.knowledge))
        self.tools.register(KnowledgeStatsTool(self.knowledge))

        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.set_context(channel, chat_id, message_id)

        if spawn_tool := self.tools.get("spawn"):
            if isinstance(spawn_tool, SpawnTool):
                spawn_tool.set_context(channel, chat_id)

        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id)

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""

        def _fmt(tc):
            if isinstance(tc.arguments, dict):
                val = next(iter(tc.arguments.values()), None) if tc.arguments else None
            elif isinstance(tc.arguments, str):
                val = tc.arguments
            else:
                val = None
            if isinstance(val, str):
                return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
            if val is None:
                return tc.name
            rendered = json.dumps(val, ensure_ascii=False)
            return (
                f"{tc.name}({rendered[:40]}…)" if len(rendered) > 40 else f"{tc.name}({rendered})"
            )

        return ", ".join(_fmt(tc) for tc in tool_calls)

    @staticmethod
    def _is_intent_only_response(text: str | None) -> bool:
        """Detect non-final assistant replies that only narrate the next step."""
        if not text:
            return False
        normalized = " ".join(text.strip().lower().split())
        if not normalized:
            return False
        return bool(
            re.match(
                r"^(let me|i(?:'ll| will)|first[, ]+let me|now[, ]+let me|next[, ]+i(?:'ll| will)|i am going to)\b",
                normalized,
            )
        )

    @staticmethod
    def _is_empty_response(text: str | None) -> bool:
        """Treat blank or whitespace-only replies as missing output."""
        return text is None or not text.strip()

    def _get_model_for_job(self, job_class: JobClass) -> str | None:
        """
        Get model for a job class.

        Routing rules (mechanical, not heuristic):
        1. Use job-specific model if configured
        2. Fall back to primary model
        3. Return None to skip (for distillation when unavailable)

        Args:
            job_class: The job class to route.

        Returns:
            Model string or None (skip if unavailable).
        """
        model = self._job_models.get(job_class)
        # For distillation, None means "skip" (local only, don't escalate)
        # For other jobs, fall back to primary model
        if model is None and job_class != JobClass.DISTILLATION:
            return self.model
        return model

    def _should_hint_subagent_delegation(self, user_message: str) -> bool:
        """Return True when the request looks like substantial implementation grunt work."""
        if not self.tools.has("spawn"):
            return False

        subagent_model = self._job_models.get(JobClass.SUBAGENT)
        if not subagent_model:
            return False

        normalized = " ".join(user_message.lower().split())
        if not normalized:
            return False

        action_markers = (
            "build",
            "create",
            "implement",
            "refactor",
            "update",
            "rewrite",
            "start with",
            "work on",
        )
        scope_markers = (
            "project",
            "folder",
            "html",
            "css",
            "javascript",
            "app.js",
            "index.html",
            "web-chat",
            "page",
            "ui",
            "frontend",
            "files",
        )

        return any(marker in normalized for marker in action_markers) and any(
            marker in normalized for marker in scope_markers
        )

    def _build_delegation_hint(self) -> str:
        """Build a deterministic reminder to delegate substantial implementation work."""
        subagent_model = self._job_models.get(JobClass.SUBAGENT) or self.model
        return (
            "This request looks like substantial implementation grunt work. "
            "Prefer using spawn() to delegate the execution to a subagent and keep the main "
            f"agent responsive. Use the configured subagent model `{subagent_model}` or an "
            "appropriate alias when delegating, unless there is a clear reason to stay in the "
            "main agent."
        )

    @staticmethod
    def _fallback_system_task_summary(content: str) -> str:
        """Create a deterministic fallback summary for background task results."""
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return "Background task finished."

        label = "Background task"
        task = ""
        result = ""

        for idx, line in enumerate(lines):
            if line.startswith("[Subagent '") and "' " in line:
                label = line.strip("[]")
            elif line.startswith("Task:"):
                task = line[5:].strip()
            elif line == "Result:":
                result = "\n".join(lines[idx + 1 :]).strip()
                break

        if result:
            summary = result.splitlines()[0].strip()
            summary = summary[:280]
            if task:
                return (
                    f"{label} finished in the background. I reviewed the result for '{task}'. "
                    f"{summary}"
                )
            return f"{label} finished in the background. {summary}"

        if task:
            return f"{label} finished in the background. The task '{task}' has completed."

        return f"{label} finished in the background."

    async def _chat_with_retry(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        job_class: JobClass | None = None,
        reasoning_effort: str | None = None,
    ):
        """Call provider.chat with bounded retries/backoff."""
        chat_with_retry = getattr(self.provider, "chat_with_retry", None)
        if callable(chat_with_retry):
            response = chat_with_retry(
                messages=messages,
                tools=tools,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            )
            if inspect.isawaitable(response):
                return await response

        return await self.provider.chat(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

    @staticmethod
    def _tool_cycle_signature(tool_calls: list) -> str:
        """Create deterministic signature for a batch of tool calls."""
        payload = [{"name": tc.name, "arguments": tc.arguments} for tc in tool_calls]
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)

    def _coerce_inline_tool_calls(self, content: str | None) -> tuple[str | None, list[ToolCallRequest]]:
        """Recover a raw JSON tool call appended to assistant text.

        Some weaker models emit a plain-text JSON object such as
        `{"name":"read_memory","arguments":{...}}` instead of using structured
        tool-calling. Others emit XML-like wrappers such as
        `<minimax:tool_call><invoke name="list_dir">...</invoke></minimax:tool_call>`.
        Recover only narrow cases that cleanly parse and match registered tools.
        """
        if not content or not isinstance(content, str):
            return content, []

        text = content.strip()
        starts = [idx for idx, ch in enumerate(text) if ch in "{["]

        for start in reversed(starts):
            prefix = text[:start].rstrip()
            candidate = text[start:].strip()
            try:
                payload = json_repair.loads(candidate)
            except Exception:
                continue

            entries = payload if isinstance(payload, list) else [payload]
            recovered: list[ToolCallRequest] = []
            for idx, entry in enumerate(entries, start=1):
                if not isinstance(entry, dict):
                    recovered = []
                    break
                name = entry.get("name")
                arguments = entry.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json_repair.loads(arguments)
                    except Exception:
                        recovered = []
                        break
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    recovered = []
                    break
                if not self.tools.has(name):
                    recovered = []
                    break
                recovered.append(
                    ToolCallRequest(
                        id=f"inline_call_{idx}",
                        name=name,
                        arguments=arguments,
                    )
                )

            if recovered:
                return prefix or None, recovered

        xml_match = re.search(r"<(?:[\w.-]+:)?tool_call>\s*(.*?)\s*</(?:[\w.-]+:)?tool_call>", text, re.DOTALL)
        if xml_match:
            prefix = text[:xml_match.start()].rstrip()
            body = xml_match.group(1)
            recovered = self._parse_xml_tool_calls(body)
            if recovered:
                return prefix or None, recovered

        return content, []

    def _normalize_tool_calls(self, tool_calls: list[ToolCallRequest]) -> list[ToolCallRequest]:
        """Repair provider quirks where tool arguments arrive as JSON strings."""
        normalized: list[ToolCallRequest] = []
        for tc in tool_calls:
            arguments = tc.arguments
            if isinstance(arguments, str):
                try:
                    arguments = json_repair.loads(arguments)
                except Exception:
                    pass
            normalized.append(ToolCallRequest(id=tc.id, name=tc.name, arguments=arguments))
        return normalized

    def _parse_xml_tool_calls(self, body: str) -> list[ToolCallRequest]:
        """Recover XML-like inline tool calls from assistant text."""
        recovered: list[ToolCallRequest] = []
        invoke_pattern = re.compile(
            r"<invoke\s+name=\"([^\"]+)\">\s*(.*?)\s*</invoke>",
            re.DOTALL,
        )
        param_pattern = re.compile(
            r"<parameter\s+name=\"([^\"]+)\">(.*?)</parameter>",
            re.DOTALL,
        )

        for idx, match in enumerate(invoke_pattern.finditer(body), start=1):
            name = match.group(1).strip()
            if not self.tools.has(name):
                return []

            arguments: dict[str, str] = {}
            for param_name, raw_value in param_pattern.findall(match.group(2)):
                arguments[param_name.strip()] = raw_value.strip()

            recovered.append(
                ToolCallRequest(
                    id=f"inline_xml_call_{idx}",
                    name=name,
                    arguments=arguments,
                )
            )

        return recovered

    def _schedule_background(
        self,
        coro: Awaitable,
        task_name: str,
    ) -> None:
        """
        Schedule a background task (fire-and-forget).

        Background tasks:
        - Never block the main loop
        - Failures are logged, never fatal
        - Tracked for cleanup on shutdown

        Args:
            coro: Async coroutine to run.
            task_name: Human-readable name for logging.
        """

        async def _wrapped():
            try:
                await coro
            except asyncio.CancelledError:
                logger.debug("Background task cancelled: {}", task_name)
            except Exception as e:
                # Background cognition failures never affect correctness
                logger.warning("Background task failed (non-fatal): {}: {}", task_name, e)
            finally:
                self._background_tasks.discard(asyncio.current_task())

        task = asyncio.create_task(_wrapped(), name=task_name)
        self._background_tasks.add(task)

    def _check_session_timeout(self, session_key: str) -> bool:
        """
        Check if a session has timed out due to inactivity.

        Timeout threshold: self.inactivity_timeout_s

        Args:
            session_key: Session identifier.

        Returns:
            True if session timed out, False otherwise.
        """
        last_activity = self._session_timers.get(session_key)
        if last_activity is None:
            return False

        elapsed = (datetime.now(timezone.utc) - last_activity).total_seconds()
        timed_out = elapsed > self.inactivity_timeout_s

        if timed_out:
            logger.info("Session timed out ({}s inactivity): {}", elapsed, session_key)

        return timed_out

    def _update_session_timer(self, session_key: str) -> None:
        """
        Update the last activity timestamp for a session.

        Called on every message to reset the inactivity timer.

        Args:
            session_key: Session identifier.
        """
        self._session_timers[session_key] = datetime.now(timezone.utc)

    def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """Get/create lock for a session key."""
        return self._session_locks[session_key]

    def _scratchpad_path(self, session_key: str) -> Path:
        """Get filesystem path for a session's scratchpad."""
        return self._scratchpad_dir / f"{safe_filename(session_key.replace(':', '_'))}.md"

    def _ensure_scratchpad(self, session_key: str) -> Path:
        """Ensure scratchpad file exists for the current session."""
        path = self._scratchpad_path(session_key)
        if not path.exists():
            path.write_text(
                f"# Scratchpad: {session_key}\n\n"
                "Transient notes for this session. Archived on session end.\n",
                encoding="utf-8",
            )
        return path

    def _finalize_scratchpad(self, session_key: str, reason: str) -> None:
        """Archive or clear session scratchpad when a session ends."""
        path = self._scratchpad_path(session_key)
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            path.unlink(missing_ok=True)
            return

        archive_dir = ensure_dir(self._scratchpad_dir / "archive")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        archive_name = f"{safe_filename(session_key.replace(':', '_'))}-{reason}-{ts}.md"
        archive_path = archive_dir / archive_name
        path.replace(archive_path)
        logger.info("Archived scratchpad for {} -> {}", session_key, archive_path.name)

    async def _on_session_end(
        self,
        session: Session,
        reason: str = "explicit",
        messages_snapshot: list[dict] | None = None,
    ) -> None:
        """
        Handle session end (explicit reset or timeout).

        Triggers:
        1. Journal synthesis (narrative summary)
        2. Optional distillation (atomic extraction)
        3. Optional reflection (meta-analysis)

        All background tasks are non-blocking.
        Failures logged, never fatal.

        Args:
            session: The session that ended.
            reason: "explicit" (user reset) or "timeout" (inactivity).
            messages_snapshot: Optional immutable copy of session messages for background tasks.
        """
        logger.info("Session ended ({}): {}", reason, session.key)
        self._finalize_scratchpad(session.key, reason)

        # Clean up timer
        self._session_timers.pop(session.key, None)

        # Use snapshot if provided (for explicit reset before clear), otherwise use session
        all_messages = (
            messages_snapshot if messages_snapshot is not None else list(session.messages)
        )
        last_cognition_index = int(session.metadata.get("last_cognition_index", 0) or 0)
        if last_cognition_index < 0:
            last_cognition_index = 0
        if last_cognition_index > len(all_messages):
            last_cognition_index = len(all_messages)
        messages_for_background = all_messages[last_cognition_index:]
        session.metadata["last_cognition_index"] = len(all_messages)
        self.sessions.save(session)

        if not messages_for_background:
            logger.debug(
                "Session end pipeline has no new messages: key={} reason={} last_index={}",
                session.key,
                reason,
                last_cognition_index,
            )
        logger.debug(
            "Session end pipeline start: key={} reason={} messages={}",
            session.key,
            reason,
            len(messages_for_background),
        )

        # Phase E: Deferred journal synthesis (non-blocking)
        # Journal is narrative, lossy, non-authoritative
        self._schedule_background(
            self._synthesize_journal_from_messages(messages_for_background, session.key),
            f"journal:{session.key}",
        )
        logger.debug("Scheduled journal synthesis for {}", session.key)

        # Optional: distillation (atomic extraction, local only)
        # Skip if no local model available
        distillation_model = self._get_model_for_job(JobClass.DISTILLATION)
        if self.distillation_enabled and distillation_model:
            self._schedule_background(
                self._distill_session_from_messages(messages_for_background, session.key),
                f"distill:{session.key}",
            )
            logger.debug("Scheduled distillation for {}", session.key)
        else:
            logger.debug(
                "Distillation skipped (enabled={}, model={}): {}",
                self.distillation_enabled,
                bool(distillation_model),
                session.key,
            )

        # Optional: reflection (meta-analysis)
        reflection_model = self._get_model_for_job(JobClass.REFLECTION)
        if reflection_model:
            self._schedule_background(
                self._reflect_on_session_from_messages(messages_for_background, session.key),
                f"reflect:{session.key}",
            )
            logger.debug("Scheduled reflection for {}", session.key)
        else:
            logger.debug("Reflection skipped (no model): {}", session.key)

    async def _synthesize_journal(self, session: Session) -> None:
        """
        Synthesize journal entry from session.

        Journal is:
        - Narrative summary of what happened
        - Lossy by design
        - Non-authoritative (never affects memory directly)
        - Human readable

        Uses weak local LLM if available, escalates only if unavailable.
        Falls back to deterministic summary if no LLM.

        Args:
            session: Session to synthesize.
        """
        try:
            # Gather session data for synthesis
            messages = session.messages
            if not messages:
                return  # Empty session, no journal needed

            # Extract tool call names (not raw outputs) for context
            tool_names = set()
            for msg in messages:
                if msg.get("role") == "tool":
                    tool_names.add(msg.get("name", "unknown"))

            # Build synthesis prompt
            user_messages = [m for m in messages if m.get("role") == "user"]
            assistant_messages = [m for m in messages if m.get("role") == "assistant"]
            timestamps = [
                m.get("timestamp") for m in messages if isinstance(m.get("timestamp"), str)
            ]
            time_span = (
                f"{timestamps[0]} -> {timestamps[-1]}" if len(timestamps) >= 2 else "unknown"
            )
            user_preview = "; ".join(
                (m.get("content", "") or "").strip().replace("\n", " ")[:120]
                for m in user_messages[:3]
                if m.get("content")
            )

            channel = (
                getattr(session, "channel", None) or getattr(session, "chat_id", None) or "unknown"
            )
            prompt = (
                f"This is your own journal, written in your own words, to help the user and yourself.\n"
                f"Your workspace is your house, your memories are here to help you help the user.\n"
                f"Summarize this session as a brief narrative, framing yourself as 'I' (the assistant).\n"
                f"Channel: {channel}\n"
                f"Time span (ISO): {time_span}\n"
                f"User messages: {len(user_messages)}\n"
                f"My responses: {len(assistant_messages)}\n"
                f"Tools used: {', '.join(tool_names) if tool_names else 'none'}\n\n"
                f"User intent snippets: {user_preview or 'none'}\n\n"
                "Write 3-5 concise sentences that mention what the user requested, what I attempted, what succeeded or failed, and any important outcome.\n"
                "Each entry should state the channel it came from."
            )

            # Try LLM synthesis if model available
            model = self._get_model_for_job(JobClass.JOURNAL_SYNTHESIS)
            if model:
                try:
                    response = await self._chat_with_retry(
                        messages=[{"role": "user", "content": prompt}],
                        model=model,
                        temperature=0.05,
                        max_tokens=256,
                        job_class=JobClass.JOURNAL_SYNTHESIS,
                        reasoning_effort=self._reasoning_effort,
                    )
                    content = self._strip_think(response.content)
                    if content:
                        self.journal.write_entry(
                            content=content,
                            session_keys=[session.key],
                            tags=["session", "synthesis"],
                        )
                        logger.info("Journal synthesized (LLM): {}", session.key)
                        return
                except Exception as e:
                    logger.warning("Journal LLM failed, using fallback: {}", e)

            # Fallback: deterministic summary
            fallback = (
                f"## Session: {session.key}\n\n"
                f"User sent {len(user_messages)} message(s). "
                f"Agent responded {len(assistant_messages)} time(s). "
                f"Tools: {', '.join(sorted(tool_names)) if tool_names else 'none'}."
            )
            self.journal.write_entry(
                content=fallback,
                session_keys=[session.key],
                tags=["session", "fallback"],
            )
            logger.info("Journal written (fallback): {}", session.key)

        except Exception as e:
            # Journal failures never block agent operation
            logger.warning("Journal synthesis failed (non-fatal): {}: {}", session.key, e)

    async def _synthesize_journal_from_messages(
        self,
        messages: list[dict],
        session_key: str,
    ) -> None:
        """
        Synthesize journal from message list (for use with session snapshots).

        Wrapper around _synthesize_journal that works with a message list instead of Session.

        Args:
            messages: List of session messages.
            session_key: Session identifier.
        """

        # Create minimal session-like object
        class _SessionSnapshot:
            def __init__(self, messages: list[dict], key: str):
                self.messages = messages
                self.key = key

        snapshot = _SessionSnapshot(messages, session_key)
        await self._synthesize_journal(snapshot)

    async def _distill_session(self, session: Session) -> None:
        """
        Extract atomic candidates from session (fact, task, goal, decision).

        Distillation:
        - Produces proposals only (not authoritative)
        - Uses strict JSON schema
        - Validation and commit happen elsewhere (Tier 0)
        - Local only, skip if unavailable

        Args:
            session: Session to distill.
        """
        try:
            logger.debug("Distillation started: {}", session.key)
            messages = self._filter_messages_for_distillation(session.messages, session.key)
            if not messages:
                logger.debug("Distillation skipped (no messages after filtering): {}", session.key)
                return  # Empty session, nothing to distill

            # Build distillation prompt
            prompt = (
                "Extract conservative atomic knowledge candidates from this session.\n\n"
                "Look for:\n"
                "- FACTS: User preferences, project context, established truths\n"
                "- DECISIONS: Architectural choices, trade-offs, locked decisions\n"
                "- GOALS: Objectives, outcomes the user wants to achieve\n"
                "- TASKS: Action items, todos, things to do (must include task_assignee)\n\n"
                "Do not produce reflections here.\n"
                "For TASK candidates, include task_assignee. Use 'user' for user tasks.\n\n"
                "Session content:\n"
            )

            for msg in messages[:50]:  # Limit to first 50 messages
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:500]  # Truncate each message
                if role == "user":
                    prompt += f"User: {content}\n"
                elif role == "assistant":
                    prompt += f"Assistant: {content}\n"

            prompt += (
                "\n\nReturn candidates as a JSON object with 'candidates' array.\n"
                "Each candidate must have: type, title, content.\n"
                "Optional: confidence (0-1), tags, and type-specific fields.\n"
                "Allowed types by default: fact, goal, task. Use decision only for clear locked choices with rationale.\n"
                "For TASK type: task_assignee (required), task_status, task_deadline, task_priority\n"
                "For GOAL type: goal_status, goal_priority, goal_horizon\n"
                "For DECISION type: decision_status, decision_rationale, decision_supersedes\n"
                "Be conservative. Skip weak, duplicate, or speculative items."
            )

            # Try LLM distillation
            model = self._get_model_for_job(JobClass.DISTILLATION)
            if not model:
                logger.debug("Distillation skipped (no model): {}", session.key)
                return

            try:
                response = await self._chat_with_retry(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=0.1,
                    max_tokens=2048,
                    job_class=JobClass.DISTILLATION,
                    reasoning_effort=self._reasoning_effort,
                )

                content = self._strip_think(response.content)
                if not content:
                    return

                # Try to extract JSON from response
                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = content[json_start:json_end]
                    data = json.loads(json_str)
                    if not isinstance(data, dict):
                        logger.warning(
                            "Distillation response root is not an object: {} ({})",
                            session.key,
                            type(data).__name__,
                        )
                        return

                    candidates = data.get("candidates", [])
                    validated_count = 0

                    for candidate_data in candidates:
                        try:
                            if not isinstance(candidate_data, dict):
                                logger.debug(
                                    "Skipping non-dict distillation candidate for {}: {}",
                                    session.key,
                                    type(candidate_data).__name__,
                                )
                                continue
                            candidate = AtomicCandidate.from_dict(candidate_data)
                            candidate.source_session = session.key

                            # Validate candidate
                            errors = candidate.validate()
                            if errors:
                                logger.warning(
                                    "Candidate validation failed: {}: {}",
                                    candidate.title,
                                    errors,
                                )
                                continue

                            # Commit to memory via Tier 0 path
                            # This is the authoritative write - distillation proposes, memory decides
                            self._commit_candidate_to_memory(candidate)
                            validated_count += 1

                        except Exception as e:
                            logger.warning(
                                "Failed to parse candidate: {}: {}",
                                candidate_data.get("title", "unknown")
                                if isinstance(candidate_data, dict)
                                else "unknown",
                                e,
                            )

                    if validated_count > 0:
                        logger.info(
                            "Distillation complete: {} candidates from {}",
                            validated_count,
                            session.key,
                        )
                    else:
                        logger.debug("No valid candidates distilled: {}", session.key)

            except json.JSONDecodeError as e:
                logger.warning("Distillation response not valid JSON: {}: {}", session.key, e)
            except Exception as e:
                logger.warning("Distillation LLM failed: {}: {}", session.key, e)

        except Exception as e:
            # Distillation failures never block agent operation
            logger.warning("Distillation failed (non-fatal): {}: {}", session.key, e)

    @staticmethod
    def _iter_strings(obj: Any) -> list[str]:
        """Collect string values recursively from nested objects."""
        values: list[str] = []
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            for v in obj.values():
                values.extend(AgentLoop._iter_strings(v))
        elif isinstance(obj, list):
            for item in obj:
                values.extend(AgentLoop._iter_strings(item))
        return values

    def _tool_call_targets_scratchpad(self, tc: dict[str, Any], session_key: str) -> bool:
        """Return True if tool call arguments reference current session scratchpad."""
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        args_raw = fn.get("arguments", {})
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except Exception:
                args = args_raw
        else:
            args = args_raw

        strings = self._iter_strings(args)
        scratchpad = self._scratchpad_path(session_key).resolve()
        for value in strings:
            try:
                p = Path(value)
                if not p.is_absolute():
                    p = (self.workspace / p).resolve()
                else:
                    p = p.resolve()
            except Exception:
                continue
            if p == scratchpad:
                return True
        return False

    def _filter_messages_for_distillation(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
    ) -> list[dict[str, Any]]:
        """Drop scratchpad-specific tool traces so they aren't distilled."""
        excluded_tool_call_ids: set[str] = set()
        filtered: list[dict[str, Any]] = []

        for msg in messages:
            if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list):
                kept_calls = []
                for tc in msg["tool_calls"]:
                    if self._tool_call_targets_scratchpad(tc, session_key):
                        if tc_id := tc.get("id"):
                            excluded_tool_call_ids.add(tc_id)
                        continue
                    kept_calls.append(tc)

                if kept_calls != msg["tool_calls"]:
                    msg_copy = dict(msg)
                    if kept_calls:
                        msg_copy["tool_calls"] = kept_calls
                    else:
                        msg_copy.pop("tool_calls", None)
                    filtered.append(msg_copy)
                    continue

            if msg.get("role") == "tool" and msg.get("tool_call_id") in excluded_tool_call_ids:
                continue

            filtered.append(msg)

        return filtered

    async def _distill_session_from_messages(
        self,
        messages: list[dict],
        session_key: str,
    ) -> None:
        """
        Distill session from message list (for use with session snapshots).

        Wrapper around _distill_session that works with a message list instead of Session.

        Args:
            messages: List of session messages.
            session_key: Session identifier.
        """

        class _SessionSnapshot:
            def __init__(self, messages: list[dict], key: str):
                self.messages = messages
                self.key = key

        snapshot = _SessionSnapshot(messages, session_key)
        await self._distill_session(snapshot)

    def _commit_candidate_to_memory(self, candidate: AtomicCandidate) -> None:
        """
        Commit a validated candidate to memory (Tier 0 operation).

        This is the authoritative write path. Distillation proposes,
        but this method decides what actually gets stored.

        Args:
            candidate: Validated atomic candidate to commit.

        Note:
            - Failures are logged but don't raise (called from background task)
            - Each candidate type maps to specific memory.write_*() method
            - This is Tier 0 logic - deterministic, Python-authoritative
        """
        try:
            if not self._should_commit_distilled_candidate(candidate):
                logger.info("Distillation filtered candidate '{}'", candidate.title)
                return

            params = candidate.to_memory_params()

            if candidate.type == CandidateType.FACT:
                self.memory.write_fact(**params)
                logger.info("Memory commit: fact '{}'", candidate.title)

            elif candidate.type == CandidateType.DECISION:
                self.memory.write_decision(**params)
                logger.info("Memory commit: decision '{}'", candidate.title)

            elif candidate.type == CandidateType.GOAL:
                self.memory.write_goal(**params)
                logger.info("Memory commit: goal '{}'", candidate.title)

            elif candidate.type == CandidateType.TASK:
                # Ensure assignee is set (required field)
                if not params.get("assignee"):
                    params["assignee"] = "distilled"  # Default for distilled tasks
                self.memory.write_task(**params)
                logger.info("Memory commit: task '{}'", candidate.title)

            elif candidate.type == CandidateType.REFLECTION:
                self.memory.write_reflection(**params)
                logger.info("Memory commit: reflection '{}'", candidate.title)

        except Exception as e:
            # Memory commit failures logged but don't propagate
            # (called from background task, must not affect main loop)
            logger.error(
                "Failed to commit candidate to memory: {}: {}",
                candidate.title,
                e,
            )

    @staticmethod
    def _normalize_memory_text(text: str) -> str:
        """Normalize text for conservative duplicate checks."""
        return " ".join(re.sub(r"[^a-z0-9\s]+", " ", text.lower()).split())

    def _is_near_duplicate_memory_item(self, candidate: AtomicCandidate, existing: Any) -> bool:
        """Return True when the candidate is effectively already stored."""
        title_ratio = SequenceMatcher(
            None,
            self._normalize_memory_text(candidate.title),
            self._normalize_memory_text(existing.title),
        ).ratio()
        content_ratio = SequenceMatcher(
            None,
            self._normalize_memory_text(candidate.content),
            self._normalize_memory_text(existing.content),
        ).ratio()
        return title_ratio >= 0.9 or (title_ratio >= 0.8 and content_ratio >= 0.85)

    def _find_existing_memory_duplicates(self, candidate: AtomicCandidate) -> list[Any]:
        """Search the target category for likely duplicates."""
        category_map = {
            CandidateType.FACT: "facts",
            CandidateType.DECISION: "decisions",
            CandidateType.GOAL: "goals",
            CandidateType.TASK: "tasks",
            CandidateType.REFLECTION: "reflections",
        }
        category = category_map[candidate.type]
        existing = self.memory.read_memory(category)
        return [item for item in existing if self._is_near_duplicate_memory_item(candidate, item)]

    def _should_commit_distilled_candidate(self, candidate: AtomicCandidate) -> bool:
        """Apply conservative distillation acceptance rules before writing memory."""
        allowed_types = {CandidateType.FACT, CandidateType.GOAL, CandidateType.TASK}
        if candidate.type == CandidateType.DECISION:
            has_rationale = bool((candidate.decision_rationale or "").strip())
            if candidate.confidence < 0.9 or not has_rationale:
                return False
        elif candidate.type not in allowed_types:
            return False

        if candidate.confidence < 0.65:
            return False

        duplicates = self._find_existing_memory_duplicates(candidate)
        if duplicates:
            return False

        return True

    async def _reflect_on_session(self, session: Session) -> None:
        """
        Reflect on session using the new ReflectionService.

        Single LLM call → 0-1 reflection → auto-promote if pattern.

        Args:
            session: Session to reflect on.
        """
        await self._reflection_service.reflect_on_session(
            messages=session.messages,
            session_key=session.key,
        )

    async def _reflect_on_session_from_messages(
        self,
        messages: list[dict],
        session_key: str,
    ) -> None:
        """
        Reflect on session from message list (for use with session snapshots).

        Args:
            messages: List of session messages.
            session_key: Session identifier.
        """
        await self._reflection_service.reflect_on_session(
            messages=messages,
            session_key=session_key,
        )

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        job_class: JobClass = JobClass.INTERACTIVE_RESPONSE,
    ) -> tuple[str | None, list[str], list[dict]]:
        """
        Run the agent iteration loop.

        Phase B: Interactive response (LLM allowed, policy gated).

        Args:
            initial_messages: Initial message list for LLM context.
            on_progress: Optional callback for progress updates.
            job_class: Job class for model routing.

        Returns:
            Tuple of (final_content, tools_used, all_messages).
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        started_at = time.monotonic()
        last_tool_signature: str | None = None
        repeated_tool_cycles = 0
        intent_reprompt_count = 0

        # Get model for this job class
        model = self._get_model_for_job(job_class)
        if model is None:
            # No model available (distillation case) - skip gracefully
            return None, [], []

        while iteration < self.max_iterations:
            if time.monotonic() - started_at > self.max_loop_seconds:
                logger.warning("Max loop time reached ({}s)", self.max_loop_seconds)
                final_content = (
                    f"I hit the time limit for this response ({self.max_loop_seconds}s) "
                    "before completing all tool calls. Try a smaller step."
                )
                break

            iteration += 1
            logger.info(
                "Agent loop iteration {}/{} started (job={}, model={})",
                iteration,
                self.max_iterations,
                job_class.value,
                model,
            )

            response = await self._chat_with_retry(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                job_class=job_class,
                reasoning_effort=self._reasoning_effort,
            )
            response.tool_calls = self._normalize_tool_calls(response.tool_calls)
            logger.info(
                "LLM response received (job={}, finish_reason={}, content_chars={}, tool_calls={})",
                job_class.value,
                response.finish_reason,
                len(response.content or ""),
                len(response.tool_calls),
            )

            if not response.has_tool_calls:
                content, inline_tool_calls = self._coerce_inline_tool_calls(response.content)
                if inline_tool_calls:
                    logger.warning(
                        "Recovered {} inline tool call(s) from assistant text in iteration {}",
                        len(inline_tool_calls),
                        iteration,
                    )
                    response.content = content
                    response.tool_calls = inline_tool_calls

            if response.has_tool_calls:
                tool_signature = self._tool_cycle_signature(response.tool_calls)
                if tool_signature == last_tool_signature:
                    repeated_tool_cycles += 1
                else:
                    repeated_tool_cycles = 1
                    last_tool_signature = tool_signature

                if repeated_tool_cycles >= self.max_identical_tool_cycles:
                    logger.warning(
                        "Breaking repeated tool cycle after {} identical iterations",
                        repeated_tool_cycles,
                    )
                    final_content = (
                        "I detected repeated tool calls without progress and stopped to avoid a loop. "
                        "Please refine the request or provide more constraints."
                    )
                    break

                if on_progress:
                    clean = self._strip_think(response.content)
                    if clean:
                        await on_progress(clean)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                logger.info(
                    "Assistant tool-call turn appended (iteration={}, tool_names={})",
                    iteration,
                    [tc.name for tc in response.tool_calls],
                )

                spawned_result: str | None = None
                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    try:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                        logger.info(
                            "Tool completed: {} -> {} chars",
                            tool_call.name,
                            len(result) if isinstance(result, str) else 0,
                        )
                    except Exception as e:
                        logger.error("Tool execution failed: {}: {}", tool_call.name, e)
                        result = f"Tool error: {type(e).__name__}: {e}"
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    if tool_call.name == "spawn" and spawned_result is None:
                        spawned_result = result

                if spawned_result is not None:
                    final_content = spawned_result
                    logger.info("Returning immediately after spawn to keep main agent responsive")
                    break
            else:
                repeated_tool_cycles = 0
                last_tool_signature = None
                final_content = self._strip_think(response.content)
                needs_reprompt = tools_used and (
                    self._is_empty_response(final_content)
                    or self._is_intent_only_response(final_content)
                )
                if needs_reprompt:
                    intent_reprompt_count += 1
                    logger.warning(
                        "Non-final response after tool usage; reprompting model (attempt {}, empty={}, intent_only={})",
                        intent_reprompt_count,
                        self._is_empty_response(final_content),
                        self._is_intent_only_response(final_content),
                    )
                    if intent_reprompt_count >= 2:
                        logger.warning("Stopping after repeated non-final responses post-tool usage")
                        final_content = (
                            "I checked the available context, but the model kept stopping without a "
                            "usable answer after tool calls. Please retry this request or switch to "
                            "a stronger tool-calling model."
                        )
                    else:
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "Do not stop with an empty reply or an intention statement. "
                                    "You already used tools. Either call the next tool now, or reply "
                                    "with the actual result for the user."
                                ),
                            }
                        )
                        final_content = None
                        continue
                logger.info(
                    "Agent loop completed without tool calls at iteration {} (final_chars={})",
                    iteration,
                    len(final_content or ""),
                )
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        logger.info(
            "Agent loop finished (job={}, iterations={}, tools_used={}, final_chars={})",
            job_class.value,
            iteration,
            tools_used,
            len(final_content or ""),
        )
        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                    try:
                        response = await self._process_message(msg)
                        if response is not None:
                            await self.bus.publish_outbound(response)
                        elif msg.channel == "cli":
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="",
                                    metadata=msg.metadata or {},
                                )
                            )
                    except Exception as e:
                        logger.error("Error processing message: {}", e)
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=f"Sorry, I encountered an error: {str(e)}",
                            )
                        )
                except asyncio.TimeoutError:
                    continue
        finally:
            await self._shutdown_background_tasks()
            await self.close_mcp()
            logger.info("Agent loop stopped")

    async def _shutdown_background_tasks(self) -> None:
        """Cancel and await all tracked background tasks."""
        if not self._background_tasks:
            return
        pending = list(self._background_tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._background_tasks.clear()

    async def wait_for_background_tasks(self, timeout_s: float = 5.0) -> tuple[int, int]:
        """
        Wait for currently scheduled background tasks to complete.

        This is useful for graceful CLI exits where we want session-end
        cognition (journal/distillation/reflection) to finish if possible
        before shutdown.

        Args:
            timeout_s: Max seconds to wait.

        Returns:
            Tuple of (completed_count, pending_count).
        """
        if not self._background_tasks:
            return 0, 0

        tasks = [t for t in self._background_tasks if not t.done()]
        if not tasks:
            return 0, 0

        done, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout_s))
        if pending:
            logger.warning(
                "Background tasks still running after {:.1f}s: {} pending",
                timeout_s,
                len(pending),
            )
        return len(done), len(pending)

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """
        Process a single inbound message with explicit phase separation.

        Phases:
        - Phase A: Input handling + session retrieval (Tier 0, deterministic)
        - Phase B: Interactive response (LLM allowed, policy gated)
        - Phase C: Deterministic session save (Tier 0)
        - Phase D: Session end detection (explicit reset or inactivity timeout)
        - Phase E: Deferred journal + background cognition (non-blocking, optional)

        Args:
            msg: Inbound message to process.
            session_key: Optional session key override.
            on_progress: Optional progress callback.

        Returns:
            OutboundMessage or None.
        """
        # ====================================================================
        # Phase A: Input handling + session retrieval (Tier 0, deterministic)
        # ====================================================================

        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            async with self._get_session_lock(key):
                session = self.sessions.get_or_create(key)
                scratchpad_path = self._ensure_scratchpad(key)
                self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))

                # Update session timer (reset inactivity clock)
                self._update_session_timer(key)

                history = session.get_history(max_messages=self.memory_window)
                messages = self.context.build_messages(
                    history=history,
                    current_message=msg.content,
                    channel=channel,
                    chat_id=chat_id,
                    scratchpad_path=str(scratchpad_path),
                )
                # Phase B happens inside _run_agent_loop
                final_content, _, all_msgs = await self._run_agent_loop(
                    messages,
                    job_class=JobClass.INTERACTIVE_RESPONSE,
                )
                # Phase C: Deterministic save
                self._save_turn(session, all_msgs, 1 + len(history))
                self.sessions.save(session)
                return OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=final_content or self._fallback_system_task_summary(msg.content),
                )

        # Regular user message
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        async with self._get_session_lock(key):
            session = self.sessions.get_or_create(key)
            scratchpad_path = self._ensure_scratchpad(key)

            # Update session timer (reset inactivity clock)
            self._update_session_timer(key)

            # ====================================================================
            # Phase D (early): Session end detection - explicit reset
            # ====================================================================

            # Slash commands
            cmd = msg.content.strip().lower()
            if cmd == "/new":
                # Explicit session end - pass snapshot before clearing to preserve data for background tasks
                messages_snapshot = list(session.messages)
                await self._on_session_end(
                    session, reason="explicit", messages_snapshot=messages_snapshot
                )

                session.clear()
                session.metadata.pop("last_cognition_index", None)
                self.sessions.save(session)
                self.sessions.invalidate(session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id, content="New session started."
                )

            if cmd == "/help":
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="🐈 hermitcrab commands:\n/new — Start a new conversation\n/help — Show available commands",
                )

            # ====================================================================
            # Phase A (continued): Tool context setup
            # ====================================================================

            self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
            if message_tool := self.tools.get("message"):
                if isinstance(message_tool, MessageTool):
                    message_tool.start_turn()

            history = session.get_history(max_messages=self.memory_window)
            initial_messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                media=msg.media if msg.media else None,
                channel=msg.channel,
                chat_id=msg.chat_id,
                scratchpad_path=str(scratchpad_path),
            )
            if self._should_hint_subagent_delegation(msg.content):
                initial_messages.insert(
                    len(initial_messages) - 1,
                    {"role": "system", "content": self._build_delegation_hint()},
                )
            async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
                meta = dict(msg.metadata or {})
                meta["_progress"] = True
                meta["_tool_hint"] = tool_hint
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=content,
                        metadata=meta,
                    )
                )

            # ====================================================================
            # Phase B: Interactive response (LLM allowed, policy gated)
            # ====================================================================

            final_content, tools_used, all_msgs = await self._run_agent_loop(
                initial_messages,
                on_progress=on_progress or _bus_progress,
                job_class=JobClass.INTERACTIVE_RESPONSE,
            )

            if final_content is None:
                final_content = "I've completed processing but have no response to give."

            preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
            logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

            # ====================================================================
            # Phase C: Deterministic session save (Tier 0)
            # ====================================================================

            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)

            # ====================================================================
            # Phase D: Session end detection - inactivity timeout check
            # ====================================================================

            # Check if any other sessions have timed out
            timed_out_sessions = [
                k for k in list(self._session_timers.keys()) if self._check_session_timeout(k)
            ]
            for timed_out_key in timed_out_sessions:
                timed_out_session = self.sessions.get_or_create(timed_out_key)
                # Trigger session end asynchronously (non-blocking)
                self._schedule_background(
                    self._on_session_end(timed_out_session, reason="timeout"),
                    f"session_end:{timed_out_key}",
                )

            # ====================================================================
            # Phase E: Deferred journal + background cognition (non-blocking)
            # ====================================================================

            # NOTE: Journal is NOT written per-turn anymore.
            # Journal synthesis happens only on session end (explicit or timeout).
            # This is intentional: journal is narrative, not authoritative.

            if message_tool := self.tools.get("message"):
                if isinstance(message_tool, MessageTool) and message_tool.has_sent_in_turn:
                    return None

            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=final_content,
                metadata=msg.metadata or {},
            )

    _TOOL_RESULT_MAX_CHARS = 500

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """
        Save new-turn messages into session, truncating large tool results.

        Also updates the session timer for inactivity timeout tracking.

        Args:
            session: Session to save.
            messages: New messages to append.
            skip: Number of messages to skip from the start.
        """
        for m in messages[skip:]:
            entry = {k: v for k, v in m.items() if k != "reasoning_content"}
            if entry.get("role") == "tool" and isinstance(entry.get("content"), str):
                content = entry["content"]
                if len(content) > self._TOOL_RESULT_MAX_CHARS:
                    entry["content"] = content[: self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now(timezone.utc)

        # Update session timer for inactivity tracking
        self._update_session_timer(session.key)

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(
            msg, session_key=session_key, on_progress=on_progress
        )
        return response.content if response else ""
