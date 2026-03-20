"""Helpers for persisting session turns without leaking large tool payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


class TurnPersistence:
    """Save new turn messages into a session with deterministic truncation."""

    TOOL_RESULT_MAX_CHARS = 500

    @classmethod
    def save_turn(
        cls,
        session: Any,
        messages: list[dict[str, Any]],
        skip: int,
        update_session_timer: Callable[[str], None],
    ) -> None:
        for message in messages[skip:]:
            entry = {k: v for k, v in message.items() if k != "reasoning_content"}
            if entry.get("role") == "tool" and isinstance(entry.get("content"), str):
                content = entry["content"]
                if len(content) > cls.TOOL_RESULT_MAX_CHARS:
                    entry["content"] = content[: cls.TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now(timezone.utc)
        update_session_timer(session.key)
