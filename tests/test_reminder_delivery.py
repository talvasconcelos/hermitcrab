"""Reminder delivery is not acknowledged until a channel accepts the message."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from hermitcrab.agent.reminders import ReminderStore
from hermitcrab.bus.outbox import DeliveryStore
from hermitcrab.reminders.service import ReminderService

DUE_AT = datetime(2099, 4, 13, 9, 1, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> ReminderStore:
    store = ReminderStore(tmp_path / "identity")
    store.upsert_reminder(
        title="Dentist",
        message="Dentist at 10",
        schedule_kind="at",
        channel="nostr",
        chat_id="recipient-pubkey",
        at="2099-04-13T09:00:00+00:00",
    )
    return store


@pytest.mark.asyncio
async def test_failed_delivery_does_not_mark_the_reminder_triggered(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def notify(item, content: str) -> None:
        raise RuntimeError("relay unavailable")

    service = ReminderService(store, on_notify=notify, max_delivery_attempts=3)
    delivered = await service.tick(now=DUE_AT)

    assert delivered == 0
    item = store.get_reminder("Dentist")
    assert item is not None
    assert item.status == "active"
    assert item.last_triggered_at == ""
    assert item.delivery_status == "retrying"
    assert item.delivery_attempts == 1
    assert "relay unavailable" in item.last_delivery_error


@pytest.mark.asyncio
async def test_reminder_is_triggered_only_after_a_retry_succeeds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    calls: list[int] = []

    async def notify(item, content: str) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary outage")

    service = ReminderService(store, on_notify=notify, max_delivery_attempts=3)

    assert await service.tick(now=DUE_AT) == 0
    item = store.get_reminder("Dentist")
    assert item.status == "active" and item.last_triggered_at == ""

    assert await service.tick(now=DUE_AT) == 1
    item = store.get_reminder("Dentist")
    assert item.status == "completed"
    assert item.last_triggered_at
    assert item.delivery_status == "sent"
    assert item.delivery_attempts == 0


@pytest.mark.asyncio
async def test_delivery_stops_after_the_attempt_budget(tmp_path: Path) -> None:
    store = _store(tmp_path)
    calls: list[int] = []

    async def notify(item, content: str) -> None:
        calls.append(1)
        raise RuntimeError("still down")

    service = ReminderService(store, on_notify=notify, max_delivery_attempts=2)

    assert await service.tick(now=DUE_AT) == 0
    assert await service.tick(now=DUE_AT) == 0
    assert await service.tick(now=DUE_AT) == 0

    assert len(calls) == 2
    item = store.get_reminder("Dentist")
    assert item.delivery_status == "failed"
    assert item.status == "active"
    assert item.last_triggered_at == ""


@pytest.mark.asyncio
async def test_outbox_records_the_failed_attempt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outbox = DeliveryStore(tmp_path / "outbox.sqlite3")

    async def notify(item, content: str) -> None:
        raise RuntimeError("relay rejected the event")

    service = ReminderService(
        store,
        on_notify=notify,
        delivery_store=outbox,
        max_delivery_attempts=3,
    )
    await service.tick(now=DUE_AT)

    item = store.get_reminder("Dentist")
    record = outbox.get_by_dedupe(service._delivery_key(item))
    assert record is not None
    assert record.status == "failed"
    assert record.attempts == 1
    assert "relay rejected" in record.last_error
    assert item.delivery_id == str(record.id)
    outbox.close()


@pytest.mark.asyncio
async def test_outbox_marks_sent_and_links_the_reminder(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outbox = DeliveryStore(tmp_path / "outbox.sqlite3")

    async def notify(item, content: str) -> None:
        return None

    service = ReminderService(
        store,
        on_notify=notify,
        delivery_store=outbox,
        max_delivery_attempts=3,
    )
    assert await service.tick(now=DUE_AT) == 1

    item = store.get_reminder("Dentist")
    record = outbox.get(int(item.delivery_id))
    assert record is not None
    assert record.status == "sent"
    assert record.attempts == 1
    outbox.close()


@pytest.mark.asyncio
async def test_uncertain_outbox_record_is_not_resent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outbox = DeliveryStore(tmp_path / "outbox.sqlite3")
    calls: list[int] = []

    async def notify(item, content: str) -> None:
        calls.append(1)

    service = ReminderService(
        store,
        on_notify=notify,
        delivery_store=outbox,
        max_delivery_attempts=3,
    )
    item = store.get_reminder("Dentist")
    record = outbox.enqueue(
        dedupe_key=service._delivery_key(item),
        kind="reminder",
        channel=item.channel,
        chat_id=item.chat_id,
        content="Reminder: Dentist\nDentist at 10",
    )
    outbox.begin_attempt(record.id)
    outbox.mark_uncertain(record.id, "process died during send")

    assert await service.tick(now=DUE_AT) == 0
    assert calls == []
    updated = store.get_reminder("Dentist")
    assert updated.delivery_status == "uncertain"
    assert updated.status == "active"
    outbox.close()
