from __future__ import annotations

import pytest

from hermitcrab.agent.tools.session_search import SessionSearchTool
from hermitcrab.session import SQLiteSessionStore
from hermitcrab.session.manager import SessionManager


def test_session_manager_mirrors_saved_sessions_to_sqlite(tmp_path) -> None:
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


def test_session_manager_mirror_is_idempotent_on_save(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("cli:local")
        session.add_message("user", "first")
        manager.save(session)
        manager.save(session)

        messages = store.get_messages("cli:local")

    assert [message.content for message in messages] == ["first"]


def test_session_manager_marks_sqlite_session_archived(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        manager = SessionManager(tmp_path, sqlite_store=store)
        session = manager.get_or_create("nostr:alice")
        session.add_message("user", "old context")
        manager.save(session)
        manager.archive(session, "timeout")

        stored = store.get_session("nostr:alice")
        messages = store.get_messages("nostr:alice")

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
