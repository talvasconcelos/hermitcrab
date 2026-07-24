"""Session export commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from hermitcrab.cli.config_helpers import load_runtime_config
from hermitcrab.session.manager import create_session_manager

session_app = typer.Typer(help="Export stored conversation sessions")
console = Console()


@session_app.command("export")
def export_session(
    session_key: str = typer.Argument(..., help="Session key, for example cli:direct"),
    output: Path = typer.Argument(..., help="Destination .md or .jsonl file"),
    format: str = typer.Option("", "--format", "-f", help="markdown or jsonl; inferred from extension"),
) -> None:
    """Export one owner-identity session as Markdown or JSONL."""
    selected_format = format.strip().lower()
    if not selected_format:
        selected_format = "jsonl" if output.suffix.lower() == ".jsonl" else "markdown"
    if selected_format not in {"markdown", "jsonl"}:
        raise typer.BadParameter("format must be markdown or jsonl")

    manager = create_session_manager(load_runtime_config().owner_identity_root_path)
    try:
        content = manager.export_session(session_key, format=selected_format)
    except KeyError as exc:
        console.print(f"[red]Error: {exc.args[0]}[/red]")
        raise typer.Exit(1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    console.print(f"Exported session [bold]{session_key}[/bold] to {output}")
