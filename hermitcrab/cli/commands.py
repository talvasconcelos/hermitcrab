"""CLI commands for hermitcrab."""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console
from rich.markdown import Markdown
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
from hermitcrab.cli.config_helpers import (
    save_runtime_config as _save_runtime_config,
)
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
from hermitcrab.cli.model_commands import model_app
from hermitcrab.cli.onboarding_commands import onboarding_app
from hermitcrab.cli.provider_factory import make_provider
from hermitcrab.cli.provider_login import provider_app
from hermitcrab.config.schema import (
    Config,
    IdentityConfig,
    NamedModelConfig,
    generate_nostr_keypair,
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


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    config = _load_runtime_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row("Telegram", "✓" if tg.enabled else "✗", tg_config)

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row("Email", "✓" if em.enabled else "✗", em_config)

    # Nostr
    nostr = config.channels.nostr
    nostr_config = (
        f"{nostr.protocol}, {len(nostr.relays)} relay(s)"
        if nostr.private_key
        else "[dim]not configured[/dim]"
    )
    table.add_row("Nostr", "✓" if nostr.enabled else "✗", nostr_config)

    console.print(table)


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")

reminders_app = typer.Typer(help="Manage reminder artifacts")
app.add_typer(reminders_app, name="reminders")

people_app = typer.Typer(help="Manage people profiles")
app.add_typer(people_app, name="people")

user_app = typer.Typer(help="Manage identity-scoped users")
app.add_typer(user_app, name="user")

app.add_typer(onboarding_app, name="onboarding")

app.add_typer(model_app, name="model")


def _build_cron_service(
    *,
    identity_root: Path | None = None,
    identity_name: str | None = None,
    conflict_finder: Callable[[Any, int], list[Any]] | None = None,
):
    """Build the CronService for an identity root."""
    from hermitcrab.cron.service import CronService

    if identity_root is None or identity_name is None:
        config = _load_runtime_config()
        identity_root = identity_root or config.owner_identity_root_path
        identity_name = identity_name or config.owner_identity_name
    return CronService(
        identity_root / "cron" / "jobs.json",
        identity_name=identity_name,
        conflict_finder=conflict_finder,
    )


def _build_reminder_store() -> Any:
    """Build the reminder store in the owner identity root."""
    from hermitcrab.agent.reminders import ReminderStore

    config = _load_runtime_config()
    return ReminderStore(config.owner_identity_root_path)


def _build_people_store() -> Any:
    """Build the people profile store in the configured workspace."""
    from hermitcrab.agent.people import PeopleStore

    config = _load_runtime_config()
    return PeopleStore(config.owner_identity_root_path)


def _require_one_schedule_option(
    every: int | None,
    cron_expr: str | None,
    at: str | None,
) -> str:
    """Return schedule kind when exactly one schedule option was provided."""
    if sum(value is not None for value in (every, cron_expr, at)) != 1:
        console.print("[red]Error: specify exactly one of --every, --cron, or --at[/red]")
        raise typer.Exit(1)
    return "every" if every is not None else ("cron" if cron_expr else "at")


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


@reminders_app.command("list")
def reminders_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include cancelled reminders"),
):
    """List reminder artifacts."""
    store = _build_reminder_store()
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
    store = _build_reminder_store()
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
    schedule_kind = _require_one_schedule_option(every, cron_expr, at or event_at)
    store = _build_reminder_store()
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
    store = _build_reminder_store()
    item = store.cancel_reminder(query)
    if item is None:
        console.print(f"[red]Reminder not found: {query}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] Cancelled reminder '{item.title}'")


