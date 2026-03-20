"""Session timer, scratchpad, and session-end lifecycle helpers."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from hermitcrab.session.manager import Session, SessionManager
from hermitcrab.utils.helpers import ensure_dir, safe_filename


class SessionLifecycleManager:
    """Own session timers, scratchpads, and session-end orchestration."""

    def __init__(
        self,
        *,
        workspace: Path,
        sessions: SessionManager,
        inactivity_timeout_s: int,
    ) -> None:
        self.workspace = workspace
        self.sessions = sessions
        self.inactivity_timeout_s = max(1, inactivity_timeout_s)
        self.scratchpad_dir = ensure_dir(workspace / "scratchpads")
        self.session_timers: dict[str, datetime] = {}
        self.session_active_turns: defaultdict[str, int] = defaultdict(int)
        self.session_end_in_progress: set[str] = set()
        self.session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def check_session_timeout(self, session_key: str) -> bool:
        last_activity = self.session_timers.get(session_key)
        if last_activity is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last_activity).total_seconds()
        timed_out = elapsed > self.inactivity_timeout_s
        if timed_out:
            logger.info("Session timed out ({}s inactivity): {}", elapsed, session_key)
        return timed_out

    def update_session_timer(self, session_key: str) -> None:
        self.session_timers[session_key] = datetime.now(timezone.utc)

    def get_session_lock(self, session_key: str) -> asyncio.Lock:
        return self.session_locks[session_key]

    def scratchpad_path(self, session_key: str) -> Path:
        return self.scratchpad_dir / f"{safe_filename(session_key.replace(':', '_'))}.md"

    def ensure_scratchpad(self, session_key: str) -> Path:
        path = self.scratchpad_path(session_key)
        if not path.exists():
            path.write_text(
                f"# Scratchpad: {session_key}\n\nTransient notes for this session. Archived on session end.\n",
                encoding="utf-8",
            )
        return path

    def finalize_scratchpad(self, session_key: str, reason: str) -> None:
        path = self.scratchpad_path(session_key)
        if not path.exists():
            return
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            path.unlink(missing_ok=True)
            return

        archive_dir = ensure_dir(self.scratchpad_dir / "archive")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        archive_name = f"{safe_filename(session_key.replace(':', '_'))}-{reason}-{ts}.md"
        archive_path = archive_dir / archive_name
        path.replace(archive_path)
        logger.info("Archived scratchpad for {} -> {}", session_key, archive_path.name)

    async def process_expired_sessions(
        self,
        *,
        schedule_background: Callable[[Awaitable[Any], str], None],
        run_session_end: Callable[[Session, str], Awaitable[None]],
    ) -> int:
        expired_keys = [
            key
            for key in list(self.session_timers.keys())
            if (
                key not in self.session_end_in_progress
                and self.session_active_turns.get(key, 0) == 0
                and self.check_session_timeout(key)
            )
        ]

        for session_key in expired_keys:
            self.session_end_in_progress.add(session_key)
            session = self.sessions.get_or_create(session_key)
            schedule_background(run_session_end(session, "timeout"), f"session_end:{session_key}")

        return len(expired_keys)

    async def run_session_end(
        self,
        session: Session,
        reason: str,
        *,
        on_session_end: Callable[[Session, str], Awaitable[None]],
    ) -> None:
        try:
            async with self.get_session_lock(session.key):
                if self.session_active_turns.get(session.key, 0) > 0:
                    logger.debug("Skipping session end while session is active: {}", session.key)
                    return
                await on_session_end(session, reason)
        finally:
            self.session_end_in_progress.discard(session.key)

    async def on_session_end(
        self,
        session: Session,
        *,
        reason: str,
        messages_snapshot: list[dict[str, Any]] | None,
        schedule_background: Callable[[Awaitable[Any], str], None],
        synthesize_journal_from_messages: Callable[[list[dict[str, Any]], str], Awaitable[None]],
        distillation_enabled: bool,
        distillation_model_available: bool,
        distill_session_from_messages: Callable[[list[dict[str, Any]], str], Awaitable[None]],
        reflection_model_available: bool,
        reflect_on_session_from_messages: Callable[[list[dict[str, Any]], str], Awaitable[None]],
    ) -> None:
        logger.info("Session ended ({}): {}", reason, session.key)
        self.finalize_scratchpad(session.key, reason)
        self.session_timers.pop(session.key, None)

        all_messages = (
            messages_snapshot if messages_snapshot is not None else list(session.messages)
        )
        last_cognition_index = int(session.metadata.get("last_cognition_index", 0) or 0)
        last_cognition_index = max(0, min(last_cognition_index, len(all_messages)))
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
            return

        logger.debug(
            "Session end pipeline start: key={} reason={} messages={}",
            session.key,
            reason,
            len(messages_for_background),
        )
        schedule_background(
            synthesize_journal_from_messages(messages_for_background, session.key),
            f"journal:{session.key}",
        )
        logger.debug("Scheduled journal synthesis for {}", session.key)

        if distillation_enabled and distillation_model_available:
            schedule_background(
                distill_session_from_messages(messages_for_background, session.key),
                f"distill:{session.key}",
            )
            logger.debug("Scheduled distillation for {}", session.key)
        else:
            logger.debug(
                "Distillation skipped (enabled={}, model={}): {}",
                distillation_enabled,
                distillation_model_available,
                session.key,
            )

        if reflection_model_available:
            schedule_background(
                reflect_on_session_from_messages(messages_for_background, session.key),
                f"reflect:{session.key}",
            )
            logger.debug("Scheduled reflection for {}", session.key)
        else:
            logger.debug("Reflection skipped (no model): {}", session.key)
