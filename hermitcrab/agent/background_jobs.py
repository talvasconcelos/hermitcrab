"""Facade for background cognition helpers used by `AgentLoop`."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from hermitcrab.agent.background_task_tracker import BackgroundTaskTracker
from hermitcrab.agent.distillation import AtomicCandidate
from hermitcrab.agent.distillation_background import DistillationManager
from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.journal_background import JournalBackgroundManager
from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.reflection import ReflectionService
from hermitcrab.agent.session_digest import SessionDigest, SessionDigestBuilder
from hermitcrab.agent.turn_persistence import TurnPersistence


class BackgroundJobManager:
    """Own background task scheduling and session-end cognition work."""

    def __init__(
        self,
        *,
        workspace: Path,
        journal: JournalStore,
        memory: MemoryStore,
        reflection_service: ReflectionService,
        chat_callable: Callable[..., Awaitable[Any]],
        get_model_for_job: Callable[[Any], str | None],
        strip_think: Callable[[str | None], str | None],
        reasoning_effort: str | None,
    ) -> None:
        self.workspace = workspace
        self.journal = journal
        self.memory = memory
        self.reflection_service = reflection_service
        self.chat_callable = chat_callable
        self.get_model_for_job = get_model_for_job
        self.strip_think = strip_think
        self.reasoning_effort = reasoning_effort
        self._tracker = BackgroundTaskTracker()
        self._digest_builder = SessionDigestBuilder()
        self._journal_manager = JournalBackgroundManager(
            journal=journal,
            reflection_service=reflection_service,
            digest_builder=self._digest_builder,
            chat_callable=chat_callable,
            get_model_for_job=get_model_for_job,
            strip_think=strip_think,
            reasoning_effort=reasoning_effort,
        )
        self._distillation_manager = DistillationManager(
            workspace=workspace,
            memory=memory,
            chat_callable=chat_callable,
            get_model_for_job=get_model_for_job,
            strip_think=strip_think,
            reasoning_effort=reasoning_effort,
        )
        self._background_tasks = self._tracker.tasks

    def schedule_background(self, coro: Awaitable[Any], task_name: str) -> None:
        """Schedule a background task and keep non-fatal failures isolated."""
        self._tracker.schedule(coro, task_name)

    async def shutdown_background_tasks(self) -> None:
        """Cancel and await all tracked background tasks."""
        await self._tracker.shutdown()

    async def wait_for_background_tasks(self, timeout_s: float = 5.0) -> tuple[int, int]:
        """Wait for currently scheduled background tasks to finish."""
        return await self._tracker.wait(timeout_s)

    @staticmethod
    def derive_channel_chat(session_key: str) -> tuple[str, str]:
        return SessionDigestBuilder.derive_channel_chat(session_key)

    @staticmethod
    def safe_iso_timestamp(value: str | None) -> str:
        return SessionDigestBuilder.safe_iso_timestamp(value)

    @staticmethod
    def extract_tool_name(call: dict[str, Any]) -> str:
        return SessionDigestBuilder.extract_tool_name(call)

    @staticmethod
    def extract_tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
        return SessionDigestBuilder.extract_tool_arguments(call)

    @staticmethod
    def build_journal_event_trace(digest: SessionDigest) -> list[str]:
        return SessionDigestBuilder.build_journal_event_trace(digest)

    def build_session_digest(
        self, messages: list[dict[str, Any]], session_key: str
    ) -> SessionDigest:
        return self._digest_builder.build_session_digest(messages, session_key)

    @staticmethod
    def format_digest_timestamp(value: str) -> str:
        return SessionDigestBuilder.format_digest_timestamp(value)

    def format_journal_entry(self, digest: SessionDigest, body: str) -> str:
        return self._digest_builder.format_journal_entry(digest, body)

    @staticmethod
    def build_fallback_journal_body(digest: SessionDigest) -> str:
        return SessionDigestBuilder.build_fallback_journal_body(digest)

    async def synthesize_journal(self, session: Any, journal_job_class: Any) -> None:
        await self._journal_manager.synthesize_journal(session, journal_job_class)

    async def synthesize_journal_from_messages(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
        journal_job_class: Any,
    ) -> None:
        await self._journal_manager.synthesize_journal_from_messages(
            messages,
            session_key,
            journal_job_class,
        )

    def tool_call_targets_scratchpad(self, tc: dict[str, Any], session_key: str) -> bool:
        return self._distillation_manager.tool_call_targets_scratchpad(tc, session_key)

    def filter_messages_for_distillation(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
    ) -> list[dict[str, Any]]:
        return self._distillation_manager.filter_messages_for_distillation(messages, session_key)

    def commit_candidate_to_memory(self, candidate: AtomicCandidate) -> None:
        self._distillation_manager.commit_candidate_to_memory(candidate)

    @staticmethod
    def normalize_memory_text(text: str) -> str:
        return DistillationManager.normalize_memory_text(text)

    def is_near_duplicate_memory_item(self, candidate: AtomicCandidate, existing: Any) -> bool:
        return self._distillation_manager.is_near_duplicate_memory_item(candidate, existing)

    def find_existing_memory_duplicates(self, candidate: AtomicCandidate) -> list[Any]:
        return self._distillation_manager.find_existing_memory_duplicates(candidate)

    def should_commit_distilled_candidate(self, candidate: AtomicCandidate) -> bool:
        return self._distillation_manager.should_commit_distilled_candidate(candidate)

    async def distill_session(self, session: Any, distillation_job_class: Any) -> None:
        await self._distillation_manager.distill_session(session, distillation_job_class)

    async def distill_session_from_messages(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
        distillation_job_class: Any,
    ) -> None:
        await self._distillation_manager.distill_session_from_messages(
            messages,
            session_key,
            distillation_job_class,
        )

    async def reflect_on_session(self, session: Any) -> None:
        await self._journal_manager.reflect_on_session(session)

    async def reflect_on_session_from_messages(
        self, messages: list[dict[str, Any]], session_key: str
    ) -> None:
        await self._journal_manager.reflect_on_session_from_messages(messages, session_key)

    def save_turn(
        self,
        session: Any,
        messages: list[dict[str, Any]],
        skip: int,
        update_session_timer: Callable[[str], None],
    ) -> None:
        TurnPersistence.save_turn(session, messages, skip, update_session_timer)
