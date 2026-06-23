"""People profile CLI commands."""

from __future__ import annotations

import typer
from rich.console import Console

from hermitcrab.cli.config_helpers import load_runtime_config
from hermitcrab.cli.reminder_commands import build_reminder_store, require_one_schedule_option

people_app = typer.Typer(help="Manage people profiles")
console = Console()


def build_people_store():
    """Build the people profile store in the configured workspace."""
    from hermitcrab.agent.people import PeopleStore

    config = load_runtime_config()
    return PeopleStore(config.owner_identity_root_path)


@people_app.command("list")
def people_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include inactive profiles"),
):
    """List people profiles."""
    store = build_people_store()
    profiles = store.list_profiles(include_inactive=all)
    if not profiles:
        console.print("No people profiles found.")
        return
    reminders = build_reminder_store()
    for item in profiles:
        console.print(store.render_summary(item))
        _, state = store.build_relationship_state(item.name, reminders=reminders)
        if state and state.last_interaction_at:
            console.print(f"  last interaction: {state.last_interaction_at}")
        if state and state.follow_up_state:
            console.print(f"  {state.follow_up_state}")


@people_app.command("show")
def people_show(
    query: str = typer.Argument(..., help="People profile name or alias"),
):
    """Show one people profile."""
    store = build_people_store()
    reminders = build_reminder_store()
    item = store.get_profile(query)
    if item is None:
        console.print(f"[red]People profile not found: {query}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{item.name}[/bold]")
    console.print(f"Role: {item.role}")
    console.print(f"Status: {item.status}")
    console.print(f"Path: {item.file_path}")
    if item.is_primary:
        console.print("Primary: yes")
    if item.timezone:
        console.print(f"Timezone: {item.timezone}")
    if item.aliases:
        console.print(f"Aliases: {', '.join(item.aliases)}")
    if item.tags:
        console.print(f"Tags: {', '.join(item.tags)}")
    if item.notes:
        console.print()
        console.print(item.notes)
    _, state = store.build_relationship_state(item.name, reminders=reminders)
    if state and (state.last_interaction_at or state.follow_up_state):
        console.print()
        console.print("[bold]Relationship state[/bold]")
        if state.last_interaction_at:
            console.print(f"Last interaction: {state.last_interaction_at}")
        if state.follow_up_state:
            console.print(f"Follow-up state: {state.follow_up_state}")
    _, interactions = store.list_interactions(item.name, limit=5)
    if interactions:
        console.print()
        console.print("[bold]Recent interactions[/bold]")
        for interaction in interactions:
            console.print(store.render_interaction_summary(interaction))
    related_reminders = reminders.list_related_reminders(item.name)
    if related_reminders:
        console.print()
        console.print("[bold]Follow-ups[/bold]")
        for reminder in related_reminders:
            console.print(reminders.render_summary(reminder))


