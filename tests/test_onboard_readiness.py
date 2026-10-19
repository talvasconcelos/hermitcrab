"""`onboard` must report the truth about readiness.

Regression: it printed "hermitcrab is ready!" on a fresh config with no provider
configured, so the first `agent` call failed after the CLI had already said the
install was done.
"""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from hermitcrab.cli import diagnostics
from hermitcrab.cli.commands import app

runner = CliRunner()


def test_onboard_refuses_to_claim_ready_without_a_configured_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 1
    assert "not ready yet" in result.output
    assert "hermitcrab is ready!" not in result.output


def test_onboard_reports_ready_when_a_provider_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    ready = SimpleNamespace(
        ready_for_chat=True,
        selected_provider="ollama",
        next_steps=[],
    )
    monkeypatch.setattr(diagnostics, "build_status_report", lambda *args, **kwargs: ready)

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "hermitcrab is ready!" in result.output
