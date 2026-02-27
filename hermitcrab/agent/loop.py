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
import json
import re
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.agent.distillation import AtomicCandidate
from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.reflection import ReflectionCandidate
from hermitcrab.agent.subagent import SubagentManager
from hermitcrab.agent.tools.cron import CronTool
from hermitcrab.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from hermitcrab.agent.tools.message import MessageTool
from hermitcrab.agent.tools.registry import ToolRegistry
from hermitcrab.agent.tools.shell import ExecTool
from hermitcrab.agent.tools.spawn import SpawnTool
from hermitcrab.agent.tools.web import WebFetchTool, WebSearchTool
from hermitcrab.bus.events import InboundMessage, OutboundMessage
from hermitcrab.bus.queue import MessageBus
from hermitcrab.providers.base import LLMProvider
from hermitcrab.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from hermitcrab.config.schema import ChannelsConfig, ExecToolConfig
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
    ):
        from hermitcrab.config.schema import ExecToolConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
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
            }

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.journal = JournalStore(workspace)
        self.memory = MemoryStore(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        # Reflection promoter for bootstrap file updates
        from hermitcrab.agent.reflection import ReflectionPromoter

        if reflection_config:
            self._reflection_promoter = ReflectionPromoter(
                workspace=workspace,
                provider=provider,
                model=self._get_model_for_job(JobClass.REFLECTION) or self.model,
                target_files=reflection_config.get("target_files"),
                max_file_lines=reflection_config.get("max_file_lines", 500),
            )
            self._reflection_auto_promote = reflection_config.get("auto_promote", True)
            self._reflection_notify = reflection_config.get("notify_user", True)
        else:
            # Default promoter with no auto-promotion
            self._reflection_promoter = ReflectionPromoter(
                workspace=workspace,
                provider=provider,
                model=self._get_model_for_job(JobClass.REFLECTION) or self.model,
            )
            self._reflection_auto_promote = False
            self._reflection_notify = True

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        # Track background tasks for cleanup (fire-and-forget, but track for shutdown)
        self._background_tasks: set[asyncio.Task] = set()
        # Session timeout tracking (checked on each message)
        self._session_timers: dict[str, datetime] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))

        # Memory tools - typed APIs for saving knowledge
        from hermitcrab.agent.tools.memory import (
            WriteDecisionTool,
            WriteFactTool,
            WriteGoalTool,
            WriteReflectionTool,
            WriteTaskTool,
        )
        self.tools.register(WriteFactTool(self.memory))
        self.tools.register(WriteDecisionTool(self.memory))
        self.tools.register(WriteGoalTool(self.memory))
        self.tools.register(WriteTaskTool(self.memory))
        self.tools.register(WriteReflectionTool(self.memory))

        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from hermitcrab.agent.tools.mcp import connect_mcp_servers
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
            val = next(iter(tc.arguments.values()), None) if tc.arguments else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
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

        Timeout threshold: INACTIVITY_TIMEOUT_S (30 minutes)

        Args:
            session_key: Session identifier.

        Returns:
            True if session timed out, False otherwise.
        """
        last_activity = self._session_timers.get(session_key)
        if last_activity is None:
            return False

        elapsed = (datetime.now(timezone.utc) - last_activity).total_seconds()
        timed_out = elapsed > INACTIVITY_TIMEOUT_S

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

        # Clean up timer
        self._session_timers.pop(session.key, None)

        # Use snapshot if provided (for explicit reset before clear), otherwise use session
        messages_for_background = messages_snapshot if messages_snapshot is not None else list(session.messages)

        # Phase E: Deferred journal synthesis (non-blocking)
        # Journal is narrative, lossy, non-authoritative
        self._schedule_background(
            self._synthesize_journal_from_messages(messages_for_background, session.key),
            f"journal:{session.key}",
        )

        # Optional: distillation (atomic extraction, local only)
        # Skip if no local model available
        distillation_model = self._get_model_for_job(JobClass.DISTILLATION)
        if distillation_model:
            self._schedule_background(
                self._distill_session_from_messages(messages_for_background, session.key),
                f"distill:{session.key}",
            )
        else:
            logger.debug("Distillation skipped (no local model): {}", session.key)

        # Optional: reflection (meta-analysis)
        reflection_model = self._get_model_for_job(JobClass.REFLECTION)
        if reflection_model:
            self._schedule_background(
                self._reflect_on_session_from_messages(messages_for_background, session.key),
                f"reflect:{session.key}",
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

            prompt = (
                f"Summarize this agent session as a brief narrative.\n"
                f"User messages: {len(user_messages)}\n"
                f"Assistant responses: {len(assistant_messages)}\n"
                f"Tools used: {', '.join(tool_names) if tool_names else 'none'}\n\n"
                f"Write 2-3 sentences about what was accomplished."
            )

            # Try LLM synthesis if model available
            model = self._get_model_for_job(JobClass.JOURNAL_SYNTHESIS)
            if model:
                try:
                    response = await self.provider.chat(
                        messages=[{"role": "user", "content": prompt}],
                        model=model,
                        temperature=0.05,
                        max_tokens=256,
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
        Extract atomic candidates from session (fact, task, goal, decision, reflection).

        Distillation:
        - Produces proposals only (not authoritative)
        - Uses strict JSON schema
        - Validation and commit happen elsewhere (Tier 0)
        - Local only, skip if unavailable

        Args:
            session: Session to distill.
        """
        try:
            from hermitcrab.agent.distillation import (
                AtomicCandidate,
            )

            messages = session.messages
            if not messages:
                return  # Empty session, nothing to distill

            # Build distillation prompt
            prompt = (
                "Extract atomic knowledge candidates from this agent session.\n\n"
                "Look for:\n"
                "- FACTS: User preferences, project context, established truths\n"
                "- DECISIONS: Architectural choices, trade-offs, locked decisions\n"
                "- GOALS: Objectives, outcomes the user wants to achieve\n"
                "- TASKS: Action items, todos, things to do\n"
                "- REFLECTIONS: Insights, patterns, observations about the work\n\n"
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
                "Be conservative - only extract clear, atomic knowledge."
            )

            # Try LLM distillation
            model = self._get_model_for_job(JobClass.DISTILLATION)
            if not model:
                logger.debug("Distillation skipped (no model): {}", session.key)
                return

            try:
                response = await self.provider.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=0.1,
                    max_tokens=2048,
                    # TODO: Add JSON schema enforcement when provider supports it
                    # json_schema=DISTILLATION_JSON_SCHEMA,
                )

                # Parse response (expecting JSON)
                import json

                content = self._strip_think(response.content)
                if not content:
                    return

                # Try to extract JSON from response
                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = content[json_start:json_end]
                    data = json.loads(json_str)

                    candidates = data.get("candidates", [])
                    validated_count = 0

                    for candidate_data in candidates:
                        try:
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
                                candidate_data.get("title", "unknown"),
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
            from hermitcrab.agent.distillation import CandidateType

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

    async def _reflect_on_session(self, session: Session) -> None:
        """
        Meta-analysis of agent behavior.

        Reflection:
        - Identifies mistakes, uncertainty, patterns
        - Never mutates memory directly (proposes reflections)
        - Used to improve future behavior
        - Local preferred, external optional
        - Promotes reflections to bootstrap file updates (if enabled)

        Args:
            session: Session to reflect on.
        """
        try:

            messages = session.messages
            if not messages:
                return  # Empty session, nothing to reflect on

            # Analyze session for reflection triggers
            reflections = await self._analyze_session_for_reflections(session)

            if not reflections:
                logger.debug("No reflections generated: {}", session.key)
                return

            # Commit valid reflections to memory
            committed = 0
            for reflection in reflections:
                reflection.source_session = session.key

                errors = reflection.validate()
                if errors:
                    logger.warning(
                        "Reflection validation failed: {}: {}",
                        reflection.title,
                        errors,
                    )
                    continue

                self._commit_reflection_to_memory(reflection)
                committed += 1

            if committed > 0:
                logger.info(
                    "Reflection complete: {} insights from {}",
                    committed,
                    session.key,
                )

                # Promote reflections to bootstrap file updates (if enabled)
                if self._reflection_auto_promote:
                    self._schedule_background(
                        self._promote_reflections_to_bootstrap(reflections),
                        f"promote:{session.key}",
                    )

        except Exception as e:
            # Reflection failures never block agent operation
            logger.warning("Reflection failed (non-fatal): {}: {}", session.key, e)

    async def _reflect_on_session_from_messages(
        self,
        messages: list[dict],
        session_key: str,
    ) -> None:
        """
        Reflect on session from message list (for use with session snapshots).

        Wrapper around _reflect_on_session that works with a message list instead of Session.

        Args:
            messages: List of session messages.
            session_key: Session identifier.
        """
        class _SessionSnapshot:
            def __init__(self, messages: list[dict], key: str):
                self.messages = messages
                self.key = key

        snapshot = _SessionSnapshot(messages, session_key)
        await self._reflect_on_session(snapshot)

    async def _analyze_session_for_reflections(
        self,
        session: Session,
    ) -> list[ReflectionCandidate]:
        """
        Analyze session for reflection candidates.

        Looks for:
        - Tool errors and failures
        - User corrections
        - Repeated attempts
        - Uncertainty markers
        - Inefficiencies

        Args:
            session: Session to analyze.

        Returns:
            List of reflection candidates.
        """
        from hermitcrab.agent.reflection import ReflectionCandidate, ReflectionType

        reflections: list[ReflectionCandidate] = []
        messages = session.messages

        # Analyze tool results for errors
        tool_errors = self._extract_tool_errors(messages)
        for error in tool_errors:
            reflections.append(
                ReflectionCandidate(
                    type=ReflectionType.MISTAKE,
                    title=f"Tool failure: {error['tool']}",
                    content=f"Tool {error['tool']} failed with: {error['error'][:200]}",
                    tool_involved=error['tool'],
                    error_pattern=error['error'][:100],
                    impact="high" if "error" in error['error'].lower() else "medium",
                    session_context=error.get('context', ''),
                )
            )

        # Analyze for user corrections (look for patterns like "no, I meant" or "that's wrong")
        corrections = self._extract_user_corrections(messages)
        for correction in corrections:
            reflections.append(
                ReflectionCandidate(
                    type=ReflectionType.MISTAKE,
                    title="User correction required",
                    content=f"User corrected agent: {correction['text'][:200]}",
                    user_correction=True,
                    session_context=correction.get('context', ''),
                    suggestion="Review context before responding",
                )
            )

        # Analyze for repeated tool calls (potential inefficiency)
        repeated_tools = self._find_repeated_tool_calls(messages)
        for tool_info in repeated_tools:
            reflections.append(
                ReflectionCandidate(
                    type=ReflectionType.PATTERN,
                    title=f"Repeated tool usage: {tool_info['tool']}",
                    content=f"Tool {tool_info['tool']} called {tool_info['count']} times in session",
                    tool_involved=tool_info['tool'],
                    frequency=f"{tool_info['count']} times in one session",
                    impact="medium",
                    suggestion="Consider caching or batching requests",
                )
            )

        # Analyze for uncertainty markers in assistant responses
        uncertainties = self._extract_uncertainty_markers(messages)
        for uncertainty in uncertainties:
            reflections.append(
                ReflectionCandidate(
                    type=ReflectionType.UNCERTAINTY,
                    title=f"Uncertainty in {uncertainty['topic']}",
                    content=f"Agent expressed uncertainty: {uncertainty['text'][:200]}",
                    session_context=uncertainty.get('context', ''),
                    suggestion="Consider adding knowledge or clarifying questions",
                )
            )

        # If we have multiple mistakes, add an improvement suggestion
        mistakes = [r for r in reflections if r.type == ReflectionType.MISTAKE]
        if len(mistakes) >= 2:
            reflections.append(
                ReflectionCandidate(
                    type=ReflectionType.IMPROVEMENT,
                    title="Multiple failures detected",
                    content=f"Session had {len(mistakes)} mistakes - review error handling",
                    impact="high",
                    suggestion="Improve error recovery or add validation",
                )
            )

        return reflections

    def _extract_tool_errors(self, messages: list[dict]) -> list[dict]:
        """Extract tool errors from messages."""
        errors = []
        for msg in messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                tool_name = msg.get("name", "unknown")
                # Look for error indicators
                if any(indicator in content.lower() for indicator in
                       ["error:", "failed", "exception", "traceback"]):
                    errors.append({
                        "tool": tool_name,
                        "error": content,
                        "context": f"Tool call: {tool_name}",
                    })
        return errors

    def _extract_user_corrections(self, messages: list[dict]) -> list[dict]:
        """Extract user corrections from messages."""
        corrections = []
        correction_patterns = ["no,", "that's wrong", "i meant", "actually,", "not ", "wrong"]

        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "").lower()
                if any(pattern in content for pattern in correction_patterns):
                    corrections.append({
                        "text": msg.get("content", ""),
                        "context": "User correction",
                    })
        return corrections

    def _find_repeated_tool_calls(self, messages: list[dict]) -> list[dict]:
        """Find repeatedly called tools."""
        tool_counts: dict[str, int] = {}
        for msg in messages:
            if msg.get("role") == "tool":
                tool_name = msg.get("name", "unknown")
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        repeated = []
        for tool, count in tool_counts.items():
            if count >= 3:  # Threshold for "repeated"
                repeated.append({"tool": tool, "count": count})
        return repeated

    def _extract_uncertainty_markers(self, messages: list[dict]) -> list[dict]:
        """Extract uncertainty markers from assistant responses."""
        uncertainties = []
        uncertainty_patterns = [
            "i'm not sure", "i don't know", "might be", "could be",
            "possibly", "perhaps", "i think", "i believe", "uncertain"
        ]

        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "").lower()
                if any(pattern in content for pattern in uncertainty_patterns):
                    uncertainties.append({
                        "text": msg.get("content", ""),
                        "topic": "General",
                        "context": "Assistant uncertainty",
                    })
        return uncertainties

    def _commit_reflection_to_memory(self, reflection: ReflectionCandidate) -> None:
        """
        Commit a validated reflection to memory (Tier 0 operation).

        Args:
            reflection: Validated reflection candidate to commit.
        """
        try:
            params = reflection.to_memory_params()
            self.memory.write_reflection(**params)
            logger.info("Memory commit: reflection '{}'", reflection.title)
        except Exception as e:
            logger.error(
                "Failed to commit reflection to memory: {}: {}",
                reflection.title,
                e,
            )

    async def _promote_reflections_to_bootstrap(
        self,
        reflections: list[ReflectionCandidate],
    ) -> dict[str, list[str]]:
        """
        Promote reflections to bootstrap file updates.

        This is the self-improvement mechanism:
        1. Analyze reflections for patterns
        2. Generate bootstrap edit proposals via LLM
        3. Apply edits to AGENTS.md, SOUL.md, IDENTITY.md, TOOLS.md
        4. Notify user of changes (if enabled)

        Args:
            reflections: List of reflection candidates to promote.

        Returns:
            Dict mapping filename to list of applied edits.
        """
        try:
            # Create notification callback
            async def notify_user(message: str) -> None:
                """Send notification to user via message tool."""
                if not self._reflection_notify:
                    return

                # Try to send via message tool if available
                message_tool = self.tools.get("message")
                if message_tool and hasattr(message_tool, "set_context"):
                    # Use last known channel context
                    # TODO: Track channel context per session
                    logger.info("🧠 Self-Improvement: {}", message)
                else:
                    logger.info("🧠 Self-Improvement: {}", message)

            # Run promotion pipeline
            applied_edits = await self._reflection_promoter.promote_reflections(
                reflections=reflections,
                notify_callback=notify_user if self._reflection_notify else None,
            )

            if applied_edits:
                logger.info(
                    "Bootstrap promotion complete: {} files updated",
                    len(applied_edits),
                )
                for filename, edits in applied_edits.items():
                    logger.info("  - {}: {} edit(s)", filename, len(edits))
            else:
                logger.debug("No bootstrap edits applied from {} reflections", len(reflections))

            return applied_edits

        except Exception as e:
            # Bootstrap promotion failures never block agent operation
            logger.warning("Bootstrap promotion failed (non-fatal): {}", e)
            return {}

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

        # Get model for this job class
        model = self._get_model_for_job(job_class)
        if model is None:
            # No model available (distillation case) - skip gracefully
            return None, [], []

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            if response.has_tool_calls:
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
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                final_content = self._strip_think(response.content)
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                try:
                    response = await self._process_message(msg)
                    if response is not None:
                        await self.bus.publish_outbound(response)
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id, content="", metadata=msg.metadata or {},
                        ))
                except Exception as e:
                    logger.error("Error processing message: {}", e)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue

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
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))

            # Update session timer (reset inactivity clock)
            self._update_session_timer(key)

            history = session.get_history(max_messages=self.memory_window)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
            )
            # Phase B happens inside _run_agent_loop
            final_content, _, all_msgs = await self._run_agent_loop(
                messages, job_class=JobClass.INTERACTIVE_RESPONSE,
            )
            # Phase C: Deterministic save
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        # Regular user message
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

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
            await self._on_session_end(session, reason="explicit", messages_snapshot=messages_snapshot)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")

        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 hermitcrab commands:\n/new — Start a new conversation\n/help — Show available commands")

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
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        # ====================================================================
        # Phase B: Interactive response (LLM allowed, policy gated)
        # ====================================================================

        final_content, tools_used, all_msgs = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
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
            k for k in list(self._session_timers.keys())
            if self._check_session_timeout(k)
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
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
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
                    entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
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
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""
