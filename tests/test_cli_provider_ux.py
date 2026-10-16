"""The CLI provider/model UX must follow the provider registry, not its own allowlist.

Regression: ``hermitcrab/cli/config_helpers.py`` hardcoded
``{openrouter, ollama, custom}`` in two places while the registry shipped ~20
providers, so ``model add --provider deepseek`` failed with
"unsupported provider for setup/model UX" even though the spec and the config
field both existed.
"""

import pytest
import typer

from hermitcrab.cli.config_helpers import (
    configure_provider,
    provider_options,
    resolve_provider_spec,
)
from hermitcrab.config.schema import Config
from hermitcrab.providers.registry import PROVIDERS


def _config(tmp_path) -> Config:
    return Config.model_validate({"root": str(tmp_path)})


def test_every_registry_provider_is_accepted_by_the_model_ux() -> None:
    for spec in PROVIDERS:
        assert resolve_provider_spec(spec.name).name == spec.name
        assert provider_options(spec.name) == {"provider": spec.name}


def test_provider_ux_accepts_registry_keywords() -> None:
    assert resolve_provider_spec("deepseek").name == "deepseek"
    assert resolve_provider_spec("openai-codex").name == "openai_codex"
    assert resolve_provider_spec("codex").name == "openai_codex"
    assert resolve_provider_spec("openaiOauth").name == "openai_codex"
    assert resolve_provider_spec("claude").name == "anthropic"


def test_unknown_provider_fails_loudly() -> None:
    with pytest.raises(typer.Exit):
        resolve_provider_spec("not-a-provider")


def test_api_key_provider_stores_the_key(tmp_path) -> None:
    config = _config(tmp_path)
    configure_provider(config, "deepseek", api_key="sk-test")
    assert config.providers.deepseek.api_key == "sk-test"


def test_local_provider_gets_the_endpoint_from_its_spec(tmp_path) -> None:
    config = _config(tmp_path)
    configure_provider(config, "ollama")
    assert config.providers.ollama.api_base == "http://localhost:11434"


def test_oauth_provider_does_not_pretend_to_take_an_api_key(tmp_path) -> None:
    config = _config(tmp_path)
    configure_provider(config, "openai-codex", api_key="sk-should-be-ignored")
    assert config.providers.openai_codex.api_key == ""


def test_standard_provider_does_not_get_a_gateway_base_url(tmp_path) -> None:
    config = _config(tmp_path)
    configure_provider(config, "deepseek", api_key="sk-test")
    assert config.providers.deepseek.api_base is None
