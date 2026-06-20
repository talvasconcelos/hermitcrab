"""Storage interfaces and record shapes for HermitCrab session persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


def utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_datetime(value: datetime | str | None) -> datetime | None:
    """Parse an ISO-like datetime value, returning ``None`` when absent."""

    if value is None or isinstance(value, datetime):
        return ensure_utc(value) if isinstance(value, datetime) else None

    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return ensure_utc(datetime.fromisoformat(text))


def isoformat(value: datetime | str | None) -> str | None:
    """Return an ISO-8601 UTC timestamp for a datetime-like value."""

    parsed = parse_datetime(value)
    return parsed.isoformat() if parsed is not None else None


@dataclass(slots=True)
class SessionRecord:
    """A normalized session row suitable for any session storage backend."""

    key: str
    identity: str = ""
    channel: str = ""
    chat_id: str = ""
    status: str = "active"
    created_at: datetime | None = field(default_factory=utc_now)
    updated_at: datetime | None = field(default_factory=utc_now)
    archived_at: datetime | None = None
    archive_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SessionRecord":
        """Build a session record from a mapping, preserving unknown metadata."""

        metadata = dict(data.get("metadata") or {})
        return cls(
            key=str(data.get("key") or data.get("session_key") or ""),
            identity=str(data.get("identity") or ""),
            channel=str(data.get("channel") or ""),
            chat_id=str(data.get("chat_id") or ""),
            status=str(data.get("status") or "active"),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            archived_at=parse_datetime(data.get("archived_at")),
            archive_reason=data.get("archive_reason"),
            metadata=metadata,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping for this session."""

        return {
            "key": self.key,
            "identity": self.identity,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            "archive_reason": self.archive_reason,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class MessageRecord:
    """A normalized message row suitable for any session storage backend."""

    session_key: str
    role: str
    content: str
    sequence: int | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    @classmethod
    def from_mapping(
        cls,
        session_key: str,
        data: Mapping[str, Any],
        *,
        sequence: int | None = None,
    ) -> "MessageRecord":
        """Build a message record from a mapping."""

        metadata = dict(data.get("metadata") or {})
        for key in ("tool_calls", "tool_call_id", "tool_result"):
            if key in data:
                metadata[key] = data[key]
        return cls(
            session_key=session_key,
            role=str(data.get("role") or ""),
            content=str(data.get("content") or ""),
            sequence=sequence,
            created_at=parse_datetime(data.get("timestamp") or data.get("created_at")),
            metadata=metadata,
            id=data.get("id") if isinstance(data.get("id"), int) else None,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping for this message."""

        return {
            "id": self.id,
            "session_key": self.session_key,
            "role": self.role,
            "content": self.content,
            "sequence": self.sequence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class SearchResult:
    """A message search result with session scope metadata."""

    id: int
    session_key: str
    identity: str
    channel: str
    chat_id: str
    role: str
    content: str
    created_at: datetime | None = None
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionStorage(Protocol):
    """Protocol for session storage backends."""

    fts_enabled: bool

    def close(self) -> None:
        """Release backend resources."""

    def upsert_session(self, session: SessionRecord) -> None:
        """Create or update a session record."""

    def get_session(self, key: str) -> SessionRecord | None:
        """Return a session by key, or ``None``."""

    def list_sessions(
        self,
        *,
        identity: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        status: str | None = None,
        archived: bool | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SessionRecord]:
        """List sessions with optional scope filters."""

    def archive_session(self, key: str, *, reason: str) -> None:
        """Mark a session archived with a reason."""

    def save_message(self, message: MessageRecord) -> int:
        """Append a message and return its backend row id."""

    def save_messages(
        self,
        session_key: str,
        messages: Iterable[Mapping[str, Any]],
        *,
        start_sequence: int = 1,
    ) -> list[int]:
        """Append message mappings and return backend row ids."""

    def get_messages(
        self,
        session_key: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        reverse: bool = False,
    ) -> list[MessageRecord]:
        """Return messages for a session."""

    def search_messages(
        self,
        query: str,
        *,
        identity: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Search message content with optional identity/channel/chat/session scope."""


__all__ = [
    "MessageRecord",
    "SearchResult",
    "SessionRecord",
    "SessionStorage",
    "ensure_utc",
    "isoformat",
    "parse_datetime",
    "utc_now",
]
