"""Gateway identity routing runtime helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from hermitcrab.cli.agent_loop_factory import build_agent_loop_kwargs
from hermitcrab.config.schema import Config


@dataclass(frozen=True)
class GatewayIdentityRouteDecision:
    """Deterministic inbound gateway identity routing decision."""

    target: Literal["identity", "denied"]
    reason: str
    identity_name: str | None = None


@dataclass
class GatewayIdentityRuntimeState:
    """Stateful runtime helpers for owner + configured identity agents in gateway mode."""

    config: Config
    bus: Any
    channels: Any
    create_provider: Callable[[], Any]
    cron_service_factory: Callable[..., Any]
    heartbeat_service_factory: Callable[..., Any]
    on_reminder_notify: Callable[[Any, str], Any]
    heartbeat_interval_s: int
    heartbeat_enabled: bool
    reminder_interval_s: int
    reminder_service_factory: Callable[..., Any]
    agents: dict[str, Any]
    cron_services: dict[str, Any]
    heartbeat_services: dict[str, Any]
    reminder_services: dict[str, Any]
    cron_services_running: bool = False
    heartbeat_services_running: bool = False
    reminder_services_running: bool = False

    def identity_agent_key(self, identity_name: str) -> str:
        identity = self.config.identities.registry.get(identity_name)
        stable_id = identity.nostr_public_key if identity else identity_name
        return f"identity:{stable_id}"

    def find_cron_conflicts(
        self,
        schedule: Any,
        *,
        now_ms: int,
        exclude_key: str,
    ) -> list[Any]:
        conflicts: list[Any] = []
        for key, service in self.cron_services.items():
            if key == exclude_key:
                continue
            conflicts.extend(service.find_local_schedule_conflicts(schedule, now_ms=now_ms))
        return conflicts

    def build_cron_service(self, identity_root: Path, identity_name: str, key: str) -> Any:
        return self.cron_service_factory(
            identity_root=identity_root,
            identity_name=identity_name,
            conflict_finder=lambda schedule, now_ms: self.find_cron_conflicts(
                schedule,
                now_ms=now_ms,
                exclude_key=key,
            ),
        )

    def pick_heartbeat_target(self, loop: Any) -> tuple[str, str]:
        enabled = set(self.channels.enabled_channels)
        for item in loop.sessions.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        return "cli", "direct"

    def attach_cron_callback(self, cron: Any, loop: Any) -> None:
        async def on_cron_job(job: Any) -> str | None:
            response = await loop.process_direct(
                job.payload.message,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
            if job.payload.deliver and job.payload.to:
                from hermitcrab.bus.events import OutboundMessage

                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=job.payload.channel or "cli",
                        chat_id=job.payload.to,
                        content=response or "",
                    )
                )
            return response

        cron.on_job = on_cron_job

    def build_heartbeat_service(self, loop: Any) -> Any:
        async def on_heartbeat_execute(tasks: str) -> str:
            channel, chat_id = self.pick_heartbeat_target(loop)

            async def _silent(*_args, **_kwargs):
                pass

            return await loop.process_direct(
                tasks,
                session_key="heartbeat",
                channel=channel,
                chat_id=chat_id,
                on_progress=_silent,
            )

        async def on_heartbeat_notify(response: str) -> None:
            from hermitcrab.bus.events import OutboundMessage

            channel, chat_id = self.pick_heartbeat_target(loop)
            if channel == "cli":
                return
            await self.bus.publish_outbound(
                OutboundMessage(channel=channel, chat_id=chat_id, content=response)
            )

        return self.heartbeat_service_factory(
            workspace=loop.identity_root,
            provider=loop.provider,
            model=loop.model,
            on_execute=on_heartbeat_execute,
            on_notify=on_heartbeat_notify,
            interval_s=self.heartbeat_interval_s,
            enabled=self.heartbeat_enabled,
        )

    async def attach_agent_services(self, key: str, loop: Any, cron: Any) -> None:
        self.agents[key] = loop
        self.cron_services[key] = cron
        self.attach_cron_callback(cron, loop)
        self.heartbeat_services[key] = self.build_heartbeat_service(loop)
        await self.ensure_reminder_service(key, loop)
        if self.cron_services_running:
            await cron.start()
        if self.heartbeat_services_running:
            await self.heartbeat_services[key].start()

    async def attach_configured_identity_agents(self) -> None:
        from hermitcrab.agent.loop import AgentLoop
        from hermitcrab.session.manager import create_session_manager

        for identity_name, identity in self.config.identities.registry.items():
            if identity_name == self.config.owner_identity_name or not identity.active:
                continue
            key = self.identity_agent_key(identity_name)
            if key in self.agents:
                continue
            identity_root = self.config.get_identity_path(identity_name)
            cron = self.build_cron_service(identity_root, identity_name, key)
            loop = AgentLoop(
                bus=self.bus,
                **build_agent_loop_kwargs(
                    self.config,
                    self.create_provider(),
                    workspace=identity_root,
                    identity_name=identity_name,
                    identity_root=identity_root,
                    cron_service=cron,
                    session_manager=create_session_manager(identity_root),
                ),
            )
            await self.attach_agent_services(key, loop, cron)

    async def ensure_reminder_service(self, identity_key: str, loop: Any) -> None:
        if loop.reminders is None or identity_key in self.reminder_services:
            return
        service = self.reminder_service_factory(
            loop.reminders,
            on_notify=self.on_reminder_notify,
            interval_s=self.reminder_interval_s,
            enabled=True,
        )
        self.reminder_services[identity_key] = service
        if self.reminder_services_running:
            await service.start()

    async def get_or_create_agent(self, identity_name: str) -> Any:
        key = self.identity_agent_key(identity_name)
        existing = self.agents.get(key)
        if existing is not None:
            return existing

        ready, reason = identity_ready_for_routing(self.config, identity_name)
        if not ready:
            raise ValueError(f"identity routing blocked: {reason}")

        identity_root = self.config.get_identity_path(identity_name)
        cron = self.build_cron_service(identity_root, identity_name, key)
        from hermitcrab.agent.loop import AgentLoop
        from hermitcrab.session.manager import create_session_manager

        loop = AgentLoop(
            bus=self.bus,
            **build_agent_loop_kwargs(
                self.config,
                self.create_provider(),
                workspace=identity_root,
                identity_name=identity_name,
                identity_root=identity_root,
                cron_service=cron,
                session_manager=create_session_manager(identity_root),
            ),
        )
        await self.attach_agent_services(key, loop, cron)
        return loop

    async def process_expired_sessions_all(self) -> int:
        """Process session inactivity across every active identity agent."""
        from loguru import logger

        expired = 0
        for loop in list(self.agents.values()):
            try:
                expired += await loop.process_expired_sessions()
            except Exception as e:
                logger.error("Failed processing expired sessions for identity agent: {}", e)
        return expired

    async def start_reminder_services(self) -> None:
        self.reminder_services_running = True
        for service in self.reminder_services.values():
            await service.start()

    async def start_cron_services(self) -> None:
        self.cron_services_running = True
        for service in self.cron_services.values():
            await service.start()

    async def start_heartbeat_services(self) -> None:
        self.heartbeat_services_running = True
        for service in self.heartbeat_services.values():
            await service.start()

    def stop_reminder_services(self) -> None:
        self.reminder_services_running = False
        for service in self.reminder_services.values():
            service.stop()

    def stop_cron_services(self) -> None:
        self.cron_services_running = False
        for service in self.cron_services.values():
            service.stop()

    def stop_heartbeat_services(self) -> None:
        self.heartbeat_services_running = False
        for service in self.heartbeat_services.values():
            service.stop()

    async def close_agents(self) -> None:
        for loop in self.agents.values():
            await loop.close()

    def stop_agents(self) -> None:
        for loop in self.agents.values():
            loop.stop()


def identity_routing_active(config: Config) -> bool:
    """Return whether explicit Nostr identity routing bindings are configured."""
    return bool(config.channels.nostr.identity_bindings)


def resolve_gateway_identity_route(
    msg: Any,
    *,
    owner_identity_name: str,
) -> GatewayIdentityRouteDecision:
    """Resolve gateway routing action for an inbound message."""
    if msg.channel != "nostr":
        identity_name = str(getattr(msg, "metadata", {}).get("identity_name") or owner_identity_name)
        return GatewayIdentityRouteDecision("identity", "non_nostr_owner_default", identity_name)

    metadata = msg.metadata or {}
    target = metadata.get("identity_target")
    if target == "denied":
        return GatewayIdentityRouteDecision("denied", "channel_metadata_denied")
    if target != "identity":
        return GatewayIdentityRouteDecision("denied", "missing_identity_target")

    identity_name = metadata.get("identity_name")
    if isinstance(identity_name, str) and identity_name:
        return GatewayIdentityRouteDecision("identity", "identity_binding", identity_name)
    return GatewayIdentityRouteDecision("denied", "missing_identity_name")


def identity_ready_for_routing(config: Config, identity_name: str) -> tuple[bool, str]:
    """Return whether a configured identity is safe/ready for gateway routing."""
    identity = config.identities.registry.get(identity_name)
    if identity is None:
        return False, "identity_not_configured"
    if not identity.active:
        return False, "identity_inactive"
    identity_path = config.get_identity_path(identity_name)
    if not identity_path.exists():
        return False, "identity_missing"
    if not (identity_path / "IDENTITY.md").exists():
        return False, "identity_not_bootstrapped"
    return True, "identity_ready"


async def run_gateway_inbound_router(
    *,
    bus: Any,
    owner_agent: Any,
    get_or_create_agent: Callable[[str], Any],
    identity_agent_key: Callable[[str], str],
) -> None:
    """Route inbound gateway messages to identity-specific agent loops."""
    from loguru import logger

    pending_tasks: set[asyncio.Task[None]] = set()

    async def handle_routed_message(msg: Any) -> None:
        route = resolve_gateway_identity_route(
            msg,
            owner_identity_name=owner_agent.identity_name,
        )
        logger.debug(
            "Gateway inbound route: channel={} chat_id={} route_target={} route_reason={} identity_agent={}",
            msg.channel,
            msg.chat_id,
            route.target,
            route.reason,
            identity_agent_key(route.identity_name or owner_agent.identity_name),
        )
        if route.target == "denied":
            owner_agent.audit_event(
                "gateway.identity_route_denied",
                session_key=msg.session_key,
                msg=msg,
                identity_agent=identity_agent_key(owner_agent.identity_name),
                route_reason=route.reason,
            )
            return
        try:
            agent_for_msg = await get_or_create_agent(route.identity_name or owner_agent.identity_name)
        except Exception as e:
            logger.warning("Identity route failed; denying message: {}", e)
            owner_agent.audit_event(
                "gateway.identity_route_denied",
                session_key=msg.session_key,
                msg=msg,
                identity_agent=identity_agent_key(owner_agent.identity_name),
                route_reason=f"identity_unavailable:{route.identity_name}",
            )
            return
        agent_for_msg.audit_event(
            "gateway.identity_route",
            session_key=msg.session_key,
            msg=msg,
            identity_agent=identity_agent_key(agent_for_msg.identity_name),
            route_reason=route.reason,
        )
        response = await agent_for_msg.handle_inbound(msg)
        if response is not None:
            await bus.publish_outbound(response)

    def track_task(task: asyncio.Task[None]) -> None:
        pending_tasks.add(task)

        def on_done(done_task: asyncio.Task[None]) -> None:
            pending_tasks.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                logger.error("Gateway inbound routed task error: {}", exc)

        task.add_done_callback(on_done)

    try:
        while True:
            try:
                msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
                task = asyncio.create_task(handle_routed_message(msg))
                track_task(task)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Gateway inbound router loop error: {}", e)
                continue
    finally:
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)


async def shutdown_gateway_runtime(
    *,
    timeout_monitor: Any,
    identity_state: GatewayIdentityRuntimeState,
    channels: Any,
) -> None:
    """Shutdown gateway runtime components in a stable order."""
    timeout_monitor.stop()
    identity_state.stop_heartbeat_services()
    identity_state.stop_reminder_services()
    identity_state.stop_cron_services()
    await identity_state.close_agents()
    identity_state.stop_agents()
    await channels.stop_all()
