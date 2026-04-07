"""Filesystem-native reminder artifacts compiled into cron jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

from hermitcrab.cron.service import CronService
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
    """Manage reminder artifacts and keep them compiled into cron jobs."""

    def __init__(self, workspace: Path, cron_service: CronService):
        self.workspace = workspace
        self.cron = cron_service
        self.reminders_dir = ensure_dir(workspace / "knowledge" / "notes" / "reminders")

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
        if item.status == "active" and item.cron_job_id and loaded.cron_job_id != item.cron_job_id:
            raise ValueError(f"reminder cron linkage verification failed for {item.file_path}")

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
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        related_people: list[str] | None = None,
        existing_query: str | None = None,
    ) -> ReminderItem:
        self._validate_schedule(
            schedule_kind=schedule_kind,
            at=at,
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            tz=tz,
        )
        existing = self.get_reminder(existing_query or title)
        if existing and existing.cron_job_id:
            self.cron.remove_job(existing.cron_job_id)

        schedule = self._build_schedule(
            schedule_kind=schedule_kind,
            at=at,
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            tz=tz,
        )
        delete_after_run = schedule.kind == "at"
        cron_job = self.cron.add_job(
            name=title[:80],
            schedule=schedule,
            message=message,
            deliver=True,
            channel=channel,
            to=chat_id,
            delete_after_run=delete_after_run,
        )

        now = _utcnow().isoformat()
        item = ReminderItem(
            file_path=(existing.file_path if existing else self._path_for_title(title)),
            title=title,
            message=message.strip(),
            schedule_kind=schedule_kind,
            at=at or "",
            every_seconds=every_seconds,
            cron_expr=cron_expr or "",
            tz=tz or "",
            enabled=True,
            status="active",
            related_people=[str(item).strip() for item in (related_people or []) if str(item).strip()],
            cron_job_id=cron_job.id,
            created_at=(existing.created_at if existing and existing.created_at else now),
            updated_at=now,
        )
        item.file_path.write_text(frontmatter.dumps(item.to_post()), encoding="utf-8")
        self._verify_reminder_write(item)
        return item

    def cancel_reminder(self, title_or_query: str) -> ReminderItem | None:
        item = self.get_reminder(title_or_query)
        if item is None:
            return None
        if item.cron_job_id:
            self.cron.remove_job(item.cron_job_id)
        item.enabled = False
        item.status = "cancelled"
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
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
    ) -> None:
        if schedule_kind not in {"at", "every", "cron"}:
            raise ValueError("schedule_kind must be one of: at, every, cron")
        if schedule_kind == "at":
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
