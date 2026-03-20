import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hermitcrab.cli.commands import _build_job_models_from_config, _build_runtime_model_aliases, app
from hermitcrab.config.schema import Config, ModelAliasConfig
from hermitcrab.providers.litellm_provider import LiteLLMProvider
from hermitcrab.providers.openai_codex_provider import _strip_model_prefix
from hermitcrab.providers.registry import find_by_model
from hermitcrab.utils.helpers import resolve_model_alias, resolve_model_alias_config

runner = CliRunner()


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with (
        patch("hermitcrab.config.loader.get_config_path") as mock_cp,
        patch("hermitcrab.config.loader.save_config") as mock_sc,
        patch("hermitcrab.config.loader.load_config"),
        patch("hermitcrab.utils.helpers.get_workspace_path") as mock_ws,
    ):
        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_sc.side_effect = lambda config: config_file.write_text("{}")

        yield config_file, workspace_dir

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """No existing config — should create from scratch."""
    config_file, workspace_dir = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "hermitcrab is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    # Check category-based memory directories exist
    assert (workspace_dir / "memory" / "facts").is_dir()
    assert (workspace_dir / "memory" / "decisions").is_dir()
    assert (workspace_dir / "memory" / "goals").is_dir()
    assert (workspace_dir / "memory" / "tasks").is_dir()
    assert (workspace_dir / "memory" / "reflections").is_dir()
    assert (workspace_dir / "scratchpads").is_dir()
    assert (workspace_dir / "scratchpads" / "archive").is_dir()


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_bootstrap_templates_include_continuity_and_delegation_guidance(mock_paths):
    """Bundled templates should seed new workspaces with continuity and delegation rules."""
    _, workspace_dir = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    agents_text = (workspace_dir / "AGENTS.md").read_text(encoding="utf-8")
    tools_text = (workspace_dir / "TOOLS.md").read_text(encoding="utf-8")

    assert "Check the recent conversation before answering" in agents_text
    assert "Broad, strategic, or ambiguous work stays owned by the main agent." in agents_text
    assert "Check the recent conversation first" in tools_text
    assert "If the same fix fails three times in a row" in tools_text


def test_config_matches_github_copilot_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "github-copilot/gpt-5.3-codex"

    assert config.get_provider_name() == "github_copilot"


def test_config_matches_openai_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.1-codex"

    assert config.get_provider_name() == "openai_codex"


def test_find_by_model_prefers_explicit_prefix_over_generic_codex_keyword():
    spec = find_by_model("github-copilot/gpt-5.3-codex")

    assert spec is not None
    assert spec.name == "github_copilot"


def test_litellm_provider_canonicalizes_github_copilot_hyphen_prefix():
    provider = LiteLLMProvider(default_model="github-copilot/gpt-5.3-codex")

    resolved = provider._resolve_model("github-copilot/gpt-5.3-codex")

    assert resolved == "github_copilot/gpt-5.3-codex"


def test_openai_codex_strip_prefix_supports_hyphen_and_underscore():
    assert _strip_model_prefix("openai-codex/gpt-5.1-codex") == "gpt-5.1-codex"
    assert _strip_model_prefix("openai_codex/gpt-5.1-codex") == "gpt-5.1-codex"


def test_config_falls_back_to_openrouter_gateway_when_only_openrouter_is_configured():
    config = Config.model_validate(
        {
            "providers": {
                "openrouter": {
                    "apiKey": "sk-or-test",
                }
            },
            "agents": {
                "defaults": {
                    "model": "anthropic/claude-opus-4-5",
                }
            },
        }
    )

    assert config.get_provider_name() == "openrouter"
    assert config.get_api_base() == "https://openrouter.ai/api/v1"


def test_litellm_provider_prefixes_model_for_openrouter_gateway():
    provider = LiteLLMProvider(
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-opus-4-5",
        provider_name="openrouter",
    )

    resolved = provider._resolve_model("anthropic/claude-opus-4-5")

    assert resolved == "openrouter/anthropic/claude-opus-4-5"


def test_litellm_provider_preserves_explicit_openrouter_prefix():
    provider = LiteLLMProvider(
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
        default_model="openrouter/anthropic/claude-opus-4-5",
        provider_name="openrouter",
    )

    resolved = provider._resolve_model("openrouter/anthropic/claude-opus-4-5")

    assert resolved == "openrouter/anthropic/claude-opus-4-5"


def test_build_job_models_includes_subagent_model():
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "model": "anthropic/claude-opus-4-5",
                    "jobModels": {
                        "subagent": "ollama/qwen3.5:4b",
                    },
                }
            }
        }
    )

    job_models = _build_job_models_from_config(config)

    assert job_models is not None
    assert job_models["subagent"] == "ollama/qwen3.5:4b"


