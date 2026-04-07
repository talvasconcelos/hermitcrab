"""People profile tools."""

from __future__ import annotations

from typing import Any

from hermitcrab.agent.people import PeopleStore
from hermitcrab.agent.reminders import ReminderStore
from hermitcrab.agent.tools.base import Tool


class PersonProfileTool(Tool):
    """Create, inspect, and update people profiles."""

    def __init__(self, people: PeopleStore, reminders: ReminderStore | None = None):
        self.people = people
        self.reminders = reminders
        self._channel = ""
        self._chat_id = ""

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "person_profile"

    @property
    def description(self) -> str:
        return (
            "Manage named people profiles. Use for family members, collaborators, clients, "
            "contacts, aliases, and per-person notes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "show", "update", "deactivate", "follow_up"],
                    "description": "People profile action",
                },
                "name": {"type": "string", "description": "Profile name"},
                "query": {"type": "string", "description": "Name or alias lookup for show/update"},
                "role": {
                    "type": "string",
                    "enum": [
                        "owner",
                        "family",
                        "child",
                        "member",
                        "guest",
                        "contact",
                        "client",
                        "collaborator",
                    ],
                    "description": "Relationship or role",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "inactive"],
                    "description": "Profile status",
                },
                "timezone": {
                    "type": "string",
                    "description": "Optional IANA timezone for the person",
                },
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alternative names or nicknames",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional organizing tags",
                },
                "notes": {"type": "string", "description": "Freeform profile notes"},
                "include_inactive": {
                    "type": "boolean",
                    "description": "Include inactive people profiles when listing",
                },
                "title": {"type": "string", "description": "Optional follow-up reminder title"},
                "message": {"type": "string", "description": "Follow-up reminder message"},
                "schedule_kind": {
                    "type": "string",
                    "enum": ["at", "every", "cron"],
                    "description": "Follow-up reminder schedule type",
                },
                "at": {
                    "type": "string",
                    "description": "One-time ISO datetime for a follow-up reminder",
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Recurring interval in seconds for a follow-up reminder",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression for a recurring follow-up reminder",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone for a cron-based follow-up reminder",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        name: str = "",
        query: str = "",
        role: str = "",
        status: str = "active",
        timezone: str = "",
        aliases: list[str] | None = None,
        tags: list[str] | None = None,
        notes: str = "",
        include_inactive: bool = False,
        title: str = "",
        message: str = "",
        schedule_kind: str = "",
        at: str = "",
        every_seconds: int | None = None,
        cron_expr: str = "",
        tz: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "list":
            items = self.people.list_profiles(include_inactive=include_inactive)
            if not items:
                return "No people profiles found."
            lines = ["People profiles:", ""]
            lines.extend(self.people.render_summary(item) for item in items)
            return "\n".join(lines)

        if action == "show":
            lookup = query.strip() or name.strip()
            if not lookup:
                return "Error: query is required for show"
            item = self.people.get_profile(lookup)
            if item is None:
                return f"People profile not found: {lookup}"
            lines = [
                f"Person profile: {item.name}",
                f"Role: {item.role}",
                f"Status: {item.status}",
                f"Path: {item.file_path}",
            ]
            if item.timezone:
                lines.append(f"Timezone: {item.timezone}")
            if item.aliases:
                lines.append(f"Aliases: {', '.join(item.aliases)}")
            if item.tags:
                lines.append(f"Tags: {', '.join(item.tags)}")
            if item.notes:
                lines.extend(["", item.notes])
            if self.reminders is not None:
                related = self.reminders.list_related_reminders(item.name)
                if related:
                    lines.extend(["", "Follow-ups:"])
                    lines.extend(self.reminders.render_summary(reminder) for reminder in related)
            return "\n".join(lines)

        if action in {"create", "update"}:
            if not name.strip():
                return "Error: name is required"
            if not role.strip():
                return "Error: role is required"
            try:
                item = self.people.upsert_profile(
                    name=name.strip(),
                    role=role,
                    status=status,
                    timezone=timezone or None,
                    aliases=aliases,
                    tags=tags,
                    notes=notes or None,
                    existing_query=(query or name).strip(),
                )
            except ValueError as exc:
                return f"Error: {exc}"
            verb = "Updated" if action == "update" else "Created"
            return f"{verb} people profile: {item.name}\nPath: {item.file_path}"

        if action == "deactivate":
            lookup = query.strip() or name.strip()
            if not lookup:
                return "Error: query is required for deactivate"
            item = self.people.deactivate_profile(lookup)
            if item is None:
                return f"People profile not found: {lookup}"
            return f"Deactivated people profile: {item.name}\nPath: {item.file_path}"

        if action == "follow_up":
            if self.reminders is None:
                return "Error: reminder support is not available"
            if not self._channel or not self._chat_id:
                return "Error: no session context (channel/chat_id)"
            lookup = query.strip() or name.strip()
            if not lookup:
                return "Error: query is required for follow_up"
            person = self.people.get_profile(lookup)
            if person is None:
                return f"People profile not found: {lookup}"
            if not message.strip():
                return "Error: message is required for follow_up"
            if not schedule_kind:
                return "Error: schedule_kind is required for follow_up"
            reminder_title = title.strip() or f"Follow up with {person.name}"
            try:
                reminder = self.reminders.upsert_reminder(
                    title=reminder_title,
                    message=message.strip(),
                    schedule_kind=schedule_kind,
                    channel=self._channel,
                    chat_id=self._chat_id,
                    at=at or None,
                    every_seconds=every_seconds,
                    cron_expr=cron_expr or None,
                    tz=tz or None,
                    related_people=[person.name],
                    existing_query=reminder_title,
                )
            except ValueError as exc:
                return f"Error: {exc}"
            return (
                f"Created follow-up for {person.name}: {reminder.title}\n"
                f"Schedule: {self.reminders.render_schedule(reminder)}\n"
                f"Path: {reminder.file_path}"
            )

        return f"Error: Unknown action: {action}"
