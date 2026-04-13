"""Filesystem-native reminder artifacts owned by the gateway runtime."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import frontmatter

from hermitcrab.cron.service import compute_next_run_ms
from hermitcrab.cron.types import CronSchedule
from hermitcrab.utils.helpers import ensure_dir, journal_day_wikilink, safe_filename


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ReminderItem:
    """Stored reminder artifact plus its compiled cron linkage."""

    file_path: Path
    title: str
    message: str
    schedule_kind: str
    at: str = ""
    every_seconds: int | None = None
    cron_expr: str = ""
    tz: str = ""
    enabled: bool = True
    status: str = "active"
    related_people: list[str] = field(default_factory=list)
    cron_job_id: str = ""
    channel: str = ""
    chat_id: str = ""
    event_at: str = ""
    remind_offset_minutes: int | None = None
    next_due_at: str = ""
    last_triggered_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, file_path: Path) -> ReminderItem | None:
        try:
            post = frontmatter.load(file_path)
        except Exception:
            return None
        meta = post.metadata or {}
        return cls(
            file_path=file_path,
            title=str(meta.get("title") or file_path.stem),
            message=(post.content or "").strip(),
            schedule_kind=str(meta.get("schedule_kind") or ""),
            at=str(meta.get("at") or ""),
            every_seconds=(
                int(meta["every_seconds"]) if meta.get("every_seconds") not in (None, "") else None
            ),
            cron_expr=str(meta.get("cron_expr") or ""),
            tz=str(meta.get("tz") or ""),
            enabled=bool(meta.get("enabled", True)),
            status=str(meta.get("status") or "active"),
            related_people=[
                str(item).strip() for item in meta.get("related_people", []) if str(item).strip()
            ],
            cron_job_id=str(meta.get("cron_job_id") or ""),
            channel=str(meta.get("channel") or ""),
            chat_id=str(meta.get("chat_id") or ""),
            event_at=str(meta.get("event_at") or ""),
            remind_offset_minutes=(
                int(meta["remind_offset_minutes"])
                if meta.get("remind_offset_minutes") not in (None, "")
                else None
            ),
            next_due_at=str(meta.get("next_due_at") or ""),
            last_triggered_at=str(meta.get("last_triggered_at") or ""),
            created_at=str(meta.get("created_at") or ""),
            updated_at=str(meta.get("updated_at") or ""),
            metadata=meta,
        )

    def to_post(self) -> frontmatter.Post:
        post = frontmatter.Post(self.message)
        post.metadata.update(
            {
                "title": self.title,
                "type": "reminder",
                "schedule_kind": self.schedule_kind,
                "enabled": self.enabled,
                "status": self.status,
                "related_people": self.related_people,
                "cron_job_id": self.cron_job_id,
                "channel": self.channel,
                "chat_id": self.chat_id,
                "event_at": self.event_at,
                "remind_offset_minutes": self.remind_offset_minutes,
                "next_due_at": self.next_due_at,
                "last_triggered_at": self.last_triggered_at,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "journal": journal_day_wikilink(self.created_at or self.updated_at),
            }
        )
        if self.at:
            post.metadata["at"] = self.at
        if self.every_seconds is not None:
            post.metadata["every_seconds"] = self.every_seconds
        if self.cron_expr:
            post.metadata["cron_expr"] = self.cron_expr
        if self.tz:
            post.metadata["tz"] = self.tz
        return post


class ReminderStore:
    """Manage reminder artifacts and their persisted delivery state."""

    def __init__(self, workspace: Path, legacy_cron_store_path: Path | None = None):
        self.workspace = workspace
        self.legacy_reminders_dir = workspace / "knowledge" / "notes" / "reminders"
        self.reminders_dir = ensure_dir(workspace / "reminders")
        self.legacy_cron_store_path = legacy_cron_store_path
        self._migrate_legacy_reminder_files()
        self._migrate_legacy_cron_metadata()

    def _migrate_legacy_reminder_files(self) -> None:
        if not self.legacy_reminders_dir.exists():
            return

        for old_path in sorted(self.legacy_reminders_dir.glob("*.md")):
            new_path = self.reminders_dir / old_path.name
            if new_path.exists():
                continue
            shutil.move(str(old_path), str(new_path))

    def _migrate_legacy_cron_metadata(self) -> None:
        if self.legacy_cron_store_path is None or not self.legacy_cron_store_path.exists():
            return
        try:
            data = json.loads(self.legacy_cron_store_path.read_text(encoding="utf-8"))
        except Exception:
            return

        jobs_by_id = {
            str(job.get("id") or ""): job
            for job in data.get("jobs", [])
            if job.get("id")
        }
        for file_path in sorted(self.reminders_dir.glob("*.md")):
            item = ReminderItem.from_file(file_path)
            if item is None or not item.cron_job_id:
                continue
            if item.channel and item.chat_id and item.next_due_at:
                continue
            job = jobs_by_id.get(item.cron_job_id)
            if not isinstance(job, dict):
                continue
            payload = job.get("payload", {}) or {}
            state = job.get("state", {}) or {}
            changed = False
            if not item.channel and payload.get("channel"):
                item.channel = str(payload["channel"])
                changed = True
            if not item.chat_id and payload.get("to"):
                item.chat_id = str(payload["to"])
                changed = True
            if not item.next_due_at and state.get("nextRunAtMs"):
                next_due = datetime.fromtimestamp(
                    int(state["nextRunAtMs"]) / 1000,
                    tz=timezone.utc,
                ).isoformat()
                item.next_due_at = next_due
                changed = True
            if changed:
                item.updated_at = _utcnow().isoformat()
                item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.casefold().split())

    def _slug_for_title(self, title: str) -> str:
        return safe_filename(title).strip().lower().replace(" ", "-")

    def _path_for_title(self, title: str) -> Path:
        return self.reminders_dir / f"{self._slug_for_title(title)}.md"

    def _verify_reminder_write(self, item: ReminderItem) -> None:
        loaded = ReminderItem.from_file(item.file_path)
        if loaded is None:
            raise ValueError(f"failed to reload reminder after write: {item.file_path}")
        if self._normalize(loaded.title) != self._normalize(item.title):
            raise ValueError(f"reminder title verification failed for {item.file_path}")
        if loaded.schedule_kind != item.schedule_kind or loaded.status != item.status:
            raise ValueError(f"reminder metadata verification failed for {item.file_path}")
        if loaded.related_people != item.related_people:
            raise ValueError(f"reminder related-people verification failed for {item.file_path}")
        if loaded.channel != item.channel or loaded.chat_id != item.chat_id:
            raise ValueError(f"reminder delivery target verification failed for {item.file_path}")
        if loaded.next_due_at != item.next_due_at:
            raise ValueError(f"reminder next-due verification failed for {item.file_path}")
        if loaded.last_triggered_at != item.last_triggered_at:
            raise ValueError(f"reminder last-triggered verification failed for {item.file_path}")

    @staticmethod
    def _compute_next_due_at(
        schedule: CronSchedule,
        *,
        now: datetime,
    ) -> str:
        next_run_ms = compute_next_run_ms(schedule, int(now.timestamp() * 1000))
        if next_run_ms is None:
            return ""
        return datetime.fromtimestamp(next_run_ms / 1000, tz=timezone.utc).isoformat()

    def get_reminder(self, title_or_query: str) -> ReminderItem | None:
        normalized = self._normalize(title_or_query)
        exact: ReminderItem | None = None
        partials: list[ReminderItem] = []
        for file_path in sorted(self.reminders_dir.glob("*.md")):
            item = ReminderItem.from_file(file_path)
            if item is None:
                continue
            title_norm = self._normalize(item.title)
            slug_norm = self._normalize(file_path.stem.replace("-", " "))
            if normalized in {title_norm, slug_norm}:
                exact = item
                break
            if normalized and (
                normalized in title_norm
                or normalized in slug_norm
                or normalized in self._normalize(item.message)
            ):
                partials.append(item)
        return exact or (partials[0] if len(partials) == 1 else None)

    def list_reminders(self, include_completed: bool = False) -> list[ReminderItem]:
        items: list[ReminderItem] = []
        for file_path in sorted(self.reminders_dir.glob("*.md")):
            item = ReminderItem.from_file(file_path)
            if item is None:
                continue
            if not include_completed and item.status != "active":
                continue
            items.append(item)
        return sorted(items, key=lambda item: (item.status != "active", item.title.casefold()))

    def upsert_reminder(
        self,
        *,
        title: str,
        message: str,
        schedule_kind: str,
        channel: str,
        chat_id: str,
        at: str | None = None,
        event_at: str | None = None,
        remind_offset_minutes: int | None = None,
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        related_people: list[str] | None = None,
        existing_query: str | None = None,
    ) -> ReminderItem:
        self._validate_schedule(
            schedule_kind=schedule_kind,
            at=at,
            event_at=event_at,
            remind_offset_minutes=remind_offset_minutes,
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            tz=tz,
        )
        existing = self.get_reminder(existing_query or title)

        schedule = self._build_schedule(
            schedule_kind=schedule_kind,
            at=self._resolve_trigger_at(
                schedule_kind=schedule_kind,
                at=at,
                event_at=event_at,
                remind_offset_minutes=remind_offset_minutes,
            ),
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            tz=tz,
        )
        now = _utcnow().isoformat()
        next_due_at = self._compute_next_due_at(schedule, now=datetime.fromisoformat(now))
        item = ReminderItem(
            file_path=(existing.file_path if existing else self._path_for_title(title)),
            title=title,
            message=message.strip(),
            schedule_kind=schedule_kind,
            at=self._resolve_trigger_at(
                schedule_kind=schedule_kind,
                at=at,
                event_at=event_at,
                remind_offset_minutes=remind_offset_minutes,
            )
            or "",
            every_seconds=every_seconds,
            cron_expr=cron_expr or "",
            tz=tz or "",
            enabled=True,
            status="active",
            related_people=[str(item).strip() for item in (related_people or []) if str(item).strip()],
            cron_job_id="",
            channel=channel,
            chat_id=chat_id,
            event_at=event_at or "",
            remind_offset_minutes=remind_offset_minutes,
            next_due_at=next_due_at,
            last_triggered_at=(existing.last_triggered_at if existing else ""),
            created_at=(existing.created_at if existing and existing.created_at else now),
            updated_at=now,
        )
        item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")
        self._verify_reminder_write(item)
        return item

    @staticmethod
    def render_notification(title: str, message: str) -> str:
        header = f"Reminder: {title.strip()}".strip()
        body = message.strip()
        if not body:
            return header
        if ReminderStore._normalize(body) == ReminderStore._normalize(title):
            return header
        return f"{header}\n{body}"

    def due_reminders(self, *, now: datetime | None = None) -> list[ReminderItem]:
        current = now or _utcnow()
        due: list[ReminderItem] = []
        for item in self.list_reminders():
            if not item.enabled or item.status != "active" or not item.next_due_at:
                continue
            try:
                due_at = datetime.fromisoformat(item.next_due_at)
            except ValueError:
                continue
            if due_at <= current:
                due.append(item)
        return due

    def mark_triggered(self, item: ReminderItem, *, triggered_at: datetime | None = None) -> ReminderItem:
        when = triggered_at or _utcnow()
        schedule = self._build_schedule(
            schedule_kind=item.schedule_kind,
            at=item.at or None,
            every_seconds=item.every_seconds,
            cron_expr=item.cron_expr or None,
            tz=item.tz or None,
        )
        item.last_triggered_at = when.isoformat()
        if item.schedule_kind == "at":
            item.enabled = False
            item.status = "completed"
            item.next_due_at = ""
        else:
            item.next_due_at = self._compute_next_due_at(schedule, now=when)
            item.enabled = bool(item.next_due_at)
            item.status = "active" if item.enabled else "completed"
        item.updated_at = when.isoformat()
        item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")
        self._verify_reminder_write(item)
        return item

    def cancel_reminder(self, title_or_query: str) -> ReminderItem | None:
        item = self.get_reminder(title_or_query)
        if item is None:
            return None
        item.enabled = False
        item.status = "cancelled"
        item.next_due_at = ""
        item.updated_at = _utcnow().isoformat()
        item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")
        self._verify_reminder_write(item)
        return item

    def render_summary(self, item: ReminderItem) -> str:
        schedule = self.render_schedule(item)
        status = item.status
        return f"- {item.title} [{status}] — {schedule}"

    def render_schedule(self, item: ReminderItem) -> str:
        if item.schedule_kind == "at":
            if item.event_at and item.remind_offset_minutes:
                return f"{item.remind_offset_minutes} minutes before {item.event_at}"
            return f"once at {item.at}"
        if item.schedule_kind == "every":
            return f"every {item.every_seconds}s"
        if item.schedule_kind == "cron":
            return (
                f"{item.cron_expr} ({item.tz})" if item.tz else item.cron_expr
            )
        return item.schedule_kind

    def list_related_reminders(
        self,
        person_name: str,
        *,
        include_completed: bool = False,
    ) -> list[ReminderItem]:
        normalized = self._normalize(person_name)
        if not normalized:
            return []
        matches: list[ReminderItem] = []
        for item in self.list_reminders(include_completed=include_completed):
            if any(self._normalize(name) == normalized for name in item.related_people):
                matches.append(item)
        return matches

    def get_next_related_reminder(self, person_name: str) -> ReminderItem | None:
        now = _utcnow()
        future_once: list[tuple[datetime, ReminderItem]] = []
        recurring: list[ReminderItem] = []
        for item in self.list_related_reminders(person_name):
            if item.status != "active":
                continue
            if item.schedule_kind == "at" and item.at:
                try:
                    at_dt = datetime.fromisoformat(item.at)
                except ValueError:
                    continue
                if at_dt >= now:
                    future_once.append((at_dt, item))
                continue
            if item.schedule_kind in {"every", "cron"}:
                recurring.append(item)
        if future_once:
            return sorted(future_once, key=lambda pair: pair[0])[0][1]
        if recurring:
            return sorted(recurring, key=lambda item: item.updated_at or item.created_at)[0]
        return None

    def describe_related_follow_up_state(self, person_name: str) -> str | None:
        reminders = self.list_related_reminders(person_name)
        active = [item for item in reminders if item.status == "active"]
        if not active:
            return None
        next_item = self.get_next_related_reminder(person_name)
        if next_item is None:
            return f"{len(active)} active follow-up(s)"
        if next_item.schedule_kind == "at":
            return f"next follow-up at {next_item.at}"
        return f"recurring follow-up scheduled: {self.render_schedule(next_item)}"

    @staticmethod
    def _validate_schedule(
        *,
        schedule_kind: str,
        at: str | None,
        event_at: str | None,
        remind_offset_minutes: int | None,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
    ) -> None:
        if schedule_kind not in {"at", "every", "cron"}:
            raise ValueError("schedule_kind must be one of: at, every, cron")
        if schedule_kind == "at":
            if at and (event_at or remind_offset_minutes is not None):
                raise ValueError(
                    "at reminders must use either at or event_at with remind_offset_minutes, not both"
                )
            if event_at or remind_offset_minutes is not None:
                if not event_at:
                    raise ValueError("event_at is required when remind_offset_minutes is provided")
                datetime.fromisoformat(event_at)
                if remind_offset_minutes is None or remind_offset_minutes < 0:
                    raise ValueError("remind_offset_minutes must be >= 0")
            else:
                if not at:
                    raise ValueError("at schedule requires an ISO datetime")
                datetime.fromisoformat(at)
            if every_seconds is not None or cron_expr or tz:
                raise ValueError("at reminders cannot include every_seconds, cron_expr, or tz")
            return
        if schedule_kind == "every":
            if every_seconds is None or every_seconds <= 0:
                raise ValueError("every schedule requires every_seconds > 0")
            if at or cron_expr or tz:
                raise ValueError("every reminders cannot include at, cron_expr, or tz")
            return
        if not cron_expr:
            raise ValueError("cron schedule requires cron_expr")
        if at or every_seconds is not None:
            raise ValueError("cron reminders cannot include at or every_seconds")
        if tz:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz)

    @staticmethod
    def _resolve_trigger_at(
        *,
        schedule_kind: str,
        at: str | None,
        event_at: str | None,
        remind_offset_minutes: int | None,
    ) -> str | None:
        if schedule_kind != "at":
            return at
        if event_at:
            event_dt = datetime.fromisoformat(event_at)
            offset = timedelta(minutes=remind_offset_minutes or 0)
            return (event_dt - offset).isoformat()
        return at

    @staticmethod
    def _build_schedule(
        *,
        schedule_kind: str,
        at: str | None,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
    ) -> CronSchedule:
        if schedule_kind == "at":
            dt = datetime.fromisoformat(at or "")
            return CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
        if schedule_kind == "every":
            return CronSchedule(kind="every", every_ms=(every_seconds or 0) * 1000)
        return CronSchedule(kind="cron", expr=cron_expr, tz=tz)
