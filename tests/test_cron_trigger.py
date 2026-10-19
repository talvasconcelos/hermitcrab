"""A due cron job executes; a future one does not; a one-shot disables itself."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from hermitcrab.cron.service import CronService


def _store(tmp_path, at_ms: int):
    job = {
        "id": "job1",
        "name": "trigger",
        "enabled": True,
        "schedule": {"kind": "at", "atMs": at_ms, "everyMs": None, "expr": None, "tz": None},
        "payload": {
            "kind": "agent_turn",
            "message": "hello",
            "deliver": False,
            "channel": None,
            "to": None,
        },
        "state": {
            "nextRunAtMs": at_ms,
            "lastRunAtMs": None,
            "lastStatus": None,
            "lastError": None,
        },
        "createdAtMs": 0,
        "updatedAtMs": 0,
        "deleteAfterRun": False,
    }
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"version": 1, "jobs": [job]}))
    return path


async def test_due_job_runs_and_one_shot_disables_itself(tmp_path) -> None:
    past = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp() * 1000)
    path = _store(tmp_path, past)
    fired: list[str] = []

    async def on_job(job):
        fired.append(job.id)
        return "ok"

    service = CronService(path, on_job, identity_name="owner")
    service._load_store()
    await service._on_timer()

    saved = json.loads(path.read_text())["jobs"][0]
    assert fired == ["job1"]
    assert saved["state"]["lastStatus"] == "ok"
    assert saved["enabled"] is False


async def test_future_job_is_left_alone(tmp_path) -> None:
    future = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
    path = _store(tmp_path, future)
    fired: list[str] = []

    async def on_job(job):
        fired.append(job.id)

    service = CronService(path, on_job, identity_name="owner")
    service._load_store()
    await service._on_timer()

    assert fired == []
