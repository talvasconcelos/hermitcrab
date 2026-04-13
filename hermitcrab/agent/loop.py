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
from contextlib import AsyncExitStack
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from hermitcrab.agent.background_jobs import BackgroundJobManager, SessionDigest
from hermitcrab.agent.background_messages import (
    fallback_system_task_summary,
    is_grounded_system_reply,
    summarize_subagent_completion,
)
from hermitcrab.agent.context import ContextBuilder
from hermitcrab.agent.distillation import AtomicCandidate
from hermitcrab.agent.execution_state import ExecutionPhase, ExecutionStateTracker
from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.knowledge import KnowledgeStore
from hermitcrab.agent.lists import ListStore
from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.message_preparation import (
    clean_snippet,
    is_empty_response,
    is_placeholder_assistant_reply,
    is_subagent_completion_prompt,
)
from hermitcrab.agent.pending_work import (
    PendingWork,
    build_pending_work_hint,
    build_skill_creation_hint,
    find_action_source,
    has_structured_payload,
    relates_to_pending_work,
    should_resume_pending_work,
    snippet,
)
from hermitcrab.agent.people import PeopleStore
from hermitcrab.agent.reflection import ReflectionService
from hermitcrab.agent.reminders import ReminderStore
from hermitcrab.agent.session_lifecycle import SessionLifecycleManager
from hermitcrab.agent.skill_runtime import SkillRuntimeManager
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
from hermitcrab.agent.tools.lists import (
    AddListItemsTool,
    DeleteListTool,
    RemoveListItemsTool,
    SetListItemStatusTool,
    ShowListTool,
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
from hermitcrab.agent.tools.people import PersonProfileTool
from hermitcrab.agent.tools.policy import build_main_agent_policy
from hermitcrab.agent.tools.registry import ToolRegistry
from hermitcrab.agent.tools.reminders import ReminderTool
from hermitcrab.agent.tools.session_search import SessionSearchTool
from hermitcrab.agent.tools.shell import ExecTool
from hermitcrab.agent.tools.spawn import SpawnTool
from hermitcrab.agent.tools.web import WebFetchTool, WebSearchTool
from hermitcrab.agent.turn_runner import TurnOutcome, TurnResult, TurnRunner, TurnRunnerConfig
from hermitcrab.bus.events import InboundMessage, OutboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.config.schema import ExecToolConfig, ModelAliasConfig, NamedModelConfig
from hermitcrab.providers.base import LLMProvider
from hermitcrab.session.manager import Session, SessionManager

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


@dataclass(slots=True)
class InteractiveTurnBuildResult:
    """Prepared interactive-turn inputs plus coordinator-side startup signals."""

    messages: list[dict[str, Any]]
    save_skip: int
    resumed_pending_work: PendingWork | None = None


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
        model_aliases: dict[str, str | ModelAliasConfig] | None = None,
        named_models: dict[str, NamedModelConfig] | None = None,
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
        self.named_models = named_models or {}

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
            named_models=self.named_models,
        )
        self.skill_runtime = SkillRuntimeManager(workspace, self.context.skills)
        self.sessions = session_manager or SessionManager(workspace)
        self.journal = JournalStore(workspace)
        self.memory = MemoryStore(workspace)
        self.knowledge = KnowledgeStore(workspace)
        self.lists = ListStore(workspace)
        self.people = PeopleStore(workspace)
        self.reminders = ReminderStore(
            workspace,
            legacy_cron_store_path=(cron_service.store_path if cron_service else None),
        )
        self.tools = ToolRegistry(default_policy=build_main_agent_policy())
        self.execution_state = ExecutionStateTracker()
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
            named_models=self.named_models,
        )

        # Initialize reflection service
        reflection_model = self._get_model_for_job(JobClass.REFLECTION) or self.model
        reflection_promotion = reflection_config or {}
        self._reflection_service = ReflectionService(
            memory=self.memory,
            chat_callable=self._chat_with_retry,
            model=reflection_model,
            auto_promote=bool(reflection_promotion.get("auto_promote", False)),
            allowed_targets=reflection_promotion.get("target_files") or [],
            max_file_lines=int(reflection_promotion.get("max_file_lines", 500) or 500),
            notify_user=bool(reflection_promotion.get("notify_user", True)),
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._background_jobs = BackgroundJobManager(
            workspace=workspace,
            journal=self.journal,
            memory=self.memory,
            reflection_service=self._reflection_service,
            chat_callable=self._chat_with_retry,
            get_model_for_job=self._get_model_for_job,
            strip_think=self._strip_think,
            reasoning_effort=self._reasoning_effort,
        )
        self._background_tasks = self._background_jobs._background_tasks
        self._session_lifecycle = SessionLifecycleManager(
            workspace=workspace,
            sessions=self.sessions,
            inactivity_timeout_s=self.inactivity_timeout_s,
        )
        self._session_timers = self._session_lifecycle.session_timers
        self._session_active_turns = self._session_lifecycle.session_active_turns
        self._session_end_in_progress = self._session_lifecycle.session_end_in_progress
        self._session_locks = self._session_lifecycle.session_locks
        self._active_turn_tasks: dict[str, asyncio.Task[OutboundMessage | None]] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        read_fallback_dir = Path.cwd() if not self.restrict_to_workspace else None
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace,
                allowed_dir=allowed_dir,
                fallback_dir=read_fallback_dir,
            )
        )
        self.tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(
            ListDirTool(
                workspace=self.workspace,
                allowed_dir=allowed_dir,
                fallback_dir=read_fallback_dir,
            )
        )
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
        self.tools.register(SessionSearchTool(self.sessions))
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
        self.tools.register(ShowListTool(self.lists))
        self.tools.register(AddListItemsTool(self.lists))
        self.tools.register(SetListItemStatusTool(self.lists))
        self.tools.register(RemoveListItemsTool(self.lists))
        self.tools.register(DeleteListTool(self.lists))
        self.tools.register(PersonProfileTool(self.people, self.reminders))

        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
        if self.reminders is not None:
            self.tools.register(ReminderTool(self.reminders))

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

    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        *,
        spawn_brief: str | None = None,
    ) -> None:
        """Update context for all tools that need routing info."""
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.set_context(channel, chat_id, message_id)

        if spawn_tool := self.tools.get("spawn"):
            if isinstance(spawn_tool, SpawnTool):
                spawn_tool.set_context(channel, chat_id, brief=spawn_brief)

        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id)
        if reminder_tool := self.tools.get("reminder"):
            if isinstance(reminder_tool, ReminderTool):
                delivery_channel, delivery_chat_id = self._resolve_reminder_delivery_target(
                    channel,
                    chat_id,
                )
                reminder_tool.set_context(
                    channel,
                    chat_id,
                    delivery_channel=delivery_channel,
                    delivery_chat_id=delivery_chat_id,
                )
        if person_tool := self.tools.get("person_profile"):
            if isinstance(person_tool, PersonProfileTool):
                person_tool.set_context(channel, chat_id)

    def _resolve_reminder_delivery_target(self, channel: str, chat_id: str) -> tuple[str, str]:
        """Prefer the latest active external session when reminders are created from CLI."""
        if channel not in {"cli", "system"} and chat_id:
            return channel, chat_id

        enabled_external_channels: set[str] = set()
        if self.channels_config is not None:
            for name in ("telegram", "email", "nostr"):
                cfg = getattr(self.channels_config, name, None)
                if getattr(cfg, "enabled", False):
                    enabled_external_channels.add(name)

        for item in self.sessions.list_sessions():
            key = str(item.get("key") or "")
            if ":" not in key:
                continue
            session_channel, session_chat_id = key.split(":", 1)
            if session_channel in {"cli", "system", "cron", "heartbeat"}:
                continue
            if enabled_external_channels and session_channel not in enabled_external_channels:
                continue
            if session_chat_id:
                return session_channel, session_chat_id

        return channel, chat_id

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

    async def _stream_chat(
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
        """Yield typed provider stream events when the provider supports them."""
        del job_class
        async for event in self.provider.stream_chat(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        ):
            yield event

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

        self._background_jobs.schedule_background(coro, task_name)

    def _check_session_timeout(self, session_key: str) -> bool:
        """
        Check if a session has timed out due to inactivity.

        Timeout threshold: self.inactivity_timeout_s

        Args:
            session_key: Session identifier.

        Returns:
            True if session timed out, False otherwise.
        """
        return self._session_lifecycle.check_session_timeout(session_key)

    def _update_session_timer(self, session_key: str) -> None:
        """
        Update the last activity timestamp for a session.

        Called on every message to reset the inactivity timer.

        Args:
            session_key: Session identifier.
        """
        self._session_lifecycle.update_session_timer(session_key)

    async def process_expired_sessions(self) -> int:
        """Finalize sessions that exceeded the inactivity timeout."""
        return await self._session_lifecycle.process_expired_sessions(
            schedule_background=self._schedule_background,
            run_session_end=self._run_session_end,
        )

    async def _run_session_end(self, session: Session, reason: str) -> None:
        """Run the session-end pipeline and clear in-progress tracking."""
        await self._session_lifecycle.run_session_end(
            session,
            reason,
            on_session_end=lambda current_session, current_reason: self._on_session_end(
                current_session,
                reason=current_reason,
            ),
        )

    def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """Get/create lock for a session key."""
        return self._session_lifecycle.get_session_lock(session_key)

    def _scratchpad_path(self, session_key: str) -> Path:
        """Get filesystem path for a session's scratchpad."""
        return self._session_lifecycle.scratchpad_path(session_key)

    def _ensure_scratchpad(self, session_key: str) -> Path:
        """Ensure scratchpad file exists for the current session."""
        return self._session_lifecycle.ensure_scratchpad(session_key)

    def _finalize_scratchpad(self, session_key: str, reason: str) -> None:
        """Archive or clear session scratchpad when a session ends."""
        self._session_lifecycle.finalize_scratchpad(session_key, reason)

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
        await self._session_lifecycle.on_session_end(
            session,
            reason=reason,
            messages_snapshot=messages_snapshot,
            schedule_background=self._schedule_background,
            synthesize_journal_from_messages=self._synthesize_journal_from_messages,
            distillation_enabled=self.distillation_enabled,
            distillation_model_available=bool(self._get_model_for_job(JobClass.DISTILLATION)),
            distill_session_from_messages=self._distill_session_from_messages,
            reflection_model_available=bool(self._get_model_for_job(JobClass.REFLECTION)),
            reflect_on_session_from_messages=self._reflect_on_session_from_messages,
        )

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
        await self._background_jobs.synthesize_journal(session, JobClass.JOURNAL_SYNTHESIS)

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
        await self._background_jobs.synthesize_journal_from_messages(
            messages,
            session_key,
            JobClass.JOURNAL_SYNTHESIS,
        )

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
        await self._background_jobs.distill_session(session, JobClass.DISTILLATION)

    def _filter_messages_for_distillation(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
    ) -> list[dict[str, Any]]:
        """Drop scratchpad-specific tool traces so they aren't distilled."""
        return self._background_jobs.filter_messages_for_distillation(messages, session_key)

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

        await self._background_jobs.distill_session_from_messages(
            messages,
            session_key,
            JobClass.DISTILLATION,
        )

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
        self._background_jobs.commit_candidate_to_memory(candidate)

    async def _reflect_on_session(self, session: Session) -> None:
        """
        Reflect on session using the new ReflectionService.

        Single LLM call → 0-1 reflection → auto-promote if pattern.

        Args:
            session: Session to reflect on.
        """
        await self._background_jobs.reflect_on_session(session)

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
        await self._background_jobs.reflect_on_session_from_messages(messages, session_key)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        job_class: JobClass = JobClass.INTERACTIVE_RESPONSE,
    ) -> TurnResult:
        """
        Run the agent iteration loop.

        Phase B: Interactive response (LLM allowed, policy gated).

        Args:
            initial_messages: Initial message list for LLM context.
            on_progress: Optional callback for progress updates.
            job_class: Job class for model routing.

        Returns:
            Structured turn result.
        """
        runner = self._build_turn_runner()
        return await runner.run(initial_messages, on_progress=on_progress, job_class=job_class)

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                    session_key = msg.session_key
                    process_task = asyncio.create_task(self._process_message(msg))
                    self._active_turn_tasks[session_key] = process_task
                    try:
                        response = await process_task
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
                    except asyncio.CancelledError:
                        logger.info("Cancelled active work for session {}", session_key)
                        self.execution_state.clear(session_key)
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="Stopped the active work. Ready for the next request.",
                                metadata=msg.metadata or {},
                            )
                        )
                    except Exception as e:
                        logger.error("Error processing message: {}", e)
                        self.execution_state.set(msg.session_key, ExecutionPhase.FAILED, str(e))
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content=f"Sorry, I encountered an error: {str(e)}",
                            )
                        )
                    finally:
                        current = self._active_turn_tasks.get(session_key)
                        if current is process_task:
                            self._active_turn_tasks.pop(session_key, None)
                except asyncio.TimeoutError:
                    continue
        finally:
            await self._shutdown_background_tasks()
            await self.close_mcp()
            logger.info("Agent loop stopped")

    async def cancel_active_work(
        self, session_key: str, *, cancel_background: bool = False
    ) -> bool:
        """Cancel active turn and delegated work for a session."""
        cancelled = False
        task = self._active_turn_tasks.get(session_key)
        if task is not None and not task.done():
            task.cancel()
            cancelled = True

        channel, chat_id = BackgroundJobManager.derive_channel_chat(session_key)
        cancelled_subagents = await self.subagents.cancel_for_origin(channel, chat_id)
        cancelled = cancelled or cancelled_subagents > 0

        if cancel_background and self._background_tasks:
            await self._shutdown_background_tasks()
            cancelled = True

        if cancelled:
            self.execution_state.clear(session_key)
        return cancelled

    async def _shutdown_background_tasks(self) -> None:
        """Cancel and await all tracked background tasks."""
        await self._background_jobs.shutdown_background_tasks()

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
        return await self._background_jobs.wait_for_background_tasks(timeout_s)

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    async def close(self) -> None:
        """Release externally owned resources used by the agent loop."""
        await self.close_mcp()
        await self.provider.close()

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
        if msg.channel == "system":
            return await self._process_system_message(msg)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)
        return await self._process_user_message(
            msg, session_key=session_key, on_progress=on_progress
        )

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage:
        """Process a synthetic system message such as a subagent completion update."""
        channel, chat_id = msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        logger.info("Processing system message from {}", msg.sender_id)
        key = f"{channel}:{chat_id}"
        self.execution_state.set(key, ExecutionPhase.WAITING_BACKGROUND, "processing system update")
        async with self._session_scope(key):
            session = self.sessions.get_or_create(key)
            history = session.get_history(max_messages=self.memory_window)

            if msg.sender_id == "subagent" and (
                is_subagent_completion_prompt(msg.content)
                or msg.content.lstrip().startswith("[Subagent '")
            ):
                safe_content = summarize_subagent_completion(msg.content)
                all_msgs = [
                    {"role": "system", "content": "deterministic background update"},
                    *history,
                    {"role": "user", "content": msg.content},
                    {"role": "assistant", "content": safe_content},
                ]
                self._save_turn(session, all_msgs, 1 + len(history))
                if "failed" not in safe_content.lower() and "error" not in safe_content.lower():
                    session.metadata.pop("pending_work", None)
                self.sessions.save(session)
                self.execution_state.set(key, ExecutionPhase.COMPLETED, "system update ready")
                return OutboundMessage(channel=channel, chat_id=chat_id, content=safe_content)

            scratchpad_path = self._ensure_scratchpad(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                scratchpad_path=str(scratchpad_path),
            )
            turn_result = await self._run_agent_loop(
                messages,
                job_class=JobClass.INTERACTIVE_RESPONSE,
            )
            self._save_turn(session, turn_result.messages, 1 + len(history))
            self.sessions.save(session)
            safe_content = (
                fallback_system_task_summary(msg.content)
                if not is_grounded_system_reply(msg.content, turn_result.final_content)
                else turn_result.final_content
            )
            self.execution_state.set(key, ExecutionPhase.COMPLETED, "system update ready")
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=safe_content or fallback_system_task_summary(msg.content),
            )

    async def _process_user_message(
        self,
        msg: InboundMessage,
        *,
        session_key: str | None,
        on_progress: Callable[[str], Awaitable[None]] | None,
    ) -> OutboundMessage | None:
        """Process a regular user-facing message turn."""
        key = session_key or msg.session_key
        self.execution_state.set(key, ExecutionPhase.PLANNING, "preparing turn")
        async with self._session_scope(key):
            session = self.sessions.get_or_create(key)
            scratchpad_path = self._ensure_scratchpad(key)

            slash_response = await self._maybe_handle_slash_command(msg, key, session)
            if slash_response is not None:
                return slash_response

            self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
            if message_tool := self.tools.get("message"):
                if isinstance(message_tool, MessageTool):
                    message_tool.start_turn()

            history = session.get_history(max_messages=self.memory_window)
            history_for_prompt = history
            if not history_for_prompt:
                history_for_prompt = self.sessions.get_recent_archived_history(
                    key,
                    max_messages=min(12, self.memory_window),
                )
            build_result = self._build_interactive_messages(
                msg,
                scratchpad_path,
                history_for_prompt,
                session,
            )
            progress_callback = on_progress or self._build_bus_progress_callback(msg, key)
            if build_result.resumed_pending_work is not None:
                await self._announce_resumed_work(
                    key,
                    build_result.resumed_pending_work,
                    progress_callback,
                )
            turn_result = await self._run_agent_loop(
                build_result.messages,
                on_progress=progress_callback,
                job_class=JobClass.INTERACTIVE_RESPONSE,
            )
            final_content = turn_result.final_content
            if is_empty_response(final_content) or is_placeholder_assistant_reply(final_content):
                final_content = self._build_unexpected_empty_turn_fallback(msg.content)
                if turn_result.outcome == TurnOutcome.COMPLETED:
                    turn_result.outcome = TurnOutcome.EMPTY_REPLY

            self._record_final_execution_state(key, final_content, turn_result.outcome)
            preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
            logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

            self._save_turn(session, turn_result.messages, build_result.save_skip)
            self.skill_runtime.update_after_turn(
                session.metadata,
                result_messages=turn_result.messages,
                tools_used=turn_result.tools_used,
            )
            self._update_pending_work(session, msg, turn_result)
            self.sessions.save(session)

            if message_tool := self.tools.get("message"):
                if isinstance(message_tool, MessageTool) and message_tool.has_sent_in_turn:
                    return None

            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=final_content,
                metadata=msg.metadata or {},
            )

    @staticmethod
    def _reply(
        msg: InboundMessage,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> OutboundMessage:
        """Build a standard outbound reply for the current inbound message."""
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata=metadata or {},
        )

    async def _handle_reflect_command(
        self,
        msg: InboundMessage,
        session_key: str,
        session: Session,
    ) -> OutboundMessage:
        """Run reflection on demand for the current conversation."""
        before = len(self.memory.list_memories("reflections"))
        try:
            await self._background_jobs.reflect_on_session_from_messages(
                list(session.messages),
                session_key,
            )
        except Exception as exc:
            logger.warning("Manual reflection failed for {}: {}", session_key, exc)
            return self._reply(msg, "Reflection failed for this conversation.")

        after_items = self.memory.list_memories("reflections")
        if len(after_items) > before and after_items:
            latest = after_items[0]
            return self._reply(
                msg,
                "Reflection complete.\n"
                f"Saved: {latest.title}\n"
                f"Path: {latest.file_path}",
            )

        return self._reply(msg, "Reflection complete. No new reflection was saved.")

    async def _maybe_handle_slash_command(
        self,
        msg: InboundMessage,
        session_key: str,
        session: Session,
    ) -> OutboundMessage | None:
        """Handle early slash commands before building interactive context."""
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            messages_snapshot = list(session.messages)
            await self._on_session_end(
                session, reason="explicit", messages_snapshot=messages_snapshot
            )
            self.execution_state.set(
                session_key, ExecutionPhase.WAITING_BACKGROUND, "starting new session"
            )
            return self._reply(msg, "New session started.")

        if cmd == "/help":
            return self._reply(
                msg,
                "🦀 hermitcrab chat commands:\n"
                "/new — Start a new conversation\n"
                "/reflect — Run reflection on this conversation\n"
                "/help — Show chat commands\n\n"
                "For CLI commands like status, doctor, or onboard, run them in the shell "
                "as `hermitcrab status`, `hermitcrab doctor`, or `hermitcrab onboard`.",
            )

        if cmd == "/reflect":
            return await self._handle_reflect_command(msg, session_key, session)

        return None

    def _build_interactive_messages(
        self,
        msg: InboundMessage,
        scratchpad_path: Path,
        history: list[dict[str, Any]],
        session: Session,
    ) -> InteractiveTurnBuildResult:
        """Build the interactive message list and insert deterministic hints when needed."""
        self.skill_runtime.maybe_activate(
            session.metadata,
            current_message=msg.content,
            history=history,
        )
        messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            scratchpad_path=str(scratchpad_path),
        )
        internal_skip = 1 + len(history)
        pending = PendingWork.from_metadata(session.metadata)
        resumed_pending_work: PendingWork | None = None
        user_index = next(
            (
                idx
                for idx in range(len(messages) - 1, -1, -1)
                if messages[idx].get("role") == "user"
            ),
            None,
        )
        procedural_skill_hint = self.skill_runtime.build_turn_guidance(session.metadata)
        if procedural_skill_hint and user_index is not None:
            messages.insert(
                user_index,
                {
                    "role": "system",
                    "content": procedural_skill_hint,
                },
            )
            internal_skip += 1
            user_index += 1
        if pending and should_resume_pending_work(pending, msg.content):
            resumed_pending_work = pending
            if user_index is not None:
                messages.insert(
                    user_index,
                    {
                        "role": "system",
                        "content": build_pending_work_hint(pending, msg.content),
                    },
                )
                internal_skip += 1
                if skill_hint := build_skill_creation_hint(pending.source_excerpt or pending.origin_request):
                    messages.insert(
                        user_index,
                        {
                            "role": "system",
                            "content": skill_hint,
                        },
                    )
                    internal_skip += 1
        elif skill_hint := build_skill_creation_hint(msg.content):
            if user_index is not None:
                messages.insert(
                    user_index,
                    {
                        "role": "system",
                        "content": skill_hint,
                        },
                    )
                internal_skip += 1
        return InteractiveTurnBuildResult(
            messages=messages,
            save_skip=internal_skip,
            resumed_pending_work=resumed_pending_work,
        )

    async def _announce_resumed_work(
        self,
        session_key: str,
        pending: PendingWork,
        progress_callback: Callable[[str], Awaitable[None]] | None,
    ) -> None:
        """Surface coordinator-owned pending work before the next turn runs."""
        detail = clean_snippet(pending.origin_request, max_chars=80) or "resuming unfinished work"
        self.execution_state.set(session_key, ExecutionPhase.RESUMING, detail)
        if progress_callback is None:
            return
        await progress_callback(
            "Resuming unfinished work from this conversation: "
            f"{clean_snippet(pending.origin_request, max_chars=120)}"
        )

    def _update_pending_work(
        self,
        session: Session,
        msg: InboundMessage,
        result: TurnResult,
    ) -> None:
        """Persist unresolved actionable work at the coordinator layer."""
        pending = PendingWork.from_metadata(session.metadata)
        outcome = result.outcome

        if outcome == TurnOutcome.DELEGATED:
            source_excerpt = find_action_source(
                session.messages + [{"role": "user", "content": msg.content}]
            )
            origin_request = pending.origin_request if pending else msg.content
            created_at = pending.created_at if pending else session.updated_at.isoformat()
            session.metadata["pending_work"] = PendingWork(
                origin_request=origin_request,
                latest_request=msg.content,
                source_excerpt=source_excerpt,
                last_failure="Delegated work is still running.",
                tools_used=result.tools_used,
                created_at=created_at,
                updated_at=session.updated_at.isoformat(),
            ).to_metadata()
            return

        unresolved = {
            TurnOutcome.BLOCKED,
            TurnOutcome.EMPTY_REPLY,
            TurnOutcome.INCOMPLETE_ACTION,
            TurnOutcome.TOOL_FALLBACK,
            TurnOutcome.MAX_ITERATIONS,
            TurnOutcome.TIMEOUT,
            TurnOutcome.REPEATED_TOOL_CYCLE,
        }
        if outcome in unresolved and (
            pending or result.tools_used or has_structured_payload(msg.content)
        ):
            source_excerpt = find_action_source(
                session.messages + [{"role": "user", "content": msg.content}]
            )
            origin_request = pending.origin_request if pending else msg.content
            created_at = pending.created_at if pending else session.updated_at.isoformat()
            session.metadata["pending_work"] = PendingWork(
                origin_request=origin_request,
                latest_request=msg.content,
                source_excerpt=source_excerpt,
                last_failure=snippet(result.final_content, max_chars=280),
                tools_used=result.tools_used,
                created_at=created_at,
                updated_at=session.updated_at.isoformat(),
            ).to_metadata()
            return

        if pending and (
            outcome == TurnOutcome.COMPLETED
            and result.tools_used
            and (
                has_structured_payload(msg.content) or relates_to_pending_work(pending, msg.content)
            )
        ):
            session.metadata.pop("pending_work", None)

    def _build_bus_progress_callback(
        self,
        msg: InboundMessage,
        session_key: str,
    ) -> Callable[[str], Awaitable[None]]:
        """Build the default progress publisher for interactive turns."""

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            if not content or not content.strip():
                return
            phase = ExecutionPhase.RUNNING_TOOLS if tool_hint else ExecutionPhase.PLANNING
            detail = "executing tools" if tool_hint else clean_snippet(content, max_chars=80)
            self.execution_state.set(session_key, phase, detail)
            metadata = dict(msg.metadata or {})
            metadata["_progress"] = True
            metadata["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=metadata,
                )
            )

        return _bus_progress

    def _record_final_execution_state(
        self,
        session_key: str,
        final_content: str,
        outcome: TurnOutcome = TurnOutcome.COMPLETED,
    ) -> None:
        """Record the final execution state for a completed interactive turn."""
        if outcome == TurnOutcome.DELEGATED or (
            "Subagent [" in final_content and "started" in final_content
        ):
            self.execution_state.set(session_key, ExecutionPhase.DELEGATED, "subagent spawned")
            return
        if outcome == TurnOutcome.BLOCKED:
            self.execution_state.set(
                session_key,
                ExecutionPhase.BLOCKED,
                clean_snippet(final_content, max_chars=80),
            )
            return
        if outcome in {
            TurnOutcome.EMPTY_REPLY,
            TurnOutcome.INCOMPLETE_ACTION,
            TurnOutcome.TOOL_FALLBACK,
            TurnOutcome.MAX_ITERATIONS,
            TurnOutcome.TIMEOUT,
            TurnOutcome.REPEATED_TOOL_CYCLE,
        }:
            self.execution_state.set(
                session_key,
                ExecutionPhase.RECOVERING,
                clean_snippet(final_content, max_chars=80),
            )
            return
        self.execution_state.set(
            session_key,
            ExecutionPhase.COMPLETED,
            clean_snippet(final_content, max_chars=80),
        )

    @staticmethod
    def _build_unexpected_empty_turn_fallback(request_text: str) -> str:
        """Last-resort user-facing fallback if a turn ends with no final content."""
        snippet = clean_snippet(request_text, max_chars=120)
        if snippet:
            return (
                "The model returned an empty reply before answering your request"
                f" about: {snippet}\n\n"
                "Please retry the request or switch to a stronger model."
            )
        return "The model returned an empty reply. Please retry the request or switch to a stronger model."

    def _build_turn_runner(self) -> TurnRunner:
        """Build a turn runner configured with the current loop dependencies."""
        return TurnRunner(
            context=self.context,
            tools=self.tools,
            config=TurnRunnerConfig(
                max_iterations=self.max_iterations,
                max_loop_seconds=self.max_loop_seconds,
                max_identical_tool_cycles=self.max_identical_tool_cycles,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self._reasoning_effort,
            ),
            chat_callable=self._chat_with_retry,
            stream_chat_callable=(
                self._stream_chat if isinstance(self.provider, LLMProvider) else None
            ),
            get_model_for_job=self._get_model_for_job,
            strip_think=self._strip_think,
            tool_hint=self._tool_hint,
            is_empty_response=is_empty_response,
        )

    class _SessionScope:
        """Context manager that tracks active turns for one session key."""

        def __init__(self, loop: "AgentLoop", session_key: str):
            self.loop = loop
            self.session_key = session_key
            self.lock: asyncio.Lock | None = None

        async def __aenter__(self) -> None:
            self.lock = self.loop._get_session_lock(self.session_key)
            await self.lock.acquire()
            self.loop._session_active_turns[self.session_key] += 1
            self.loop._update_session_timer(self.session_key)

        async def __aexit__(self, exc_type, exc, tb) -> None:
            self.loop._session_active_turns[self.session_key] -= 1
            if self.loop._session_active_turns[self.session_key] <= 0:
                self.loop._session_active_turns.pop(self.session_key, None)
            if self.lock is not None:
                self.lock.release()

    def _session_scope(self, session_key: str) -> _SessionScope:
        """Return a scoped active-turn guard for one session."""
        return self._SessionScope(self, session_key)

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """
        Save new-turn messages into session, truncating large tool results.

        Also updates the session timer for inactivity timeout tracking.

        Args:
            session: Session to save.
            messages: New messages to append.
            skip: Number of messages to skip from the start.
        """
        self._background_jobs.save_turn(session, messages, skip, self._update_session_timer)

    @staticmethod
    def _build_journal_event_trace(digest: SessionDigest) -> list[str]:
        """Build a journal-safe event trace with raw tool mechanics filtered out."""
        return BackgroundJobManager.build_journal_event_trace(digest)

    def _build_session_digest(
        self, messages: list[dict[str, Any]], session_key: str
    ) -> SessionDigest:
        """Build a deterministic digest of a session for weak-model jobs."""
        return self._background_jobs.build_session_digest(messages, session_key)

    def _format_journal_entry(self, digest: SessionDigest, body: str) -> str:
        """Wrap a journal body with deterministic per-entry metadata."""
        return self._background_jobs.format_journal_entry(digest, body)

    def _build_fallback_journal_body(self, digest: SessionDigest) -> str:
        """Build a deterministic journal narrative when LLM synthesis fails."""
        return self._background_jobs.build_fallback_journal_body(digest)

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
