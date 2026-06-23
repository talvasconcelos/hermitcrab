"""Gateway CLI command."""

import asyncio
import sys
from typing import Any

import typer
from rich.console import Console

from hermitcrab import __logo__
from hermitcrab.cli.agent_loop_factory import build_agent_loop_kwargs
from hermitcrab.cli.config_helpers import load_runtime_config
from hermitcrab.cli.cron_helpers import build_cron_service
from hermitcrab.cli.gateway_runtime import (
    GatewayIdentityRuntimeState,
    identity_routing_active,
    run_gateway_inbound_router,
    shutdown_gateway_runtime,
)
from hermitcrab.cli.provider_factory import make_provider

console = Console()


def register_gateway_commands(app: typer.Typer) -> None:
    """Register top-level gateway commands."""
    app.command()(gateway)


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

    config = load_runtime_config()
    if config.workspace_path != config.admin_workspace_path:
        raise RuntimeError("admin workspace invariant failed: workspace_path must equal admin_workspace_path")
    bus = MessageBus()
    provider = make_provider(config, console)
    session_manager = create_session_manager(config.owner_identity_root_path)
    routing_active = identity_routing_active(config)

    owner_key = f"identity:{config.identities.registry[config.owner_identity_name].nostr_public_key}"
    admin_cron = build_cron_service(
        identity_root=config.owner_identity_root_path,
        identity_name=config.owner_identity_name,
    )

    agent = AgentLoop(
        bus=bus,
        **build_agent_loop_kwargs(
            config,
            provider,
            workspace=config.owner_identity_root_path,
            identity_name=config.owner_identity_name,
            identity_root=config.owner_identity_root_path,
            cron_service=admin_cron,
            session_manager=session_manager,
        ),
    )

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
        create_provider=lambda: make_provider(config, console),
        cron_service_factory=build_cron_service,
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
        identity_routing_active=routing_active,
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
                run_gateway_inbound_router(
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
            await shutdown_gateway_runtime(
                timeout_monitor=timeout_monitor,
                identity_state=identity_state,
                channels=channels,
            )

    asyncio.run(run())
