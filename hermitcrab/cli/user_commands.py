"""Identity-scoped user CLI commands."""

from __future__ import annotations

import asyncio
import json
import os

import typer
from rich.console import Console
from rich.table import Table

from hermitcrab.cli.bootstrap import (
    create_identity_directories as _create_identity_directories,
)
from hermitcrab.cli.bootstrap import (
    create_template_files as _create_template_files,
)
from hermitcrab.cli.bootstrap import (
    ensure_root as _ensure_root,
)
from hermitcrab.cli.config_helpers import (
    load_runtime_config as _load_runtime_config,
)
from hermitcrab.cli.config_helpers import (
    save_runtime_config as _save_runtime_config,
)
from hermitcrab.cli.cron_helpers import build_cron_service as _build_cron_service
from hermitcrab.cli.identity_helpers import (
    bind_nostr_pubkey_to_identity as _bind_nostr_pubkey_to_identity,
)
from hermitcrab.cli.identity_helpers import (
    effective_identity_model as _effective_identity_model,
)
from hermitcrab.cli.identity_helpers import (
    identity_rows as _identity_rows,
)
from hermitcrab.cli.identity_helpers import (
    remove_identity_routes as _remove_identity_routes,
)
from hermitcrab.cli.identity_helpers import (
    send_nostr_onboarding_intro as _send_nostr_onboarding_intro,
)
from hermitcrab.config.schema import (
    Config,
    IdentityConfig,
    generate_nostr_keypair,
    nostr_pubkey_from_private_key,
)

user_app = typer.Typer(help="Manage identity-scoped users")
console = Console()


@user_app.command("list")
def user_list(
    as_json: bool = typer.Option(False, "--json", help="Print users as JSON"),
):
    """List configured users."""
    config = _load_runtime_config()
    rows = _identity_rows(config)

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "name": name,
                        "label": identity.label,
                        "role": identity.role,
                        "active": identity.active,
                        "root": str(path),
                        "nostr_public_key": identity.nostr_public_key,
                        "route_count": len(config.channels.nostr.identity_bindings.get(name, [])),
                    }
                    for name, identity, path in rows
                ],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            nl=False,
        )
        return

    table = Table(title="Users")
    table.add_column("Alias", style="cyan")
    table.add_column("Label")
    table.add_column("Role")
    table.add_column("State")
    table.add_column("Nostr Pubkey")
    table.add_column("Root")

    for name, identity, path in rows:
        state = "[green]active[/green]" if identity.active else "[dim]inactive[/dim]"
        table.add_row(
            name,
            identity.label or "-",
            identity.role,
            state,
            f"{identity.nostr_public_key[:12]}...",
            str(path),
        )

    console.print(table)


