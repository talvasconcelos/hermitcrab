"""Shared CLI config helpers."""

from __future__ import annotations

import os

import typer
from rich.console import Console

from hermitcrab.config.schema import Config
from hermitcrab.providers.registry import (
    PROVIDERS,
    ProviderSpec,
    find_by_name,
    normalize_provider_name,
)

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


def resolve_provider_spec(provider: str) -> ProviderSpec:
    """Resolve CLI provider input to a registry spec — the single source of truth.

    Accepts the canonical config name (``deepseek``, ``openai_codex``) and registry
    keywords (``codex``, ``openai-codex``, ``claude``), so the CLI never needs its own
    allowlist.
    """
    candidate = normalize_provider_name(provider)
    spec = find_by_name(candidate)
    if spec is not None:
        return spec

    lowered = str(provider or "").strip().lower()
    for candidate_spec in PROVIDERS:
        for keyword in candidate_spec.keywords:
            if lowered == keyword or normalize_provider_name(keyword) == candidate:
                return candidate_spec

    supported = ", ".join(candidate_spec.name for candidate_spec in PROVIDERS)
    console.print(f"[red]Error: unknown provider: {provider}[/red]")
    console.print(f"Supported providers: {supported}")
    raise typer.Exit(1)


def configure_provider(config: Config, provider: str, *, api_key: str | None = None) -> None:
    """Apply a provider choice to config without forcing manual JSON edits.

    The shape comes from the registry spec: OAuth providers authenticate through
    ``hermitcrab provider login``, local providers need an endpoint, the rest take an
    API key.
    """
    spec = resolve_provider_spec(provider)
    target = getattr(config.providers, spec.name)

    if spec.is_oauth:
        console.print(
            f"[yellow]{spec.label} authenticates with OAuth, not an API key.[/yellow]\n"
            f"Run [cyan]hermitcrab provider login {spec.name}[/cyan], then add the model."
        )
        return

    if spec.is_local:
        target.api_base = target.api_base or spec.default_api_base
        return

    target.api_key = api_key or target.api_key
    # Only gateway specs carry a usable default base; standard providers keep api_base
    # unset so they never pollute the global LiteLLM config.
    if spec.is_gateway and spec.default_api_base and not target.api_base:
        target.api_base = spec.default_api_base


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
    """Persist the admin-selected provider with a named model, canonically named."""
    if not provider:
        return {}
    return {"provider": resolve_provider_spec(provider).name}
