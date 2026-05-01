"""Focused regressions for identity-scoped reminder delivery."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermitcrab.agent.reminders import ReminderStore
from hermitcrab.agent.tools.cron import CronTool
from hermitcrab.agent.tools.reminders import ReminderTool
from hermitcrab.reminders.service import ReminderService


@pytest.mark.asyncio
async def test_reminder_services_deliver_only_their_identity_due_items(tmp_path: Path) -> None:
    alice_store = ReminderStore(tmp_path / "identities" / "alice")
    bob_store = ReminderStore(tmp_path / "identities" / "bob")
    alice_store.upsert_reminder(
        title="Alice dentist",
        message="Dentist at 10",
        schedule_kind="at",
        channel="nostr",
        chat_id="alice-pubkey",
        at="2099-04-13T09:00:00+00:00",
    )
    bob_store.upsert_reminder(
        title="Bob school pickup",
        message="Pickup at 16",
        schedule_kind="at",
        channel="nostr",
        chat_id="bob-pubkey",
        at="2099-04-13T09:00:00+00:00",
    )
    alice_delivered: list[str] = []
    bob_delivered: list[str] = []

    async def notify_alice(item, content: str) -> None:
        alice_delivered.append(f"{item.chat_id}:{content}")

    async def notify_bob(item, content: str) -> None:
        bob_delivered.append(f"{item.chat_id}:{content}")

    due_at = datetime(2099, 4, 13, 9, 1, tzinfo=timezone.utc)
    alice_count = await ReminderService(alice_store, on_notify=notify_alice).tick(now=due_at)
    bob_count = await ReminderService(bob_store, on_notify=notify_bob).tick(now=due_at)

    assert alice_count == 1
    assert bob_count == 1
    assert alice_delivered == ["alice-pubkey:Reminder: Alice dentist\nDentist at 10"]
    assert bob_delivered == ["bob-pubkey:Reminder: Bob school pickup\nPickup at 16"]
    assert alice_store.get_reminder("Bob school pickup") is None
    assert bob_store.get_reminder("Alice dentist") is None


def test_tool_descriptions_enforce_reminder_vs_cron_responsibilities(tmp_path: Path) -> None:
    reminder_description = ReminderTool(ReminderStore(tmp_path / "identity")).description
    cron_description = CronTool(MagicMock()).description

    assert "morning brief" in reminder_description
    assert "Use cron instead" in reminder_description
    assert "daily briefs" in cron_description
    assert "generated output" in cron_description
