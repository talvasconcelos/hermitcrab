"""Cron service for scheduling agent tasks."""

import asyncio
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

from hermitcrab.cron.types import (
    CronConflict,
    CronJob,
    CronJobState,
    CronPayload,
    CronSchedule,
    CronStore,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _local_tz() -> Any:
    """Resolve the local IANA timezone for DST-aware cron scheduling."""
    try:
        from zoneinfo import ZoneInfo

        from tzlocal import get_localzone_name

        return ZoneInfo(get_localzone_name())
    except Exception:
        # Fall back to the previous fixed-offset behavior rather than failing scheduling.
        return datetime.now().astimezone().tzinfo


def _local_timezone_name() -> str | None:
    """Return the local IANA timezone name, or None when it cannot be resolved."""
    try:
        from tzlocal import get_localzone_name

        return get_localzone_name()
    except Exception:
        return None


def compute_next_run_ms(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter
            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else _local_tz()
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """Validate schedule fields that would otherwise create non-runnable jobs."""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


class CronService:
    """Service for managing and executing scheduled jobs."""

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
        *,
        identity_name: str = "owner",
        conflict_finder: Callable[[CronSchedule, int], list[CronConflict]] | None = None,
    ):
        self.store_path = store_path
        self.on_job = on_job  # Callback to execute job, returns response text
        self.identity_name = identity_name
        self.conflict_finder = conflict_finder
        self._store: CronStore | None = None
        self._timer_task: asyncio.Task | None = None
        self._running = False

    def _load_store(self) -> CronStore:
        """Load jobs from disk."""
        if self._store:
            return self._store

        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                jobs = []
                for j in data.get("jobs", []):
                    jobs.append(CronJob(
                        id=j["id"],
                        name=j["name"],
                        enabled=j.get("enabled", True),
                        schedule=CronSchedule(
                            kind=j["schedule"]["kind"],
                            at_ms=j["schedule"].get("atMs"),
                            every_ms=j["schedule"].get("everyMs"),
                            expr=j["schedule"].get("expr"),
                            tz=j["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            kind=j["payload"].get("kind", "agent_turn"),
                            message=j["payload"].get("message", ""),
                            deliver=j["payload"].get("deliver", False),
                            channel=j["payload"].get("channel"),
                            to=j["payload"].get("to"),
                        ),
                        state=CronJobState(
                            next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                            last_status=j.get("state", {}).get("lastStatus"),
                            last_error=j.get("state", {}).get("lastError"),
                        ),
                        created_at_ms=j.get("createdAtMs", 0),
                        updated_at_ms=j.get("updatedAtMs", 0),
                        delete_after_run=j.get("deleteAfterRun", False),
                    ))
                self._store = CronStore(jobs=jobs)
            except Exception as e:
                logger.warning("Failed to load cron store: {}", e)
                self._store = CronStore()
        else:
            self._store = CronStore()

        return self._store

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ]
        }

        self.store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        self._load_store()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        logger.info("Cron service started with {} jobs", len(self._store.jobs if self._store else []))

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs."""
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = compute_next_run_ms(job.schedule, now)

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest next run time across all jobs."""
        if not self._store:
            return None
        times = [j.state.next_run_at_ms for j in self._store.jobs
                 if j.enabled and j.state.next_run_at_ms]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """Schedule the next timer tick."""
        if self._timer_task:
            self._timer_task.cancel()

        next_wake = self._get_next_wake_ms()
        if not next_wake or not self._running:
            return

        delay_ms = max(0, next_wake - _now_ms())
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs."""
        if not self._store:
            return

        now = _now_ms()
        due_jobs = [
            j for j in self._store.jobs
            if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
        ]

        due_jobs = sorted(due_jobs, key=lambda j: (j.state.next_run_at_ms or 0, j.name, j.id))

        for job in due_jobs:
            await self._execute_job(job)

        self._save_store()
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job."""
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        try:
            if self.on_job:
                await self.on_job(job)

            job.state.last_status = "ok"
            job.state.last_error = None
            logger.info("Cron: job '{}' completed", job.name)

        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.error("Cron: job '{}' failed: {}", job.name, e)

        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = _now_ms()

        # Handle one-shot jobs
        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # Compute next run
                job.state.next_run_at_ms = compute_next_run_ms(job.schedule, _now_ms())

    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    def find_local_schedule_conflicts(
        self,
        schedule: CronSchedule,
        *,
        now_ms: int | None = None,
        window_ms: int = 0,
    ) -> list[CronConflict]:
        """Find enabled jobs whose next run conflicts with the proposed schedule."""
        now = now_ms if now_ms is not None else _now_ms()
        next_run = compute_next_run_ms(schedule, now)
        if next_run is None:
            return []

        conflicts: list[CronConflict] = []
        store = self._load_store()
        for job in store.jobs:
            if not job.enabled:
                continue
            job_next_run = job.state.next_run_at_ms
            if job_next_run is None or job_next_run <= now:
                job_next_run = compute_next_run_ms(job.schedule, now)
            if job_next_run is None:
                continue
            if abs(job_next_run - next_run) <= window_ms:
                conflicts.append(
                    CronConflict(
                        identity_name=self.identity_name,
                        job_id=job.id,
                        job_name=job.name,
                        next_run_at_ms=job_next_run,
                    )
                )

        return conflicts

    def find_schedule_conflicts(
        self,
        schedule: CronSchedule,
        *,
        now_ms: int | None = None,
        window_ms: int = 0,
    ) -> list[CronConflict]:
        """Find local and externally reported conflicts for the proposed schedule."""
        now = now_ms if now_ms is not None else _now_ms()
        conflicts = self.find_local_schedule_conflicts(schedule, now_ms=now, window_ms=window_ms)
        if self.conflict_finder is not None:
            conflicts.extend(self.conflict_finder(schedule, now))
        return conflicts

    def _format_conflicts(self, conflicts: list[CronConflict]) -> str:
        return ", ".join(
            f"{conflict.identity_name}:{conflict.job_name} ({conflict.job_id})"
            for conflict in conflicts
        )

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        payload_kind: str = "agent_turn",
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
        allow_conflicts: bool = False,
        conflict_window_s: int = 0,
    ) -> CronJob:
        """Add a new job."""
        store = self._load_store()
        _validate_schedule_for_add(schedule)
        # Persist the resolved IANA zone name so scheduling stays deterministic across DST
        # transitions. An explicit `--tz` (schedule.tz) already set above is left untouched.
        if schedule.kind == "cron" and not schedule.tz:
            local_name = _local_timezone_name()
            if local_name:
                schedule.tz = local_name
        now = _now_ms()
        next_run_at_ms = compute_next_run_ms(schedule, now)
        if next_run_at_ms is None:
            raise ValueError("schedule has no future run")
        if not allow_conflicts:
            conflicts = self.find_schedule_conflicts(
                schedule,
                now_ms=now,
                window_ms=max(0, conflict_window_s) * 1000,
            )
            if conflicts:
                raise ValueError(
                    "schedule conflicts with existing job(s): "
                    f"{self._format_conflicts(conflicts)}"
                )

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind=payload_kind,
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
            ),
            state=CronJobState(next_run_at_ms=next_run_at_ms),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )

        store.jobs.append(job)
        self._save_store()
        self._arm_timer()

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        store = self._load_store()
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            self._save_store()
            self._arm_timer()
            logger.info("Cron: removed job {}", job_id)

        return removed

    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """Enable or disable a job."""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                job.enabled = enabled
                job.updated_at_ms = _now_ms()
                if enabled:
                    job.state.next_run_at_ms = compute_next_run_ms(job.schedule, _now_ms())
                else:
                    job.state.next_run_at_ms = None
                self._save_store()
                self._arm_timer()
                return job
        return None

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job."""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                if not force and not job.enabled:
                    return False
                await self._execute_job(job)
                self._save_store()
                self._arm_timer()
                return True
        return False

    def status(self) -> dict:
        """Get service status."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