@user_app.command("add")
def user_add(
    name: str = typer.Argument(..., help="User alias"),
    label: str | None = typer.Option(None, "--label", help="Display label"),
    nostr_public_key: str | None = typer.Option(
        None,
        "--nostr-public-key",
        help="Identity Nostr public key as npub or hex.",
    ),
    use_private_key: bool = typer.Option(
        False,
        "--nostr-private-key",
        help="Provide the identity nsec interactively via a hidden prompt (not persisted).",
    ),
    nostr_private_key_env: str | None = typer.Option(
        None,
        "--nostr-private-key-env",
        help="Read the identity nsec from this environment variable (not persisted).",
    ),
):
    """Add a user identity and bootstrap its identity root."""
    config = _load_runtime_config()
    if name in config.identities.registry:
        console.print(f"[red]Error: user already exists: {name}[/red]")
        raise typer.Exit(1)

    if nostr_public_key and (use_private_key or nostr_private_key_env):
        console.print(
            "[red]Error: provide either --nostr-public-key or a private key source, not both[/red]"
        )
        raise typer.Exit(1)

    # Resolve the nsec without ever placing it on argv. The nsec is only used to
    # derive the npub; the raw key is intentionally NOT persisted (the admin passes
    # the nsec to the user, and hermitcrab routes by npub alone).
    private_key_value: str | None = None
    if nostr_private_key_env:
        private_key_value = (os.environ.get(nostr_private_key_env) or "").strip()
        if not private_key_value:
            console.print(f"[red]Error: env var '{nostr_private_key_env}' is empty or unset[/red]")
            raise typer.Exit(1)
    elif use_private_key:
        private_key_value = typer.prompt(
            "Identity nsec (nsec/hex)", hide_input=True, confirmation_prompt=True
        )

    generated_private_key: str | None = None
    selected_pubkey = nostr_public_key or ""
    if private_key_value:
        try:
            selected_pubkey = nostr_pubkey_from_private_key(private_key_value)
        except ValueError as exc:
            console.print(f"[red]Error: {exc}[/red]")
            raise typer.Exit(1) from exc
    elif not selected_pubkey:
        generated_private_key, generated_pubkey = generate_nostr_keypair()
        selected_pubkey = generated_pubkey

    try:
        config.identities.registry[name] = IdentityConfig(
            label=label,
            nostr_public_key=selected_pubkey,
            nostr_private_key="",
        )
        identity = config.identities.registry[name]
        _bind_nostr_pubkey_to_identity(config, name, identity.nostr_public_key)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        validated = Config.model_validate(config.model_dump(by_alias=True))
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    identity_root = validated.get_identity_path(name)
    _ensure_root(identity_root, "identity root", announce=console.print)
    _create_template_files(
        identity_root,
        ["IDENTITY.md", "SOUL.md", "USER.md", "HEARTBEAT.md", "ONBOARDING_MODE.md"],
        announce=console.print,
    )
    _create_identity_directories(identity_root, announce=console.print)
    _save_runtime_config(validated)
    identity = validated.identities.registry[name]
    console.print(f"[green]✓[/green] Added user '{name}'")
    if nostr_public_key:
        console.print(f"Nostr pubkey: {identity.nostr_public_key}")
        console.print("User added with provided public key.")
    elif private_key_value:
        console.print(f"Nostr pubkey: {identity.nostr_public_key}")
        console.print("Private key accepted; only the derived npub was stored.")
    else:
        generated_private_nsec = ""
        try:
            from pynostr.key import PrivateKey

            if generated_private_key:
                generated_private_nsec = PrivateKey.from_hex(generated_private_key).bech32()
        except Exception:
            generated_private_nsec = ""
        console.print("Generated onboarding Nostr keypair:")
        console.print(f"  Public key (hex): {identity.nostr_public_key}")
        if generated_private_nsec:
            console.print(f"  Private key (nsec): {generated_private_nsec}")
        console.print(f"  Private key (hex): {generated_private_key}")
        console.print("[yellow]Warning:[/yellow] private key is NOT stored in config; share it securely if needed.")

    sent_intro = asyncio.run(_send_nostr_onboarding_intro(validated, identity.nostr_public_key, name))
    if sent_intro:
        console.print("Best-effort Nostr intro message: attempted.")
    else:
        console.print("Best-effort Nostr intro message: skipped/unavailable.")


@user_app.command("remove")
def user_remove(
    name: str = typer.Argument(..., help="User alias"),
):
    """Disable routing for a user while keeping data in place."""
    config = _load_runtime_config()
    identity = config.identities.registry.get(name)
    if identity is None:
        console.print(f"[red]Error: unknown user: {name}[/red]")
        raise typer.Exit(1)
    if name == config.owner_identity_name:
        console.print("[red]Error: owner user cannot be removed[/red]")
        raise typer.Exit(1)

    identity.active = False
    _remove_identity_routes(config, name)
    _save_runtime_config(config)
    console.print(f"[green]✓[/green] Disabled user '{name}' and removed inbound routes")


