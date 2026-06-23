"""Named model CLI commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from hermitcrab.cli.config_helpers import (
    api_key_from_env,
    configure_provider,
    load_runtime_config,
    provider_options,
    save_runtime_config,
)
from hermitcrab.config.schema import NamedModelConfig

model_app = typer.Typer(help="Manage named models and defaults")
console = Console()


@model_app.command("list")
def model_list(as_json: bool = typer.Option(False, "--json", help="Print models as JSON")):
    """List named models and the current default."""
    config = load_runtime_config()
    rows = [
        {
            "name": name,
            "model": model.model,
            "reasoning_effort": model.reasoning_effort,
            "default": name == config.agents.defaults.model,
        }
        for name, model in sorted(config.models.items())
    ]
    if config.agents.defaults.model not in config.models:
        rows.insert(
            0,
            {
                "name": "(default)",
                "model": config.agents.defaults.model,
                "reasoning_effort": None,
                "default": True,
            },
        )

    if as_json:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", nl=False)
        return

    table = Table(title="Models")
    table.add_column("Name", style="cyan")
    table.add_column("Model")
    table.add_column("Reasoning")
    table.add_column("Default")
    for row in rows:
        table.add_row(
            row["name"],
            row["model"],
            row["reasoning_effort"] or "-",
            "yes" if row["default"] else "",
        )
    console.print(table)


@model_app.command("add")
def model_add(
    name: str = typer.Argument(..., help="Friendly model name"),
    model_id: str = typer.Argument(..., help="Provider model id, e.g. openrouter/anthropic/..."),
    provider: str | None = typer.Option(None, "--provider", help="Provider to configure/ensure"),
    api_key_env: str | None = typer.Option(
        None, "--api-key-env", help="Read provider API key from this environment variable"
    ),
    reasoning_effort: str | None = typer.Option(
        None, "--reasoning-effort", help="none, low, medium, or high"
    ),
):
    """Add or update a named model."""
    config = load_runtime_config()
    options = provider_options(provider)
    if provider:
        configure_provider(config, provider, api_key=api_key_from_env(api_key_env))
    if reasoning_effort not in {None, "none", "low", "medium", "high"}:
        console.print("[red]Error: --reasoning-effort must be none, low, medium, or high[/red]")
        raise typer.Exit(1)
    config.models[name] = NamedModelConfig(
        model=model_id,
        reasoning_effort=reasoning_effort,
        provider_options=options,
    )
    save_runtime_config(config)
    console.print(f"[green]✓[/green] Saved model '{name}' -> {model_id}")


@model_app.command("set-default")
def model_set_default(name_or_model_id: str = typer.Argument(..., help="Named model or raw model id")):
    """Set the default model used by the owner/solo assistant."""
    config = load_runtime_config()
    config.agents.defaults.model = name_or_model_id
    save_runtime_config(config)
    console.print(f"[green]✓[/green] Default model set to '{name_or_model_id}'")


@model_app.command("test")
def model_test(name_or_model_id: str = typer.Argument(..., help="Named model or raw model id")):
    """Validate that a model resolves to a configured provider."""
    config = load_runtime_config()
    resolved = config.resolve_model_config(name_or_model_id)
    provider_name = config.get_provider_name(name_or_model_id)
    if provider_name is None:
        console.print(f"[red]Error: no configured provider found for {name_or_model_id}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] {name_or_model_id} resolves to {resolved.model}")
    console.print(f"Provider: [cyan]{provider_name}[/cyan]")
