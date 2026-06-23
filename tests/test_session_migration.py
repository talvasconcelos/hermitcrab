from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermitcrab.session import SQLiteSessionStore
from hermitcrab.session.migration import import_jsonl_sessions, migrate_jsonl_sessions_once


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_import_jsonl_sessions_imports_active_and_archived_sessions(tmp_path) -> None:
    workspace = tmp_path / "identity"
    active_path = workspace / "sessions" / "telegram_tal.jsonl"
    archived_path = workspace / "sessions" / "archive" / "telegram_tal-timeout-2026-06-20T10-00-00.jsonl"
    write_jsonl(
        active_path,
        [
            {
                "_type": "metadata",
                "key": "telegram:tal",
                "created_at": "2026-06-20T09:00:00+00:00",
                "updated_at": "2026-06-20T09:05:00+00:00",
                "metadata": {"identity": "owner", "topic": "active"},
            },
            {"role": "user", "content": "current question", "timestamp": "2026-06-20T09:01:00+00:00"},
        ],
    )
    write_jsonl(
        archived_path,
        [
            {
                "_type": "metadata",
                "key": "telegram:tal",
                "created_at": "2026-06-19T09:00:00+00:00",
                "updated_at": "2026-06-19T09:05:00+00:00",
                "metadata": {"identity": "owner", "topic": "archived"},
            },
            {"role": "user", "content": "old workshop plan", "timestamp": "2026-06-19T09:01:00+00:00"},
        ],
    )

    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        report = import_jsonl_sessions(workspace, store)
        sessions = store.list_sessions(identity="owner")
        active = store.get_session("telegram:tal")
        archived = [session for session in sessions if session.status == "archived"]

    assert report.ok
    assert report.scanned == 2
    assert report.imported == 2
    assert active is not None
    assert active.status == "active"
    assert active.channel == "telegram"
    assert active.chat_id == "tal"
    assert active.metadata["original_session_key"] == "telegram:tal"
    assert len(archived) == 1
    assert archived[0].key.startswith("telegram:tal#archive:")
    assert archived[0].archive_reason == "timeout"
    assert archived[0].metadata["original_session_key"] == "telegram:tal"


def test_import_jsonl_sessions_dry_run_does_not_write(tmp_path) -> None:
    workspace = tmp_path / "identity"
    write_jsonl(
        workspace / "sessions" / "nostr_alice.jsonl",
        [
            {"_type": "metadata", "key": "nostr:alice", "metadata": {}},
            {"role": "user", "content": "hello"},
        ],
    )

    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        report = import_jsonl_sessions(workspace, store, dry_run=True)
        sessions = store.list_sessions()

    assert report.ok
    assert report.scanned == 1
    assert report.imported == 0
    assert report.skipped == 1
    assert report.items[0].status == "planned"
    assert sessions == []


def test_import_jsonl_sessions_is_idempotent_for_same_source(tmp_path) -> None:
    workspace = tmp_path / "identity"
    path = workspace / "sessions" / "cli_local.jsonl"
    write_jsonl(
        path,
        [
            {"_type": "metadata", "key": "cli:local", "metadata": {}},
            {"role": "user", "content": "first"},
        ],
    )

    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        import_jsonl_sessions(workspace, store)
        import_jsonl_sessions(workspace, store)
        messages = store.get_messages("cli:local")

    assert [message.content for message in messages] == ["first"]


def test_import_jsonl_sessions_records_errors_and_continues(tmp_path) -> None:
    workspace = tmp_path / "identity"
    write_jsonl(
        workspace / "sessions" / "good_chat.jsonl",
        [
            {"_type": "metadata", "key": "good:chat", "metadata": {}},
            {"role": "user", "content": "valid"},
        ],
    )
    bad_path = workspace / "sessions" / "bad_chat.jsonl"
    bad_path.write_text('{"_type":"metadata","key":"bad:chat"}\nnot-json\n', encoding="utf-8")

    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        report = import_jsonl_sessions(workspace, store)
        good = store.get_session("good:chat")

    assert report.scanned == 2
    assert report.imported == 1
    assert report.failed == 1
    assert not report.ok
    assert good is not None
    failed = [item for item in report.items if item.status == "failed"]
    assert len(failed) == 1
    assert "invalid JSON" in (failed[0].error or "")


def test_import_jsonl_sessions_preserves_tool_messages(tmp_path) -> None:
    workspace = tmp_path / "identity"
    write_jsonl(
        workspace / "sessions" / "cli_local.jsonl",
        [
            {"_type": "metadata", "key": "cli:local", "metadata": {}},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}
                ],
            },
            {"role": "tool", "content": "contents", "tool_call_id": "call_1"},
        ],
    )
    db_path = tmp_path / "sessions.sqlite3"

    with SQLiteSessionStore(db_path) as store:
        import_jsonl_sessions(workspace, store)

    with sqlite3.connect(db_path) as conn:
        call_count = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
        result_count = conn.execute("SELECT COUNT(*) FROM tool_results").fetchone()[0]

    assert call_count == 1
    assert result_count == 1


def test_migrate_jsonl_sessions_once_writes_marker_and_moves_legacy_files(tmp_path) -> None:
    workspace = tmp_path / "identity"
    jsonl_path = workspace / "sessions" / "cli_local.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"_type": "metadata", "key": "cli:local", "metadata": {}},
            {"role": "user", "content": "legacy session"},
        ],
    )

    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        report = migrate_jsonl_sessions_once(workspace, store)
        second_report = migrate_jsonl_sessions_once(workspace, store)
        messages = store.get_messages("cli:local")

    assert report.imported == 1
    assert second_report.scanned == 0
    assert [message.content for message in messages] == ["legacy session"]
    assert not jsonl_path.exists()
    assert (workspace / "sessions" / ".sqlite_migration_complete").exists()
    assert list((workspace / "sessions" / "jsonl-migrated-backup").glob("**/*.jsonl"))
