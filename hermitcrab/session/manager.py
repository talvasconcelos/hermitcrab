"""Session management for conversation history."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from hermitcrab.session.sqlite_store import SQLiteSessionStore
from hermitcrab.session.storage import MessageRecord, SessionRecord, utc_now
from hermitcrab.utils.helpers import ensure_dir, safe_filename


def _clean_snippet(value: Any, *, max_chars: int = 160) -> str:
    """Normalize text snippets locally to avoid importing the agent package."""
    if value is None:
        return ""
    text = str(value).strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    Sessions are ephemeral conversation history, not long-term memory.
    Long-term memory is stored separately in identity-root memory categories.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {"role": role, "content": content, "timestamp": utc_now().isoformat(), **kwargs}
        self.messages.append(msg)
        self.updated_at = utc_now()

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

    def get_recent_visible_dialogue(
        self,
        *,
        max_messages: int = 6,
        max_chars: int = 240,
    ) -> list[dict[str, str]]:
        """Return recent user/assistant turns with tool scaffolding removed."""
        visible: list[dict[str, str]] = []
        for message in reversed(self.messages):
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            if role == "assistant" and message.get("tool_calls"):
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            visible.append({"role": role, "content": _clean_snippet(content, max_chars=max_chars)})
            if len(visible) >= max_messages:
                break
        return list(reversed(visible))

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.updated_at = utc_now()


def create_session_manager(workspace: Path, *, import_existing: bool = True) -> "SessionManager":
    """Create the default beta5 SQLite-backed session manager."""
    sessions_dir = ensure_dir(workspace / "sessions")
    sqlite_store = SQLiteSessionStore(sessions_dir / "sessions.sqlite3")
    if import_existing:
        try:
            from hermitcrab.session.migration import migrate_jsonl_sessions_once

            migrate_jsonl_sessions_once(workspace, sqlite_store)
        except Exception as exc:
            logger.warning("Failed to migrate existing JSONL sessions into SQLite: {}", exc)
    return SessionManager(workspace, sqlite_store=sqlite_store)


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path, *, sqlite_store: SQLiteSessionStore | None = None):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.archive_dir = ensure_dir(self.sessions_dir / "archive")
        self.sqlite_store = sqlite_store
        self._cache: dict[str, Session] = {}

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _archived_session_paths(self, key: str) -> list[Path]:
        """Return archived session files for a key, newest first."""
        safe_key = safe_filename(key.replace(":", "_"))
        return sorted(
            self.archive_dir.glob(f"{safe_key}-*.jsonl"),
            key=self._archive_sort_key,
            reverse=True,
        )

    @staticmethod
    def _archive_reason_from_path(path: Path, key: str) -> str:
        safe_key = safe_filename(key.replace(":", "_"))
        stem = path.stem
        prefix = f"{safe_key}-"
        if stem.startswith(prefix):
            match = re.match(r"(.+)-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$", stem[len(prefix) :])
            if match:
                return match.group(1)
        return "archived"

    def _all_session_paths(self) -> list[Path]:
        """Return active and archived session files, newest first."""
        paths = list(self.sessions_dir.glob("*.jsonl")) + list(self.archive_dir.glob("*.jsonl"))
        return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)

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
        if self.sqlite_store is not None:
            session = self._load_from_sqlite(key)
            if session is not None:
                return session
            return None

        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            return self._load_from_path(path, key=key)
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def _load_from_sqlite(self, key: str) -> Session | None:
        if self.sqlite_store is None:
            return None
        record = self.sqlite_store.get_session(key)
        if record is None:
            return None
        messages = [message.to_mapping() for message in self.sqlite_store.get_messages(key)]
        return Session(
            key=record.key,
            messages=messages,
            created_at=record.created_at or utc_now(),
            updated_at=record.updated_at or utc_now(),
            metadata=record.metadata,
        )

    def _load_from_path(self, path: Path, *, key: str) -> Session:
        """Load a session object from a specific JSONL path."""
        session_key = key
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
                    session_key = str(data.get("key") or session_key)
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
            key=session_key,
            messages=messages,
            created_at=created_at or utc_now(),
            updated_at=updated_at or utc_now(),
            metadata=metadata,
        )

    def save(self, session: Session) -> None:
        """Save a session."""
        self._cache[session.key] = session
        if self.sqlite_store is not None:
            self._write_session_to_sqlite(session, archived=False)
            return

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

    def _write_session_to_sqlite(
        self,
        session: Session,
        *,
        archived: bool,
        archive_reason: str | None = None,
    ) -> None:
        """Write a session into the SQLite source of truth."""
        if self.sqlite_store is None:
            return
        try:
            channel, chat_id = self._split_session_key(session.key)
            self._replace_sqlite_messages(session.key)
            self.sqlite_store.upsert_session(
                SessionRecord(
                    key=session.key,
                    channel=channel,
                    chat_id=chat_id,
                    status="archived" if archived else "active",
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                    archived_at=session.updated_at if archived else None,
                    archive_reason=archive_reason,
                    metadata=dict(session.metadata),
                )
            )
            ids: list[int] = []
            for sequence, message in enumerate(session.messages, start=1):
                ids.append(
                    self.sqlite_store.save_message(
                        MessageRecord.from_mapping(session.key, message, sequence=sequence)
                    )
                )
            logger.debug("Mirrored session {} to SQLite (messages={})", session.key, len(ids))
        except Exception as exc:
            logger.warning("Failed to mirror session {} to SQLite: {}", session.key, exc)

    def _replace_sqlite_messages(self, key: str) -> None:
        store = self.sqlite_store
        if store is None:
            return
        store._require_open()  # noqa: SLF001 - manager owns optional mirror lifecycle
        conn = store.conn
        if conn is None:
            return
        conn.execute("DELETE FROM messages WHERE session_key = ?", (key,))
        conn.execute("DELETE FROM tool_calls WHERE session_key = ?", (key,))
        conn.execute("DELETE FROM tool_results WHERE session_key = ?", (key,))
        if store.fts_enabled:
            conn.execute("DELETE FROM messages_fts WHERE rowid NOT IN (SELECT id FROM messages)")
        conn.commit()

    @staticmethod
    def _split_session_key(key: str) -> tuple[str, str]:
        if ":" not in key:
            return key, ""
        channel, chat_id = key.split(":", 1)
        return channel, chat_id

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def archive(self, session: Session, reason: str) -> Path | None:
        """Archive the current session and reset in-memory state."""
        if self.sqlite_store is not None:
            self._write_session_to_sqlite(session, archived=True, archive_reason=reason)
            self.invalidate(session.key)
            session.clear()
            session.metadata.clear()
            return None

        path = self._get_session_path(session.key)
        if not path.exists():
            self.invalidate(session.key)
            session.clear()
            session.metadata.clear()
            return None

        ts = utc_now().strftime("%Y-%m-%dT%H-%M-%S")
        archive_name = f"{safe_filename(session.key.replace(':', '_'))}-{reason}-{ts}.jsonl"
        archive_path = self.archive_dir / archive_name
        path.replace(archive_path)
        logger.info("Archived session {} -> {}", session.key, archive_path.name)

        self.invalidate(session.key)
        session.clear()
        session.metadata.clear()
        return archive_path

    def export_session(self, key: str, *, format: str = "markdown") -> str:
        """Export a stored session as readable Markdown or portable JSONL."""
        normalized_format = format.strip().lower()
        if normalized_format not in {"markdown", "jsonl"}:
            raise ValueError(f"unsupported export format: {format}")

        session = self._load(key)
        archived_path: Path | None = None
        record: SessionRecord | None = self.sqlite_store.get_session(key) if self.sqlite_store else None
        if session is None and self.sqlite_store is not None:
            record = next(
                (
                    candidate
                    for candidate in self.sqlite_store.list_sessions(archived=True)
                    if candidate.metadata.get("original_session_key") == key
                ),
                None,
            )
            if record is not None:
                session = self._load_from_sqlite(record.key)
        if session is None and self.sqlite_store is None:
            archived_paths = self._archived_session_paths(key)
            if archived_paths:
                archived_path = archived_paths[0]
                session = self._load_from_path(archived_path, key=key)
        if session is None:
            raise KeyError(f"session not found: {key}")

        export_key = key if record and record.metadata.get("original_session_key") == key else session.key
        status = "archived" if archived_path else "active"
        archive_reason: str | None = self._archive_reason_from_path(archived_path, key) if archived_path else None
        archived_at: datetime | None = None
        if self.sqlite_store is not None:
            if record is None:
                raise KeyError(f"session not found: {key}")
            status = record.status
            archive_reason = record.archive_reason
            archived_at = record.archived_at

        if normalized_format == "jsonl":
            metadata = {
                "_type": "metadata",
                "key": export_key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "status": status,
                "archived_at": archived_at.isoformat() if archived_at else None,
                "archive_reason": archive_reason,
                "metadata": session.metadata,
            }
            lines = [json.dumps(metadata, ensure_ascii=False)]
            lines.extend(json.dumps(message, ensure_ascii=False) for message in session.messages)
            return "\n".join(lines) + "\n"

        lines = [f"# Session: {export_key}", "", f"Status: {status}"]
        if archive_reason:
            lines.append(f"Archive reason: {archive_reason}")
        lines.append("")
        for message in session.messages:
            role = str(message.get("role") or "unknown").replace("_", " ").title()
            content = str(message.get("content") or "")
            lines.extend((f"## {role}", "", content, ""))
        return "\n".join(lines).rstrip() + "\n"

    def get_recent_archived_history(
        self,
        key: str,
        *,
        max_messages: int = 12,
        max_age: timedelta = timedelta(days=1),
    ) -> list[dict[str, Any]]:
        """Return the latest recent archived history for same-chat continuity.

        This is deliberately query-free: a timeout/archive should finalize the
        inspectable JSONL file, not force the next user message to restate exact
        words from the previous conversation before the agent can continue.
        """
        if self.sqlite_store is not None:
            record = self.sqlite_store.get_session(key)
            if record is None or record.status != "archived" or not record.updated_at:
                return []
            now = datetime.now(timezone.utc)
            if now - record.updated_at.astimezone(timezone.utc) > max_age:
                return []
            session = self._load_from_sqlite(key)
            return session.get_history(max_messages=max_messages) if session else []

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
                logger.info(
                    "Loaded recent archived session tail for {} from {} (age={}, messages={})",
                    key,
                    path.name,
                    age,
                    len(history),
                )
                return history
        return []

    def get_resume_history(
        self,
        key: str,
        *,
        query: str | None,
        max_messages: int = 24,
        recent_max_age: timedelta = timedelta(days=3),
        relevance_max_age: timedelta = timedelta(days=30),
    ) -> list[dict[str, Any]]:
        """Return archived same-chat history that is recent or relevant to the new turn."""
        if self.sqlite_store is not None:
            session = self._load_from_sqlite(key)
            record = self.sqlite_store.get_session(key)
            if session is None or record is None or record.status != "archived" or not record.updated_at:
                return []
            now = datetime.now(timezone.utc)
            age = now - record.updated_at.astimezone(timezone.utc)
            if age > relevance_max_age:
                return []
            history = session.get_history(max_messages=max_messages)
            normalized_query = self._normalize_query(query)
            if age <= recent_max_age and self._query_signals_resume(normalized_query):
                return history
            if normalized_query and self._history_matches_query(history, normalized_query):
                return history
            if age > recent_max_age and self._query_signals_resume(normalized_query):
                return history
            return []

        now = datetime.now(timezone.utc)
        normalized_query = self._normalize_query(query)

        for path in self._archived_session_paths(key):
            try:
                session = self._load_from_path(path, key=key)
            except Exception:
                logger.exception("Failed to load archived resume context from {}", path)
                continue

            age = now - session.updated_at.astimezone(timezone.utc)
            if age > relevance_max_age:
                return []

            history = session.get_history(max_messages=max_messages)
            if not history:
                continue

            if age <= recent_max_age:
                if self._query_signals_resume(normalized_query):
                    return history
                if normalized_query and self._history_matches_query(history, normalized_query):
                    return history
                continue

            if self._query_signals_resume(normalized_query):
                return history

            if normalized_query and self._history_matches_query(history, normalized_query):
                return history

        return []

    def search_history(
        self,
        query: str,
        *,
        max_results: int = 3,
        max_messages: int = 6,
    ) -> list[dict[str, Any]]:
        """Search current and archived sessions for matching conversation history."""
        if self.sqlite_store is not None:
            return self._search_sqlite_history(
                query,
                max_results=max_results,
                max_messages=max_messages,
            )

        normalized_query = " ".join(query.lower().split())
        if not normalized_query:
            return []

        results: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for path in self._all_session_paths():
            try:
                session = self._load_from_path(path, key=path.stem.replace("_", ":", 1))
            except Exception:
                logger.exception("Failed to search session path {}", path)
                continue

            excerpts = self._matching_excerpts(
                session.messages,
                normalized_query,
                max_messages=max_messages,
            )
            if not excerpts:
                continue

            result_key = f"{session.key}:{path.name}"
            if result_key in seen_keys:
                continue
            seen_keys.add(result_key)
            results.append(
                {
                    "session_key": session.key,
                    "updated_at": session.updated_at.isoformat(),
                    "archived": path.parent == self.archive_dir,
                    "path": str(path),
                    "excerpts": excerpts,
                }
            )
            if len(results) >= max_results:
                break

        return results

    def recent_history(
        self,
        *,
        max_results: int = 3,
        max_messages: int = 8,
    ) -> list[dict[str, Any]]:
        """Return recent active/archived session excerpts without keyword matching."""
        if self.sqlite_store is not None:
            return self._recent_sqlite_history(
                max_results=max_results,
                max_messages=max_messages,
            )

        results: list[dict[str, Any]] = []
        for path in self._all_session_paths():
            try:
                session = self._load_from_path(path, key=path.stem.replace("_", ":", 1))
            except Exception:
                logger.exception("Failed to read recent session path {}", path)
                continue

            excerpt = self._tail_excerpt(session.messages, max_messages=max_messages)
            if not excerpt:
                continue
            results.append(
                {
                    "session_key": session.key,
                    "updated_at": session.updated_at.isoformat(),
                    "archived": path.parent == self.archive_dir,
                    "path": str(path),
                    "excerpts": [excerpt],
                }
            )
            if len(results) >= max_results:
                break
        return results

    def _search_sqlite_history(
        self,
        query: str,
        *,
        max_results: int,
        max_messages: int,
    ) -> list[dict[str, Any]]:
        if self.sqlite_store is None:
            return []
        matches = self.sqlite_store.search_messages(query, limit=max_results)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in matches:
            if match.session_key in seen:
                continue
            seen.add(match.session_key)
            messages = self.sqlite_store.get_messages(match.session_key)
            excerpts = self._matching_excerpts(
                [message.to_mapping() for message in messages],
                " ".join(query.lower().split()),
                max_messages=max_messages,
            )
            if not excerpts and match.content:
                excerpts = [f"{match.role}: {_clean_snippet(match.content, max_chars=220)}"]
            session = self.sqlite_store.get_session(match.session_key)
            original_key = session.metadata.get("original_session_key") if session else None
            results.append(
                {
                    "session_key": original_key or match.session_key,
                    "updated_at": session.updated_at.isoformat() if session and session.updated_at else "",
                    "archived": bool(session and session.status == "archived"),
                    "path": session.metadata.get("source_path", "sqlite") if session else "sqlite",
                    "excerpts": excerpts,
                }
            )
            if len(results) >= max_results:
                break
        return results

    def _recent_sqlite_history(self, *, max_results: int, max_messages: int) -> list[dict[str, Any]]:
        if self.sqlite_store is None:
            return []
        sessions = self.sqlite_store.list_sessions(limit=max_results)
        results: list[dict[str, Any]] = []
        for session in sessions:
            messages = [message.to_mapping() for message in self.sqlite_store.get_messages(session.key)]
            excerpt = self._tail_excerpt(messages, max_messages=max_messages)
            if not excerpt:
                continue
            results.append(
                {
                    "session_key": session.metadata.get("original_session_key", session.key),
                    "updated_at": session.updated_at.isoformat() if session.updated_at else "",
                    "archived": session.status == "archived",
                    "path": session.metadata.get("source_path", "sqlite"),
                    "excerpts": [excerpt],
                }
            )
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _matching_excerpts(
        messages: list[dict[str, Any]],
        normalized_query: str,
        *,
        max_messages: int,
    ) -> list[str]:
        """Extract compact matched windows from session messages."""
        excerpts: list[str] = []
        for index, message in enumerate(messages):
            content = message.get("content")
            if not isinstance(content, str):
                continue
            normalized_content = " ".join(content.lower().split())
            if normalized_query not in normalized_content:
                continue

            start = max(0, index - 1)
            end = min(len(messages), index + max_messages - 1)
            lines: list[str] = []
            for excerpt_message in messages[start:end]:
                role = str(excerpt_message.get("role") or "unknown")
                excerpt_content = str(excerpt_message.get("content") or "").strip()
                if not excerpt_content:
                    continue
                excerpt_content = excerpt_content[:220]
                if len(str(excerpt_message.get("content") or "").strip()) > 220:
                    excerpt_content += "..."
                lines.append(f"{role}: {excerpt_content}")
            if lines:
                excerpts.append("\n".join(lines))
            if len(excerpts) >= 2:
                break
        return excerpts

    @staticmethod
    def _tail_excerpt(messages: list[dict[str, Any]], *, max_messages: int) -> str:
        """Build a compact visible tail excerpt from recent conversation messages."""
        lines: list[str] = []
        for message in messages[-max_messages:]:
            role = str(message.get("role") or "unknown")
            if role not in {"user", "assistant", "tool"}:
                continue
            if role == "assistant" and message.get("tool_calls"):
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            clipped = content[:220]
            if len(content) > 220:
                clipped += "..."
            lines.append(f"{role}: {clipped}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_query(query: str | None) -> str:
        return " ".join((query or "").strip().lower().split())

    @staticmethod
    def _archive_sort_key(path: Path) -> tuple[datetime, float, str]:
        """Sort archives by embedded timestamp instead of lexical reason labels."""
        mtime = path.stat().st_mtime
        match = re.search(r"-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})\.jsonl$", path.name)
        if match:
            try:
                timestamp = datetime.strptime(match.group(1), "%Y-%m-%dT%H-%M-%S")
                return timestamp, mtime, path.name
            except ValueError:
                pass
        return datetime.fromtimestamp(mtime), mtime, path.name

    @staticmethod
    def _query_signals_resume(normalized_query: str) -> bool:
        if not normalized_query:
            return False
        if len(normalized_query) <= 24:
            return normalized_query in {
                "so",
                "so?",
                "and",
                "and?",
                "continue",
                "continue.",
                "go on",
                "what now",
                "where were we",
                "where were we?",
                "what were we doing",
                "what were we doing?",
                "pick this back up",
                "resume",
                "resume?",
            }
        return False

    @staticmethod
    def _history_matches_query(history: list[dict[str, Any]], normalized_query: str) -> bool:
        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", normalized_query)
            if len(token) >= 4
        }
        if not query_tokens:
            return False

        history_text = " ".join(
            str(message.get("content") or "")
            for message in history
            if message.get("role") in {"user", "assistant", "tool"}
        ).lower()
        history_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", history_text)
            if len(token) >= 4
        }
        return len(query_tokens & history_tokens) >= 1

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all active sessions.

        Returns:
            List of session info dicts.
        """
        if self.sqlite_store is not None:
            sessions = [
                {
                    "key": record.key,
                    "created_at": record.created_at.isoformat() if record.created_at else None,
                    "updated_at": record.updated_at.isoformat() if record.updated_at else None,
                    "path": None,
                }
                for record in self.sqlite_store.list_sessions(archived=False)
            ]
            return sorted(sessions, key=lambda x: x.get("updated_at") or "", reverse=True)

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
