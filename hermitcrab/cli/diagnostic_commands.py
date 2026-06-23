"""Diagnostic CLI commands."""

import json
from typing import Any

import typer
from rich.console import Console

from hermitcrab import __logo__
from hermitcrab.cli.config_helpers import load_runtime_config
from hermitcrab.cli.diagnostics import (
    build_doctor_report,
    build_status_report,
    render_json_report,
)

console = Console()


def register_diagnostic_commands(app: typer.Typer) -> None:
    """Register top-level diagnostic commands."""
    app.command()(status)
    app.command()(doctor)
    app.command()(audit)


def status(
    as_json: bool = typer.Option(False, "--json", help="Print the status report as JSON"),
):
    """Show HermitCrab runtime and setup status."""
    report = build_status_report()
    if as_json:
        typer.echo(render_json_report(report), nl=False)
        return

    console.print(f"{__logo__} hermitcrab Status\n")
    if report.overall_state == "ready":
        console.print("[green]Ready[/green] HermitCrab looks ready for a useful local session.")
    elif report.overall_state == "warning":
        console.print("[yellow]Almost ready[/yellow] HermitCrab can run, but setup still has rough edges.")
    else:
        console.print("[red]Needs setup[/red] HermitCrab has blockers to fix before it is ready.")
    console.print()

    config_status = "[green]valid[/green]" if report.config_valid else "[red]invalid[/red]"
    if not report.config_exists:
        config_status = "[red]missing[/red]"
    console.print(f"Config: {report.config_path} {config_status}")
    if report.config_error:
        console.print(f"  [red]{report.config_error}[/red]")

    workspace_status = "[green]ready[/green]" if report.workspace_exists else "[red]missing[/red]"
    console.print(f"Workspace: {report.workspace} {workspace_status}")
    console.print(
        "Bootstrap: "
        + ("[green]ready[/green]" if report.bootstrap_ready else "[yellow]incomplete[/yellow]")
    )
    if report.nostr_identity_bindings:
        console.print(f"Nostr identity bindings: {report.nostr_identity_bindings}")
    console.print(
        "Identity routing: "
        + (
            "[green]active[/green] (Nostr bound senders route to identities)"
            if report.identity_routing_active
            else "[dim]owner fallback only[/dim]"
        )
    )
    console.print("[dim]Cron/heartbeat/reminders run per active identity; unresolved routes are denied[/dim]")

    console.print(f"Selected model: {report.selected_model}")
    if report.resolved_model and report.resolved_model != report.selected_model:
        console.print(f"Resolved model: {report.resolved_model}")
    console.print(f"Selected provider: {report.selected_provider or 'none'}")

    console.print("\n[bold]Providers[/bold]")
    for item in report.provider_statuses:
        marker = "[green]✓[/green]" if item.configured else "[dim]•[/dim]"
        selected = " [cyan](selected)[/cyan]" if item.selected else ""
        console.print(f"- {item.label}: {marker} {item.detail}{selected}")

    available_skills = sum(1 for skill in report.skill_statuses if skill.available)
    unavailable_skills = len(report.skill_statuses) - available_skills
    console.print("\n[bold]Skills[/bold]")
    console.print(
        f"- Available: {available_skills}  [dim](unavailable: {unavailable_skills})[/dim]"
    )
    for skill in [item for item in report.skill_statuses if not item.available][:3]:
        if skill.missing_requirements:
            console.print(f"- {skill.name}: [yellow]{skill.missing_requirements}[/yellow]")

    console.print("\n[bold]MCP[/bold]")
    console.print(
        f"- Configured servers: {report.mcp_servers_configured}"
        f"  [dim](valid: {report.mcp_servers_valid})[/dim]"
    )

    if report.audit is not None:
        console.print("\n[bold]Audit[/bold]")
        audit_state = "[green]present[/green]" if report.audit.exists else "[dim]not started[/dim]"
        console.print(f"- Log: {report.audit.path} {audit_state}")
        if report.audit.exists:
            console.print(f"- Events: {report.audit.event_count}")
            if report.audit.last_event:
                stamp = f" at {report.audit.last_timestamp}" if report.audit.last_timestamp else ""
                console.print(f"- Latest: {report.audit.last_event}{stamp}")
        for highlight in report.audit_highlights:
            console.print(f"- {highlight}")

    if report.next_steps:
        console.print("\n[bold]Try This Next[/bold]")
        console.print(f"- {report.next_steps[0]}")

    if len(report.next_steps) > 1:
        console.print("\n[bold]Next Steps[/bold]")
        for step in report.next_steps[1:]:
            console.print(f"- {step}")


def doctor(
    as_json: bool = typer.Option(False, "--json", help="Print the doctor report as JSON"),
):
    """Run first-run diagnostics and suggest concrete fixes."""
    report = build_doctor_report()
    if as_json:
        typer.echo(render_json_report(report), nl=False)
        return

    console.print(f"{__logo__} hermitcrab Doctor\n")
    urgent = [item for item in report.findings if item.severity == "error"]
    if urgent:
        console.print("[red]Start Here[/red]")
        for finding in urgent[:3]:
            console.print(f"- {finding.remediation}")
        console.print()
    elif report.status.next_steps:
        console.print("[green]Start Here[/green]")
        for step in report.status.next_steps[:3]:
            console.print(f"- {step}")
        console.print()

    if report.status.ready_for_chat and report.status.next_steps:
        console.print("[bold]Try This Next[/bold]")
        console.print(f"- {report.status.next_steps[0]}")
        console.print()

    for finding in report.findings:
        if finding.severity == "ok":
            marker = "[green]OK[/green]"
        elif finding.severity == "error":
            marker = "[red]ERROR[/red]"
        elif finding.severity == "warning":
            marker = "[yellow]WARN[/yellow]"
        else:
            marker = "[cyan]INFO[/cyan]"
        console.print(f"{marker} {finding.title}")
        console.print(f"  {finding.detail}")
        console.print(f"  Fix: {finding.remediation}\n")


def audit(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum audit entries to show"),
    event: str = typer.Option("", "--event", "-e", help="Show only entries for one event type"),
    as_json: bool = typer.Option(False, "--json", help="Print audit entries as JSON"),
):
    """Show recent durable audit trail events."""
    from hermitcrab.agent.audit import AuditTrail

    config = load_runtime_config()
    trail = AuditTrail(config.system_root_path)
    entries = trail.read_recent(limit)
    if event:
        entries = _filter_audit_entries(entries, event)

    if as_json:
        typer.echo(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", nl=False)
        return

    console.print(f"{__logo__} hermitcrab Audit\n")
    console.print(f"Log: {trail.path}")
    if not entries:
        console.print("No audit events recorded yet.")
        return

    for item in entries:
        event = str(item.get("event") or "unknown")
        timestamp = str(item.get("ts") or "unknown")
        console.print(f"[bold]{event}[/bold] [dim]{timestamp}[/dim]")
        for key, value in item.items():
            if key in {"event", "ts"}:
                continue
            console.print(f"- {key}: {value}")
        console.print()


def _filter_audit_entries(entries: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    """Filter audit entries by exact event name."""
    event_name = event.strip()
    if not event_name:
        return entries
    return [item for item in entries if str(item.get("event") or "") == event_name]
