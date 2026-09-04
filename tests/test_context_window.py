from rich.console import Console

from hermitcrab.cli.agent_loop_factory import build_agent_loop_kwargs
from hermitcrab.cli.provider_factory import make_provider
from hermitcrab.config.schema import Config


def test_context_window_defaults_to_auto() -> None:
    assert Config().agents.defaults.context_window is None


def test_explicit_context_window_wins_over_auto() -> None:
    config = Config.model_validate({"agents": {"defaults": {"contextWindow": 32768}}})
    assert config.resolve_context_window("qwen3.8-4b:latest") == 32768


def test_auto_context_window_derives_from_model_family_with_buffer() -> None:
    config = Config()
    # qwen -> 262144 context, 20% buffer -> 209715
    assert config.resolve_context_window("qwen3.8-4b:latest") == int(262144 * 0.8)
    # unknown model -> fallback 32768, 20% buffer -> 26214
    assert config.resolve_context_window("totally-unknown-model") == int(32768 * 0.8)


def test_auto_context_window_resolves_named_model() -> None:
    config = Config.model_validate(
        {"models": {"main": {"model": "gemma4:cloud"}}, "agents": {"defaults": {"model": "main"}}}
    )
    assert config.resolve_context_window() == int(262144 * 0.8)


def test_context_window_clamps_to_usable_minimum() -> None:
    config = Config.model_validate({"agents": {"defaults": {"contextWindow": 100}}})
    assert config.resolve_context_window() == 1024


def test_per_model_num_ctx_wins_in_resolution(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {
                "main": {
                    "model": "qwen3.8-4b:latest",
                    "providerOptions": {"provider": "ollama", "num_ctx": 4096},
                }
            },
            "agents": {"defaults": {"model": "main"}},
        }
    )
    assert config.resolve_context_window() == 4096


def _make_ollama_provider(config: Config):
    return make_provider(config, Console())


def test_ollama_request_gets_auto_num_ctx(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {"main": {"model": "qwen3.8-4b:latest", "providerOptions": {"provider": "ollama"}}},
            "providers": {"ollama": {"apiBase": "http://localhost:11434"}},
        }
    )
    provider = _make_ollama_provider(config)
    request_config = provider._get_request_config("main")
    assert request_config["provider_options"]["num_ctx"] == int(262144 * 0.8)


def test_ollama_request_per_model_num_ctx_overrides_default(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {
                "main": {
                    "model": "qwen3.8-4b:latest",
                    "providerOptions": {"provider": "ollama", "num_ctx": 4096},
                }
            },
            "providers": {"ollama": {"apiBase": "http://localhost:11434"}},
        }
    )
    provider = _make_ollama_provider(config)
    request_config = provider._get_request_config("main")
    assert request_config["provider_options"]["num_ctx"] == 4096


def test_prompt_budget_scales_with_explicit_context_window(tmp_path) -> None:
    config = Config.model_validate(
        {"root": str(tmp_path), "agents": {"defaults": {"contextWindow": 32768}}}
    )
    kwargs = build_agent_loop_kwargs(config, provider=None)
    assert kwargs["prompt_token_budget"] == 32768 - 6000 - 8192


def test_prompt_budget_scales_with_auto_resolution(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {"main": {"model": "qwen3.8-4b:latest"}},
            "agents": {"defaults": {"model": "main"}},
        }
    )
    kwargs = build_agent_loop_kwargs(config, provider=None)
    assert kwargs["prompt_token_budget"] == int(262144 * 0.8) - 6000 - 8192


def test_prompt_budget_has_a_floor(tmp_path) -> None:
    config = Config.model_validate(
        {"root": str(tmp_path), "agents": {"defaults": {"contextWindow": 4096}}}
    )
    kwargs = build_agent_loop_kwargs(config, provider=None)
    assert kwargs["prompt_token_budget"] == 6000