@user_app.command("archive")
def user_archive(
    name: str = typer.Argument(..., help="User alias"),
):
    """Archive a user without deleting identity data."""
    config = _load_runtime_config()
    identity = config.identities.registry.get(name)
    if identity is None:
        console.print(f"[red]Error: unknown user: {name}[/red]")
        raise typer.Exit(1)
    if name == config.owner_identity_name:
        console.print("[red]Error: owner user cannot be archived[/red]")
        raise typer.Exit(1)

    identity.active = False
    identity.role = "archived"
    _remove_identity_routes(config, name)
    _save_runtime_config(config)
    console.print(f"[green]✓[/green] Archived user '{name}'")


@user_app.command("route")
def user_route(
    channel: str = typer.Argument(..., help="Route channel. Currently only 'nostr'."),
    name: str = typer.Argument(..., help="User alias"),
    pubkey: str = typer.Argument(..., help="Inbound sender Nostr public key as npub or hex"),
):
    """Bind an inbound channel sender to a user."""
    if channel != "nostr":
        console.print("[red]Error: only nostr routes are supported[/red]")
        raise typer.Exit(1)

    config = _load_runtime_config()
    identity = config.identities.registry.get(name)
    if identity is None:
        console.print(f"[red]Error: unknown user: {name}[/red]")
        raise typer.Exit(1)
    if not identity.active:
        console.print(f"[red]Error: user is inactive: {name}[/red]")
        raise typer.Exit(1)

    try:
        normalized = _bind_nostr_pubkey_to_identity(config, name, pubkey)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    _save_runtime_config(config)
    console.print(f"[green]✓[/green] Routed Nostr sender to user '{name}'")
    console.print(f"Pubkey: {normalized}")


