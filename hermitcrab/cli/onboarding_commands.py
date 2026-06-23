"""Identity onboarding CLI commands."""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console

from hermitcrab.cli.config_helpers import load_runtime_config
from hermitcrab.config.schema import Config

onboarding_app = typer.Typer(help="Manage identity onboarding state")
console = Console()


def _onboarding_service_for_identity(config: Config, name: str):
    from hermitcrab.agent.onboarding import OnboardingProfileService

    if name not in config.identities.registry:
        console.print(f"[red]Error: unknown user: {name}[/red]")
        raise typer.Exit(1)
    root = config.get_identity_path(name)
    return OnboardingProfileService(root, chat_callable=lambda **_: None, model="")


def _onboarding_status_payload(config: Config, name: str) -> dict[str, Any]:
    service = _onboarding_service_for_identity(config, name)
    state = service.read_state()
    return {
        "identity": name,
        "root": str(service.workspace),
        "state_path": str(service.state_path),
        "status": state.get("status"),
        "enabled": service.is_enabled(),
        "observed_domains": state.get("observed_domains", []),
        "pending_assumptions": state.get("pending_assumptions", []),
    }


@onboarding_app.command("status")
def onboarding_status(
    name: str = typer.Argument(..., help="User alias"),
    as_json: bool = typer.Option(False, "--json", help="Print onboarding status as JSON"),
):
    """Inspect one identity's onboarding state."""
    config = load_runtime_config()
    payload = _onboarding_status_payload(config, name)
    if as_json:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", nl=False)
        return
    console.print(f"[bold]{name}[/bold]")
    console.print(f"Onboarding: {payload['status']} ({'enabled' if payload['enabled'] else 'disabled'})")
    console.print(f"State: {payload['state_path']}")
    console.print(f"Observed domains: {', '.join(payload['observed_domains']) or '-'}")
    console.print(f"Pending assumptions: {len(payload['pending_assumptions'])}")


@onboarding_app.command("pause")
def onboarding_pause(name: str = typer.Argument(..., help="User alias")):
    """Pause onboarding prompt injection for an identity."""
    config = load_runtime_config()
    service = _onboarding_service_for_identity(config, name)
    service.pause()
    console.print(f"Paused onboarding for [bold]{name}[/bold]")


@onboarding_app.command("resume")
def onboarding_resume(name: str = typer.Argument(..., help="User alias")):
    """Resume onboarding prompt injection for an identity."""
    config = load_runtime_config()
    service = _onboarding_service_for_identity(config, name)
    service.resume()
    console.print(f"Resumed onboarding for [bold]{name}[/bold]")


@onboarding_app.command("complete")
def onboarding_complete(name: str = typer.Argument(..., help="User alias")):
    """Mark onboarding complete for an identity."""
    config = load_runtime_config()
    service = _onboarding_service_for_identity(config, name)
    service.complete()
    console.print(f"Completed onboarding for [bold]{name}[/bold]")
