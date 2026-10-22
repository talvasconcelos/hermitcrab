"""Durable outbound delivery records with bounded retry state.

The reminder service uses this ledger so a reminder is only marked triggered after
a channel accepts its notification. A crash between send and acknowledgement leaves
a ``sending`` record, which is surfaced as ``uncertain`` rather than silently retried.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUS_QUEUED = "queued"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_UNCERTAIN = "uncertain"


@dataclass(slots=True)
class DeliveryRecord:
    """One outbound notification and its delivery state."""

    id: int
    dedupe_key: str
    kind: str
    channel: str
    chat_id: str
    content: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str = ""
    event_ids: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    sent_at: float | None = None

    @property
    def retryable(self) -> bool:
        return self.status in {STATUS_QUEUED, STATUS_FAILED} and self.attempts < self.max_attempts


class DeliveryStore:
    """SQLite ledger for outbound notification delivery."""

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
            CREATE TABLE IF NOT EXISTS outbound_delivery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                channel TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                last_error TEXT NOT NULL DEFAULT '',
                event_ids TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                sent_at REAL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbound_delivery_status ON outbound_delivery(status)"
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
            raise RuntimeError("delivery store is closed")
        return self.conn

    def enqueue(
        self,
        *,
        dedupe_key: str,
        kind: str,
        channel: str,
        chat_id: str,
        content: str,
        max_attempts: int = 5,
    ) -> DeliveryRecord:
        """Return the existing record for ``dedupe_key`` or create a queued one."""
        now = time.time()
        with self._lock:
            conn = self._require_conn()
            conn.execute(
                """
                INSERT INTO outbound_delivery (
                    dedupe_key, kind, channel, chat_id, content, status,
                    attempts, max_attempts, last_error, event_ids, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, '', '[]', ?, ?)
                ON CONFLICT(dedupe_key) DO NOTHING
                """,
                (
                    dedupe_key,
                    kind,
                    channel,
                    chat_id,
                    content,
                    STATUS_QUEUED,
                    max(1, max_attempts),
                    now,
                    now,
                ),
            )
            conn.commit()
            record = self._get_by_dedupe_locked(dedupe_key)
        assert record is not None
        return record

    def begin_attempt(self, delivery_id: int) -> bool:
        """Increment attempts and mark the record sending when retries remain."""
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT status, attempts, max_attempts FROM outbound_delivery WHERE id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                return False
            if row["status"] in {STATUS_SENT, STATUS_UNCERTAIN}:
                return False
            if int(row["attempts"]) >= int(row["max_attempts"]):
                conn.execute(
                    "UPDATE outbound_delivery SET status = ?, updated_at = ? WHERE id = ?",
                    (STATUS_FAILED, time.time(), delivery_id),
                )
                conn.commit()
                return False
            conn.execute(
                """
                UPDATE outbound_delivery
                SET status = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ?
                """,
                (STATUS_SENDING, time.time(), delivery_id),
            )
            conn.commit()
            return True

    def mark_sent(self, delivery_id: int, *, event_ids: list[str] | None = None) -> None:
        with self._lock:
            self._require_conn().execute(
                """
                UPDATE outbound_delivery
                SET status = ?, last_error = '', event_ids = ?, sent_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    STATUS_SENT,
                    json.dumps(event_ids or []),
                    time.time(),
                    time.time(),
                    delivery_id,
                ),
            )
            self._require_conn().commit()

    def mark_failed(self, delivery_id: int, error: str) -> None:
        with self._lock:
            self._require_conn().execute(
                "UPDATE outbound_delivery SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (STATUS_FAILED, error[:500], time.time(), delivery_id),
            )
            self._require_conn().commit()

    def mark_uncertain(self, delivery_id: int, error: str) -> None:
        with self._lock:
            self._require_conn().execute(
                "UPDATE outbound_delivery SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                (STATUS_UNCERTAIN, error[:500], time.time(), delivery_id),
            )
            self._require_conn().commit()

    def get(self, delivery_id: int) -> DeliveryRecord | None:
        with self._lock:
            row = self._require_conn().execute(
                "SELECT * FROM outbound_delivery WHERE id = ?", (delivery_id,)
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def get_by_dedupe(self, dedupe_key: str) -> DeliveryRecord | None:
        with self._lock:
            return self._get_by_dedupe_locked(dedupe_key)

    def _get_by_dedupe_locked(self, dedupe_key: str) -> DeliveryRecord | None:
        row = self._require_conn().execute(
            "SELECT * FROM outbound_delivery WHERE dedupe_key = ?", (dedupe_key,)
        ).fetchone()
        return _record_from_row(row) if row is not None else None

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._require_conn().execute(
                "SELECT status, COUNT(*) AS total FROM outbound_delivery GROUP BY status"
            )
            return {str(row["status"]): int(row["total"]) for row in rows}


def _record_from_row(row: sqlite3.Row) -> DeliveryRecord:
    return DeliveryRecord(
        id=int(row["id"]),
        dedupe_key=str(row["dedupe_key"]),
        kind=str(row["kind"]),
        channel=str(row["channel"]),
        chat_id=str(row["chat_id"]),
        content=str(row["content"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        last_error=str(row["last_error"]),
        event_ids=_load_event_ids(row["event_ids"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        sent_at=float(row["sent_at"]) if row["sent_at"] is not None else None,
    )


def _load_event_ids(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []
