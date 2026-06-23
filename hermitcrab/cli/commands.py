"""CLI commands for hermitcrab."""

import asyncio
import json
import os
import signal
import sys
import time
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from hermitcrab import __logo__, __version__
from hermitcrab.cli import gateway_runtime
from hermitcrab.cli.agent_loop_factory import build_agent_loop_kwargs as _build_agent_loop_kwargs
from hermitcrab.cli.bootstrap import (
    bootstrap_standard_layout,
)
from hermitcrab.cli.bootstrap import (
    build_onboard_next_steps as _build_onboard_next_steps,
)
from hermitcrab.cli.channel_commands import channels_app
from hermitcrab.cli.config_helpers import (
    api_key_from_env as _api_key_from_env,
)
from hermitcrab.cli.config_helpers import (
    configure_provider as _configure_provider,
)
from hermitcrab.cli.config_helpers import (
    load_runtime_config as _load_runtime_config,
)
from hermitcrab.cli.config_helpers import (
    provider_options as _provider_options,
)
from hermitcrab.cli.cron_helpers import build_cron_service as _build_cron_service
from hermitcrab.cli.interactive import (
    consume_outbound_loop as _consume_outbound_loop,
)
from hermitcrab.cli.interactive import (
    print_agent_response as _print_agent_response,
)
from hermitcrab.cli.interactive import (
    restore_terminal as _restore_terminal,
)
from hermitcrab.cli.interactive import (
    run_interactive_mode,
)
from hermitcrab.cli.journal_commands import journal_app
from hermitcrab.cli.model_commands import model_app
from hermitcrab.cli.onboarding_commands import onboarding_app
from hermitcrab.cli.people_commands import people_app
from hermitcrab.cli.provider_factory import make_provider
from hermitcrab.cli.provider_login import provider_app
from hermitcrab.cli.reminder_commands import reminders_app
from hermitcrab.cli.user_commands import user_app
from hermitcrab.config.schema import (
    Config,
    NamedModelConfig,
)

GatewayIdentityRuntimeState = gateway_runtime.GatewayIdentityRuntimeState
_identity_routing_active = gateway_runtime.identity_routing_active
_resolve_gateway_identity_route = gateway_runtime.resolve_gateway_identity_route
_run_gateway_inbound_router = gateway_runtime.run_gateway_inbound_router
_shutdown_gateway_runtime = gateway_runtime.shutdown_gateway_runtime

app = typer.Typer(
    name="hermitcrab",
    help=f"{__logo__} hermitcrab - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()


def _print_gateway_runtime_summary(
    *,
    channels: Any,
    identity_routing_active: bool,
    cron_statuses: dict[str, dict[str, Any]],
    heartbeat_interval_s: int,
    reminders_interval_s: int,
) -> None:
    """Print concise gateway runtime status lines."""
    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    if identity_routing_active:
        console.print("[green]✓[/green] Identity routing: active (Nostr bindings)")
        console.print("[dim]Unresolved/invalid identity routes are denied[/dim]")
    else:
        console.print("[dim]Identity routing: owner fallback only[/dim]")

    cron_jobs = sum(status.get("jobs", 0) for status in cron_statuses.values())
    if cron_jobs > 0:
        console.print(f"[green]✓[/green] Cron: {cron_jobs} scheduled job(s)")

    console.print(f"[green]✓[/green] Heartbeat: every {heartbeat_interval_s}s")
    console.print(f"[green]✓[/green] Reminders: every {reminders_interval_s}s")
    console.print("[dim]Scheduler: gateway-owned, identity-scoped cron/heartbeat/reminders[/dim]")


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} hermitcrab v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
):
    """hermitcrab - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize hermitcrab configuration and workspace."""
    from hermitcrab.config.loader import get_config_path, load_config, save_config
    from hermitcrab.config.schema import Config

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print(
            "  [bold]N[/bold] = refresh config, keeping existing values and adding new fields"
        )
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(
                f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)"
            )
    else:
        config = Config()
        save_config(config)
        console.print(f"[green]✓[/green] Created config at {config_path}")

    bootstrap_standard_layout(config, announce=console.print)

    console.print(f"\n{__logo__} hermitcrab is ready!")
    for line in _build_onboard_next_steps():
        console.print(line)


