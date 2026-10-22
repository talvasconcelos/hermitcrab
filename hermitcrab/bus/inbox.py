"""Durable inbound work ledger for channel ingestion.

A channel acknowledges an event only after this ledger has a row for it. Pending rows
are replayed on startup; rows left ``processing`` by a crash are marked ``uncertain``
instead of blindly repeating a turn whose external side effects are unknown.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hermitcrab.bus.events import InboundMessage

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_UNCERTAIN = "uncertain"


@dataclass(slots=True)
class InboxRecord:
    """One persisted inbound event and its processing state."""

    canonical_id: str
    channel: str
    sender_id: str
    chat_id: str
    content: str
    status: str
    attempts: int = 0
    error: str = ""
    session_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    received_at: float = 0.0
    updated_at: float = 0.0


class InboundInbox:
    """SQLite-backed inbound ledger keyed by a channel's canonical event id."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn: sqlite3.Connection | None = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._create_schema()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _create_schema(self) -> None:
        assert self.conn is not None
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_work (
                canonical_id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                session_key TEXT,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                received_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbound_work_status ON inbound_work(status)"
        )
        self.conn.commit()

    def close(self) -> None:
        conn = self.conn
        if conn is None:
            return
        conn.commit()
        conn.close()
        self.conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("inbound inbox is closed")
        return self.conn

    def record(self, message: InboundMessage, canonical_id: str) -> bool:
        """Persist an event before it is queued. Return False when already known."""
        if not canonical_id:
            raise ValueError("canonical_id is required for durable inbound messages")
        now = time.time()
        with self._lock:
            conn = self._require_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO inbound_work (
                        canonical_id, channel, sender_id, chat_id, session_key,
                        content, metadata, status, attempts, error, received_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?)
                    """,
                    (
                        canonical_id,
                        message.channel,
                        message.sender_id,
                        message.chat_id,
                        message.session_key_override,
                        message.content,
                        json.dumps(message.metadata or {}, sort_keys=True),
                        STATUS_PENDING,
                        now,
                        now,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def claim(self, canonical_id: str) -> bool:
        """Move a pending row to processing; False when it is not pending."""
        with self._lock:
            cursor = self._require_conn().execute(
                """
                UPDATE inbound_work
                SET status = ?, attempts = attempts + 1, updated_at = ?
                WHERE canonical_id = ? AND status = ?
                """,
                (STATUS_PROCESSING, time.time(), canonical_id, STATUS_PENDING),
            )
            self._require_conn().commit()
            return cursor.rowcount > 0

    def complete(self, canonical_id: str) -> None:
        with self._lock:
            self._require_conn().execute(
                "UPDATE inbound_work SET status = ?, error = '', updated_at = ? WHERE canonical_id = ?",
                (STATUS_COMPLETED, time.time(), canonical_id),
            )
            self._require_conn().commit()

    def fail(self, canonical_id: str, error: str) -> None:
        with self._lock:
            self._require_conn().execute(
                "UPDATE inbound_work SET status = ?, error = ?, updated_at = ? WHERE canonical_id = ?",
                (STATUS_FAILED, error[:500], time.time(), canonical_id),
            )
            self._require_conn().commit()

    def recover(self) -> tuple[list[InboundMessage], list[str]]:
        """Return pending messages to replay and ids of interrupted turns.

        Interrupted turns are marked ``uncertain`` and are never replayed automatically:
        the process cannot know whether the previous attempt performed side effects.
        """
        with self._lock:
            conn = self._require_conn()
            interrupted = [
                str(row["canonical_id"])
                for row in conn.execute(
                    "SELECT canonical_id FROM inbound_work WHERE status = ?",
                    (STATUS_PROCESSING,),
                )
            ]
            if interrupted:
                conn.executemany(
                    "UPDATE inbound_work SET status = ?, updated_at = ? WHERE canonical_id = ?",
                    [(STATUS_UNCERTAIN, time.time(), canonical_id) for canonical_id in interrupted],
                )
                conn.commit()
            pending = [
                _message_from_row(row)
                for row in conn.execute(
                    "SELECT * FROM inbound_work WHERE status = ? ORDER BY received_at, canonical_id",
                    (STATUS_PENDING,),
                )
            ]
            return pending, interrupted

    def get(self, canonical_id: str) -> InboxRecord | None:
        with self._lock:
            row = self._require_conn().execute(
                "SELECT * FROM inbound_work WHERE canonical_id = ?", (canonical_id,)
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._require_conn().execute(
                "SELECT status, COUNT(*) AS total FROM inbound_work GROUP BY status"
            )
            return {str(row["status"]): int(row["total"]) for row in rows}


def _message_from_row(row: sqlite3.Row) -> InboundMessage:
    return InboundMessage(
        channel=str(row["channel"]),
        sender_id=str(row["sender_id"]),
        chat_id=str(row["chat_id"]),
        content=str(row["content"]),
        metadata=json.loads(row["metadata"] or "{}"),
        session_key_override=row["session_key"],
    )


def _record_from_row(row: sqlite3.Row) -> InboxRecord:
    return InboxRecord(
        canonical_id=str(row["canonical_id"]),
        channel=str(row["channel"]),
        sender_id=str(row["sender_id"]),
        chat_id=str(row["chat_id"]),
        session_key=row["session_key"],
        content=str(row["content"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        error=str(row["error"]),
        metadata=json.loads(row["metadata"] or "{}"),
        received_at=float(row["received_at"]),
        updated_at=float(row["updated_at"]),
    )
