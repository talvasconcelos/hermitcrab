"""Reminder CLI commands."""

from __future__ import annotations

import typer
from rich.console import Console

from hermitcrab.cli.config_helpers import load_runtime_config

reminders_app = typer.Typer(help="Manage reminder artifacts")
console = Console()


def build_reminder_store():
    """Build the reminder store in the owner identity root."""
    from hermitcrab.agent.reminders import ReminderStore

    config = load_runtime_config()
    return ReminderStore(config.owner_identity_root_path)


def require_one_schedule_option(
    every: int | None,
    cron_expr: str | None,
    at: str | None,
) -> str:
    """Return schedule kind when exactly one schedule option was provided."""
    if sum(value is not None for value in (every, cron_expr, at)) != 1:
        console.print("[red]Error: specify exactly one of --every, --cron, or --at[/red]")
        raise typer.Exit(1)
    return "every" if every is not None else ("cron" if cron_expr else "at")


@reminders_app.command("list")
def reminders_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include cancelled reminders"),
):
    """List reminder artifacts."""
    store = build_reminder_store()
    reminders = store.list_reminders(include_completed=all)
    if not reminders:
        console.print("No reminders found.")
        return
    for item in reminders:
        console.print(store.render_summary(item))


@reminders_app.command("show")
def reminders_show(
    query: str = typer.Argument(..., help="Reminder title or search text"),
):
    """Show a reminder artifact."""
    store = build_reminder_store()
    item = store.get_reminder(query)
    if item is None:
        console.print(f"[red]Reminder not found: {query}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{item.title}[/bold]")
    console.print(f"Status: {item.status}")
    console.print(f"Schedule: {store.render_schedule(item)}")
    console.print(f"Path: {item.file_path}")
    console.print()
    console.print(item.message)


@reminders_app.command("add")
def reminders_add(
    title: str = typer.Option(..., "--title", "-t", help="Reminder title"),
    message: str = typer.Option(..., "--message", "-m", help="Reminder message"),
    every: int = typer.Option(None, "--every", "-e", help="Repeat every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression"),
    tz: str | None = typer.Option(None, "--tz", help="IANA timezone for cron schedules"),
    at: str = typer.Option(None, "--at", help="One-time ISO datetime"),
    event_at: str = typer.Option(None, "--event-at", help="Actual event ISO datetime"),
    remind_before: int = typer.Option(
        None, "--remind-before", help="Minutes before --event-at to trigger"
    ),
):
    """Add a reminder artifact."""
    schedule_kind = require_one_schedule_option(every, cron_expr, at or event_at)
    store = build_reminder_store()
    try:
        item = store.upsert_reminder(
            title=title,
            message=message,
            schedule_kind=schedule_kind,
            at=at,
            event_at=event_at,
            remind_offset_minutes=remind_before,
            every_seconds=every,
            cron_expr=cron_expr,
            tz=tz,
            channel="cli",
            chat_id="direct",
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Saved reminder '{item.title}'")
    console.print(f"Schedule: {store.render_schedule(item)}")
    console.print(f"Path: {item.file_path}")


@reminders_app.command("cancel")
def reminders_cancel(
    query: str = typer.Argument(..., help="Reminder title or search text"),
):
    """Cancel a reminder and remove its scheduled job."""
    store = build_reminder_store()
    item = store.cancel_reminder(query)
    if item is None:
        console.print(f"[red]Reminder not found: {query}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] Cancelled reminder '{item.title}'")
