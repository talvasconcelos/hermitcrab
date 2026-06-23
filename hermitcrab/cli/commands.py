"""CLI commands for hermitcrab."""

import asyncio
import os
import signal
import sys
from typing import Any

import typer
from rich.console import Console

from hermitcrab import __logo__, __version__
from hermitcrab.cli import gateway_runtime
from hermitcrab.cli.agent_loop_factory import build_agent_loop_kwargs as _build_agent_loop_kwargs
from hermitcrab.cli.channel_commands import channels_app
from hermitcrab.cli.config_helpers import (
    load_runtime_config as _load_runtime_config,
)
from hermitcrab.cli.cron_commands import cron_app
from hermitcrab.cli.cron_helpers import build_cron_service as _build_cron_service
from hermitcrab.cli.diagnostic_commands import register_diagnostic_commands
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
from hermitcrab.cli.setup_commands import register_setup_commands
from hermitcrab.cli.user_commands import user_app
from hermitcrab.config.schema import (
    Config,
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
register_setup_commands(app)


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


app.add_typer(cron_app, name="cron")

app.add_typer(reminders_app, name="reminders")

app.add_typer(people_app, name="people")

app.add_typer(user_app, name="user")

app.add_typer(onboarding_app, name="onboarding")

app.add_typer(model_app, name="model")


register_diagnostic_commands(app)
app.add_typer(journal_app, name="journal")
app.add_typer(provider_app, name="provider")


if __name__ == "__main__":
    app()