@app.command()
def setup(
    yes: bool = typer.Option(False, "--yes", "-y", help="Run non-interactively with safe defaults"),
    provider: str | None = typer.Option(None, "--provider", help="Provider to configure, e.g. openrouter or ollama"),
    model: str | None = typer.Option(None, "--model", help="Default model id or existing named model"),
    model_name: str = typer.Option("main", "--model-name", help="Name to save the default model under"),
    api_key_env: str | None = typer.Option(
        None, "--api-key-env", help="Read provider API key from this environment variable"
    ),
    owner_label: str | None = typer.Option(None, "--owner-label", help="Display label for the owner identity"),
):
    """Guided admin setup for config, owner identity, and default model."""
    from hermitcrab.config.loader import get_config_path, load_config, save_config

    config_path = get_config_path()
    config = load_config() if config_path.exists() else Config(root=str(config_path.parent))

    if not yes:
        console.print(f"{__logo__} hermitcrab setup\n")
        console.print("This configures the admin CLI and the owner identity.")
        console.print(
            "Other users can be added later and should normally talk through channels like Nostr DMs.\n"
        )

        if provider is None:
            provider = typer.prompt(
                "Provider (ollama/openrouter/custom, blank to keep current)", default=""
            ) or None
        if model is None:
            model = typer.prompt("Default model id or named model (blank to keep current)", default="") or None
        if owner_label is None:
            owner_label = typer.prompt("Owner display label (blank to keep current)", default="") or None

    if provider:
        _configure_provider(config, provider, api_key=_api_key_from_env(api_key_env))

    if model:
        if model in config.models:
            config.agents.defaults.model = model
        else:
            provider_options = _provider_options(provider)
            config.models[model_name] = NamedModelConfig(model=model, provider_options=provider_options)
            config.agents.defaults.model = model_name

    owner = config.identities.registry[config.owner_identity_name]
    if owner_label:
        owner.label = owner_label

    validated = Config.model_validate(config.model_dump(by_alias=True))
    save_config(validated)
    bootstrap_standard_layout(validated, announce=console.print)

    console.print(f"[green]✓[/green] Setup saved at {config_path}")
    console.print(f"Owner identity: [cyan]{validated.owner_identity_name}[/cyan]")
    console.print(f"Default model: [cyan]{validated.agents.defaults.model}[/cyan]")
    console.print("\nNext steps:")
    console.print("  1. Run [cyan]hermitcrab doctor[/cyan]")
    console.print('  2. Try [cyan]hermitcrab agent -m "Hello"[/cyan]')
    console.print("  3. Add users when needed: [cyan]hermitcrab user add alice --label Alice[/cyan]")


