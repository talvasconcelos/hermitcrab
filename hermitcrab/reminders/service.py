"""Gateway-owned reminder delivery service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from loguru import logger

from hermitcrab.agent.reminders import ReminderItem, ReminderStore
from hermitcrab.bus.outbox import (
    STATUS_SENDING,
    STATUS_SENT,
    STATUS_UNCERTAIN,
    DeliveryRecord,
    DeliveryStore,
)


class ReminderService:
    """Periodically checks reminder artifacts and delivers due notifications."""

    def __init__(
        self,
        store: ReminderStore,
        *,
        on_notify: Callable[[ReminderItem, str], Awaitable[None]],
        interval_s: int = 15,
        enabled: bool = True,
        delivery_store: DeliveryStore | None = None,
        max_delivery_attempts: int = 5,
    ) -> None:
        self.store = store
        self.on_notify = on_notify
        self.interval_s = max(1, interval_s)
        self.enabled = enabled
        self.delivery_store = delivery_store
        self.max_delivery_attempts = max(1, max_delivery_attempts)
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

    @staticmethod
    def _delivery_key(item: ReminderItem) -> str:
        return f"reminder:{item.file_path}:{item.next_due_at}"

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
            if item.delivery_status == "uncertain":
                logger.warning(
                    "Reminder '{}' has an uncertain delivery; not resending automatically",
                    item.title,
                )
                continue
            if (
                item.delivery_status == "failed"
                and item.delivery_attempts >= self.max_delivery_attempts
            ):
                logger.warning(
                    "Reminder '{}' delivery failed permanently after {} attempt(s); inspect {}",
                    item.title,
                    item.delivery_attempts,
                    item.file_path,
                )
                continue

            content = self.store.render_notification(item.title, item.message)
            delivery: DeliveryRecord | None = None
            if self.delivery_store is not None:
                delivery = self.delivery_store.enqueue(
                    dedupe_key=self._delivery_key(item),
                    kind="reminder",
                    channel=item.channel,
                    chat_id=item.chat_id,
                    content=content,
                    max_attempts=self.max_delivery_attempts,
                )
                if delivery.status == STATUS_SENT:
                    # A previous process died after the send but before the reminder advanced.
                    self.store.mark_triggered(
                        item,
                        triggered_at=now,
                        delivery_id=str(delivery.id),
                        delivery_status="sent",
                    )
                    delivered += 1
                    continue
                if delivery.status == STATUS_SENDING:
                    # The previous process died mid-send; the relay may have accepted it.
                    error = "interrupted during send"
                    self.delivery_store.mark_uncertain(delivery.id, error)
                    self.store.mark_delivery_uncertain(
                        item, error, delivery_id=str(delivery.id)
                    )
                    logger.warning(
                        "Reminder '{}' was interrupted mid-send; marked uncertain",
                        item.title,
                    )
                    continue
                if delivery.status == STATUS_UNCERTAIN:
                    self.store.mark_delivery_uncertain(
                        item,
                        delivery.last_error or "delivery outcome unknown",
                        delivery_id=str(delivery.id),
                    )
                    continue
                if not self.delivery_store.begin_attempt(delivery.id):
                    latest = self.delivery_store.get(delivery.id)
                    self.store.mark_delivery_failed(
                        item,
                        (latest.last_error if latest else "") or "delivery attempts exhausted",
                        max_attempts=self.max_delivery_attempts,
                        delivery_id=str(delivery.id),
                    )
                    continue

            try:
                await self.on_notify(item, content)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if delivery is not None:
                    self.delivery_store.mark_failed(delivery.id, str(exc))
                self.store.mark_delivery_failed(
                    item,
                    str(exc),
                    max_attempts=self.max_delivery_attempts,
                    delivery_id=str(delivery.id) if delivery else "",
                )
                logger.error("Reminder delivery failed for '{}': {}", item.title, exc)
                continue

            if delivery is not None:
                self.delivery_store.mark_sent(delivery.id)
            self.store.mark_triggered(
                item,
                triggered_at=now,
                delivery_id=str(delivery.id) if delivery else "",
                delivery_status="sent",
            )
            delivered += 1

        if delivered:
            logger.info("Reminder service delivered {} reminder(s)", delivered)
        return delivered