@user_app.command("models")
def user_models(
    name: str = typer.Argument(..., help="User alias"),
    interactive: str | None = typer.Option(None, "--interactive", help="Named model for interactive responses"),
    exclude: list[str] = typer.Option([], "--exclude", help="Model name to block for this user"),
    clear_interactive: bool = typer.Option(False, "--clear-interactive", help="Use the global default again"),
    as_json: bool = typer.Option(False, "--json", help="Print model policy as JSON"),
):
    """Show or set per-user model policy."""
    config = _load_runtime_config()
    identity = config.identities.registry.get(name)
    if identity is None:
        console.print(f"[red]Error: unknown user: {name}[/red]")
        raise typer.Exit(1)

    changed = False
    if interactive is not None:
        if interactive not in config.models:
            console.print(f"[red]Error: unknown named model: {interactive}[/red]")
            raise typer.Exit(1)
        if interactive in identity.excluded_models:
            console.print(f"[red]Error: model '{interactive}' is excluded for user '{name}'[/red]")
            raise typer.Exit(1)
        identity.models["interactiveResponse"] = interactive
        changed = True

    if clear_interactive:
        identity.models.pop("interactiveResponse", None)
        identity.models.pop("interactive_response", None)
        changed = True

    for model_name in exclude:
        if model_name not in config.models:
            console.print(f"[red]Error: unknown named model: {model_name}[/red]")
            raise typer.Exit(1)
        if model_name == _effective_identity_model(config, identity):
            console.print(f"[red]Error: cannot exclude effective model '{model_name}'[/red]")
            raise typer.Exit(1)
        if model_name not in identity.excluded_models:
            identity.excluded_models.append(model_name)
            changed = True

    if changed:
        _save_runtime_config(config)
        if not as_json:
            console.print(f"[green]✓[/green] Updated model policy for '{name}'")

    payload = {
        "name": name,
        "effective_interactive_model": _effective_identity_model(config, identity),
        "explicit_models": dict(identity.models),
        "excluded_models": list(identity.excluded_models),
        "available_models": [m for m in sorted(config.models) if m not in identity.excluded_models],
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", nl=False)
        return
    console.print(f"[bold]{name} model policy[/bold]")
    console.print(f"Effective interactive model: [cyan]{payload['effective_interactive_model']}[/cyan]")
    console.print(f"Excluded models: {', '.join(identity.excluded_models) or '-'}")
    console.print(f"Available named models: {', '.join(payload['available_models']) or '-'}")


@user_app.command("status")
def user_status(
    name: str = typer.Argument(..., help="User alias"),
    as_json: bool = typer.Option(False, "--json", help="Print user status as JSON"),
):
    """Inspect user heartbeat and cron state."""
    config = _load_runtime_config()
    identity = config.identities.registry.get(name)
    if identity is None:
        console.print(f"[red]Error: unknown user: {name}[/red]")
        raise typer.Exit(1)

    root = config.get_identity_path(name)
    heartbeat_file = root / "HEARTBEAT.md"
    cron_service = _build_cron_service(identity_root=root, identity_name=name)
    cron_jobs = cron_service.list_jobs(include_disabled=True)
    effective_model = _effective_identity_model(config, identity)
    nostr_routes = config.channels.nostr.identity_bindings.get(name, [])
    payload = {
        "name": name,
        "active": identity.active,
        "role": identity.role,
        "root": str(root),
        "nostr_public_key": identity.nostr_public_key,
        "models": {
            "effective_interactive": effective_model,
            "explicit": dict(identity.models),
            "excluded": list(identity.excluded_models),
        },
        "routes": {
            "nostr": {
                "enabled": config.channels.nostr.enabled,
                "bound_senders": len(nostr_routes),
            }
        },
        "heartbeat": {
            "path": str(heartbeat_file),
            "exists": heartbeat_file.exists(),
        },
        "cron": {
            "path": str(cron_service.store_path),
            "jobs": len(cron_jobs),
            "enabled_jobs": len([job for job in cron_jobs if job.enabled]),
            "next_wake_at_ms": cron_service.status()["next_wake_at_ms"],
        },
    }

    if as_json:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", nl=False)
        return

    console.print(f"[bold]{name}[/bold]")
    console.print(f"State: {'active' if identity.active else 'inactive'}")
    console.print(f"Role: {identity.role}")
    console.print(f"Root: {root}")
    console.print(f"Interactive model: {effective_model}")
    console.print(f"Excluded models: {', '.join(identity.excluded_models) or '-'}")
    console.print(f"Nostr pubkey: {identity.nostr_public_key}")
    console.print(
        f"Nostr routes: {len(nostr_routes)} sender(s), channel {'enabled' if config.channels.nostr.enabled else 'disabled'}"
    )
    console.print(f"Heartbeat: {'present' if heartbeat_file.exists() else 'missing'} ({heartbeat_file})")
    console.print(f"Cron jobs: {payload['cron']['enabled_jobs']} enabled / {payload['cron']['jobs']} total")


@user_app.command("resolve-nostr")
def user_resolve_nostr(
    pubkey: str = typer.Argument(..., help="Inbound Nostr sender pubkey (64-char hex)"),
    as_json: bool = typer.Option(False, "--json", help="Print resolution as JSON"),
):
    """Resolve inbound Nostr sender to an identity or denial."""
    config = _load_runtime_config()
    resolution = config.resolve_nostr_sender_identity(pubkey)

    payload = {
        "target": resolution.target,
        "identity_name": resolution.identity_name,
        "identity_path": (str(resolution.identity_path) if resolution.identity_path else None),
        "normalized_pubkey": resolution.normalized_pubkey,
        "reason": resolution.reason,
    }

    if as_json:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", nl=False)
        return

    console.print(f"Target: {resolution.target}")
    console.print(f"Reason: {resolution.reason or '-'}")
    if resolution.normalized_pubkey:
        console.print(f"Pubkey: {resolution.normalized_pubkey}")
    if resolution.identity_name:
        console.print(f"Identity: {resolution.identity_name}")
    if resolution.identity_path:
        console.print(f"Path: {resolution.identity_path}")
