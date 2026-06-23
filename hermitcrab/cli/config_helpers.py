"""Shared CLI config helpers."""

from __future__ import annotations

import os

import typer
from rich.console import Console

from hermitcrab.config.schema import Config

console = Console()


def load_runtime_config() -> Config:
    """Load config strictly for runtime commands that should fail clearly."""
    from hermitcrab.config.loader import ConfigLoadError, load_config

    try:
        return load_config(strict=True)
    except ConfigLoadError as exc:
        console.print("[red]Error: Failed to load config.[/red]")
        console.print(f"Path: {exc.path}")
        console.print(f"Reason: {exc}")
        console.print("Fix the file or run [cyan]hermitcrab doctor[/cyan] for diagnostics.")
        raise typer.Exit(1) from exc


def save_runtime_config(config: Config) -> None:
    """Validate and save the runtime config after CLI mutation."""
    from hermitcrab.config.loader import save_config

    try:
        validated = Config.model_validate(config.model_dump(by_alias=True))
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    save_config(validated)


def configure_provider(config: Config, provider: str, *, api_key: str | None = None) -> None:
    """Apply a provider choice to config without forcing manual JSON edits."""
    provider_key = provider.strip().lower().replace("-", "_")
    if provider_key == "openrouter":
        config.providers.openrouter.api_key = api_key or config.providers.openrouter.api_key
    elif provider_key == "ollama":
        config.providers.ollama.api_base = config.providers.ollama.api_base or "http://localhost:11434"
    elif provider_key == "custom":
        config.providers.custom.api_key = api_key or config.providers.custom.api_key
    else:
        console.print(f"[red]Error: unsupported provider for setup/model UX: {provider}[/red]")
        raise typer.Exit(1)


def api_key_from_env(env_name: str | None) -> str | None:
    """Read an API key from an explicit environment variable name."""
    if not env_name:
        return None
    api_key = os.environ.get(env_name)
    if not api_key:
        console.print(f"[red]Error: environment variable is empty or missing: {env_name}[/red]")
        raise typer.Exit(1)
    return api_key


def provider_options(provider: str | None) -> dict[str, str]:
    """Persist the admin-selected provider with a named model."""
    if not provider:
        return {}
    provider_key = provider.strip().lower().replace("-", "_")
    if provider_key not in {"openrouter", "ollama", "custom"}:
        console.print(f"[red]Error: unsupported provider for setup/model UX: {provider}[/red]")
        raise typer.Exit(1)
    return {"provider": provider_key}
