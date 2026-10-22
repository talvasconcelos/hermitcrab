"""Durable inbound work: persist before ack, replay pending, flag interrupted turns."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermitcrab.bus.events import InboundMessage
from hermitcrab.bus.inbox import (
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_UNCERTAIN,
    InboundInbox,
)
from hermitcrab.bus.queue import MessageBus


def _message(canonical_id: str = "nip04:abc123", content: str = "hello") -> InboundMessage:
    return InboundMessage(
        channel="nostr",
        sender_id="sender",
        chat_id="chat",
        content=content,
        metadata={"canonical_id": canonical_id, "event_id": canonical_id.split(":", 1)[1]},
        session_key_override="nostr:chat",
    )


def test_record_persists_before_the_first_ack(tmp_path: Path) -> None:
    inbox = InboundInbox(tmp_path / "inbound.sqlite3")

    assert inbox.record(_message(), "nip04:abc123") is True
    assert inbox.record(_message(), "nip04:abc123") is False

    record = inbox.get("nip04:abc123")
    assert record is not None
    assert record.status == STATUS_PENDING
    assert record.metadata["event_id"] == "abc123"
    inbox.close()


@pytest.mark.asyncio
async def test_bus_claims_on_consume_and_completes_on_ack(tmp_path: Path) -> None:
    inbox = InboundInbox(tmp_path / "inbound.sqlite3")
    bus = MessageBus(inbox=inbox)

    await bus.publish_inbound(_message())
    assert inbox.get("nip04:abc123").status == STATUS_PENDING

    consumed = await bus.consume_inbound()
    assert inbox.get("nip04:abc123").status == STATUS_PROCESSING
    assert consumed.content == "hello"

    await bus.ack_inbound(consumed)
    assert inbox.get("nip04:abc123").status == STATUS_COMPLETED
    inbox.close()


@pytest.mark.asyncio
async def test_duplicate_publish_does_not_enqueue_twice(tmp_path: Path) -> None:
    inbox = InboundInbox(tmp_path / "inbound.sqlite3")
    bus = MessageBus(inbox=inbox)

    await bus.publish_inbound(_message())
    await bus.publish_inbound(_message())

    assert bus.inbound_size == 1
    inbox.close()


@pytest.mark.asyncio
async def test_pending_work_is_replayed_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "inbound.sqlite3"
    first = InboundInbox(path)
    await MessageBus(inbox=first).publish_inbound(_message())
    first.close()

    second = InboundInbox(path)
    bus = MessageBus(inbox=second)
    assert await bus.recover_inbound() == 1

    recovered = await bus.consume_inbound()
    assert recovered.content == "hello"
    assert recovered.metadata["canonical_id"] == "nip04:abc123"
    await bus.ack_inbound(recovered)
    assert second.get("nip04:abc123").status == STATUS_COMPLETED
    second.close()


@pytest.mark.asyncio
async def test_interrupted_turn_is_marked_uncertain_and_not_replayed(tmp_path: Path) -> None:
    path = tmp_path / "inbound.sqlite3"
    first = InboundInbox(path)
    bus = MessageBus(inbox=first)
    await bus.publish_inbound(_message())
    await bus.consume_inbound()
    # Simulate a crash mid-turn: no ack, no failure.
    first.close()

    second = InboundInbox(path)
    restarted = MessageBus(inbox=second)
    assert await restarted.recover_inbound() == 0
    assert restarted.inbound_size == 0
    assert second.get("nip04:abc123").status == STATUS_UNCERTAIN
    second.close()


@pytest.mark.asyncio
async def test_completed_work_is_not_replayed(tmp_path: Path) -> None:
    path = tmp_path / "inbound.sqlite3"
    first = InboundInbox(path)
    bus = MessageBus(inbox=first)
    await bus.publish_inbound(_message())
    msg = await bus.consume_inbound()
    await bus.ack_inbound(msg)
    first.close()

    second = InboundInbox(path)
    restarted = MessageBus(inbox=second)
    assert await restarted.recover_inbound() == 0
    assert second.get("nip04:abc123").status == STATUS_COMPLETED
    second.close()


@pytest.mark.asyncio
async def test_failure_is_recorded_and_not_replayed(tmp_path: Path) -> None:
    path = tmp_path / "inbound.sqlite3"
    first = InboundInbox(path)
    bus = MessageBus(inbox=first)
    await bus.publish_inbound(_message())
    msg = await bus.consume_inbound()
    await bus.fail_inbound(msg, "provider exploded")
    first.close()

    second = InboundInbox(path)
    record = second.get("nip04:abc123")
    assert record.status == "failed"
    assert "provider exploded" in record.error
    assert await MessageBus(inbox=second).recover_inbound() == 0
    second.close()


@pytest.mark.asyncio
async def test_messages_without_a_canonical_id_remain_ephemeral(tmp_path: Path) -> None:
    inbox = InboundInbox(tmp_path / "inbound.sqlite3")
    bus = MessageBus(inbox=inbox)
    cli_message = InboundMessage(channel="cli", sender_id="local", chat_id="local", content="hi")

    await bus.publish_inbound(cli_message)

    assert bus.inbound_size == 1
    assert inbox.counts() == {}
    inbox.close()