@people_app.command("add")
def people_add(
    name: str = typer.Option(..., "--name", "-n", help="Profile name"),
    role: str = typer.Option(
        ...,
        "--role",
        "-r",
        help="owner|family|child|member|guest|contact|client|collaborator",
    ),
    primary: bool = typer.Option(False, "--primary", help="Mark as the primary person profile"),
    timezone: str = typer.Option("", "--tz", help="Optional IANA timezone"),
    alias: list[str] = typer.Option([], "--alias", help="Nickname or alternate name"),
    tag: list[str] = typer.Option([], "--tag", help="Optional organizing tag"),
    notes: str = typer.Option("", "--notes", help="Freeform profile notes"),
):
    """Add a people profile."""
    store = build_people_store()
    try:
        item = store.upsert_profile(
            name=name,
            role=role,
            timezone=timezone or None,
            make_primary=primary,
            aliases=alias,
            tags=tag,
            notes=notes or None,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓[/green] Saved people profile '{item.name}'")
    console.print(f"Path: {item.file_path}")


@people_app.command("update")
def people_update(
    query: str = typer.Argument(..., help="Existing profile name or alias"),
    name: str = typer.Option(..., "--name", "-n", help="Profile name"),
    role: str = typer.Option(
        ...,
        "--role",
        "-r",
        help="owner|family|child|member|guest|contact|client|collaborator",
    ),
    status: str = typer.Option("active", "--status", help="active|inactive"),
    primary: bool | None = typer.Option(
        None,
        "--primary/--no-primary",
        help="Set or clear the primary person flag",
    ),
    timezone: str = typer.Option("", "--tz", help="Optional IANA timezone"),
    alias: list[str] = typer.Option([], "--alias", help="Nickname or alternate name"),
    tag: list[str] = typer.Option([], "--tag", help="Optional organizing tag"),
    notes: str = typer.Option("", "--notes", help="Freeform profile notes"),
):
    """Update an existing people profile."""
    store = build_people_store()
    try:
        item = store.upsert_profile(
            name=name,
            role=role,
            status=status,
            timezone=timezone or None,
            make_primary=primary,
            aliases=alias,
            tags=tag,
            notes=notes or None,
            existing_query=query,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓[/green] Updated people profile '{item.name}'")
    console.print(f"Path: {item.file_path}")


@people_app.command("deactivate")
def people_deactivate(
    query: str = typer.Argument(..., help="Profile name or alias"),
):
    """Mark a people profile as inactive."""
    store = build_people_store()
    item = store.deactivate_profile(query)
    if item is None:
        console.print(f"[red]People profile not found: {query}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] Deactivated people profile '{item.name}'")
    console.print(f"Path: {item.file_path}")


@people_app.command("set-primary")
def people_set_primary(
    query: str = typer.Argument(..., help="Profile name or alias"),
):
    """Mark one profile as the workspace's primary person."""
    store = build_people_store()
    item = store.set_primary_profile(query)
    if item is None:
        console.print(f"[red]People profile not found: {query}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] Set primary person '{item.name}'")
    console.print(f"Path: {item.file_path}")


@people_app.command("follow-up")
def people_follow_up(
    query: str = typer.Argument(..., help="Person profile name or alias"),
    message: str = typer.Option(..., "--message", "-m", help="Follow-up reminder message"),
    title: str = typer.Option("", "--title", "-t", help="Optional follow-up reminder title"),
    every: int = typer.Option(None, "--every", "-e", help="Repeat every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression"),
    tz: str | None = typer.Option(None, "--tz", help="IANA timezone for cron schedules"),
    at: str = typer.Option(None, "--at", help="One-time ISO datetime"),
    event_at: str = typer.Option(None, "--event-at", help="Actual event ISO datetime"),
    remind_before: int = typer.Option(
        None, "--remind-before", help="Minutes before --event-at to trigger"
    ),
):
    """Create a reminder linked to a person profile."""
    people = build_people_store()
    person = people.get_profile(query)
    if person is None:
        console.print(f"[red]People profile not found: {query}[/red]")
        raise typer.Exit(1)
    schedule_kind = require_one_schedule_option(every, cron_expr, at or event_at)
    reminders = build_reminder_store()
    reminder_title = title or f"Follow up with {person.name}"
    try:
        item = reminders.upsert_reminder(
            title=reminder_title,
            message=message,
            schedule_kind=schedule_kind,
            at=at,
            event_at=event_at,
            remind_offset_minutes=remind_before,
            every_seconds=every,
            cron_expr=cron_expr,
            tz=tz,
            related_people=[person.name],
            channel="cli",
            chat_id="direct",
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓[/green] Saved follow-up '{item.title}' for {person.name}")
    console.print(f"Schedule: {reminders.render_schedule(item)}")
    console.print(f"Path: {item.file_path}")


@people_app.command("log")
def people_log_interaction(
    query: str = typer.Argument(..., help="Person profile name or alias"),
    summary: str = typer.Option(..., "--summary", "-s", help="Short interaction summary"),
    occurred_at: str = typer.Option("", "--at", help="When it happened, ideally ISO datetime"),
    channel: str = typer.Option("", "--channel", "-c", help="Interaction channel label"),
    tag: list[str] = typer.Option([], "--tag", help="Optional interaction tags"),
):
    """Log one interaction note for a person profile."""
    store = build_people_store()
    try:
        person, interaction = store.add_interaction(
            query=query,
            summary=summary,
            occurred_at=occurred_at or None,
            channel=channel or None,
            tags=tag,
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓[/green] Logged interaction for '{person.name}'")
    console.print(f"When: {interaction.occurred_at}")
    console.print(f"Path: {interaction.file_path}")


@people_app.command("history")
def people_history(
    query: str = typer.Argument(..., help="Person profile name or alias"),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum interactions to show"),
):
    """Show recent interaction history for a person profile."""
    store = build_people_store()
    person, interactions = store.list_interactions(query, limit=limit)
    if person is None:
        console.print(f"[red]People profile not found: {query}[/red]")
        raise typer.Exit(1)
    if not interactions:
        console.print(f"No interactions found for {person.name}.")
        return
    console.print(f"[bold]Interactions for {person.name}[/bold]")
    for interaction in interactions:
        console.print(store.render_interaction_summary(interaction))
