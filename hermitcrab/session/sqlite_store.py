"""SQLite implementation of the HermitCrab session storage interface."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from hermitcrab.session.storage import (
    MessageRecord,
    SearchResult,
    SessionRecord,
    isoformat,
    parse_datetime,
    utc_now,
)


class SQLiteSessionStore:
    """A SQLite-backed session store with scoped rows and optional FTS5 search."""

    def __init__(self, path: str | Path, *, require_fts5: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.fts_enabled = False
        self.fts_error: str | None = None
        self._create_schema()
        if require_fts5 and not self.fts_enabled:
            detail = self.fts_error or "SQLite FTS5 is unavailable"
            self.close()
            raise RuntimeError(f"SQLite FTS5 is required but unavailable: {detail}")

    def __enter__(self) -> "SQLiteSessionStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Commit pending writes and close the SQLite connection."""

        conn = self.conn
        if conn is None:
            return
        conn.commit()
        conn.close()
        self.conn = None

    def upsert_session(self, session: SessionRecord) -> None:
        """Create or update a session row."""

        self._require_open()
        channel, chat_id = _split_session_key(session.key)
        now = utc_now().isoformat()
        self.conn.execute(
            """
            INSERT INTO sessions (
                key, identity, channel, chat_id, status, created_at, updated_at,
                archived_at, archive_reason, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                identity = excluded.identity,
                channel = excluded.channel,
                chat_id = excluded.chat_id,
                status = excluded.status,
                updated_at = excluded.updated_at,
                archived_at = excluded.archived_at,
                archive_reason = excluded.archive_reason,
                metadata = excluded.metadata
            """,
            (
                session.key,
                session.identity,
                session.channel or channel,
                session.chat_id or chat_id,
                session.status,
                isoformat(session.created_at) or now,
                isoformat(session.updated_at) or now,
                isoformat(session.archived_at),
                session.archive_reason,
                _json_dumps(session.metadata),
            ),
        )
        self.conn.commit()

    def get_session(self, key: str) -> SessionRecord | None:
        """Return a session by key, or ``None`` when it does not exist."""

        self._require_open()
        row = self.conn.execute("SELECT * FROM sessions WHERE key = ?", (key,)).fetchone()
        return _session_from_row(row) if row is not None else None

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
        """List sessions with optional identity/channel/chat/status filters."""

        self._require_open()
        clauses: list[str] = []
        params: list[Any] = []
        if identity is not None:
            clauses.append("identity = ?")
            params.append(identity)
        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel)
        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if archived is True:
            clauses.append("status = 'archived'")
        elif archived is False:
            clauses.append("status != 'archived'")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM sessions {where} ORDER BY updated_at DESC, key ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        if offset:
            sql += " OFFSET ?"
            params.append(offset)
        return [_session_from_row(row) for row in self.conn.execute(sql, params).fetchall()]

    def archive_session(self, key: str, *, reason: str) -> None:
        """Mark a session as archived with a lifecycle reason."""

        self._require_open()
        archived_at = utc_now().isoformat()
        cursor = self.conn.execute(
            """
            UPDATE sessions
            SET status = 'archived', archived_at = ?, archive_reason = ?
            WHERE key = ?
            """,
            (archived_at, reason, key),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"session not found: {key}")
        self.conn.commit()

    def save_message(self, message: MessageRecord) -> int:
        """Append a message and return its SQLite row id."""

        self._require_open()
        sequence = message.sequence
        if sequence is None:
            sequence = self._next_sequence(message.session_key)
        created_at = isoformat(message.created_at) or utc_now().isoformat()
        metadata_json = _json_dumps(message.metadata)
        cursor = self.conn.execute(
            """
            INSERT INTO messages (session_key, sequence, role, content, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                message.session_key,
                sequence,
                message.role,
                message.content,
                created_at,
                metadata_json,
            ),
        )
        message_id = int(cursor.lastrowid)
        self._sync_message_fts(message_id, message, metadata_json)
        self._store_tool_events(message.session_key, message_id, message, metadata_json)
        self.conn.commit()
        return message_id

    def save_messages(
        self,
        session_key: str,
        messages: Iterable[Mapping[str, Any]],
        *,
        start_sequence: int = 1,
    ) -> list[int]:
        """Append message mappings and return their SQLite row ids."""

        ids: list[int] = []
        sequence = self._next_sequence(session_key, start_sequence=start_sequence)
        for item in messages:
            record = MessageRecord.from_mapping(session_key, item, sequence=sequence)
            ids.append(self.save_message(record))
            sequence += 1
        return ids

    def get_messages(
        self,
        session_key: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        reverse: bool = False,
    ) -> list[MessageRecord]:
        """Return messages for a session in sequence order."""

        self._require_open()
        order = "DESC" if reverse else "ASC"
        sql = (
            f"SELECT * FROM messages WHERE session_key = ? "
            f"ORDER BY sequence {order}, id {order}"
        )
        params: list[Any] = [session_key]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        if offset:
            sql += " OFFSET ?"
            params.append(offset)
        return [_message_from_row(row) for row in self.conn.execute(sql, params).fetchall()]

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
        """Search message content with optional scope filters."""

        normalized_query = " ".join(query.lower().split())
        if not normalized_query:
            return []

        self._require_open()
        if self.fts_enabled:
            return self._search_fts(
                normalized_query,
                identity=identity,
                channel=channel,
                chat_id=chat_id,
                session_key=session_key,
                limit=limit,
            )
        return self._search_like(
            normalized_query,
            identity=identity,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            limit=limit,
        )

    def _create_schema(self) -> None:
        self._require_open()
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                key TEXT PRIMARY KEY,
                identity TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                chat_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                archive_reason TEXT,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_scope
                ON sessions(identity, channel, chat_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(session_key) REFERENCES sessions(key) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_sequence
                ON messages(session_key, sequence, id);

            CREATE TABLE IF NOT EXISTS tool_calls (
                id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                message_id INTEGER,
                name TEXT,
                arguments TEXT NOT NULL DEFAULT '{}',
                created_at TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(session_key) REFERENCES sessions(key) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tool_calls_session
                ON tool_calls(session_key, message_id);

            CREATE TABLE IF NOT EXISTS tool_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                message_id INTEGER,
                tool_call_id TEXT,
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(session_key) REFERENCES sessions(key) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tool_results_session
                ON tool_results(session_key, message_id, tool_call_id);
            """
        )
        self._create_fts_table()
        self.conn.commit()

    def _create_fts_table(self) -> None:
        try:
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    session_key UNINDEXED,
                    role UNINDEXED,
                    content,
                    metadata UNINDEXED,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            self.fts_enabled = True
        except sqlite3.OperationalError as exc:
            self.fts_error = str(exc)

    def _next_sequence(self, session_key: str, *, start_sequence: int = 1) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(sequence), ?) FROM messages WHERE session_key = ?",
            (start_sequence - 1, session_key),
        ).fetchone()
        return int(row[0]) + 1

    def _sync_message_fts(self, message_id: int, message: MessageRecord, metadata_json: str) -> None:
        if not self.fts_enabled:
            return
        self.conn.execute(
            "INSERT INTO messages_fts(rowid, session_key, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
            (message_id, message.session_key, message.role, message.content, metadata_json),
        )

    def _store_tool_events(
        self,
        session_key: str,
        message_id: int,
        message: MessageRecord,
        metadata_json: str,
    ) -> None:
        tool_calls = message.metadata.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw_call in tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                call_id = raw_call.get("id") or raw_call.get("tool_call_id")
                if not call_id:
                    continue
                function = raw_call.get("function") or {}
                name = function.get("name") if isinstance(function, dict) else None
                arguments = function.get("arguments") if isinstance(function, dict) else None
                if arguments is None:
                    arguments = raw_call.get("arguments", {})
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO tool_calls (
                        id, session_key, message_id, name, arguments, created_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(call_id),
                        session_key,
                        message_id,
                        str(name) if name else None,
                        _arguments_text(arguments),
                        isoformat(message.created_at) or utc_now().isoformat(),
                        _json_dumps({"tool_call": raw_call}),
                    ),
                )

        if message.role == "tool":
            tool_call_id = message.metadata.get("tool_call_id") or message.metadata.get("id")
            self.conn.execute(
                """
                INSERT INTO tool_results (
                    session_key, message_id, tool_call_id, content, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_key,
                    message_id,
                    str(tool_call_id) if tool_call_id else None,
                    message.content,
                    isoformat(message.created_at) or utc_now().isoformat(),
                    _json_dumps({"message": message.metadata}),
                ),
            )
        elif message.metadata.get("tool_result") is not None:
            tool_result = message.metadata["tool_result"]
            tool_call_id = None
            content = message.content
            if isinstance(tool_result, dict):
                tool_call_id = tool_result.get("tool_call_id") or tool_result.get("id")
                content = str(tool_result.get("content", message.content))
            self.conn.execute(
                """
                INSERT INTO tool_results (
                    session_key, message_id, tool_call_id, content, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_key,
                    message_id,
                    str(tool_call_id) if tool_call_id else None,
                    content,
                    isoformat(message.created_at) or utc_now().isoformat(),
                    _json_dumps({"tool_result": tool_result, "message": message.metadata}),
                ),
            )

    def _search_fts(
        self,
        query: str,
        *,
        identity: str | None,
        channel: str | None,
        chat_id: str | None,
        session_key: str | None,
        limit: int,
    ) -> list[SearchResult]:
        clauses, params = _scope_clauses(
            identity=identity,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
        )
        where = " AND ".join(["messages_fts MATCH ?"] + clauses)
        params = [_fts_query(query), *params, max(1, limit)]
        rows = self.conn.execute(
            f"""
            SELECT
                m.id,
                m.session_key,
                s.identity,
                s.channel,
                s.chat_id,
                m.role,
                m.content,
                m.created_at,
                m.metadata,
                bm25(messages_fts) AS score
            FROM messages_fts
            JOIN messages AS m ON m.id = messages_fts.rowid
            JOIN sessions AS s ON s.key = m.session_key
            WHERE {where}
            ORDER BY score ASC, m.sequence ASC, m.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_search_result_from_row(row, fts=True) for row in rows]

    def _search_like(
        self,
        query: str,
        *,
        identity: str | None,
        channel: str | None,
        chat_id: str | None,
        session_key: str | None,
        limit: int,
    ) -> list[SearchResult]:
        clauses, params = _scope_clauses(
            identity=identity,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
        )
        where = " AND ".join(["LOWER(m.content) LIKE ?"] + clauses)
        params = [f"%{query}%", *params, max(1, limit)]
        rows = self.conn.execute(
            f"""
            SELECT
                m.id,
                m.session_key,
                s.identity,
                s.channel,
                s.chat_id,
                m.role,
                m.content,
                m.created_at,
                m.metadata,
                NULL AS score
            FROM messages AS m
            JOIN sessions AS s ON s.key = m.session_key
            WHERE {where}
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_search_result_from_row(row, fts=False) for row in rows]

    def _require_open(self) -> None:
        if self.conn is None:
            raise RuntimeError("SQLite session store is closed")


