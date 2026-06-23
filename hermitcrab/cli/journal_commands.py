"""Journal CLI commands."""

from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.markdown import Markdown

from hermitcrab.agent.journal import JournalStore
from hermitcrab.cli.config_helpers import load_runtime_config

journal_app = typer.Typer(help="Manage journal entries")
console = Console()


def _journal_store() -> JournalStore:
    config = load_runtime_config()
    return JournalStore(config.workspace_path)


def _parse_entry_date(date: str):
    if not date:
        return None
    try:
        return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        console.print("[red]Invalid date format. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)


@journal_app.command("write")
def journal_write(
    content: str = typer.Option(
        "", "--content", "-c", help="Journal content (optional, prompts if not provided)"
    ),
    date: str = typer.Option(
        "", "--date", "-d", help="Date in YYYY-MM-DD format (defaults to today)"
    ),
    tag: list[str] = typer.Option([], "--tag", "-t", help="Tags (can be specified multiple times)"),
):
    """Write a journal entry."""
    journal = _journal_store()
    entry_date = _parse_entry_date(date)

    if not content:
        console.print("[cyan]Enter journal content (end with empty line):[/cyan]")
        lines = []
        while True:
            try:
                line = input()
                if not line.strip():
                    break
                lines.append(line)
            except EOFError:
                break
        content = "\n".join(lines)

    if not content.strip():
        console.print("[red]Journal content cannot be empty.[/red]")
        raise typer.Exit(1)

    try:
        file_path = journal.write_entry(
            content=content,
            tags=tag if tag else None,
            date=entry_date,
        )
        console.print(f"[green]✓ Journal entry written:[/green] {file_path}")
    except Exception as e:
        console.print(f"[red]Failed to write journal entry: {e}[/red]")
        raise typer.Exit(1)


@journal_app.command("read")
def journal_read(
    date: str = typer.Option(
        "", "--date", "-d", help="Date in YYYY-MM-DD format (defaults to today)"
    ),
    body_only: bool = typer.Option(
        False, "--body", "-b", help="Show body content only (no frontmatter)"
    ),
):
    """Read a journal entry."""
    journal = _journal_store()
    entry_date = _parse_entry_date(date)
    content = journal.read_entry_body(entry_date) if body_only else journal.read_entry(entry_date)

    if content is None:
        target_date = entry_date or datetime.now(timezone.utc)
        console.print(
            f"[yellow]No journal entry found for {target_date.strftime('%Y-%m-%d')}[/yellow]"
        )
        raise typer.Exit(0)

    console.print()
    console.print(Markdown(content))


@journal_app.command("list")
def journal_list(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of entries to show"),
):
    """List recent journal entries."""
    journal = _journal_store()
    entries = journal.list_entries(limit=limit)

    if not entries:
        console.print("[yellow]No journal entries found.[/yellow]")
        raise typer.Exit(0)

    console.print(f"\n[bold]Journal Entries[/bold] (showing {len(entries)} of {limit})\n")

    for entry_path in entries:
        date_str = entry_path.stem
        metadata = journal.get_entry_metadata(
            datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        )

        tags_str = ""
        if metadata and metadata.get("tags"):
            tags_str = f" [dim]({', '.join(metadata['tags'])})[/dim]"

        console.print(f"  [cyan]{date_str}[/cyan]{tags_str}")
        console.print(f"    [dim]{entry_path}[/dim]\n")
