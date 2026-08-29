from __future__ import annotations

from hermitcrab.cron.service import CronService
from hermitcrab.cron.types import CronSchedule


def test_cron_job_without_tz_persists_local_iana_zone(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, identity_name="owner")

    job = service.add_job(
        name="Morning brief",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *"),
        message="Brief me",
    )

    assert job.schedule.tz

    # Reload from disk to confirm the resolved zone name is persisted.
    reloaded = CronService(store_path, identity_name="owner")
    persisted = reloaded.list_jobs()
    assert persisted[0].schedule.tz == job.schedule.tz


def test_cron_job_explicit_tz_is_preserved(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, identity_name="owner")

    job = service.add_job(
        name="New York brief",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/New_York"),
        message="Brief me",
    )

    assert job.schedule.tz == "America/New_York"
