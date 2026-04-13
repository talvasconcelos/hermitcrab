"""Gateway-owned reminder delivery service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from loguru import logger

from hermitcrab.agent.reminders import ReminderItem, ReminderStore


class ReminderService:
    """Periodically checks reminder artifacts and delivers due notifications."""

    def __init__(
        self,
        store: ReminderStore,
        *,
        on_notify: Callable[[ReminderItem, str], Awaitable[None]],
        interval_s: int = 15,
        enabled: bool = True,
    ) -> None:
        self.store = store
        self.on_notify = on_notify
        self.interval_s = max(1, interval_s)
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Reminder service disabled")
            return
        if self._running:
            logger.warning("Reminder service already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="reminder-service")
        logger.info("Reminder service started (every {}s)", self.interval_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.tick()
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reminder service error: {}", exc)

    async def tick(self, *, now: datetime | None = None) -> int:
        due = self.store.due_reminders(now=now)
        delivered = 0
        for item in due:
            if not item.channel or not item.chat_id:
                logger.warning(
                    "Skipping reminder without delivery target: {} ({})",
                    item.title,
                    item.file_path,
                )
                continue
            content = self.store.render_notification(item.title, item.message)
            await self.on_notify(item, content)
            self.store.mark_triggered(item, triggered_at=now)
            delivered += 1
        if delivered:
            logger.info("Reminder service delivered {} reminder(s)", delivered)
        return delivered
