"""Focused regressions for beta4 identity bootstrap layout."""

from __future__ import annotations

from typer.testing import CliRunner

from hermitcrab.cli.commands import app, bootstrap_beta4_layout
from hermitcrab.config.schema import Config

runner = CliRunner()


def test_bootstrap_beta4_layout_creates_system_and_owner_identity_roots(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})

    bootstrap_beta4_layout(config)

    assert (tmp_path / "system" / "AGENTS.md").exists()
    assert (tmp_path / "system" / "TOOLS.md").exists()
    assert (tmp_path / "system" / "logs").is_dir()
    assert (tmp_path / "system" / "indexes").is_dir()
    assert (tmp_path / "system" / "history").is_dir()

    owner_root = tmp_path / "identities" / "owner"
    for filename in ["IDENTITY.md", "SOUL.md", "USER.md", "HEARTBEAT.md"]:
        assert (owner_root / filename).exists()
    assert not (owner_root / "AGENTS.md").exists()
    assert not (owner_root / "TOOLS.md").exists()

    for dirname in [
        "cron",
        "journal",
        "knowledge",
        "lists",
        "memory",
        "people",
        "projects",
        "reminders",
        "reports",
        "scratchpads",
        "sessions",
        "skills",
    ]:
        assert (owner_root / dirname).is_dir()


def test_bootstrap_beta4_layout_is_idempotent_and_preserves_files(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})

    bootstrap_beta4_layout(config)
    agents_path = tmp_path / "system" / "AGENTS.md"
    agents_path.write_text("custom system guidance\n", encoding="utf-8")

    bootstrap_beta4_layout(config)

    assert agents_path.read_text(encoding="utf-8") == "custom system guidance\n"


def test_onboard_creates_beta4_layout_without_legacy_workspace(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    config_path = home / ".hermitcrab" / "config.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("hermitcrab.config.loader.get_config_path", lambda: config_path)

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert config_path.exists()
    assert (home / ".hermitcrab" / "system" / "AGENTS.md").exists()
    assert (home / ".hermitcrab" / "system" / "TOOLS.md").exists()
    assert (home / ".hermitcrab" / "identities" / "owner" / "IDENTITY.md").exists()
    assert (home / ".hermitcrab" / "identities" / "owner" / "memory" / "facts").is_dir()
    assert not (home / ".hermitcrab" / "workspace").exists()
