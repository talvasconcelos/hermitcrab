"""`model set-default` must reject a name that does not exist.

Regression: a failed `model add` still let `model set-default <name>` succeed, so the config
pointed at a phantom default that every later command accepted (`model test` then printed a
green check for provider `vllm`, which was never configured).

Rule: a named model that exists is always allowed (its provider key may arrive from the
environment at run time), and a raw model id is allowed when it resolves to a configured
provider. Anything else — a typo, or the alias whose `model add` failed — is rejected.
"""

import pytest
import typer

from hermitcrab.cli.model_commands import model_set_default
from hermitcrab.config.schema import Config


def _config(tmp_path, **extra) -> Config:
    return Config.model_validate({"root": str(tmp_path), **extra})


def test_named_model_is_allowed_even_without_a_key(monkeypatch, tmp_path) -> None:
    """A provider key may arrive from the environment, so existence is the check."""
    config = _config(
        tmp_path,
        models={"cheap": {"model": "openrouter/nvidia/nemotron-3-super-120b-a12b:free"}},
    )
    _use_config(monkeypatch, tmp_path, config)
    model_set_default("cheap")
    assert config.agents.defaults.model == "cheap"


def test_raw_model_id_is_allowed_when_a_provider_is_configured(monkeypatch, tmp_path) -> None:
    config = _config(
        tmp_path,
        providers={"deepseek": {"apiKey": "sk-test"}},
    )
    _use_config(monkeypatch, tmp_path, config)
    model_set_default("deepseek-flash")
    assert config.agents.defaults.model == "deepseek-flash"


def test_phantom_name_is_rejected(monkeypatch, tmp_path) -> None:
    config = _config(tmp_path)
    _use_config(monkeypatch, tmp_path, config)
    with pytest.raises(typer.Exit):
        model_set_default("main")
    assert config.agents.defaults.model == "anthropic/claude-opus-4-5"


def _use_config(monkeypatch, tmp_path, config: Config) -> None:
    """Point the CLI's config loader at this config file."""
    from hermitcrab.cli import model_commands

    config_file = tmp_path / "config.json"
    config_file.write_text(config.model_dump_json(by_alias=True))
    monkeypatch.setattr(model_commands, "load_runtime_config", lambda: config)
    monkeypatch.setattr(model_commands, "save_runtime_config", lambda _config: None)
