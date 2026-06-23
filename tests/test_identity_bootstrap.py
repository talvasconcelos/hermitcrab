"""Focused regressions for identity bootstrap layout."""

from __future__ import annotations

from typer.testing import CliRunner

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.cli.bootstrap import bootstrap_standard_layout
from hermitcrab.cli.commands import app
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


def test_context_builder_trims_history_to_prompt_budget_and_keeps_tool_pairs(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_standard_layout(config)
    builder = ContextBuilder(
        config.owner_identity_root_path,
        system_root=config.system_root_path,
        prompt_token_budget=5600,
    )

    history = [
        {"role": "user", "content": "old " * 800},
        {"role": "assistant", "content": "call tool"},
        {"role": "tool", "content": "tool output " * 800},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "recent question"},
    ]

    messages = builder.build_messages(history=history, current_message="now")

    assert {"role": "assistant", "content": "call tool"} not in messages
    assert {"role": "tool", "content": "tool output " * 120} not in messages
    assert {"role": "assistant", "content": "recent answer"} in messages
    assert {"role": "user", "content": "recent question"} in messages


def test_context_builder_keeps_recent_history_when_fixed_prompt_exceeds_budget(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_standard_layout(config)
    (config.system_root_path / "AGENTS.md").write_text("system rules " * 5000, encoding="utf-8")
    builder = ContextBuilder(
        config.owner_identity_root_path,
        system_root=config.system_root_path,
        prompt_token_budget=1000,
    )

    history = [
        {"role": "user", "content": "can you search what i asked you about dinner tonight?"},
        {"role": "assistant", "content": "I found no matching dinner plans."},
        {"role": "user", "content": "dude WTF, that was two messages ago"},
    ]

    messages = builder.build_messages(history=history, current_message="how did you forget?")

    assert {"role": "user", "content": "dude WTF, that was two messages ago"} in messages
    assert messages[-1] == {"role": "user", "content": "how did you forget?"}


def test_context_builder_warns_against_unverified_platform_truncation_blame(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_standard_layout(config)

    prompt = ContextBuilder(
        config.owner_identity_root_path,
        system_root=config.system_root_path,
    ).build_system_prompt()

    assert "Do not blame platform truncation" in prompt
    assert "recent=true" in prompt


def test_context_builder_uses_relevant_memory_without_auto_general_memory(tmp_path, monkeypatch) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    bootstrap_standard_layout(config)
    builder = ContextBuilder(
        config.owner_identity_root_path,
        system_root=config.system_root_path,
        prompt_token_budget=7000,
    )

    called_general = {"value": False}

    def _fake_relevant(*args, **kwargs):
        return "relevant hit"

    def _fake_general(*args, **kwargs):
        called_general["value"] = True
        return "general memory"

    monkeypatch.setattr(builder.memory, "get_relevant_context_for_queries", _fake_relevant)
    monkeypatch.setattr(builder.memory, "get_memory_context", _fake_general)

    prompt = builder.build_system_prompt(current_message="what about my prior plan", history=[])

    assert "# Relevant Memory" in prompt
    assert "relevant hit" in prompt
    assert "general memory" not in prompt
    assert called_general["value"] is False
