"""Focused regressions for model CLI context-window handling."""

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


def test_model_add_context_window_stores_num_ctx(monkeypatch, tmp_path) -> None:
    config_path = _use_config(
        monkeypatch, tmp_path, Config.model_validate({"root": str(tmp_path)})
    )
    result = runner.invoke(
        app,
        ["model", "add", "local", "qwen3.8-4b:latest", "--provider", "ollama", "--context-window", "32768"],
    )
    assert result.exit_code == 0, result.output
    reloaded = load_config(config_path)
    assert reloaded.models["local"].provider_options["num_ctx"] == 32768
    assert reloaded.resolve_context_window("local") == 32768


def test_model_add_context_window_rejects_too_small(monkeypatch, tmp_path) -> None:
    _use_config(monkeypatch, tmp_path, Config.model_validate({"root": str(tmp_path)}))
    result = runner.invoke(
        app, ["model", "add", "local", "qwen3.8-4b:latest", "--context-window", "100"]
    )
    assert result.exit_code != 0
    assert "at least 1024" in result.output


def test_model_list_json_shows_context_window(monkeypatch, tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {
                "local": {
                    "model": "qwen3.8-4b:latest",
                    "providerOptions": {"provider": "ollama", "num_ctx": 4096},
                }
            },
        }
    )
    _use_config(monkeypatch, tmp_path, config)
    result = runner.invoke(app, ["model", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    local = next(row for row in payload if row["name"] == "local")
    assert local["num_ctx"] == 4096
