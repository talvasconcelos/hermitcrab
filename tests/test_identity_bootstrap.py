"""Focused regressions for identity bootstrap layout."""

from __future__ import annotations

from typer.testing import CliRunner

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.cli.commands import app, bootstrap_standard_layout
from hermitcrab.cli.diagnostics import build_status_report
from hermitcrab.config.loader import save_config
from hermitcrab.config.schema import Config

runner = CliRunner()


def test_bootstrap_standard_layout_creates_system_and_owner_identity_roots(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})

    bootstrap_standard_layout(config)

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


def test_bootstrap_standard_layout_is_idempotent_and_preserves_files(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})

    bootstrap_standard_layout(config)
    agents_path = tmp_path / "system" / "AGENTS.md"
    agents_path.write_text("custom system guidance\n", encoding="utf-8")

    bootstrap_standard_layout(config)

    assert agents_path.read_text(encoding="utf-8") == "custom system guidance\n"


def test_onboard_creates_standard_identity_layout(monkeypatch, tmp_path) -> None:
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


def test_status_treats_identity_layout_as_bootstrapped(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "providers": {"anthropic": {"apiKey": "test-key"}},
        }
    )
    save_config(config, config_path)
    bootstrap_standard_layout(config)

    report = build_status_report(config_path)

    assert report.bootstrap_ready is True
    assert report.overall_state == "ready"


def test_context_builder_loads_system_and_identity_bootstrap_files(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_standard_layout(config)

    (config.system_root_path / "AGENTS.md").write_text("system rules\n", encoding="utf-8")
    (config.owner_identity_root_path / "IDENTITY.md").write_text(
        "identity rules\n",
        encoding="utf-8",
    )

    prompt = ContextBuilder(
        config.owner_identity_root_path,
        system_root=config.system_root_path,
    ).build_system_prompt()

    assert "system rules" in prompt
    assert "identity rules" in prompt


def test_context_builder_warns_filesystem_tools_are_identity_scoped(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_standard_layout(config)

    prompt = ContextBuilder(
        config.owner_identity_root_path,
        system_root=config.system_root_path,
    ).build_system_prompt()

    assert f"filesystem tool boundary is: {config.owner_identity_root_path}" in prompt
    assert "Use relative paths like `memory/`, `knowledge/`, or `scratchpads/...`" in prompt
    assert f"parent HermitCrab directories such as `{config.hermitcrab_root_path}`" in prompt
    assert f"`{config.identities_root_path}`" in prompt
    assert "outside this identity's allowed workspace" in prompt


def test_context_builder_keeps_volatile_session_context_out_of_system_prefix(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_standard_layout(config)

    builder = ContextBuilder(
        config.owner_identity_root_path,
        system_root=config.system_root_path,
    )

    messages = builder.build_messages(
        history=[],
        current_message="hello",
        channel="nostr",
        chat_id="abc123",
        scratchpad_path="/tmp/session.md",
    )

    system_prompt = messages[0]["content"]
    runtime_context = messages[-2]["content"]

    assert "Current time:" not in system_prompt
    assert "Chat ID: abc123" not in system_prompt
    assert "Session scratchpad: /tmp/session.md" not in system_prompt
    assert "Current time:" in runtime_context
    assert "Chat ID: abc123" in runtime_context
    assert "Session scratchpad: /tmp/session.md" in runtime_context