def test_resolve_model_alias_returns_configured_model():
    aliases = {
        "coder": "ollama/qwen3.5:4b",
        "fast": "ollama/lfm2.5-thinking:latest",
    }

    assert resolve_model_alias("coder", aliases) == "ollama/qwen3.5:4b"
    assert resolve_model_alias("anthropic/claude-opus-4-5", aliases) == "anthropic/claude-opus-4-5"


def test_resolve_model_alias_config_returns_reasoning_override():
    aliases = {
        "fast": ModelAliasConfig(model="openai/lfm2.5-thinking:latest", thinking=False),
    }

    resolved = resolve_model_alias_config("fast", aliases)

    assert resolved.model == "openai/lfm2.5-thinking:latest"
    assert resolved.reasoning_effort == "none"


def test_channels_status_only_lists_supported_channels():
    with patch("hermitcrab.config.loader.load_config", return_value=Config()):
        result = runner.invoke(app, ["channels", "status"])

    assert result.exit_code == 0
    assert "Telegram" in result.stdout
    assert "Email" in result.stdout
    assert "Nostr" in result.stdout
    assert "WhatsApp" not in result.stdout
    assert "Discord" not in result.stdout
    assert "Feishu" not in result.stdout
    assert "Slack" not in result.stdout


def test_removed_channels_login_command_is_not_exposed():
    result = runner.invoke(app, ["channels", "login"])

    assert result.exit_code != 0


def test_agent_interactive_exit_closes_loop_resources(tmp_path):
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    class FakeCronService:
        def __init__(self, path):
            self.path = path

    class FakeTimeoutService:
        def __init__(self, *args, **kwargs):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    class FakeAgentLoop:
        instances: list["FakeAgentLoop"] = []

        def __init__(self, **kwargs):
            self.provider = kwargs["provider"]
            self.channels_config = None
            self._running = True
            self.closed = False
            FakeAgentLoop.instances.append(self)

        async def run(self):
            while self._running:
                await asyncio.sleep(0.01)

        async def process_direct(self, *args, **kwargs):
            return ""

        async def wait_for_background_tasks(self, timeout_s: float = 5.0):
            return 0, 0

        async def process_expired_sessions(self):
            return None

        def stop(self):
            self._running = False

        async def close(self):
            self.closed = True

    async def fake_read_interactive_input_async():
        return "/exit"

    with (
        patch("hermitcrab.config.loader.load_config", return_value=config),
        patch("hermitcrab.config.loader.get_data_dir", return_value=tmp_path),
        patch("hermitcrab.cli.commands._make_provider", return_value=SimpleNamespace()),
        patch("hermitcrab.cron.service.CronService", FakeCronService),
        patch("hermitcrab.session.timeout_service.SessionTimeoutService", FakeTimeoutService),
        patch("hermitcrab.agent.loop.AgentLoop", FakeAgentLoop),
        patch("hermitcrab.cli.commands._init_prompt_session"),
        patch("hermitcrab.cli.commands._flush_pending_tty_input"),
        patch("hermitcrab.cli.commands._restore_terminal"),
        patch("hermitcrab.cli.commands._read_interactive_input_async", fake_read_interactive_input_async),
        patch("hermitcrab.cli.commands.signal.signal"),
    ):
        result = runner.invoke(app, ["agent"])

    assert result.exit_code == 0
    assert "Finalizing session before exit" in result.stdout
    assert "\nGoodbye!" in result.stdout
    assert FakeAgentLoop.instances
    assert FakeAgentLoop.instances[0].closed is True


def test_build_job_models_preserves_named_model_references():
    config = Config.model_validate(
        {
            "models": {
                "main": {"model": "openai/gpt-4.1"},
                "local_coder": {
                    "model": "ollama/qwen2.5-coder:7b",
                    "providerOptions": {"num_ctx": 16384},
                },
            },
            "agents": {
                "defaults": {
                    "model": "main",
                    "jobModels": {"subagent": "local_coder"},
                }
            },
        }
    )

    job_models = _build_job_models_from_config(config)

    assert job_models is not None
    assert job_models["subagent"] == "local_coder"


def test_runtime_model_aliases_resolve_named_model_targets():
    config = Config.model_validate(
        {
            "models": {
                "fast_local": {
                    "model": "ollama/llama3.2:3b",
                    "reasoningEffort": "none",
                }
            },
            "agents": {
                "modelAliases": {
                    "fast": {"model": "fast_local"},
                    "direct": "fast_local",
                }
            },
        }
    )

    aliases = _build_runtime_model_aliases(config)

    assert isinstance(aliases["fast"], ModelAliasConfig)
    assert aliases["fast"].model == "fast_local"
    assert aliases["fast"].effective_reasoning_effort() == "none"
    assert aliases["direct"] == "fast_local"
