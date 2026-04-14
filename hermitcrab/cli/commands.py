"""CLI commands for hermitcrab."""

import asyncio
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

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
from hermitcrab.config.schema import Config, ModelAliasConfig

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
class GatewayWorkspaceRouteDecision:
    """Deterministic inbound gateway workspace routing decision."""

    target: Literal["admin", "workspace", "denied"]
    reason: str
    workspace_name: str | None = None


def _multi_workspace_routing_active(config: Config) -> bool:
    """Enable multi-workspace routing only when registry and bindings are both configured."""
    return bool(config.workspaces.registry) and bool(config.channels.nostr.workspace_bindings)


def _resolve_gateway_workspace_route(
    msg: Any,
    *,
    multi_workspace_active: bool,
) -> GatewayWorkspaceRouteDecision:
    """Resolve gateway routing action for an inbound message."""
    if msg.channel != "nostr":
        return GatewayWorkspaceRouteDecision("admin", "non_nostr_channel")

    metadata = msg.metadata or {}
    target = metadata.get("workspace_target")
    if target == "denied":
        return GatewayWorkspaceRouteDecision("denied", "channel_metadata_denied")
    if target != "workspace":
        return GatewayWorkspaceRouteDecision("admin", "admin_default")
    if not multi_workspace_active:
        return GatewayWorkspaceRouteDecision("denied", "workspace_mode_disabled")

    workspace_name = metadata.get("workspace_name")
    if isinstance(workspace_name, str) and workspace_name:
        return GatewayWorkspaceRouteDecision("workspace", "workspace_binding", workspace_name)
    return GatewayWorkspaceRouteDecision("denied", "missing_workspace_name")


def _workspace_ready_for_routing(config: Config, workspace_name: str) -> tuple[bool, str]:
    """Return whether a configured workspace is safe/ready for gateway routing."""
    if workspace_name not in config.workspaces.registry:
        return False, "workspace_not_configured"
    workspace_path = config.get_workspace_path(workspace_name)
    if not workspace_path.exists():
        return False, "workspace_missing"
    if not (workspace_path / "AGENTS.md").exists():
        return False, "workspace_not_bootstrapped"
    return True, "workspace_ready"


async def _run_gateway_inbound_router(
    *,
    bus: Any,
    multi_workspace_active: bool,
    admin_agent: Any,
    get_or_create_agent: Callable[[str | None], Any],
    workspace_agent_key: Callable[[str | None], str],
) -> None:
    """Route inbound gateway messages to admin or workspace-specific agent loops."""
    from loguru import logger

    while True:
        try:
            msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
            route = _resolve_gateway_workspace_route(
                msg,
                multi_workspace_active=multi_workspace_active,
            )
            logger.debug(
                "Gateway inbound route: channel={} chat_id={} route_target={} route_reason={} workspace_agent={}",
                msg.channel,
                msg.chat_id,
                route.target,
                route.reason,
                workspace_agent_key(route.workspace_name),
            )
            if route.target == "denied":
                admin_agent.audit_event(
                    "gateway.workspace_route_denied",
                    session_key=msg.session_key,
                    msg=msg,
                    workspace_agent="__admin__",
                    route_reason=route.reason,
                )
                continue
            try:
                agent_for_msg = await get_or_create_agent(route.workspace_name)
            except Exception as e:
                logger.warning("Workspace route failed; denying message: {}", e)
                admin_agent.audit_event(
                    "gateway.workspace_route_denied",
                    session_key=msg.session_key,
                    msg=msg,
                    workspace_agent="__admin__",
                    route_reason=f"workspace_unavailable:{route.workspace_name}",
                )
                continue
            agent_for_msg.audit_event(
                "gateway.workspace_route",
                session_key=msg.session_key,
                msg=msg,
                workspace_agent=workspace_agent_key(route.workspace_name),
                route_reason=route.reason,
            )
            response = await agent_for_msg.handle_inbound(msg)
            if response is not None:
                await bus.publish_outbound(response)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            logger.error("Gateway inbound router loop error: {}", e)
            continue