@people_app.command("list")
def people_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include inactive profiles"),
):
    """List people profiles."""
    store = _build_people_store()
    profiles = store.list_profiles(include_inactive=all)
    if not profiles:
        console.print("No people profiles found.")
        return
    reminders = _build_reminder_store()
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
    store = _build_people_store()
    reminders = _build_reminder_store()
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
    store = _build_people_store()
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
    store = _build_people_store()
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
    store = _build_people_store()
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
    store = _build_people_store()
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
    people = _build_people_store()
    person = people.get_profile(query)
    if person is None:
        console.print(f"[red]People profile not found: {query}[/red]")
        raise typer.Exit(1)
    schedule_kind = _require_one_schedule_option(every, cron_expr, at or event_at)
    reminders = _build_reminder_store()
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
    store = _build_people_store()
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
    store = _build_people_store()
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
    nostr_private_key: str | None = typer.Option(
        None,
        "--nostr-private-key",
        help="(Backward compatibility) Identity Nostr private key as nsec or hex.",
    ),
):
    """Add a user identity and bootstrap its identity root."""
    config = _load_runtime_config()
    if name in config.identities.registry:
        console.print(f"[red]Error: user already exists: {name}[/red]")
        raise typer.Exit(1)

    if nostr_public_key and nostr_private_key:
        console.print("[red]Error: provide either --nostr-public-key or --nostr-private-key, not both[/red]")
        raise typer.Exit(1)

    generated_private_key: str | None = None
    selected_pubkey = nostr_public_key or ""
    selected_private_key = nostr_private_key or ""
    if not selected_pubkey and not selected_private_key:
        generated_private_key, generated_pubkey = generate_nostr_keypair()
        selected_pubkey = generated_pubkey

    try:
        config.identities.registry[name] = IdentityConfig(
            label=label,
            nostr_public_key=selected_pubkey,
            nostr_private_key=selected_private_key,
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
    elif selected_private_key:
        console.print(f"Nostr pubkey: {identity.nostr_public_key}")
        console.print("Private key accepted for backward compatibility.")
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


# ============================================================================
# Journal Commands
# ============================================================================

journal_app = typer.Typer(help="Manage journal entries")
app.add_typer(journal_app, name="journal")
app.add_typer(provider_app, name="provider")


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
    from datetime import datetime, timezone

    from hermitcrab.agent.journal import JournalStore
    config = _load_runtime_config()
    workspace = config.workspace_path
    journal = JournalStore(workspace)

    # Parse date if provided
    entry_date = None
    if date:
        try:
            entry_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            console.print("[red]Invalid date format. Use YYYY-MM-DD.[/red]")
            raise typer.Exit(1)

    # Get content from option or prompt
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

    # Write the entry
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
    from datetime import datetime, timezone

    from hermitcrab.agent.journal import JournalStore
    config = _load_runtime_config()
    workspace = config.workspace_path
    journal = JournalStore(workspace)

    # Parse date if provided
    entry_date = None
    if date:
        try:
            entry_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            console.print("[red]Invalid date format. Use YYYY-MM-DD.[/red]")
            raise typer.Exit(1)

    # Read the entry
    if body_only:
        content = journal.read_entry_body(entry_date)
    else:
        content = journal.read_entry(entry_date)

    if content is None:
        target_date = entry_date or datetime.now(timezone.utc)
        console.print(
            f"[yellow]No journal entry found for {target_date.strftime('%Y-%m-%d')}[/yellow]"
        )
        raise typer.Exit(0)

    # Display with markdown rendering
    console.print()
    console.print(Markdown(content))


@journal_app.command("list")
def journal_list(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of entries to show"),
):
    """List recent journal entries."""
    from datetime import datetime, timezone

    from hermitcrab.agent.journal import JournalStore
    config = _load_runtime_config()
    workspace = config.workspace_path
    journal = JournalStore(workspace)

    entries = journal.list_entries(limit=limit)

    if not entries:
        console.print("[yellow]No journal entries found.[/yellow]")
        raise typer.Exit(0)

    console.print(f"\n[bold]Journal Entries[/bold] (showing {len(entries)} of {limit})\n")

    for entry_path in entries:
        date_str = entry_path.stem  # YYYY-MM-DD
        metadata = journal.get_entry_metadata(
            datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        )

        tags_str = ""
        if metadata and metadata.get("tags"):
            tags_str = f" [dim]({', '.join(metadata['tags'])})[/dim]"

        console.print(f"  [cyan]{date_str}[/cyan]{tags_str}")
        console.print(f"    [dim]{entry_path}[/dim]\n")


if __name__ == "__main__":
    app()
