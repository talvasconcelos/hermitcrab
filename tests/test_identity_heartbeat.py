"""Focused regressions for identity scheduling behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermitcrab.bus.queue import MessageBus
from hermitcrab.cli.gateway_runtime import GatewayIdentityRuntimeState
from hermitcrab.config.schema import Config
from hermitcrab.cron.service import CronService
from hermitcrab.cron.types import CronSchedule
from hermitcrab.heartbeat.service import HeartbeatService


def _future_at(offset_ms: int = 3_600_000) -> CronSchedule:
    import time

    return CronSchedule(kind="at", at_ms=int(time.time() * 1000) + offset_ms)


def test_cron_rejects_same_identity_next_run_conflict(tmp_path: Path) -> None:
    service = CronService(tmp_path / "alice" / "cron" / "jobs.json", identity_name="alice")
    schedule = _future_at()
    service.add_job(name="Morning brief", schedule=schedule, message="Brief me")

    with pytest.raises(ValueError, match="schedule conflicts"):
        service.add_job(name="Calendar check", schedule=schedule, message="Check calendar")


def test_cron_rejects_schedule_with_no_future_run(tmp_path: Path) -> None:
    import time

    service = CronService(tmp_path / "cron" / "jobs.json", identity_name="owner")
    past = CronSchedule(kind="at", at_ms=int(time.time() * 1000) - 1_000)

    with pytest.raises(ValueError, match="no future run"):
        service.add_job(name="Past job", schedule=past, message="Too late")


def test_cron_conflict_detection_ignores_disabled_jobs(tmp_path: Path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json", identity_name="owner")
    schedule = _future_at()
    job = service.add_job(name="Disabled", schedule=schedule, message="Disabled")
    service.enable_job(job.id, enabled=False)

    created = service.add_job(name="Replacement", schedule=schedule, message="Replacement")

    assert created.name == "Replacement"


def test_gateway_scheduler_rejects_cross_identity_cron_conflict(tmp_path: Path) -> None:
    class Channels:
        enabled_channels: list[str] = []

    state = GatewayIdentityRuntimeState(
        config=Config.model_validate({"root": str(tmp_path)}),
        bus=MagicMock(),
        channels=Channels(),
        create_provider=MagicMock(),
        cron_service_factory=MagicMock(),
        heartbeat_service_factory=MagicMock(),
        on_reminder_notify=MagicMock(),
        heartbeat_interval_s=60,
        heartbeat_enabled=True,
        reminder_interval_s=60,
        reminder_service_factory=MagicMock(),
        agents={},
        cron_services={},
        heartbeat_services={},
        reminder_services={},
    )
    alice = CronService(tmp_path / "alice" / "cron" / "jobs.json", identity_name="alice")
    bob = CronService(
        tmp_path / "bob" / "cron" / "jobs.json",
        identity_name="bob",
        conflict_finder=lambda schedule, now_ms: state.find_cron_conflicts(
            schedule,
            now_ms=now_ms,
            exclude_key="bob",
        ),
    )
    state.cron_services["alice"] = alice
    state.cron_services["bob"] = bob

    schedule = _future_at()
    alice.add_job(name="Alice brief", schedule=schedule, message="Brief Alice")

    with pytest.raises(ValueError, match="alice:Alice brief"):
        bob.add_job(name="Bob brief", schedule=schedule, message="Brief Bob")


@pytest.mark.asyncio
async def test_gateway_scheduler_registers_configured_active_identities(tmp_path: Path) -> None:
    class Channels:
        enabled_channels: list[str] = []

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "identities": {
                "ownerIdentity": "owner",
                "registry": {
                    "alice": {"active": True},
                    "bob": {"active": False},
                },
            },
        }
    )
    state = GatewayIdentityRuntimeState(
        config=config,
        bus=MessageBus(),
        channels=Channels(),
        create_provider=lambda: provider,
        cron_service_factory=lambda **kwargs: CronService(
            kwargs["identity_root"] / "cron" / "jobs.json",
            identity_name=kwargs["identity_name"],
            conflict_finder=kwargs["conflict_finder"],
        ),
        heartbeat_service_factory=MagicMock(),
        on_reminder_notify=MagicMock(),
        heartbeat_interval_s=60,
        heartbeat_enabled=True,
        reminder_interval_s=60,
        reminder_service_factory=MagicMock(),
        agents={},
        cron_services={},
        heartbeat_services={},
        reminder_services={},
    )

    await state.attach_configured_identity_agents()

    alice_pubkey = config.identities.registry["alice"].nostr_public_key
    alice_key = f"identity:{alice_pubkey}"
    bob_pubkey = config.identities.registry["bob"].nostr_public_key
    bob_key = f"identity:{bob_pubkey}"

    assert alice_key in state.agents
    assert alice_key in state.cron_services
    assert state.cron_services[alice_key].store_path == (
        tmp_path / "identities" / "alice" / "cron" / "jobs.json"
    )
    assert bob_key not in state.agents
    assert bob_key not in state.cron_services


@pytest.mark.asyncio
async def test_cron_runs_preexisting_simultaneous_jobs_sequentially(tmp_path: Path) -> None:
    import time

    service = CronService(tmp_path / "cron" / "jobs.json", identity_name="owner")
    first = service.add_job(
        name="First",
        schedule=_future_at(),
        message="first",
        allow_conflicts=True,
    )
    second = service.add_job(
        name="Second",
        schedule=first.schedule,
        message="second",
        allow_conflicts=True,
    )
    ran: list[str] = []

    async def on_job(job):
        ran.append(job.name)

    service.on_job = on_job
    service._load_store()
    now_ms = int(time.time() * 1000)
    first.state.next_run_at_ms = now_ms - 1
    second.state.next_run_at_ms = now_ms - 1

    await service._on_timer()

    assert ran == ["First", "Second"]


@pytest.mark.asyncio
async def test_heartbeat_reads_each_identity_root_independently(tmp_path: Path) -> None:
    alice_root = tmp_path / "identities" / "alice"
    bob_root = tmp_path / "identities" / "bob"
    alice_root.mkdir(parents=True)
    bob_root.mkdir(parents=True)
    alice_root.joinpath("HEARTBEAT.md").write_text(
        "<!-- HEARTBEAT_DIRECT -->\n\n## Active Tasks\n- Check Alice calendar\n",
        encoding="utf-8",
    )
    bob_root.joinpath("HEARTBEAT.md").write_text(
        "<!-- HEARTBEAT_DISABLED -->\n\n## Active Tasks\n- Check Bob calendar\n",
        encoding="utf-8",
    )
    executed: list[str] = []

    async def on_execute(tasks: str) -> str:
        executed.append(tasks)
        return "done"

    alice = HeartbeatService(
        alice_root,
        provider=MagicMock(),
        model="test-model",
        on_execute=on_execute,
    )
    bob = HeartbeatService(
        bob_root,
        provider=MagicMock(),
        model="test-model",
        on_execute=on_execute,
    )

    await alice._tick()
    await bob._tick()

    assert executed == ["- Check Alice calendar"]
