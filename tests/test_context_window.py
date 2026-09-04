from rich.console import Console

from hermitcrab.cli.provider_factory import make_provider
from hermitcrab.config.schema import Config


def test_context_window_defaults_to_32k() -> None:
    assert Config().agents.defaults.context_window == 32768


def test_context_window_clamps_to_usable_minimum() -> None:
    config = Config.model_validate({"agents": {"defaults": {"contextWindow": 100}}})
    assert config.agents.defaults.context_window == 1024


def _make_ollama_provider(config: Config):
    return make_provider(config, Console())


def test_ollama_request_gets_default_num_ctx(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {"main": {"model": "qwen3.8-4b:latest", "providerOptions": {"provider": "ollama"}}},
            "providers": {"ollama": {"apiBase": "http://localhost:11434"}},
        }
    )
    provider = _make_ollama_provider(config)
    request_config = provider._get_request_config("main")
    assert request_config["provider_options"]["num_ctx"] == 32768


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
