"""Focused regressions for user CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hermitcrab.cli.commands import app
from hermitcrab.config.loader import load_config, save_config
from hermitcrab.config.schema import Config

runner = CliRunner()


def _use_config(monkeypatch, tmp_path: Path, config: Config) -> Path:
    config_path = tmp_path / "config.json"
    save_config(config, config_path)
    monkeypatch.setattr("hermitcrab.config.loader.get_config_path", lambda: config_path)
    return config_path


def test_user_list_json_shows_owner_identity(monkeypatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, Config.model_validate({"root": str(tmp_path)}))

    result = runner.invoke(app, ["user", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["name"] == "owner"
    assert payload[0]["active"] is True
    assert len(payload[0]["nostr_public_key"]) == 64


def test_user_add_creates_identity_config_and_bootstrap_root(monkeypatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, Config.model_validate({"root": str(tmp_path)}))

    result = runner.invoke(app, ["user", "add", "alice", "--label", "Alice"])

    assert result.exit_code == 0
    config = load_config(tmp_path / "config.json", strict=True)
    assert "alice" in config.identities.registry
    assert config.identities.registry["alice"].label == "Alice"
    assert len(config.identities.registry["alice"].nostr_public_key) == 64
    assert (tmp_path / "identities" / "alice" / "IDENTITY.md").exists()
    assert (tmp_path / "identities" / "alice" / "cron").is_dir()


def test_user_add_rejects_reserved_name(monkeypatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, Config.model_validate({"root": str(tmp_path)}))

    result = runner.invoke(app, ["user", "add", "system"])

    assert result.exit_code == 1
    assert "reserved" in result.stdout


def test_user_remove_disables_identity_and_routes(monkeypatch, tmp_path) -> None:
    from pynostr.key import PrivateKey

    sender = PrivateKey().public_key.hex()
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "identities": {"registry": {"alice": {}}},
            "channels": {"nostr": {"allowedPubkeys": [sender], "identityBindings": {"alice": [sender]}}},
        }
    )
    _use_config(monkeypatch, tmp_path, config)

    result = runner.invoke(app, ["user", "remove", "alice"])

    assert result.exit_code == 0
    reloaded = load_config(tmp_path / "config.json", strict=True)
    assert reloaded.identities.registry["alice"].active is False
    assert "alice" not in reloaded.channels.nostr.identity_bindings
    assert sender not in reloaded.channels.nostr.allowed_pubkeys


def test_user_archive_marks_archived_without_deleting_root(monkeypatch, tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path), "identities": {"registry": {"alice": {}}}})
    _use_config(monkeypatch, tmp_path, config)
    identity_root = tmp_path / "identities" / "alice"
    identity_root.mkdir(parents=True)

    result = runner.invoke(app, ["user", "archive", "alice"])

    assert result.exit_code == 0
    reloaded = load_config(tmp_path / "config.json", strict=True)
    assert reloaded.identities.registry["alice"].active is False
    assert reloaded.identities.registry["alice"].role == "archived"
    assert identity_root.exists()


def test_user_route_nostr_binds_sender_pubkey(monkeypatch, tmp_path) -> None:
    from pynostr.key import PrivateKey

    sender = PrivateKey().public_key.hex()
    config = Config.model_validate({"root": str(tmp_path), "identities": {"registry": {"alice": {}}}})
    _use_config(monkeypatch, tmp_path, config)

    result = runner.invoke(app, ["user", "route", "nostr", "alice", sender])

    assert result.exit_code == 0
    reloaded = load_config(tmp_path / "config.json", strict=True)
    assert reloaded.channels.nostr.identity_bindings == {"alice": [sender]}
    assert reloaded.channels.nostr.allowed_pubkeys == [sender]


def test_user_route_rejects_duplicate_sender_pubkey(monkeypatch, tmp_path) -> None:
    from pynostr.key import PrivateKey

    sender = PrivateKey().public_key.hex()
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "identities": {"registry": {"alice": {}, "bob": {}}},
            "channels": {"nostr": {"identityBindings": {"alice": [sender]}}},
        }
    )
    _use_config(monkeypatch, tmp_path, config)

    result = runner.invoke(app, ["user", "route", "nostr", "bob", sender])

    assert result.exit_code == 1
    assert "already routed" in result.stdout


def test_user_models_sets_interactive_named_model(monkeypatch, tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {"fast": {"model": "ollama/qwen3"}},
            "identities": {"registry": {"alice": {}}},
        }
    )
    _use_config(monkeypatch, tmp_path, config)

    result = runner.invoke(app, ["user", "models", "alice", "--interactive", "fast"])

    assert result.exit_code == 0
    reloaded = load_config(tmp_path / "config.json", strict=True)
    assert reloaded.identities.registry["alice"].models["interactiveResponse"] == "fast"


def test_user_status_reports_heartbeat_and_cron(monkeypatch, tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path), "identities": {"registry": {"alice": {}}}})
    _use_config(monkeypatch, tmp_path, config)
    root = tmp_path / "identities" / "alice"
    root.mkdir(parents=True)
    (root / "HEARTBEAT.md").write_text("## Active Tasks\n", encoding="utf-8")

    result = runner.invoke(app, ["user", "status", "alice", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "alice"
    assert payload["heartbeat"]["exists"] is True
    assert payload["cron"]["jobs"] == 0
