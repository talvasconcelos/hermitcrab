"""Journal and reflection helpers for background cognition."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger

from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.message_preparation import is_low_signal_journal_body
from hermitcrab.agent.reflection import ReflectionService
from hermitcrab.agent.session_digest import SessionDigestBuilder


class JournalBackgroundManager:
    """Own journal synthesis and reflection jobs."""

    def __init__(
        self,
        *,
        journal: JournalStore,
        reflection_service: ReflectionService,
        digest_builder: SessionDigestBuilder,
        chat_callable: Callable[..., Awaitable[Any]],
        get_model_for_job: Callable[[Any], str | None],
        strip_think: Callable[[str | None], str | None],
        reasoning_effort: str | None,
    ) -> None:
        self.journal = journal
        self.reflection_service = reflection_service
        self.digest_builder = digest_builder
        self.chat_callable = chat_callable
        self.get_model_for_job = get_model_for_job
        self.strip_think = strip_think
        self.reasoning_effort = reasoning_effort

    async def synthesize_journal(self, session: Any, journal_job_class: Any) -> None:
        try:
            if not session.messages:
                return
            digest = self.digest_builder.build_session_digest(session.messages, session.key)
            prompt = self._build_journal_prompt(digest)

            model = self.get_model_for_job(journal_job_class)
            if model:
                try:
                    response = await self.chat_callable(
                        messages=[{"role": "user", "content": prompt}],
                        model=model,
                        temperature=0.05,
                        max_tokens=256,
                        job_class=journal_job_class,
                        reasoning_effort=self.reasoning_effort,
                    )
                    content = self.strip_think(response.content)
                    if content and not is_low_signal_journal_body(content):
                        self.journal.write_entry(
                            content=self.digest_builder.format_journal_entry(digest, content),
                            session_keys=[session.key],
                            tags=["session", "synthesis"],
                        )
                        logger.info("Journal synthesized (LLM): {}", session.key)
                        return
                except Exception as exc:
                    logger.warning("Journal LLM failed, using fallback: {}", exc)

            fallback = self.digest_builder.format_journal_entry(
                digest,
                self.digest_builder.build_fallback_journal_body(digest),
            )
            self.journal.write_entry(
                content=fallback, session_keys=[session.key], tags=["session", "fallback"]
            )
            logger.info("Journal written (fallback): {}", session.key)
        except Exception as exc:
            logger.warning("Journal synthesis failed (non-fatal): {}: {}", session.key, exc)

    def _build_journal_prompt(self, digest: Any) -> str:
        candidate_links = ", ".join(digest.wikilinks) if digest.wikilinks else "none"
        journal_event_trace = self.digest_builder.build_journal_event_trace(digest)
        return (
            "Write a short first-person journal entry about what happened in this session.\n"
            "Sound like a useful human journal entry that is still understandable days later, not telemetry.\n"
            "Preserve concrete specifics: what the user wanted, what changed, the important artifacts, the outcome, and anything still open.\n"
            "Use Obsidian-style wikilinks when referencing tasks, goals, decisions, reflections, or named work items.\n"
            "Do not mention counts of messages, requests, or tool calls.\n\n"
            f"Session: {digest.session_key}\n"
            f"Channel: {digest.channel}\n"
            f"Chat: {digest.chat_id}\n"
            f"Time range: {digest.first_timestamp} -> {digest.last_timestamp}\n"
            f"Candidate wikilinks: {candidate_links}\n\n"
            f"User goal: {digest.user_goal or 'unknown'}\n"
            f"Artifacts changed: {', '.join(digest.artifacts_changed) if digest.artifacts_changed else 'none'}\n"
            f"Decisions made: {', '.join(digest.decisions_made) if digest.decisions_made else 'none'}\n"
            f"Open loops: {', '.join(digest.open_loops) if digest.open_loops else 'none'}\n\n"
            "Event trace:\n"
            f"{chr(10).join(journal_event_trace[:18])}\n\n"
            "Write 4-6 sentences. Avoid vague phrasing like 'worked on it' without specifics."
        )

    async def synthesize_journal_from_messages(
        self,
        messages: list[dict[str, Any]],
        session_key: str,
        journal_job_class: Any,
    ) -> None:
        class _SessionSnapshot:
            def __init__(self, snapshot_messages: list[dict[str, Any]], key: str):
                self.messages = snapshot_messages
                self.key = key

        await self.synthesize_journal(_SessionSnapshot(messages, session_key), journal_job_class)

    async def reflect_on_session(self, session: Any) -> None:
        await self.reflection_service.reflect_on_session(
            messages=session.messages,
            session_key=session.key,
            digest=self.digest_builder.build_session_digest(session.messages, session.key),
        )

    async def reflect_on_session_from_messages(
        self, messages: list[dict[str, Any]], session_key: str
    ) -> None:
        await self.reflection_service.reflect_on_session(
            messages=messages,
            session_key=session_key,
            digest=self.digest_builder.build_session_digest(messages, session_key),
        )
