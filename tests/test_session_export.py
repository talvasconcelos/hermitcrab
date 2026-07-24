from __future__ import annotations

import json

import pytest

from hermitcrab.session import SQLiteSessionStore
from hermitcrab.session.manager import SessionManager, create_session_manager


def _manager(tmp_path) -> SessionManager:
    return SessionManager(tmp_path, sqlite_store=SQLiteSessionStore(tmp_path / "sessions.sqlite3"))


def test_session_export_jsonl_preserves_lifecycle_and_messages(tmp_path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("telegram:tal")
    session.add_message("user", "Please export this conversation.")
    session.add_message("assistant", "Here is the export.")
    manager.save(session)
    manager.archive(session, "timeout")

    exported = manager.export_session("telegram:tal", format="jsonl")

    lines = [json.loads(line) for line in exported.splitlines()]
    assert lines[0]["_type"] == "metadata"
    assert lines[0]["status"] == "archived"
    assert lines[0]["archive_reason"] == "timeout"
    assert [line["content"] for line in lines[1:]] == [
        "Please export this conversation.",
        "Here is the export.",
    ]


def test_session_export_reads_archived_jsonl_session(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("cli:direct")
    session.add_message("user", "Archived legacy session")
    manager.save(session)
    manager.archive(session, "timeout")

    exported = manager.export_session("cli:direct", format="jsonl")

    lines = [json.loads(line) for line in exported.splitlines()]
    assert lines[0]["status"] == "archived"
    assert lines[0]["archive_reason"] == "timeout"
    assert lines[1]["content"] == "Archived legacy session"


def test_session_export_uses_original_key_for_migrated_archive(tmp_path) -> None:
    archive = tmp_path / "sessions" / "archive"
    archive.mkdir(parents=True)
    (archive / "cli_direct-timeout-2026-01-01T00-00-00.jsonl").write_text(
        '{"_type":"metadata","key":"cli:direct","metadata":{}}\n'
        '{"role":"user","content":"Migrated archive"}\n',
        encoding="utf-8",
    )

    manager = create_session_manager(tmp_path)
    exported = manager.export_session("cli:direct", format="jsonl")

    lines = [json.loads(line) for line in exported.splitlines()]
    assert lines[0]["key"] == "cli:direct"
    assert lines[0]["status"] == "archived"
    assert lines[1]["content"] == "Migrated archive"


def test_session_export_markdown_is_readable(tmp_path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:direct")
    session.add_message("user", "Hello")
    manager.save(session)

    exported = manager.export_session("cli:direct", format="markdown")

    assert "# Session: cli:direct" in exported
    assert "Status: active" in exported
    assert "## User" in exported
    assert "Hello" in exported


def test_session_export_rejects_unknown_format(tmp_path) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(ValueError, match="format"):
        manager.export_session("cli:direct", format="html")


def test_session_export_requires_existing_session(tmp_path) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(KeyError, match="session not found"):
        manager.export_session("cli:missing", format="jsonl")


def test_session_export_cli_writes_requested_format(monkeypatch, tmp_path) -> None:
    from typer.testing import CliRunner

    from hermitcrab.cli.commands import app
    from hermitcrab.config.loader import save_config
    from hermitcrab.config.schema import Config

    config = Config.model_validate({"root": str(tmp_path)})
    config_path = tmp_path / "config.json"
    save_config(config, config_path)
    monkeypatch.setattr("hermitcrab.config.loader.get_config_path", lambda: config_path)
    from hermitcrab.session.manager import create_session_manager

    manager = create_session_manager(config.owner_identity_root_path)
    session = manager.get_or_create("cli:direct")
    session.add_message("user", "Export me")
    manager.save(session)

    output_path = tmp_path / "session.md"
    result = CliRunner().invoke(app, ["session", "export", "cli:direct", str(output_path)])

    assert result.exit_code == 0
    assert "Exported session" in result.stdout
    assert "Export me" in output_path.read_text(encoding="utf-8")
