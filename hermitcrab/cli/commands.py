"""CLI commands for hermitcrab."""

import asyncio
import json
import os
import re
import select
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from hermitcrab import __logo__, __version__
from hermitcrab.config.schema import (
    Config,
    IdentityConfig,
    ModelAliasConfig,
    NamedModelConfig,
    generate_nostr_keypair,
    normalize_nostr_pubkey,
)

app = typer.Typer(
    name="hermitcrab",
    help=f"{__logo__} hermitcrab - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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
        from hermitcrab.session.manager import SessionManager

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
                **_build_agent_loop_kwargs(
                    self.config,
                    self.create_provider(),
                    workspace=identity_root,
                    identity_name=identity_name,
                    identity_root=identity_root,
                    cron_service=cron,
                    session_manager=SessionManager(identity_root),
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

        ready, reason = _identity_ready_for_routing(self.config, identity_name)
        if not ready:
            raise ValueError(f"identity routing blocked: {reason}")

        identity_root = self.config.get_identity_path(identity_name)
        cron = self.build_cron_service(identity_root, identity_name, key)
        from hermitcrab.agent.loop import AgentLoop
        from hermitcrab.session.manager import SessionManager

        loop = AgentLoop(
            bus=self.bus,
            **_build_agent_loop_kwargs(
                self.config,
                self.create_provider(),
                workspace=identity_root,
                identity_name=identity_name,
                identity_root=identity_root,
                cron_service=cron,
                session_manager=SessionManager(identity_root),
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


def _identity_routing_active(config: Config) -> bool:
    """Return whether explicit Nostr identity routing bindings are configured."""
    return bool(config.channels.nostr.identity_bindings)


def _resolve_gateway_identity_route(
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


def _identity_ready_for_routing(config: Config, identity_name: str) -> tuple[bool, str]:
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


async def _run_gateway_inbound_router(
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
        route = _resolve_gateway_identity_route(
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


async def _shutdown_gateway_runtime(
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


def _build_job_models_from_config(config: Config, identity_name: str | None = None) -> dict | None:
    """
    Build job_models dict from config for AgentLoop initialization.

    Args:
        config: Root configuration object.
        identity_name: Active identity whose model overrides should apply.

    Returns:
        Dict mapping JobClass to model string (or None to skip).
        Returns None if no job models configured (use defaults).
    """
    from hermitcrab.agent.loop import JobClass

    job_models_config = config.agents.defaults.job_models

    identity = config.identities.registry.get(identity_name or config.owner_identity_name)
    identity_models = identity.models if identity is not None else {}

    # Check if any job models are actually configured
    has_config = (
        job_models_config.interactive_response
        or job_models_config.journal_synthesis is not None
        or job_models_config.distillation is not None
        or job_models_config.reflection is not None
        or job_models_config.summarisation is not None
        or job_models_config.subagent is not None
        or bool(identity_models)
        or bool(config.identities.default_identity_model)
    )

    if not has_config:
        return None  # Use AgentLoop defaults

    primary_model = config.agents.defaults.model
    job_models = {
        JobClass.INTERACTIVE_RESPONSE: job_models_config.get_model(
            "interactive_response", primary_model
        ),
        JobClass.JOURNAL_SYNTHESIS: job_models_config.get_model("journal_synthesis", primary_model),
        JobClass.DISTILLATION: job_models_config.get_model("distillation", primary_model),
        JobClass.REFLECTION: job_models_config.get_model("reflection", primary_model),
        JobClass.SUMMARISATION: job_models_config.get_model("summarisation", primary_model),
        JobClass.SUBAGENT: job_models_config.get_model("subagent", primary_model),
    }
    if config.identities.default_identity_model and not identity_models.get("interactiveResponse"):
        job_models[JobClass.INTERACTIVE_RESPONSE] = config.identities.default_identity_model

    identity_job_keys = {
        "interactiveResponse": JobClass.INTERACTIVE_RESPONSE,
        "interactive_response": JobClass.INTERACTIVE_RESPONSE,
        "journalSynthesis": JobClass.JOURNAL_SYNTHESIS,
        "journal_synthesis": JobClass.JOURNAL_SYNTHESIS,
        "distillation": JobClass.DISTILLATION,
        "reflection": JobClass.REFLECTION,
        "summarisation": JobClass.SUMMARISATION,
        "subagent": JobClass.SUBAGENT,
    }
    for key, value in identity_models.items():
        job_class = identity_job_keys.get(key)
        if job_class is not None and isinstance(value, str) and value.strip():
            job_models[job_class] = value.strip()
    return job_models


def _build_runtime_model_aliases(config: Config) -> dict[str, str | ModelAliasConfig]:
    """Resolve any named-model references inside runtime aliases."""
    resolved_aliases: dict[str, str | ModelAliasConfig] = {}
    for alias, value in config.agents.model_aliases.items():
        if isinstance(value, ModelAliasConfig):
            resolved = config.resolve_model_config(value.model)
            resolved_aliases[alias] = ModelAliasConfig(
                model=value.model,
                reasoning_effort=value.reasoning_effort or resolved.reasoning_effort,
                thinking=value.thinking,
            )
            continue

        resolved_aliases[alias] = (
            value if value in config.models else (config.resolve_model_config(value).model or value)
        )

    return resolved_aliases


def _get_tty_stdin_fd() -> int | None:
    """Return the stdin file descriptor when attached to a TTY."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    return fd if os.isatty(fd) else None


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically to avoid leaving partial template files behind."""
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _should_render_progress(channels_config: Any, *, is_tool_hint: bool) -> bool:
    """Apply channel progress visibility rules consistently across CLI modes."""
    if channels_config is None:
        return True
    if is_tool_hint:
        return bool(channels_config.send_tool_hints)
    return bool(channels_config.send_progress)


def _build_reflection_config(config: Config) -> dict[str, Any]:
    """Build reflection promotion settings for AgentLoop."""
    return {
        "auto_promote": config.reflection.promotion.auto_promote,
        "target_files": config.reflection.promotion.target_files,
        "max_file_lines": config.reflection.promotion.max_file_lines,
        "notify_user": config.reflection.promotion.notify_user,
    }


def _build_agent_loop_kwargs(
    config: Config,
    provider: Any,
    *,
    workspace: Path | None = None,
    identity_name: str | None = None,
    identity_root: Path | None = None,
    cron_service: Any | None = None,
    session_manager: Any | None = None,
) -> dict[str, Any]:
    """Build the shared AgentLoop configuration used by CLI entrypoints."""
    target_identity_name = identity_name or config.owner_identity_name
    target_identity_root = identity_root or workspace or config.workspace_path
    return {
        "provider": provider,
        "workspace": target_identity_root,
        "identity_name": target_identity_name,
        "identity_root": target_identity_root,
        "system_root": config.system_root_path,
        "model": config.agents.defaults.model,
        "temperature": config.agents.defaults.temperature,
        "max_tokens": config.agents.defaults.max_tokens,
        "max_iterations": config.agents.defaults.max_tool_iterations,
        "memory_window": config.agents.defaults.memory_window,
        "brave_api_key": config.tools.web.search.api_key or None,
        "exec_config": config.tools.exec,
        "cron_service": cron_service,
        "restrict_to_workspace": True,
        "session_manager": session_manager,
        "mcp_servers": config.tools.mcp_servers,
        "channels_config": config.channels,
        "job_models": _build_job_models_from_config(config, target_identity_name),
        "distillation_enabled": config.agents.defaults.enable_distillation,
        "model_aliases": _build_runtime_model_aliases(config),
        "named_models": config.models,
        "reasoning_effort_config": {
            "reasoning_effort": config.agents.defaults.job_models.reasoning_effort,
        },
        "inactivity_timeout_s": config.agents.defaults.inactivity_timeout_s,
        "llm_max_retries": config.agents.defaults.llm_max_retries,
        "llm_retry_base_delay_s": config.agents.defaults.llm_retry_base_delay_s,
        "max_loop_seconds": config.agents.defaults.max_loop_seconds,
        "max_identical_tool_cycles": config.agents.defaults.max_identical_tool_cycles,
        "memory_context_max_chars": config.agents.defaults.memory_context_max_chars,
        "memory_context_max_items_per_category": config.agents.defaults.memory_context_max_items_per_category,
        "memory_context_max_item_chars": config.agents.defaults.memory_context_max_item_chars,
        "reflection_config": _build_reflection_config(config),
    }


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    fd = _get_tty_stdin_fd()
    if fd is None:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except (ImportError, OSError, ValueError, termios.error):
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except OSError:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except (ImportError, OSError, ValueError, termios.error):
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except (ImportError, OSError, ValueError, termios.error):
        pass

    history_file = Path.home() / ".hermitcrab" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    key_bindings = _build_prompt_key_bindings()

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=True,
        key_bindings=key_bindings,
    )


def _build_prompt_key_bindings() -> KeyBindings:
    """Build prompt-toolkit bindings for submit-vs-newline behavior."""
    bindings = KeyBindings()

    @bindings.add("c-m")
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    return bindings


async def _watch_for_escape(on_escape) -> None:
    """Watch stdin for Esc while the agent is busy and trigger cancellation."""
    fd = _get_tty_stdin_fd()
    if fd is None:
        return

    try:
        import termios
        import tty

        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except (ImportError, OSError, ValueError, termios.error):
        return

    loop = asyncio.get_running_loop()
    escape_pressed = asyncio.Event()

    def _on_stdin_ready() -> None:
        try:
            data = os.read(fd, 32)
        except OSError:
            return
        if b"\x1b" in data:
            escape_pressed.set()

    loop.add_reader(fd, _on_stdin_ready)
    try:
        await escape_pressed.wait()
        await on_escape()
    except asyncio.CancelledError:
        raise
    finally:
        loop.remove_reader(fd)
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except (OSError, ValueError, termios.error):
            pass


def _strip_ansi(text: str) -> str:
    """Remove terminal escape sequences from model output before plain rendering."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _print_agent_response(
    response: str,
    render_markdown: bool,
    *,
    prompt_safe: bool = False,
    model_label: str | None = None,
) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    try:
        if prompt_safe:
            clean = _strip_ansi(content)
            print_formatted_text("")
            heading = "🦀 hermitcrab"
            if model_label:
                heading += f" [{_strip_ansi(model_label)}]"
            print_formatted_text(HTML(f"<ansicyan>{heading}</ansicyan>"))
            print_formatted_text(clean)
            print_formatted_text("")
            return

        body = Markdown(content) if render_markdown else Text(content)
        console.print()
        heading = f"[cyan]{__logo__} hermitcrab[/cyan]"
        if model_label:
            heading += f" [dim][{model_label}][/dim]"
        console.print(heading)
        console.print(body)
        console.print()
    except (BrokenPipeError, OSError, ValueError):
        return


async def _consume_outbound_loop(
    bus: Any,
    agent_loop: Any,
    turn_done: asyncio.Event,
    turn_response: list[tuple[str, str | None]],
    *,
    render_markdown: bool,
) -> None:
    """Consume outbound bus messages, render progress, and collect turn responses."""
    while True:
        try:
            msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            if msg.metadata.get("_progress"):
                if not msg.content or not msg.content.strip():
                    continue
                is_tool_hint = msg.metadata.get("_tool_hint", False)
                if _should_render_progress(
                    agent_loop.channels_config,
                    is_tool_hint=is_tool_hint,
                ):
                    console.print(f"  [dim]↳ {msg.content}[/dim]")
            elif not turn_done.is_set():
                if msg.content:
                    turn_response.append((msg.content, msg.metadata.get("_active_model_label")))
                turn_done.set()
            elif msg.content:
                _print_agent_response(
                    msg.content,
                    render_markdown=render_markdown,
                    prompt_safe=True,
                    model_label=msg.metadata.get("_active_model_label"),
                )
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break


def _load_runtime_config() -> Config:
    """Load config strictly for runtime commands that should fail clearly."""
    from hermitcrab.config.loader import ConfigLoadError, load_config

    try:
        return load_config(strict=True)
    except ConfigLoadError as exc:
        console.print("[red]Error: Failed to load config.[/red]")
        console.print(f"Path: {exc.path}")
        console.print(f"Reason: {exc}")
        console.print("Fix the file or run [cyan]hermitcrab doctor[/cyan] for diagnostics.")
        raise typer.Exit(1) from exc


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    - Ctrl+J inserts a newline; Enter submits
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


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


def bootstrap_standard_layout(config: Config, announce: Callable[[str], None] | None = None) -> None:
    """Create or refresh the system and owner identity roots."""
    system_root = config.system_root_path
    owner_root = config.owner_identity_root_path

    _ensure_root(system_root, "system root", announce=announce)
    _create_template_files(system_root, ["AGENTS.md", "TOOLS.md"], announce=announce)
    (system_root / "logs").mkdir(exist_ok=True)
    (system_root / "indexes").mkdir(exist_ok=True)
    (system_root / "history").mkdir(exist_ok=True)

    _ensure_root(owner_root, "owner identity root", announce=announce)
    _create_template_files(
        owner_root,
        ["IDENTITY.md", "SOUL.md", "USER.md", "HEARTBEAT.md", "ONBOARDING_MODE.md"],
        announce=announce,
    )
    _create_identity_directories(owner_root, announce=announce)


def _ensure_root(
    root: Path,
    label: str,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Create one root directory if missing."""
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        if announce is not None:
            announce(f"[green]✓[/green] Created {label} at {root}")


def _create_template_files(
    root: Path,
    names: list[str],
    announce: Callable[[str], None] | None = None,
) -> None:
    """Create selected bundled template files in a root."""
    from importlib.resources import files as pkg_files

    templates_dir = pkg_files("hermitcrab") / "templates"
    for name in names:
        dest = root / name
        if not dest.exists():
            _atomic_write_text(dest, (templates_dir / name).read_text(encoding="utf-8"))
            if announce is not None:
                announce(f"  [dim]Created {dest.name}[/dim]")


def _create_identity_directories(
    identity_root: Path,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Create per-identity runtime directories."""
    for dirname in ["cron", "journal", "projects", "reports", "sessions", "skills"]:
        (identity_root / dirname).mkdir(exist_ok=True)
        if announce is not None:
            announce(f"  [dim]Created {dirname}/[/dim]")

    # Create category-based memory directories
    memory_dir = identity_root / "memory"
    memory_dir.mkdir(exist_ok=True)
    for category in ["facts", "decisions", "goals", "tasks", "reflections"]:
        (memory_dir / category).mkdir(exist_ok=True)
        if announce is not None:
            announce(f"  [dim]Created memory/{category}/[/dim]")

    # Create knowledge base directories (reference library, not memory)
    knowledge_dir = identity_root / "knowledge"
    knowledge_dir.mkdir(exist_ok=True)
    for category in ["articles", "books", "docs", "notes"]:
        (knowledge_dir / category).mkdir(exist_ok=True)
        if announce is not None:
            announce(f"  [dim]Created knowledge/{category}/[/dim]")

    (identity_root / "lists").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created lists/[/dim]")

    people_dir = identity_root / "people"
    people_dir.mkdir(exist_ok=True)
    (people_dir / "profiles").mkdir(exist_ok=True)
    (people_dir / "interactions").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created people/profiles/ and people/interactions/[/dim]")

    (identity_root / "reminders").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created reminders/[/dim]")

    scratchpads_dir = identity_root / "scratchpads"
    scratchpads_dir.mkdir(exist_ok=True)
    (scratchpads_dir / "archive").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created scratchpads/ and scratchpads/archive/[/dim]")

    onboarding_flag = identity_root / ".onboarding_mode"
    if not onboarding_flag.exists():
        _atomic_write_text(
            onboarding_flag,
            (
                "Onboarding mode is enabled for this identity.\n"
                "Delete this file to disable onboarding prompt injection.\n"
            ),
        )
        if announce is not None:
            announce("  [dim]Enabled onboarding mode (.onboarding_mode)[/dim]")


def _build_onboard_next_steps() -> list[str]:
    """Build concise first-run guidance based on the local environment."""
    lines = ["\nNext steps:"]

    if shutil.which("ollama"):
        lines.extend(
            [
                "  1. Recommended local setup detected: [cyan]ollama[/cyan] is installed",
                "     Start it with [cyan]ollama serve[/cyan] and pull a model like [cyan]ollama pull qwen3.5:4b[/cyan]",
                "  2. Review [cyan]~/.hermitcrab/config.json[/cyan] and point your main model at Ollama or your preferred provider",
                "  3. Run a quick readiness check: [cyan]hermitcrab doctor[/cyan]",
                '  4. Start chatting: [cyan]hermitcrab agent[/cyan] or [cyan]hermitcrab agent -m "Hello!"[/cyan]',
            ]
        )
        return lines

    lines.extend(
        [
            "  1. Choose a provider in [cyan]~/.hermitcrab/config.json[/cyan]",
            "     - Local: install [cyan]Ollama[/cyan] from https://ollama.com and use its local OpenAI-compatible endpoint",
            "     - Cloud: add an API key such as OpenRouter from https://openrouter.ai/keys",
            "     - OAuth: run [cyan]hermitcrab provider login openai-codex[/cyan]",
            "  2. Run a quick readiness check: [cyan]hermitcrab doctor[/cyan]",
            '  3. Start chatting: [cyan]hermitcrab agent[/cyan] or [cyan]hermitcrab agent -m "Hello!"[/cyan]',
        ]
    )
    return lines


def _build_interactive_intro() -> str:
    """Build the interactive CLI intro shown on startup."""
    return (
        f"{__logo__} Interactive mode "
        "(type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit; press [bold]Esc[/bold] "
        "while working to stop the current task)\n"
        "  [dim]/help shows chat commands. Lines prefixed with ↳ are live progress updates while "
        "HermitCrab is gathering context, resuming work, or running tools.[/dim]\n"
    )


def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    from hermitcrab.providers.attribution_headers import merge_provider_headers
    from hermitcrab.providers.custom_provider import CustomProvider
    from hermitcrab.providers.litellm_provider import LiteLLMProvider
    from hermitcrab.providers.ollama_provider import OllamaProvider
    from hermitcrab.providers.openai_codex_provider import OpenAICodexProvider
    from hermitcrab.providers.registry import normalize_provider_name
    from hermitcrab.providers.routing_provider import RoutingProvider

    model = config.agents.defaults.model
    resolved_model = config.resolve_model_config(model)
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    if provider_name is None:
        console.print("[red]Error: Could not resolve a provider for the selected model.[/red]")
        console.print(f"Model: {model}")
        console.print("Check [cyan]hermitcrab status[/cyan] or [cyan]hermitcrab doctor[/cyan].")
        raise typer.Exit(1)

    def _uses_ollama_anywhere() -> bool:
        candidates: set[str] = set()

        if model:
            candidates.add(model)

        job_models = config.agents.defaults.job_models
        for value in (
            job_models.interactive_response,
            job_models.journal_synthesis,
            job_models.distillation,
            job_models.reflection,
            job_models.summarisation,
            job_models.subagent,
        ):
            if isinstance(value, str) and value.strip():
                candidates.add(value.strip())

        for name, named_model in config.models.items():
            candidates.add(name)
            if named_model.model:
                candidates.add(named_model.model)

        for alias_name, alias_value in config.agents.model_aliases.items():
            candidates.add(alias_name)
            if isinstance(alias_value, str) and alias_value.strip():
                candidates.add(alias_value.strip())
            elif getattr(alias_value, "model", None):
                candidates.add(alias_value.model)

        return any(config.get_provider_name(candidate) == "ollama" for candidate in candidates)

    # OpenAI Codex (OAuth)
    if provider_name == "openai_codex" or (
        "/" in model and normalize_provider_name(model.split("/", 1)[0]) == "openai_codex"
    ):
        return OpenAICodexProvider(default_model=resolved_model.model or model)

    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    if provider_name == "custom":
        api_base = config.get_api_base(model) or "http://localhost:8000/v1"
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=api_base,
            default_model=resolved_model.model or model,
            extra_headers=merge_provider_headers(
                provider_name=provider_name,
                api_base=api_base,
                configured_headers=p.extra_headers if p else None,
            ),
        )

    from hermitcrab.providers.registry import find_by_name

    def _request_config_resolver(request_model: str) -> dict[str, Any]:
        resolved_request = config.resolve_model_config(request_model)
        request_provider = config.get_provider(request_model)
        request_provider_name = config.get_provider_name(request_model)
        request_api_base = config.get_api_base(request_model)
        return {
            "model": resolved_request.model or request_model,
            "api_key": request_provider.api_key if request_provider else None,
            "api_base": request_api_base,
            "extra_headers": merge_provider_headers(
                provider_name=request_provider_name,
                api_base=request_api_base,
                configured_headers=request_provider.extra_headers if request_provider else None,
            ),
            "provider_name": request_provider_name,
            "provider_options": resolved_request.provider_options or {},
            "reasoning_effort": resolved_request.reasoning_effort,
        }

    spec = find_by_name(provider_name)

    # Special handling for Ollama - show helpful message if misconfigured
    resolved_model_name = resolved_model.model or model

    if provider_name == "ollama" or "ollama" in resolved_model_name.lower():
        # Check if api_base is explicitly set to None/empty (not using default)
        ollama_config = config.providers.ollama if hasattr(config.providers, "ollama") else None
        api_base = config.get_api_base(model)

        # If user explicitly configured ollama provider but with null/empty api_base
        if ollama_config and ollama_config.api_base is None and api_base is None:
            console.print("[yellow]Warning: Ollama provider configured without api_base.[/yellow]")
            console.print("Using default: http://localhost:11434")
            console.print("\n[dim]If this is wrong, edit ~/.hermitcrab/config.json:[/dim]")
            console.print("""{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434"
    }
  },
  "agents": {
    "defaults": {
      "model": "ollama_chat/llama3.1"
    }
  }
}""")
            console.print("\n[dim]Notes:[/dim]")
            console.print("  • Use [bold]ollama_chat/[/bold] prefix for chat models (recommended)")
            console.print("  • Or [bold]ollama/[/bold] for text completion")
            console.print("  • api_base should NOT include /v1 suffix")

    if (
        not resolved_model_name.startswith("bedrock/")
        and not (p and p.api_key)
        and not (spec and (spec.is_oauth or spec.is_local))
    ):
        console.print("[red]Error: No API key configured for the selected provider.[/red]")
        console.print(f"Provider: {provider_name}")
        console.print(f"Model: {resolved_model_name}")
        console.print(
            "Set it in ~/.hermitcrab/config.json or run [cyan]hermitcrab doctor[/cyan]."
        )
        raise typer.Exit(1)

    fallback_provider = LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
        request_config_resolver=_request_config_resolver,
    )
    routed_fallback = RoutingProvider(
        fallback_provider=fallback_provider,
        request_config_resolver=_request_config_resolver,
    )

    if provider_name == "ollama" or _uses_ollama_anywhere():
        return OllamaProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            request_config_resolver=_request_config_resolver,
            fallback_provider=routed_fallback,
        )

    return routed_fallback


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
    from hermitcrab.session.manager import SessionManager
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
    session_manager = SessionManager(config.owner_identity_root_path)
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
                bus, agent_loop, turn_done, turn_response, render_markdown=markdown
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
            _print_agent_response(response, render_markdown=markdown)
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
        from hermitcrab.bus.events import InboundMessage

        if _get_tty_stdin_fd() is None:
            console.print("[red]Error: Interactive mode requires a TTY on stdin.[/red]")
            console.print(
                "Use [cyan]hermitcrab agent -m \"...\"[/cyan] for one-shot mode or run from a terminal."
            )
            raise typer.Exit(1)

        _init_prompt_session()
        console.print(_build_interactive_intro())

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            await timeout_monitor.start()
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[tuple[str, str | None]] = []

            outbound_task = asyncio.create_task(
                _consume_outbound_loop(
                    bus, agent_loop, turn_done, turn_response, render_markdown=markdown
                )
            )

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            # Finalize session so journal/distillation/reflection run on exit.
                            console.print("[dim]Finalizing session before exit...[/dim]")
                            try:
                                await agent_loop.process_direct(
                                    "/new",
                                    session_key=f"{cli_channel}:{cli_chat_id}",
                                    channel=cli_channel,
                                    chat_id=cli_chat_id,
                                )
                                # Wait up to 20s for background tasks (journal/distillation/reflection)
                                done, pending = await agent_loop.wait_for_background_tasks(
                                    timeout_s=20.0
                                )
                                if done > 0:
                                    console.print(f"[dim]Background tasks completed: {done}[/dim]")
                                if pending > 0:
                                    console.print(
                                        f"[yellow]Background tasks still running: {pending} "
                                        "(continuing shutdown)[/yellow]"
                                    )
                            except Exception as e:
                                console.print(f"[yellow]Session finalization failed: {e}[/yellow]")
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()

                        await bus.publish_inbound(
                            InboundMessage(
                                channel=cli_channel,
                                sender_id="user",
                                chat_id=cli_chat_id,
                                content=user_input,
                            )
                        )

                        stop_requested = False

                        async def _stop_active_turn() -> None:
                            nonlocal stop_requested
                            if stop_requested:
                                return
                            stop_requested = True
                            console.print(
                                "  [yellow]Esc pressed - stopping active work...[/yellow]"
                            )
                            cancelled = await agent_loop.cancel_active_work(
                                f"{cli_channel}:{cli_chat_id}",
                                cancel_background=True,
                            )
                            if not cancelled:
                                console.print("  [dim]No active work to stop.[/dim]")

                        escape_task = asyncio.create_task(_watch_for_escape(_stop_active_turn))
                        try:
                            with _thinking_ctx():
                                await turn_done.wait()
                        finally:
                            escape_task.cancel()
                            await asyncio.gather(escape_task, return_exceptions=True)

                        if turn_response:
                            content, model_label = turn_response[0]
                            _print_agent_response(
                                content,
                                render_markdown=markdown,
                                model_label=model_label,
                            )
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                timeout_monitor.stop()
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close()

        asyncio.run(run_interactive())


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

model_app = typer.Typer(help="Manage named models and defaults")
app.add_typer(model_app, name="model")


def _save_runtime_config(config: Config) -> None:
    """Validate and save the runtime config after CLI mutation."""
    from hermitcrab.config.loader import save_config

    try:
        validated = Config.model_validate(config.model_dump(by_alias=True))
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    save_config(validated)


def _configure_provider(config: Config, provider: str, *, api_key: str | None = None) -> None:
    """Apply a provider choice to config without forcing manual JSON edits."""
    provider_key = provider.strip().lower().replace("-", "_")
    if provider_key == "openrouter":
        config.providers.openrouter.api_key = api_key or config.providers.openrouter.api_key
    elif provider_key == "ollama":
        config.providers.ollama.api_base = config.providers.ollama.api_base or "http://localhost:11434"
    elif provider_key == "custom":
        config.providers.custom.api_key = api_key or config.providers.custom.api_key
    else:
        console.print(f"[red]Error: unsupported provider for setup/model UX: {provider}[/red]")
        raise typer.Exit(1)


def _api_key_from_env(env_name: str | None) -> str | None:
    """Read an API key from an explicit environment variable name."""
    if not env_name:
        return None
    api_key = os.environ.get(env_name)
    if not api_key:
        console.print(f"[red]Error: environment variable is empty or missing: {env_name}[/red]")
        raise typer.Exit(1)
    return api_key


def _provider_options(provider: str | None) -> dict[str, Any]:
    """Persist the admin-selected provider with a named model."""
    if not provider:
        return {}
    provider_key = provider.strip().lower().replace("-", "_")
    if provider_key not in {"openrouter", "ollama", "custom"}:
        console.print(f"[red]Error: unsupported provider for setup/model UX: {provider}[/red]")
        raise typer.Exit(1)
    return {"provider": provider_key}


def _effective_identity_model(config: Config, identity: IdentityConfig) -> str:
    """Return the effective interactive model ref for one identity."""
    return (
        identity.models.get("interactiveResponse")
        or identity.models.get("interactive_response")
        or config.identities.default_identity_model
        or config.agents.defaults.model
    )


def _identity_rows(config: Config) -> list[tuple[str, IdentityConfig, Path]]:
    """Return configured identities for CLI display."""
    return [
        (name, identity, config.get_identity_path(name))
        for name, identity in sorted(config.identities.registry.items())
    ]


def _remove_identity_routes(config: Config, identity_name: str) -> None:
    """Remove inbound Nostr routes for one identity."""
    removed_pubkeys = {
        normalize_nostr_pubkey(pubkey)
        for pubkey in config.channels.nostr.identity_bindings.pop(identity_name, [])
    }
    if not removed_pubkeys:
        return

    remaining_routed = {
        normalize_nostr_pubkey(pubkey)
        for bindings in config.channels.nostr.identity_bindings.values()
        for pubkey in bindings
    }
    config.channels.nostr.allowed_pubkeys = [
        pubkey
        for pubkey in config.channels.nostr.allowed_pubkeys
        if pubkey.strip().lower() in {"*", "all"}
        or normalize_nostr_pubkey(pubkey) not in removed_pubkeys
        or normalize_nostr_pubkey(pubkey) in remaining_routed
    ]


def _bind_nostr_pubkey_to_identity(config: Config, identity_name: str, pubkey: str) -> str:
    """Bind one normalized sender pubkey to an identity and maintain allowlist."""
    normalized = normalize_nostr_pubkey(pubkey)
    for existing_name, pubkeys in config.channels.nostr.identity_bindings.items():
        if existing_name == identity_name:
            continue
        if normalized in {normalize_nostr_pubkey(value) for value in pubkeys}:
            raise ValueError(f"pubkey already routed to user '{existing_name}'")

    routes = config.channels.nostr.identity_bindings.setdefault(identity_name, [])
    if normalized not in {normalize_nostr_pubkey(value) for value in routes}:
        routes.append(normalized)

    allowed = config.channels.nostr.allowed_pubkeys
    allowed_modes = {value.strip().lower() for value in allowed}
    allowed_pubkeys = {
        normalize_nostr_pubkey(value)
        for value in allowed
        if value.strip().lower() not in {"*", "all"}
    }
    if not allowed_modes.intersection({"*", "all"}) and normalized not in allowed_pubkeys:
        allowed.append(normalized)
    return normalized


async def _send_nostr_onboarding_intro(config: Config, recipient_pubkey: str, identity_name: str) -> bool:
    """Best-effort onboarding intro DM; never raise to caller."""
    if not config.channels.nostr.enabled or not config.channels.nostr.private_key:
        return False

    try:
        from hermitcrab.bus.events import OutboundMessage
        from hermitcrab.bus.queue import MessageBus
        from hermitcrab.channels.nostr import NostrChannel
    except Exception:
        return False

    bus = MessageBus()
    channel = NostrChannel(
        config.channels.nostr,
        bus,
        identity_resolver=config.resolve_nostr_sender_identity,
    )
    try:
        await channel.start()
        await channel.send(
            OutboundMessage(
                channel="nostr",
                chat_id=recipient_pubkey,
                content=(
                    f"Hello from HermitCrab. You were added as user '{identity_name}'. "
                    "If this was unexpected, contact the operator."
                ),
            )
        )
        return True
    except Exception:
        return False
    finally:
        try:
            await channel.stop()
        except Exception:
            pass


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

    import time
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
            _print_agent_response(result_holder[0], render_markdown=True)
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


@model_app.command("list")
def model_list(as_json: bool = typer.Option(False, "--json", help="Print models as JSON")):
    """List named models and the current default."""
    config = _load_runtime_config()
    rows = [
        {
            "name": name,
            "model": model.model,
            "reasoning_effort": model.reasoning_effort,
            "default": name == config.agents.defaults.model,
        }
        for name, model in sorted(config.models.items())
    ]
    if config.agents.defaults.model not in config.models:
        rows.insert(
            0,
            {
                "name": "(default)",
                "model": config.agents.defaults.model,
                "reasoning_effort": None,
                "default": True,
            },
        )

    if as_json:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", nl=False)
        return

    table = Table(title="Models")
    table.add_column("Name", style="cyan")
    table.add_column("Model")
    table.add_column("Reasoning")
    table.add_column("Default")
    for row in rows:
        table.add_row(
            row["name"],
            row["model"],
            row["reasoning_effort"] or "-",
            "yes" if row["default"] else "",
        )
    console.print(table)


@model_app.command("add")
def model_add(
    name: str = typer.Argument(..., help="Friendly model name"),
    model_id: str = typer.Argument(..., help="Provider model id, e.g. openrouter/anthropic/..."),
    provider: str | None = typer.Option(None, "--provider", help="Provider to configure/ensure"),
    api_key_env: str | None = typer.Option(
        None, "--api-key-env", help="Read provider API key from this environment variable"
    ),
    reasoning_effort: str | None = typer.Option(
        None, "--reasoning-effort", help="none, low, medium, or high"
    ),
):
    """Add or update a named model."""
    config = _load_runtime_config()
    provider_options = _provider_options(provider)
    if provider:
        _configure_provider(config, provider, api_key=_api_key_from_env(api_key_env))
    if reasoning_effort not in {None, "none", "low", "medium", "high"}:
        console.print("[red]Error: --reasoning-effort must be none, low, medium, or high[/red]")
        raise typer.Exit(1)
    config.models[name] = NamedModelConfig(
        model=model_id,
        reasoning_effort=reasoning_effort,
        provider_options=provider_options,
    )
    _save_runtime_config(config)
    console.print(f"[green]✓[/green] Saved model '{name}' -> {model_id}")


@model_app.command("set-default")
def model_set_default(name_or_model_id: str = typer.Argument(..., help="Named model or raw model id")):
    """Set the default model used by the owner/solo assistant."""
    config = _load_runtime_config()
    config.agents.defaults.model = name_or_model_id
    _save_runtime_config(config)
    console.print(f"[green]✓[/green] Default model set to '{name_or_model_id}'")


@model_app.command("test")
def model_test(name_or_model_id: str = typer.Argument(..., help="Named model or raw model id")):
    """Validate that a model resolves to a configured provider."""
    config = _load_runtime_config()
    resolved = config.resolve_model_config(name_or_model_id)
    provider_name = config.get_provider_name(name_or_model_id)
    if provider_name is None:
        console.print(f"[red]Error: no configured provider found for {name_or_model_id}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] {name_or_model_id} resolves to {resolved.model}")
    console.print(f"Provider: [cyan]{provider_name}[/cyan]")


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


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(
        ..., help="OAuth provider (e.g. 'openai-codex')"
    ),
):
    """Authenticate with an OAuth provider."""
    from hermitcrab.providers.registry import PROVIDERS, normalize_provider_name

    key = normalize_provider_name(provider)
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    from hermitcrab.providers.openai_codex_auth import (
        CODEX_OAUTH_CLIENT_ID,
        CODEX_OAUTH_TOKEN_URL,
        DEFAULT_CODEX_BASE_URL,
        codex_access_token_is_expiring,
        read_codex_cli_tokens,
        resolve_codex_runtime_credentials,
        save_codex_tokens,
    )

    try:
        existing = resolve_codex_runtime_credentials()
        access_token = str(existing.get("api_key", "") or "")
        if access_token and not codex_access_token_is_expiring(access_token, 60):
            console.print("[green]✓ Existing Codex OAuth credentials are valid[/green]")
            return
    except Exception:
        pass

    cli_tokens = read_codex_cli_tokens()
    if cli_tokens:
        console.print("Found existing Codex CLI credentials at [cyan]~/.codex/auth.json[/cyan].")
        console.print("HermitCrab can import them, but a separate login avoids token rotation conflicts.")
        if typer.confirm("Import Codex CLI credentials instead of starting a new login?", default=False):
            save_codex_tokens(cli_tokens)
            console.print("[green]✓ Imported Codex OAuth credentials[/green]")
            return

    issuer = "https://auth.openai.com"
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            response = client.post(
                f"{issuer}/api/accounts/deviceauth/usercode",
                json={"client_id": CODEX_OAUTH_CLIENT_ID},
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:
        console.print(f"[red]Failed to request Codex device code: {exc}[/red]")
        raise typer.Exit(1)

    if response.status_code != 200:
        console.print(f"[red]Codex device code request failed: HTTP {response.status_code}[/red]")
        raise typer.Exit(1)

    device_data = response.json()
    user_code = str(device_data.get("user_code", "") or "")
    device_auth_id = str(device_data.get("device_auth_id", "") or "")
    poll_interval = max(3, int(device_data.get("interval", "5") or 5))
    if not user_code or not device_auth_id:
        console.print("[red]Codex device code response was missing required fields[/red]")
        raise typer.Exit(1)

    console.print("To continue, open this URL in your browser:")
    console.print(f"[cyan]{issuer}/codex/device[/cyan]\n")
    console.print("Enter this code:")
    console.print(f"[bold cyan]{user_code}[/bold cyan]\n")
    console.print("Waiting for sign-in... press Ctrl+C to cancel.")

    code_response = None
    deadline = time.monotonic() + 15 * 60
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            while time.monotonic() < deadline:
                time.sleep(poll_interval)
                poll_response = client.post(
                    f"{issuer}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll_response.status_code == 200:
                    code_response = poll_response.json()
                    break
                if poll_response.status_code in (403, 404):
                    continue
                console.print(
                    f"[red]Codex device auth polling failed: HTTP {poll_response.status_code}[/red]"
                )
                raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Login cancelled[/yellow]")
        raise typer.Exit(130)

    if code_response is None:
        console.print("[red]Codex login timed out after 15 minutes[/red]")
        raise typer.Exit(1)

    authorization_code = str(code_response.get("authorization_code", "") or "")
    code_verifier = str(code_response.get("code_verifier", "") or "")
    if not authorization_code or not code_verifier:
        console.print("[red]Codex device auth response was missing exchange fields[/red]")
        raise typer.Exit(1)

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            token_response = client.post(
                CODEX_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": f"{issuer}/deviceauth/callback",
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as exc:
        console.print(f"[red]Codex token exchange failed: {exc}[/red]")
        raise typer.Exit(1)

    if token_response.status_code != 200:
        console.print(f"[red]Codex token exchange failed: HTTP {token_response.status_code}[/red]")
        raise typer.Exit(1)

    tokens = token_response.json()
    access_token = str(tokens.get("access_token", "") or "")
    refresh_token = str(tokens.get("refresh_token", "") or "")
    if not access_token or not refresh_token:
        console.print("[red]Codex token exchange did not return complete credentials[/red]")
        raise typer.Exit(1)

    save_codex_tokens({"access_token": access_token, "refresh_token": refresh_token})
    console.print("[green]✓ Authenticated with OpenAI Codex OAuth[/green]")
    console.print(f"[dim]Endpoint: {DEFAULT_CODEX_BASE_URL}[/dim]")


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion

        await acompletion(
            model="github_copilot/gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