def _session_from_row(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        key=str(row["key"]),
        identity=str(row["identity"] or ""),
        channel=str(row["channel"] or ""),
        chat_id=str(row["chat_id"] or ""),
        status=str(row["status"] or "active"),
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
        archived_at=parse_datetime(row["archived_at"]),
        archive_reason=row["archive_reason"],
        metadata=_json_loads(row["metadata"]),
    )


def _message_from_row(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(
        id=int(row["id"]),
        session_key=str(row["session_key"]),
        role=str(row["role"]),
        content=str(row["content"] or ""),
        sequence=int(row["sequence"]),
        created_at=parse_datetime(row["created_at"]),
        metadata=_json_loads(row["metadata"]),
    )


def _search_result_from_row(row: sqlite3.Row, *, fts: bool) -> SearchResult:
    score = float(row["score"]) if fts and row["score"] is not None else None
    return SearchResult(
        id=int(row["id"]),
        session_key=str(row["session_key"]),
        identity=str(row["identity"] or ""),
        channel=str(row["channel"] or ""),
        chat_id=str(row["chat_id"] or ""),
        role=str(row["role"]),
        content=str(row["content"] or ""),
        created_at=parse_datetime(row["created_at"]),
        score=score,
        metadata=_json_loads(row["metadata"]),
    )


def _scope_clauses(
    *,
    identity: str | None,
    channel: str | None,
    chat_id: str | None,
    session_key: str | None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if identity is not None:
        clauses.append("s.identity = ?")
        params.append(identity)
    if channel is not None:
        clauses.append("s.channel = ?")
        params.append(channel)
    if chat_id is not None:
        clauses.append("s.chat_id = ?")
        params.append(chat_id)
    if session_key is not None:
        clauses.append("m.session_key = ?")
        params.append(session_key)
    return clauses, params


def _split_session_key(key: str) -> tuple[str, str]:
    if ":" not in key:
        return key, ""
    channel, chat_id = key.split(":", 1)
    return channel, chat_id


def _json_loads(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)


def _arguments_text(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    return _json_dumps(arguments)


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", query.lower())
    if not tokens:
        return '""'
    return " AND ".join(f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens)


__all__ = ["SQLiteSessionStore"]
