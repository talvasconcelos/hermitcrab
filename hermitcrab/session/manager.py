"""Session management for conversation history."""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from hermitcrab.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    Sessions are ephemeral conversation history, not long-term memory.
    Long-term memory is stored separately in workspace/memory/ categories.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **kwargs}
        self.messages.append(msg)
        self.updated_at = datetime.now()

    @staticmethod
    def _leading_segment_end(messages: list[dict[str, Any]]) -> int:
        """Return the end index of the leading conversation segment."""
        if not messages:
            return 0
        for idx in range(1, len(messages)):
            if messages[idx].get("role") == "user":
                return idx
        return len(messages)

    @staticmethod
    def _leading_segment_is_broken(messages: list[dict[str, Any]]) -> bool:
        """Detect whether the leading truncated segment starts mid-turn."""
        if not messages:
            return False

        first = messages[0]
        first_role = first.get("role")
        if first_role == "tool":
            return True
        if (
            first_role == "assistant"
            and isinstance(first.get("tool_calls"), list)
            and first["tool_calls"]
        ):
            return True
        if first_role != "user":
            return False

        segment = messages[: Session._leading_segment_end(messages)]
        visible_tool_call_ids: set[str] = set()

        for msg in segment:
            if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id"):
                        visible_tool_call_ids.add(str(tc["id"]))
            elif msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id and str(tool_call_id) not in visible_tool_call_ids:
                    return True

        return False

    @classmethod
    def _repair_truncated_history(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop broken leading segments until history starts at a safe boundary."""
        repaired = list(messages)
        while repaired and cls._leading_segment_is_broken(repaired):
            segment_end = cls._leading_segment_end(repaired)
            if segment_end <= 0:
                break
            repaired = repaired[segment_end:]
        return repaired

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return recent messages for LLM context."""
        sliced = self.messages[-max_messages:]
        sliced = self._repair_truncated_history(sliced)

        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.archive_dir = ensure_dir(self.sessions_dir / "archive")
        self.legacy_sessions_dir = Path.home() / ".hermitcrab" / "sessions"
        self._cache: dict[str, Session] = {}

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.hermitcrab/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def _archived_session_paths(self, key: str) -> list[Path]:
        """Return archived session files for a key, newest first."""
        safe_key = safe_filename(key.replace(":", "_"))
        return sorted(
            self.archive_dir.glob(f"{safe_key}-*.jsonl"),
            key=lambda path: path.name,
            reverse=True,
        )

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            return self._load_from_path(path, key=key)
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def _load_from_path(self, path: Path, *, key: str) -> Session:
        """Load a session object from a specific JSONL path."""
        messages = []
        metadata = {}
        created_at = None
        updated_at = None

        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)

                if data.get("_type") == "metadata":
                    metadata = data.get("metadata", {})
                    created_at = (
                        datetime.fromisoformat(data["created_at"])
                        if data.get("created_at")
                        else None
                    )
                    updated_at = (
                        datetime.fromisoformat(data["updated_at"])
                        if data.get("updated_at")
                        else None
                    )
                else:
                    messages.append(data)

        return Session(
            key=key,
            messages=messages,
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
            metadata=metadata,
        )

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def archive(self, session: Session, reason: str) -> Path | None:
        """Archive the current on-disk session and reset in-memory state."""
        path = self._get_session_path(session.key)
        if not path.exists():
            self.invalidate(session.key)
            session.clear()
            session.metadata.clear()
            return None

        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        archive_name = f"{safe_filename(session.key.replace(':', '_'))}-{reason}-{ts}.jsonl"
        archive_path = self.archive_dir / archive_name
        path.replace(archive_path)
        logger.info("Archived session {} -> {}", session.key, archive_path.name)

        self.invalidate(session.key)
        session.clear()
        session.metadata.clear()
        return archive_path

    def get_recent_archived_history(
        self,
        key: str,
        *,
        max_messages: int = 12,
        max_age: timedelta = timedelta(hours=6),
    ) -> list[dict[str, Any]]:
        """Return recent archived history for the same chat when it ended recently."""
        now = datetime.now(timezone.utc)
        for path in self._archived_session_paths(key):
            try:
                session = self._load_from_path(path, key=key)
            except Exception:
                logger.exception("Failed to load archived session context from {}", path)
                continue

            age = now - session.updated_at.astimezone(timezone.utc)
            if age > max_age:
                return []

            history = session.get_history(max_messages=max_messages)
            if history:
                return history
        return []

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append(
                                {
                                    "key": key,
                                    "created_at": data.get("created_at"),
                                    "updated_at": data.get("updated_at"),
                                    "path": str(path),
                                }
                            )
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
