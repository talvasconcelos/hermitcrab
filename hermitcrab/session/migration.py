"""Import helpers for migrating file-backed JSONL sessions into SQLite."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from hermitcrab.session.sqlite_store import SQLiteSessionStore
from hermitcrab.session.storage import SessionRecord, parse_datetime


@dataclass(slots=True)
class SessionImportItem:
    """One planned or completed JSONL session import."""

    path: Path
    source_id: str
    session_key: str
    original_key: str
    archived: bool
    message_count: int
    status: str
    archive_reason: str | None = None
    error: str | None = None


@dataclass(slots=True)
class SessionImportReport:
    """Summary of a JSONL-to-SQLite session import run."""

    dry_run: bool
    scanned: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    items: list[SessionImportItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


MIGRATION_MARKER = ".sqlite_migration_complete"
JSONL_BACKUP_DIR = "jsonl-migrated-backup"


def migrate_jsonl_sessions_once(
    workspace: Path,
    store: SQLiteSessionStore,
    *,
    backup: bool = True,
) -> SessionImportReport:
    """Migrate legacy JSONL sessions once, then mark SQLite authoritative."""
    workspace = Path(workspace)
    sessions_dir = workspace / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    marker = sessions_dir / MIGRATION_MARKER
    if marker.exists():
        return SessionImportReport(dry_run=False)

    report = import_jsonl_sessions(workspace, store)
    if not report.ok:
        return report

    if backup:
        _backup_jsonl_sessions(workspace, report)
    else:
        _delete_imported_jsonl_sessions(report)

    marker.write_text(
        "SQLite session migration complete. Legacy JSONL session files are no longer live.\n",
        encoding="utf-8",
    )
    return report


def _backup_jsonl_sessions(workspace: Path, report: SessionImportReport) -> None:
    imported = [item for item in report.items if item.status == "imported"]
    if not imported:
        return
    backup_root = workspace / "sessions" / JSONL_BACKUP_DIR / datetime.now().strftime(
        "%Y-%m-%dT%H-%M-%S"
    )
    for item in imported:
        if not item.path.exists():
            continue
        target = backup_root / item.source_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item.path), str(target))


def _delete_imported_jsonl_sessions(report: SessionImportReport) -> None:
    for item in report.items:
        if item.status == "imported" and item.path.exists():
            item.path.unlink()


def import_jsonl_sessions(
    workspace: Path,
    store: SQLiteSessionStore,
    *,
    dry_run: bool = False,
) -> SessionImportReport:
    """Import active and archived JSONL session files from an identity workspace.

    The existing file-backed runtime remains authoritative until a later beta5
    ticket wires SQLite into the manager/search path. This helper is deliberately
    idempotent: each JSONL file maps to a stable ``source_id`` and SQLite session
    key. Re-importing the same file replaces that imported session's messages
    instead of appending duplicates.
    """

    workspace = Path(workspace)
    sessions_dir = workspace / "sessions"
    archive_dir = sessions_dir / "archive"
    report = SessionImportReport(dry_run=dry_run)

    paths = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.name)
    paths.extend(sorted(archive_dir.glob("*.jsonl"), key=lambda p: p.name))

    for path in paths:
        report.scanned += 1
        archived = path.parent == archive_dir
        try:
            parsed = _read_jsonl_session(path)
            original_key = parsed["key"] or _key_from_path(path, archived=archived)
            source_id = _source_id(workspace, path)
            session_key = _sqlite_session_key(original_key, source_id, archived=archived)
            archive_reason = _archive_reason(path, original_key) if archived else None
            item = SessionImportItem(
                path=path,
                source_id=source_id,
                session_key=session_key,
                original_key=original_key,
                archived=archived,
                message_count=len(parsed["messages"]),
                status="planned" if dry_run else "imported",
                archive_reason=archive_reason,
            )
            if not dry_run:
                _replace_imported_session(
                    store,
                    session_key=session_key,
                    original_key=original_key,
                    source_id=source_id,
                    archived=archived,
                    archive_reason=archive_reason,
                    metadata=parsed["metadata"],
                    created_at=parsed["created_at"],
                    updated_at=parsed["updated_at"],
                    messages=parsed["messages"],
                    source_path=path,
                )
                report.imported += 1
            else:
                report.skipped += 1
            report.items.append(item)
        except Exception as exc:  # keep migrating other files
            logger.warning("Failed to import session JSONL {}: {}", path, exc)
            report.failed += 1
            report.items.append(
                SessionImportItem(
                    path=path,
                    source_id=_source_id(workspace, path),
                    session_key="",
                    original_key="",
                    archived=archived,
                    message_count=0,
                    status="failed",
                    error=str(exc),
                )
            )

    return report


def _read_jsonl_session(path: Path) -> dict[str, Any]:
    session_key = ""
    metadata: dict[str, Any] = {}
    created_at: datetime | None = None
    updated_at: datetime | None = None
    messages: list[dict[str, Any]] = []

    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"line {line_number}: expected JSON object")

            if data.get("_type") == "metadata":
                session_key = str(data.get("key") or session_key)
                raw_metadata = data.get("metadata")
                metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                created_at = parse_datetime(data.get("created_at"))
                updated_at = parse_datetime(data.get("updated_at"))
                continue

            role = data.get("role")
            if not isinstance(role, str) or not role:
                raise ValueError(f"line {line_number}: message missing role")
            messages.append(data)

    if not session_key:
        session_key = _key_from_path(path, archived=path.parent.name == "archive")
    if updated_at is None:
        updated_at = _latest_message_time(messages) or created_at
    if created_at is None:
        created_at = _earliest_message_time(messages) or updated_at

    return {
        "key": session_key,
        "metadata": metadata,
        "created_at": created_at,
        "updated_at": updated_at,
        "messages": messages,
    }


def _replace_imported_session(
    store: SQLiteSessionStore,
    *,
    session_key: str,
    original_key: str,
    source_id: str,
    archived: bool,
    archive_reason: str | None,
    metadata: dict[str, Any],
    created_at: datetime | None,
    updated_at: datetime | None,
    messages: list[dict[str, Any]],
    source_path: Path,
) -> None:
    store._require_open()  # noqa: SLF001 - migration intentionally coordinates low-level replace
    store.conn.execute("DELETE FROM messages WHERE session_key = ?", (session_key,))
    store.conn.execute("DELETE FROM tool_calls WHERE session_key = ?", (session_key,))
    store.conn.execute("DELETE FROM tool_results WHERE session_key = ?", (session_key,))
    if store.fts_enabled:
        store.conn.execute(
            "DELETE FROM messages_fts WHERE rowid NOT IN (SELECT id FROM messages)"
        )

    channel, chat_id = _split_session_key(original_key)
    merged_metadata = dict(metadata)
    merged_metadata.update(
        {
            "import_source": "jsonl_session",
            "import_source_id": source_id,
            "source_path": str(source_path),
            "original_session_key": original_key,
        }
    )
    store.upsert_session(
        SessionRecord(
            key=session_key,
            identity=str(metadata.get("identity") or ""),
            channel=channel,
            chat_id=chat_id,
            status="archived" if archived else "active",
            created_at=created_at,
            updated_at=updated_at,
            archived_at=updated_at if archived else None,
            archive_reason=archive_reason,
            metadata=merged_metadata,
        )
    )
    store.save_messages(session_key, messages)


def _source_id(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _sqlite_session_key(original_key: str, source_id: str, *, archived: bool) -> str:
    if not archived:
        return original_key
    return f"{original_key}#archive:{_slug(source_id)}"


def _key_from_path(path: Path, *, archived: bool) -> str:
    stem = path.stem
    if archived:
        match = re.match(r"(?P<safe_key>.+?)-[^-]+-\d{4}-\d{2}-\d{2}T", stem)
        if match:
            stem = match.group("safe_key")
    return stem.replace("_", ":", 1)


def _archive_reason(path: Path, original_key: str) -> str:
    safe_key = original_key.replace(":", "_")
    stem = path.stem
    prefix = f"{safe_key}-"
    if stem.startswith(prefix):
        rest = stem[len(prefix) :]
        match = re.match(r"(?P<reason>.+)-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$", rest)
        if match:
            return match.group("reason")
    return "imported_archive"


def _split_session_key(key: str) -> tuple[str, str]:
    if ":" not in key:
        return key, ""
    return key.split(":", 1)


def _earliest_message_time(messages: list[dict[str, Any]]) -> datetime | None:
    for message in messages:
        parsed = parse_datetime(message.get("timestamp") or message.get("created_at"))
        if parsed is not None:
            return parsed
    return None


def _latest_message_time(messages: list[dict[str, Any]]) -> datetime | None:
    for message in reversed(messages):
        parsed = parse_datetime(message.get("timestamp") or message.get("created_at"))
        if parsed is not None:
            return parsed
    return None


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return slug or "session"


__all__ = [
    "JSONL_BACKUP_DIR",
    "MIGRATION_MARKER",
    "SessionImportItem",
    "SessionImportReport",
    "import_jsonl_sessions",
    "migrate_jsonl_sessions_once",
]