def _build_job_models_from_config(config: Config) -> dict | None:
    """
    Build job_models dict from config for AgentLoop initialization.

    Args:
        config: Root configuration object.

    Returns:
        Dict mapping JobClass to model string (or None to skip).
        Returns None if no job models configured (use defaults).
    """
    from hermitcrab.agent.loop import JobClass

    job_models_config = config.agents.defaults.job_models

    # Check if any job models are actually configured
    has_config = (
        job_models_config.interactive_response
        or job_models_config.journal_synthesis is not None
        or job_models_config.distillation is not None
        or job_models_config.reflection is not None
        or job_models_config.summarisation is not None
        or job_models_config.subagent is not None
    )

    if not has_config:
        return None  # Use AgentLoop defaults

    primary_model = config.agents.defaults.model

    return {
        JobClass.INTERACTIVE_RESPONSE: job_models_config.get_model(
            "interactive_response", primary_model
        ),
        JobClass.JOURNAL_SYNTHESIS: job_models_config.get_model("journal_synthesis", primary_model),
        JobClass.DISTILLATION: job_models_config.get_model("distillation", primary_model),
        JobClass.REFLECTION: job_models_config.get_model("reflection", primary_model),
        JobClass.SUMMARISATION: job_models_config.get_model("summarisation", primary_model),
        JobClass.SUBAGENT: job_models_config.get_model("subagent", primary_model),
    }


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
    cron_service: Any | None = None,
    session_manager: Any | None = None,
) -> dict[str, Any]:
    """Build the shared AgentLoop configuration used by CLI entrypoints."""
    target_workspace = workspace or config.workspace_path
    return {
        "provider": provider,
        "workspace": target_workspace,
        "model": config.agents.defaults.model,
        "temperature": config.agents.defaults.temperature,
        "max_tokens": config.agents.defaults.max_tokens,
        "max_iterations": config.agents.defaults.max_tool_iterations,
        "memory_window": config.agents.defaults.memory_window,
        "brave_api_key": config.tools.web.search.api_key or None,
        "exec_config": config.tools.exec,
        "cron_service": cron_service,
        "restrict_to_workspace": config.tools.restrict_to_workspace,
        "session_manager": session_manager,
        "mcp_servers": config.tools.mcp_servers,
        "channels_config": config.channels,
        "job_models": _build_job_models_from_config(config),
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
    from hermitcrab.utils.helpers import get_workspace_path

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
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    bootstrap_workspace(get_workspace_path(), announce=console.print)

    console.print(f"\n{__logo__} hermitcrab is ready!")
    for line in _build_onboard_next_steps():
        console.print(line)


def bootstrap_workspace(workspace: Path, announce: Callable[[str], None] | None = None) -> None:
    """Create or refresh one workspace root with default structure."""
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        if announce is not None:
            announce(f"[green]✓[/green] Created workspace at {workspace}")

    _create_workspace_templates(workspace, announce=announce)


def _create_workspace_templates(
    workspace: Path,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Create default workspace template files from bundled templates."""
    from importlib.resources import files as pkg_files

    templates_dir = pkg_files("hermitcrab") / "templates"

    for item in templates_dir.iterdir():
        if not item.name.endswith(".md"):
            continue
        dest = workspace / item.name
        if not dest.exists():
            _atomic_write_text(dest, item.read_text(encoding="utf-8"))
            if announce is not None:
                announce(f"  [dim]Created {item.name}[/dim]")

    # Create category-based memory directories
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)

    for category in ["facts", "decisions", "goals", "tasks", "reflections"]:
        category_dir = memory_dir / category
        category_dir.mkdir(exist_ok=True)
        if announce is not None:
            announce(f"  [dim]Created memory/{category}/[/dim]")

    # Create knowledge base directories (reference library, not memory)
    knowledge_dir = workspace / "knowledge"
    knowledge_dir.mkdir(exist_ok=True)

    for category in ["articles", "books", "docs", "notes"]:
        category_dir = knowledge_dir / category
        category_dir.mkdir(exist_ok=True)
        if announce is not None:
            announce(f"  [dim]Created knowledge/{category}/[/dim]")

    (workspace / "lists").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created lists/[/dim]")

    people_dir = workspace / "people"
    people_dir.mkdir(exist_ok=True)
    (people_dir / "profiles").mkdir(exist_ok=True)
    (people_dir / "interactions").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created people/profiles/ and people/interactions/[/dim]")

    (workspace / "reminders").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created reminders/[/dim]")

    scratchpads_dir = workspace / "scratchpads"
    scratchpads_dir.mkdir(exist_ok=True)
    (scratchpads_dir / "archive").mkdir(exist_ok=True)
    if announce is not None:
        announce("  [dim]Created scratchpads/ and scratchpads/archive/[/dim]")

    (workspace / "skills").mkdir(exist_ok=True)


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
            "     - OAuth: run [cyan]hermitcrab provider login openai-oauth[/cyan] or [cyan]hermitcrab provider login qwen-oauth[/cyan]",
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
    from hermitcrab.providers.custom_provider import CustomProvider
    from hermitcrab.providers.litellm_provider import LiteLLMProvider
    from hermitcrab.providers.ollama_provider import OllamaProvider
    from hermitcrab.providers.openai_codex_provider import OpenAICodexProvider
    from hermitcrab.providers.qwen_oauth_provider import QwenOAuthProvider

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
    if provider_name in {"openai_codex", "openai_oauth"} or model.startswith(
        ("openai-codex/", "openai-oauth/")
    ):
        return OpenAICodexProvider(default_model=resolved_model.model or model)

    if provider_name == "qwen_oauth" or model.startswith(("qwen-oauth/", "qwen-portal/")):
        return QwenOAuthProvider(
            default_model=resolved_model.model or model,
            api_base=config.get_api_base(model),
        )

    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=resolved_model.model or model,
        )

    from hermitcrab.providers.registry import find_by_name

    def _request_config_resolver(request_model: str) -> dict[str, Any]:
        resolved_request = config.resolve_model_config(request_model)
        request_provider = config.get_provider(request_model)
        request_provider_name = config.get_provider_name(request_model)
        return {
            "model": resolved_request.model or request_model,
            "api_key": request_provider.api_key if request_provider else None,
            "api_base": config.get_api_base(request_model),
            "extra_headers": request_provider.extra_headers if request_provider else None,
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

    if provider_name == "ollama" or _uses_ollama_anywhere():
        return OllamaProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            request_config_resolver=_request_config_resolver,
            fallback_provider=fallback_provider,
        )

    return fallback_provider


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
    from hermitcrab.cron.types import CronJob
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
    session_manager = SessionManager(config.workspace_path)
    multi_workspace_active = _multi_workspace_routing_active(config)

    # Create cron service first (callback set after agent creation)
    cron = _build_cron_service()

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        **_build_agent_loop_kwargs(
            config,
            provider,
            workspace=config.workspace_path,
            cron_service=cron,
            session_manager=session_manager,
        ),
    )
    workspace_agents: dict[str, AgentLoop] = {"__admin__": agent}

    def _workspace_agent_key(workspace_name: str | None) -> str:
        return workspace_name or "__admin__"

    reminder_services: dict[str, ReminderService] = {}
    reminder_services_running = False

    async def _ensure_reminder_service(workspace_key: str, loop: AgentLoop) -> None:
        if loop.reminders is None or workspace_key in reminder_services:
            return
        service = ReminderService(
            loop.reminders,
            on_notify=on_reminder_notify,
            interval_s=config.gateway.reminders.interval_s,
            enabled=True,
        )
        reminder_services[workspace_key] = service
        if reminder_services_running:
            await service.start()

    async def _get_or_create_agent(workspace_name: str | None) -> AgentLoop:
        key = _workspace_agent_key(workspace_name)
        existing = workspace_agents.get(key)
        if existing is not None:
            return existing

        assert workspace_name is not None
        ready, reason = _workspace_ready_for_routing(config, workspace_name)
        if not ready:
            raise ValueError(f"workspace routing blocked: {reason}")

        workspace_path = config.get_workspace_path(workspace_name)
        loop = AgentLoop(
            bus=bus,
            **_build_agent_loop_kwargs(
                config,
                _make_provider(config),
                workspace=workspace_path,
                session_manager=SessionManager(workspace_path),
            ),
        )
        workspace_agents[key] = loop
        await _ensure_reminder_service(key, loop)
        logger.info("Created workspace agent for {}", workspace_name)
        return loop

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
        )
        if job.payload.deliver and job.payload.to:
            from hermitcrab.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response or "",
                )
            )
        return response

    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from hermitcrab.bus.events import OutboundMessage

        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(
            OutboundMessage(channel=channel, chat_id=chat_id, content=response)
        )

    async def on_reminder_notify(item, content: str) -> None:
        """Deliver a due reminder to its persisted channel target."""
        from hermitcrab.bus.events import OutboundMessage

        await bus.publish_outbound(
            OutboundMessage(channel=item.channel, chat_id=item.chat_id, content=content)
        )

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    async def _process_expired_sessions_all() -> int:
        """Process session inactivity across every active workspace agent."""
        expired = 0
        for loop in list(workspace_agents.values()):
            try:
                expired += await loop.process_expired_sessions()
            except Exception as e:
                logger.error("Failed processing expired sessions for workspace agent: {}", e)
        return expired

    timeout_monitor = SessionTimeoutService(
        _process_expired_sessions_all,
        interval_s=min(60, max(5, config.agents.defaults.inactivity_timeout_s // 6)),
        enabled=True,
    )
    if agent.reminders is not None:
        reminder_services["__admin__"] = ReminderService(
            agent.reminders,
            on_notify=on_reminder_notify,
            interval_s=config.gateway.reminders.interval_s,
            enabled=True,
        )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    if multi_workspace_active:
        console.print("[green]✓[/green] Multi-workspace routing: active (Nostr bindings)")
        console.print("[dim]Unresolved/invalid workspace routes are denied (no admin fallback)[/dim]")
    else:
        console.print("[dim]Multi-workspace routing: inactive[/dim]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")
    console.print(f"[green]✓[/green] Reminders: every {config.gateway.reminders.interval_s}s")
    console.print("[dim]Cron/heartbeat execution stays in admin workspace[/dim]")

    async def run():
        nonlocal reminder_services_running
        try:
            await cron.start()
            await heartbeat.start()
            await timeout_monitor.start()
            reminder_services_running = True
            for service in reminder_services.values():
                await service.start()
            await asyncio.gather(
                _run_gateway_inbound_router(
                    bus=bus,
                    multi_workspace_active=multi_workspace_active,
                    admin_agent=agent,
                    get_or_create_agent=_get_or_create_agent,
                    workspace_agent_key=_workspace_agent_key,
                ),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            timeout_monitor.stop()
            heartbeat.stop()
            reminder_services_running = False
            for service in reminder_services.values():
                service.stop()
            cron.stop()
            for loop in workspace_agents.values():
                await loop.close()
            for loop in workspace_agents.values():
                loop.stop()
            await channels.stop_all()

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

workspaces_app = typer.Typer(help="Manage named personal workspaces")
app.add_typer(workspaces_app, name="workspaces")


def _configured_workspace_rows(config: Config) -> list[tuple[str, Path, str, bool]]:
    """Return configured named workspaces for CLI display."""
    rows: list[tuple[str, Path, str, bool]] = []
    for name, workspace in config.workspaces.registry.items():
        path = config.get_workspace_path(name)
        label = workspace.label or "-"
        rows.append((name, path, label, workspace.channel_only))
    return rows


def _build_cron_service():
    """Build the CronService in the configured data directory."""
    from hermitcrab.config.loader import get_data_dir
    from hermitcrab.cron.service import CronService

    return CronService(get_data_dir() / "cron" / "jobs.json")


def _build_reminder_store() -> Any:
    """Build the reminder store in the configured workspace."""
    from hermitcrab.agent.reminders import ReminderStore
    from hermitcrab.config.loader import get_data_dir

    config = _load_runtime_config()
    return ReminderStore(
        config.workspace_path,
        legacy_cron_store_path=get_data_dir() / "cron" / "jobs.json",
    )


def _build_people_store() -> Any:
    """Build the people profile store in the configured workspace."""
    from hermitcrab.agent.people import PeopleStore

    config = _load_runtime_config()
    return PeopleStore(config.workspace_path)


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


@workspaces_app.command("list")
def workspaces_list(
    as_json: bool = typer.Option(False, "--json", help="Print named workspaces as JSON"),
):
    """List configured named workspaces."""
    config = _load_runtime_config()
    rows = _configured_workspace_rows(config)

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "name": name,
                        "path": str(path),
                        "label": label if label != "-" else None,
                        "channel_only": channel_only,
                        "exists": path.exists(),
                        "bootstrapped": (path / "AGENTS.md").exists(),
                    }
                    for name, path, label, channel_only in rows
                ],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            nl=False,
        )
        return

    if not rows:
        console.print("No named workspaces configured.")
        return

    table = Table(title="Named Workspaces")
    table.add_column("Name", style="cyan")
    table.add_column("Path")
    table.add_column("Label")
    table.add_column("Mode")
    table.add_column("State")

    for name, path, label, channel_only in rows:
        mode = "channel-only" if channel_only else "interactive"
        if not path.exists():
            state = "[red]missing[/red]"
        elif (path / "AGENTS.md").exists():
            state = "[green]ready[/green]"
        else:
            state = "[yellow]needs bootstrap[/yellow]"
        table.add_row(name, str(path), label, mode, state)

    console.print(table)


@workspaces_app.command("init")
def workspaces_init(
    name: str | None = typer.Argument(None, help="Configured workspace name"),
    all: bool = typer.Option(False, "--all", help="Bootstrap all configured named workspaces"),
):
    """Bootstrap configured named workspaces without changing admin workspace behavior."""
    if all and name is not None:
        console.print("[red]Error: choose a workspace name or --all, not both[/red]")
        raise typer.Exit(1)
    if not all and name is None:
        console.print("[red]Error: provide a configured workspace name or use --all[/red]")
        raise typer.Exit(1)

    config = _load_runtime_config()
    rows = _configured_workspace_rows(config)
    names = [workspace_name for workspace_name, _, _, _ in rows]
    if not rows:
        console.print("[red]Error: no named workspaces configured[/red]")
        raise typer.Exit(1)

    target_names = names if all else [name]
    missing = [workspace_name for workspace_name in target_names if workspace_name not in names]
    if missing:
        console.print(f"[red]Error: unknown workspace: {missing[0]}[/red]")
        raise typer.Exit(1)

    for workspace_name in target_names:
        workspace_path = config.get_workspace_path(workspace_name)
        console.print(f"[bold]Bootstrapping {workspace_name}[/bold]")
        bootstrap_workspace(workspace_path, announce=console.print)


@workspaces_app.command("resolve-nostr")
def workspaces_resolve_nostr(
    pubkey: str = typer.Argument(..., help="Inbound Nostr sender pubkey (64-char hex)"),
    as_json: bool = typer.Option(False, "--json", help="Print resolution as JSON"),
):
    """Resolve inbound Nostr sender to admin workspace, named workspace, or denial."""
    config = _load_runtime_config()
    resolution = config.resolve_nostr_sender_workspace(pubkey)

    payload = {
        "target": resolution.target,
        "workspace_name": resolution.workspace_name,
        "workspace_path": (str(resolution.workspace_path) if resolution.workspace_path else None),
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
    if resolution.workspace_name:
        console.print(f"Workspace: {resolution.workspace_name}")
    if resolution.workspace_path:
        console.print(f"Path: {resolution.workspace_path}")


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
    if report.named_workspaces:
        console.print(
            "Named workspaces: "
            f"{report.bootstrapped_named_workspaces}/{report.named_workspaces} bootstrapped"
        )
    if report.nostr_workspace_bindings:
        console.print(f"Nostr workspace bindings: {report.nostr_workspace_bindings}")
    console.print(
        "Workspace routing: "
        + (
            "[green]active[/green] (Nostr bound senders can route to named workspaces)"
            if report.multi_workspace_routing_active
            else "[dim]inactive[/dim] (admin workspace only)"
        )
    )
    console.print("[dim]Cron/heartbeat remain admin-owned; unresolved workspace routes are denied[/dim]")

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
    trail = AuditTrail(config.workspace_path)
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
        ..., help="OAuth provider (e.g. 'openai-oauth', 'openai-codex', 'qwen-oauth')"
    ),
):
    """Authenticate with an OAuth provider."""
    from hermitcrab.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
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


@_register_login("openai_oauth")
@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive

        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(
            f"[green]✓ Authenticated with OpenAI OAuth[/green]  [dim]{token.account_id}[/dim]"
        )
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("qwen_oauth")
def _login_qwen_oauth() -> None:
    from hermitcrab.providers.qwen_oauth_provider import resolve_qwen_runtime_credentials

    qwen_binary = shutil.which("qwen")
    try:
        creds = resolve_qwen_runtime_credentials(refresh_if_expiring=False)
        console.print(f"[green]✓ Authenticated with Qwen OAuth[/green]  [dim]{creds['auth_file']}[/dim]")
        return
    except Exception:
        pass

    if qwen_binary:
        console.print("[cyan]Starting Qwen CLI OAuth flow...[/cyan]\n")
        result = subprocess.run([qwen_binary, "auth", "qwen-oauth"], check=False)
        if result.returncode != 0:
            console.print("[red]Qwen CLI login failed.[/red]")
            raise typer.Exit(1)
        try:
            creds = resolve_qwen_runtime_credentials(refresh_if_expiring=False)
            console.print(
                f"[green]✓ Authenticated with Qwen OAuth[/green]  [dim]{creds['auth_file']}[/dim]"
            )
            return
        except Exception as exc:
            console.print(f"[red]Qwen OAuth credentials were not usable after login: {exc}[/red]")
            raise typer.Exit(1) from exc

    console.print("[red]Qwen CLI not found and no existing Qwen OAuth credentials were detected.[/red]")
    console.print("Install the Qwen CLI, then run: [cyan]qwen auth qwen-oauth[/cyan]")
    raise typer.Exit(1)


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