def _make_provider(config: Config):
    return make_provider(config, console)


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Log level: TRACE, DEBUG, INFO, WARNING, ERROR",
    ),
):
    """Start the hermitcrab gateway."""
    from loguru import logger

    from hermitcrab.agent.loop import AgentLoop
    from hermitcrab.bus.queue import MessageBus
    from hermitcrab.channels.manager import ChannelManager
    from hermitcrab.heartbeat.service import HeartbeatService
    from hermitcrab.reminders.service import ReminderService
    from hermitcrab.session.manager import create_session_manager
    from hermitcrab.session.timeout_service import SessionTimeoutService

    configured_level = "DEBUG" if verbose else log_level.upper()
    logger.remove()
    logger.add(sys.stderr, level=configured_level)

    console.print(f"{__logo__} Starting hermitcrab gateway on port {port}...")
    console.print(f"[dim]Log level: {configured_level}[/dim]")

    config = _load_runtime_config()
    if config.workspace_path != config.admin_workspace_path:
        raise RuntimeError("admin workspace invariant failed: workspace_path must equal admin_workspace_path")
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = create_session_manager(config.owner_identity_root_path)
    identity_routing_active = _identity_routing_active(config)

    owner_key = f"identity:{config.identities.registry[config.owner_identity_name].nostr_public_key}"
    admin_cron = _build_cron_service(
        identity_root=config.owner_identity_root_path,
        identity_name=config.owner_identity_name,
    )

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        **_build_agent_loop_kwargs(
            config,
            provider,
            workspace=config.owner_identity_root_path,
            identity_name=config.owner_identity_name,
            identity_root=config.owner_identity_root_path,
            cron_service=admin_cron,
            session_manager=session_manager,
        ),
    )

    # Create channel manager
    channels = ChannelManager(config, bus, audit_event=agent.audit_event)

    async def on_reminder_notify(item, content: str) -> None:
        """Deliver a due reminder to its persisted channel target."""
        from hermitcrab.bus.events import OutboundMessage

        if item.channel == "cli":
            return

        await bus.publish_outbound(
            OutboundMessage(channel=item.channel, chat_id=item.chat_id, content=content)
        )

    hb_cfg = config.gateway.heartbeat
    identity_state = GatewayIdentityRuntimeState(
        config=config,
        bus=bus,
        channels=channels,
        create_provider=lambda: _make_provider(config),
        cron_service_factory=_build_cron_service,
        heartbeat_service_factory=HeartbeatService,
        on_reminder_notify=on_reminder_notify,
        heartbeat_interval_s=hb_cfg.interval_s,
        heartbeat_enabled=hb_cfg.enabled,
        reminder_interval_s=config.gateway.reminders.interval_s,
        reminder_service_factory=ReminderService,
        agents={},
        cron_services={},
        heartbeat_services={},
        reminder_services={},
    )
    admin_cron.conflict_finder = lambda schedule, now_ms: identity_state.find_cron_conflicts(
        schedule,
        now_ms=now_ms,
        exclude_key=owner_key,
    )
    asyncio.run(identity_state.attach_agent_services(owner_key, agent, admin_cron))
    asyncio.run(identity_state.attach_configured_identity_agents())

    timeout_monitor = SessionTimeoutService(
        identity_state.process_expired_sessions_all,
        interval_s=min(60, max(5, config.agents.defaults.inactivity_timeout_s // 6)),
        enabled=True,
    )

    cron_statuses = {key: service.status() for key, service in identity_state.cron_services.items()}
    _print_gateway_runtime_summary(
        channels=channels,
        identity_routing_active=identity_routing_active,
        cron_statuses=cron_statuses,
        heartbeat_interval_s=hb_cfg.interval_s,
        reminders_interval_s=config.gateway.reminders.interval_s,
    )

    async def run():
        try:
            await identity_state.start_cron_services()
            await identity_state.start_heartbeat_services()
            await timeout_monitor.start()
            await identity_state.start_reminder_services()
            await asyncio.gather(
                _run_gateway_inbound_router(
                    bus=bus,
                    owner_agent=agent,
                    get_or_create_agent=identity_state.get_or_create_agent,
                    identity_agent_key=identity_state.identity_agent_key,
                ),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await _shutdown_gateway_runtime(
                timeout_monitor=timeout_monitor,
                identity_state=identity_state,
                channels=channels,
            )

    asyncio.run(run())


# ============================================================================
# Nostr Listen Mode
# ============================================================================


def _run_nostr_mode(
    agent_loop: Any,
    bus: Any,
    nostr_pubkey: str,
    markdown: bool,
    thinking_ctx: Any,
    timeout_monitor: Any,
) -> None:
    """
    Run agent in Nostr listen mode.

    Listens for encrypted DMs from the specified pubkey and responds via Nostr.

    Args:
        agent_loop: AgentLoop instance for processing messages.
        bus: MessageBus for communication.
        nostr_pubkey: Nostr pubkey (npub or hex) to listen for.
        markdown: Whether to render responses as Markdown.
        thinking_ctx: Context manager for "thinking" spinner.
        timeout_monitor: Session timeout monitor service.
    """

    # Normalize pubkey to hex
    try:
        if nostr_pubkey.startswith("npub"):
            from pynostr.key import PublicKey

            hex_pubkey = PublicKey.from_npub(nostr_pubkey).hex()
        else:
            hex_pubkey = nostr_pubkey
    except Exception as e:
        console.print(f"[red]Invalid Nostr pubkey format: {e}[/red]")
        console.print("Use npub... or hex format")
        raise typer.Exit(1)

    session_key = f"nostr:{hex_pubkey}"

    console.print(f"{__logo__} Nostr listen mode")
    console.print(f"Listening for DMs from: [cyan]{nostr_pubkey[:10]}...[/cyan]")
    console.print(f"Session key: [dim]{session_key}[/dim]")
    console.print("Press Ctrl+C to quit\n")

    def _exit_on_sigint(signum, frame):
        _restore_terminal()
        console.print("\nGoodbye!")
        os._exit(0)

    signal.signal(signal.SIGINT, _exit_on_sigint)

    async def run_nostr_listen():
        await timeout_monitor.start()
        bus_task = asyncio.create_task(agent_loop.run())
        turn_done = asyncio.Event()
        turn_done.set()
        turn_response: list[tuple[str, str | None]] = []

        outbound_task = asyncio.create_task(
            _consume_outbound_loop(
                bus,
                agent_loop,
                turn_done,
                turn_response,
                render_markdown=markdown,
                console=console,
            )
        )

        try:
            while True:
                try:
                    # Wait for inbound message via bus
                    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

                    if msg.session_key != session_key:
                        continue

                    turn_done.clear()
                    turn_response.clear()

                    console.print(
                        f"\n[cyan]Received message from Nostr:[/cyan] {msg.content[:50]}..."
                    )

                    with thinking_ctx():
                        await turn_done.wait()

                    if turn_response:
                        content, model_label = turn_response[0]
                        _print_agent_response(
                            content,
                            render_markdown=markdown,
                            console=console,
                            model_label=model_label,
                        )

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
        finally:
            timeout_monitor.stop()
            agent_loop.stop()
            outbound_task.cancel()
            await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
            await agent_loop.close()

    asyncio.run(run_nostr_listen())


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
    ),
    logs: bool = typer.Option(
        False, "--logs/--no-logs", help="Show hermitcrab runtime logs during chat"
    ),
    nostr_pubkey: str | None = typer.Option(
        None,
        "--nostr-pubkey",
        help="Nostr pubkey (npub or hex) to listen for DMs. If provided, starts Nostr listen loop instead of console input.",
    ),
):
    """
    Interact with the agent directly.

    Use --nostr-pubkey to listen for Nostr DMs, or run without flags for interactive CLI mode.
    """
    from loguru import logger

    from hermitcrab.agent.loop import AgentLoop
    from hermitcrab.bus.queue import MessageBus
    from hermitcrab.session.timeout_service import SessionTimeoutService

    config = _load_runtime_config()

    bus = MessageBus()
    provider = _make_provider(config)

    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron = _build_cron_service()

    if logs:
        logger.enable("hermitcrab")
    else:
        logger.disable("hermitcrab")

    agent_loop = AgentLoop(
        bus=bus,
        **_build_agent_loop_kwargs(config, provider, cron_service=cron),
    )
    timeout_monitor = SessionTimeoutService(
        agent_loop.process_expired_sessions,
        interval_s=min(60, max(5, config.agents.defaults.inactivity_timeout_s // 6)),
        enabled=bool(nostr_pubkey or not message),
    )

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext

            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]hermitcrab is thinking...[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        if not content or not content.strip():
            return
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(
                    message, session_id, on_progress=_cli_progress
                )
            _print_agent_response(response, render_markdown=markdown, console=console)
            await agent_loop.close()

        asyncio.run(run_once())
    elif nostr_pubkey:
        # Nostr DM listen mode
        _run_nostr_mode(
            agent_loop=agent_loop,
            bus=bus,
            nostr_pubkey=nostr_pubkey,
            markdown=markdown,
            thinking_ctx=_thinking_ctx,
            timeout_monitor=timeout_monitor,
        )
    else:
        # Interactive mode — route through bus like other channels
        run_interactive_mode(
            bus=bus,
            agent_loop=agent_loop,
            timeout_monitor=timeout_monitor,
            session_id=session_id,
            markdown=markdown,
            thinking_ctx=_thinking_ctx,
            console=console,
        )


app.add_typer(channels_app, name="channels")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")

app.add_typer(reminders_app, name="reminders")

app.add_typer(people_app, name="people")

app.add_typer(user_app, name="user")

app.add_typer(onboarding_app, name="onboarding")

app.add_typer(model_app, name="model")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    service = _build_cron_service()

    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("No scheduled jobs.")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")

    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = (
                f"{job.schedule.expr or ''} ({job.schedule.tz})"
                if job.schedule.tz
                else (job.schedule.expr or "")
            )
        else:
            sched = "one-time"

        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            ts = job.state.next_run_at_ms / 1000
            try:
                tz = ZoneInfo(job.schedule.tz) if job.schedule.tz else None
                next_run = _dt.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")
            except Exception:
                next_run = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

        table.add_row(job.id, job.name, sched, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    tz: str | None = typer.Option(
        None, "--tz", help="IANA timezone for cron (e.g. 'America/Vancouver')"
    ),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(
        None, "--channel", help="Channel for delivery (e.g. 'telegram', 'email', 'nostr')"
    ),
):
    """Add a scheduled job."""
    from hermitcrab.cron.types import CronSchedule

    if tz and not cron_expr:
        console.print("[red]Error: --tz can only be used with --cron[/red]")
        raise typer.Exit(1)

    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
    elif at:
        import datetime

        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)

    service = _build_cron_service()

    try:
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            to=to,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    service = _build_cron_service()

    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    service = _build_cron_service()

    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from loguru import logger

    from hermitcrab.agent.loop import AgentLoop
    from hermitcrab.bus.queue import MessageBus
    from hermitcrab.cron.types import CronJob

    logger.disable("hermitcrab")

    config = _load_runtime_config()
    provider = _make_provider(config)
    bus = MessageBus()

    agent_loop = AgentLoop(
        bus=bus,
        **_build_agent_loop_kwargs(config, provider),
    )

    service = _build_cron_service()

    result_holder = []

    async def on_job(job: CronJob) -> str | None:
        response = await agent_loop.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
        )
        result_holder.append(response)
        return response

    service.on_job = on_job

    async def run():
        return await service.run_job(job_id, force=force)

    if asyncio.run(run()):
        console.print("[green]✓[/green] Job executed")
        if result_holder:
            _print_agent_response(result_holder[0], render_markdown=True, console=console)
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status(
    as_json: bool = typer.Option(False, "--json", help="Print the status report as JSON"),
):
    """Show HermitCrab runtime and setup status."""
    from hermitcrab.cli.diagnostics import build_status_report, render_json_report

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


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Print the doctor report as JSON"),
):
    """Run first-run diagnostics and suggest concrete fixes."""
    from hermitcrab.cli.diagnostics import build_doctor_report, render_json_report

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


@app.command()
def audit(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum audit entries to show"),
    event: str = typer.Option("", "--event", "-e", help="Show only entries for one event type"),
    as_json: bool = typer.Option(False, "--json", help="Print audit entries as JSON"),
):
    """Show recent durable audit trail events."""
    from hermitcrab.agent.audit import AuditTrail

    config = _load_runtime_config()
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


app.add_typer(journal_app, name="journal")
app.add_typer(provider_app, name="provider")


if __name__ == "__main__":
    app()
