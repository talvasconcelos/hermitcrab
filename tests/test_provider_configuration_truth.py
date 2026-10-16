"""One truth for "is this provider configured", shared by the resolver and the doctor.

Regression: the resolver's fallback accepted any ``is_local`` provider, so an unknown model
name silently resolved to ``vllm`` — whose ``default_api_base`` is ``""`` — while ``doctor``
reported that same provider as "not configured". ``model set-default`` then accepted a name
that did not exist, and ``model test`` printed a green check for provider ``vllm``.
"""

from hermitcrab.cli.diagnostics import _provider_ready
from hermitcrab.config.schema import Config
from hermitcrab.providers.registry import PROVIDERS


def _config(tmp_path, **providers) -> Config:
    return Config.model_validate({"root": str(tmp_path), "providers": providers})


def test_unknown_model_name_has_no_provider(tmp_path) -> None:
    assert _config(tmp_path).get_provider_name("main") is None


def test_local_provider_without_an_endpoint_is_not_configured(tmp_path) -> None:
    config = _config(tmp_path)
    assert config.provider_is_configured("vllm") is False
    assert config.get_provider_name("vllm/Llama-3-8B") is None


def test_local_provider_with_a_spec_default_is_configured(tmp_path) -> None:
    config = _config(tmp_path)
    assert config.provider_is_configured("ollama") is True
    assert config.get_provider_name("ollama/llama3.1") == "ollama"


def test_api_key_provider_needs_the_key(tmp_path) -> None:
    assert _config(tmp_path).provider_is_configured("deepseek") is False
    assert _config(tmp_path).get_provider_name("deepseek-flash") is None

    keyed = _config(tmp_path, deepseek={"apiKey": "sk-test"})
    assert keyed.provider_is_configured("deepseek") is True
    assert keyed.get_provider_name("deepseek-flash") == "deepseek"


def test_resolver_and_diagnostics_agree_on_every_provider(tmp_path) -> None:
    config = _config(tmp_path)
    for spec in PROVIDERS:
        if spec.is_oauth:
            # doctor checks live credentials; the resolver only needs explicit selection.
            continue
        provider_config = getattr(config.providers, spec.name)
        configured, _ = _provider_ready(spec.name, spec, provider_config)
        assert configured is config.provider_is_configured(spec.name), spec.name
