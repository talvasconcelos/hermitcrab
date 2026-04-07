"""Reminder tools backed by filesystem artifacts and cron jobs."""

from __future__ import annotations

from typing import Any

from hermitcrab.agent.reminders import ReminderStore
from hermitcrab.agent.tools.base import Tool


class ReminderTool(Tool):
    """Create, inspect, update, and cancel reminder artifacts."""

    def __init__(self, reminders: ReminderStore):
        self.reminders = reminders
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "reminder"

    @property
    def description(self) -> str:
        return (
            "Manage first-class reminders and simple recurring events. Use this instead of raw cron "
            "for user-facing reminders, recurring schedules, inspection, updates, and cancellation."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "show", "update", "cancel"],
                    "description": "Reminder action to perform",
                },
                "title": {"type": "string", "description": "Reminder title"},
                "query": {
                    "type": "string",
                    "description": "Reminder title or search text for show/cancel/update",
                },
                "message": {"type": "string", "description": "What to remind the user about"},
                "schedule_kind": {
                    "type": "string",
                    "enum": ["at", "every", "cron"],
                    "description": "Reminder schedule type",
                },
                "at": {
                    "type": "string",
                    "description": "One-time ISO datetime, e.g. 2026-04-09T09:00:00",
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Recurring interval in seconds",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression for recurring reminders",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone for cron schedules",
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "Include cancelled/completed reminders when listing",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        title: str = "",
        query: str = "",
        message: str = "",
        schedule_kind: str = "",
        at: str = "",
        every_seconds: int | None = None,
        cron_expr: str = "",
        tz: str = "",
        include_completed: bool = False,
        **kwargs: Any,
    ) -> str:
        if action == "list":
            return self._list(include_completed=include_completed)
        if action == "show":
            return self._show(query or title)
        if action == "cancel":
            return self._cancel(query or title)
        if action in {"create", "update"}:
            return self._upsert(
                title=title,
                query=query,
                message=message,
                schedule_kind=schedule_kind,
                at=at,
                every_seconds=every_seconds,
                cron_expr=cron_expr,
                tz=tz,
            )
        return f"Error: Unknown action: {action}"

    def _list(self, *, include_completed: bool) -> str:
        reminders = self.reminders.list_reminders(include_completed=include_completed)
        if not reminders:
            return "No reminders found."
        lines = ["Reminders:", ""]
        lines.extend(self.reminders.render_summary(item) for item in reminders)
        return "\n".join(lines)

    def _show(self, query: str) -> str:
        if not query.strip():
            return "Error: query is required for show"
        item = self.reminders.get_reminder(query)
        if item is None:
            return f"Reminder not found: {query}"
        lines = [
            f"Reminder: {item.title}",
            f"Status: {item.status}",
            f"Schedule: {self.reminders.render_schedule(item)}",
            f"Path: {item.file_path}",
            "",
            item.message,
        ]
        return "\n".join(lines)

    def _cancel(self, query: str) -> str:
        if not query.strip():
            return "Error: query is required for cancel"
        item = self.reminders.cancel_reminder(query)
        if item is None:
            return f"Reminder not found: {query}"
        return f"Cancelled reminder: {item.title}\nPath: {item.file_path}"

    def _upsert(
        self,
        *,
        title: str,
        query: str,
        message: str,
        schedule_kind: str,
        at: str,
        every_seconds: int | None,
        cron_expr: str,
        tz: str,
    ) -> str:
        if not self._channel or not self._chat_id:
            return "Error: no session context (channel/chat_id)"
        if not title.strip():
            return "Error: title is required"
        if not message.strip():
            return "Error: message is required"
        if not schedule_kind:
            return "Error: schedule_kind is required"
        try:
            item = self.reminders.upsert_reminder(
                title=title.strip(),
                message=message.strip(),
                schedule_kind=schedule_kind,
                channel=self._channel,
                chat_id=self._chat_id,
                at=at or None,
                every_seconds=every_seconds,
                cron_expr=cron_expr or None,
                tz=tz or None,
                existing_query=(query or title).strip(),
            )
        except ValueError as exc:
            return f"Error: {exc}"
        verb = "Updated" if query.strip() else "Created"
        return (
            f"{verb} reminder: {item.title}\n"
            f"Schedule: {self.reminders.render_schedule(item)}\n"
            f"Path: {item.file_path}"
        )
