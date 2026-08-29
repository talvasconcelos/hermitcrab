from __future__ import annotations

import pytest

from hermitcrab.agent.tools.session_search import SessionSearchTool
from hermitcrab.session import SQLiteSessionStore
from hermitcrab.session.manager import SessionManager, create_session_manager


def test_session_manager_writes_new_sessions_to_sqlite_only(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("telegram:tal")
        session.add_message("user", "remember the sqlite migration plan")
        manager.save(session)

        stored = store.get_session("telegram:tal")
        messages = store.get_messages("telegram:tal")

    assert stored is not None
    assert stored.status == "active"
    assert stored.channel == "telegram"
    assert stored.chat_id == "tal"
    assert [message.content for message in messages] == ["remember the sqlite migration plan"]
    assert not (tmp_path / "sessions" / "telegram_tal.jsonl").exists()


def test_session_manager_sqlite_save_is_idempotent(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("cli:local")
        session.add_message("user", "first")
        manager.save(session)
        manager.save(session)

        messages = store.get_messages("cli:local")

    assert [message.content for message in messages] == ["first"]


def test_session_manager_list_sessions_reads_sqlite(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("telegram:tal")
        session.add_message("user", "hello")
        manager.save(session)

        listed = manager.list_sessions()

    assert [item["key"] for item in listed] == ["telegram:tal"]


def test_session_manager_marks_sqlite_session_archived(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("nostr:alice")
        session.add_message("user", "old context")
        manager.save(session)
        archive_path = manager.archive(session, "timeout")

        stored = store.get_session("nostr:alice")
        messages = store.get_messages("nostr:alice")

    assert archive_path is None
    assert stored is not None
    assert stored.status == "archived"
    assert stored.archive_reason == "timeout"
    assert [message.content for message in messages] == ["old context"]


@pytest.mark.asyncio
async def test_session_search_tool_uses_sqlite_history_when_available(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("telegram:tal")
        session.add_message("user", "the beta5 sqlite search topic")
        manager.save(session)

        result = await SessionSearchTool(manager).execute(query="sqlite search")

    assert "Found 1 matching session" in result
    assert "Session: telegram:tal" in result
    assert "beta5 sqlite search topic" in result


@pytest.mark.asyncio
async def test_session_search_tool_recent_uses_sqlite_history_when_available(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("telegram:tal")
        session.add_message("user", "recent sqlite tail")
        manager.save(session)

        result = await SessionSearchTool(manager).execute(recent=True)

    assert "Found 1 recent session" in result
    assert "recent sqlite tail" in result


def test_create_session_manager_migrates_existing_jsonl_once_then_uses_marker(tmp_path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    jsonl_path = sessions_dir / "telegram_tal.jsonl"
    jsonl_path.write_text(
        '{"_type":"metadata","key":"telegram:tal","metadata":{}}\n'
        '{"role":"user","content":"existing jsonl context"}\n',
        encoding="utf-8",
    )

    manager = create_session_manager(tmp_path)

    assert manager.sqlite_store is not None
    assert manager.sqlite_store.get_session("telegram:tal") is not None
    assert manager.search_history("existing jsonl")
    assert not jsonl_path.exists()
    assert (sessions_dir / ".sqlite_migration_complete").exists()
    assert list((sessions_dir / "jsonl-migrated-backup").glob("**/*.jsonl"))

    # A second startup should trust the marker and ignore new stray JSONL files.
    stray_path = sessions_dir / "telegram_tal.jsonl"
    stray_path.write_text(
        '{"_type":"metadata","key":"telegram:tal","metadata":{}}\n'
        '{"role":"user","content":"should not import"}\n',
        encoding="utf-8",
    )
    manager = create_session_manager(tmp_path)

    assert manager.search_history("should not import") == []
