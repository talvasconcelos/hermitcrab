"""Channel CLI commands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from hermitcrab.cli.config_helpers import load_runtime_config

channels_app = typer.Typer(help="Manage channels")
console = Console()


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    config = load_runtime_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    tg = config.channels.telegram
    tg_config = "token: configured" if tg.token else "[dim]not configured[/dim]"
    table.add_row("Telegram", "✓" if tg.enabled else "✗", tg_config)

    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row("Email", "✓" if em.enabled else "✗", em_config)

    nostr = config.channels.nostr
    nostr_config = (
        f"{nostr.protocol}, {len(nostr.relays)} relay(s)"
        if nostr.private_key
        else "[dim]not configured[/dim]"
    )
    table.add_row("Nostr", "✓" if nostr.enabled else "✗", nostr_config)

    console.print(table)
