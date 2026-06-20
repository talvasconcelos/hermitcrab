from __future__ import annotations

import sqlite3

import pytest

from hermitcrab.session import MessageRecord, SQLiteSessionStore, SessionRecord


def test_sqlite_store_persists_session_lifecycle_and_messages(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"

    with SQLiteSessionStore(db_path) as store:
        store.upsert_session(
            SessionRecord(
                key="telegram:chat-1",
                identity="owner",
                channel="telegram",
                chat_id="chat-1",
                metadata={"source": "test"},
            )
        )
        first_id = store.save_message(
            MessageRecord(
                session_key="telegram:chat-1",
                role="user",
                content="Remember that my workshop is on Tuesdays.",
            )
        )
        second_id = store.save_message(
            MessageRecord(
                session_key="telegram:chat-1",
                role="assistant",
                content="Got it — Tuesdays are workshop days.",
            )
        )
        store.archive_session("telegram:chat-1", reason="timeout")

        session = store.get_session("telegram:chat-1")
        messages = store.get_messages("telegram:chat-1")

    assert first_id < second_id
    assert session is not None
    assert session.status == "archived"
    assert session.archive_reason == "timeout"
    assert session.identity == "owner"
    assert session.channel == "telegram"
    assert session.chat_id == "chat-1"
    assert session.metadata == {"source": "test"}
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].sequence == 1
    assert messages[1].sequence == 2

    with SQLiteSessionStore(db_path) as reopened:
        assert reopened.get_session("telegram:chat-1") is not None
        assert len(reopened.get_messages("telegram:chat-1")) == 2


def test_sqlite_store_scopes_sessions_and_search_by_identity_channel_chat(tmp_path) -> None:
    with SQLiteSessionStore(tmp_path / "sessions.sqlite3") as store:
        store.upsert_session(
            SessionRecord(
                key="telegram:chat-1",
                identity="owner",
                channel="telegram",
                chat_id="chat-1",
            )
        )
        store.upsert_session(
            SessionRecord(
                key="nostr:chat-1",
                identity="alice",
                channel="nostr",
                chat_id="chat-1",
            )
        )
        store.save_message(
            MessageRecord(
                session_key="telegram:chat-1",
                role="user",
                content="The secret project is called hermit shell.",
            )
        )
        store.save_message(
            MessageRecord(
                session_key="nostr:chat-1",
                role="user",
                content="The secret project is called hermit shell.",
            )
        )

        owner_results = store.search_messages("hermit shell", identity="owner")
        nostr_results = store.search_messages("hermit shell", channel="nostr", chat_id="chat-1")
        missing_results = store.search_messages("hermit shell", identity="owner", channel="nostr")

    assert [result.session_key for result in owner_results] == ["telegram:chat-1"]
    assert [result.session_key for result in nostr_results] == ["nostr:chat-1"]
    assert missing_results == []


def test_sqlite_store_records_tool_calls_and_results_for_debugging(tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    with SQLiteSessionStore(db_path) as store:
        store.upsert_session(SessionRecord(key="cli:local", identity="owner", channel="cli", chat_id="local"))
        assistant_message_id = store.save_message(
            MessageRecord(
                session_key="cli:local",
                role="assistant",
                content="",
                metadata={
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                        }
                    ]
                },
            )
        )
        tool_message_id = store.save_message(
            MessageRecord(
                session_key="cli:local",
                role="tool",
                content="README contents",
                metadata={"tool_call_id": "call_123"},
            )
        )

    with sqlite3.connect(db_path) as conn:
        call = conn.execute("SELECT id, session_key, message_id, name, arguments FROM tool_calls").fetchone()
        result = conn.execute(
            "SELECT session_key, message_id, tool_call_id, content FROM tool_results"
        ).fetchone()

    assert call == ("call_123", "cli:local", assistant_message_id, "read_file", '{"path":"README.md"}')
    assert result == ("cli:local", tool_message_id, "call_123", "README contents")


def test_sqlite_store_requires_fts5_when_requested(tmp_path, monkeypatch) -> None:
    def fake_create_fts_table(self) -> None:  # type: ignore[no-untyped-def]
        self.fts_enabled = False
        self.fts_error = "no such module: fts5"

    monkeypatch.setattr(SQLiteSessionStore, "_create_fts_table", fake_create_fts_table)

    with pytest.raises(RuntimeError, match="FTS5 is required"):
        SQLiteSessionStore(tmp_path / "sessions.sqlite3", require_fts5=True)
