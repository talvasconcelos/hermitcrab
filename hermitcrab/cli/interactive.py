"""Interactive CLI terminal helpers."""

from __future__ import annotations

import asyncio
import os
import re
import select
import signal
import sys
from pathlib import Path
from typing import Any, Callable

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from hermitcrab import __logo__
from hermitcrab.bus.events import InboundMessage

EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def get_tty_stdin_fd() -> int | None:
    """Return the stdin file descriptor when attached to a TTY."""
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    return fd if os.isatty(fd) else None


def should_render_progress(channels_config: Any, *, is_tool_hint: bool) -> bool:
    """Apply channel progress visibility rules consistently across CLI modes."""
    if channels_config is None:
        return True
    if is_tool_hint:
        return bool(channels_config.send_tool_hints)
    return bool(channels_config.send_progress)


def flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    fd = get_tty_stdin_fd()
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


def restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except (ImportError, OSError, ValueError, termios.error):
        pass


def init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except (ImportError, OSError, ValueError, termios.error):
        pass

    history_file = Path.home() / ".hermitcrab" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=True,
        key_bindings=build_prompt_key_bindings(),
    )


def build_prompt_key_bindings() -> KeyBindings:
    """Build prompt-toolkit bindings for submit-vs-newline behavior."""
    bindings = KeyBindings()

    @bindings.add("c-m")
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    return bindings


async def watch_for_escape(on_escape) -> None:
    """Watch stdin for Esc while the agent is busy and trigger cancellation."""
    fd = get_tty_stdin_fd()
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


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences from model output before plain rendering."""
    return _ANSI_ESCAPE_RE.sub("", text)


def print_agent_response(
    response: str,
    render_markdown: bool,
    console: Console,
    *,
    prompt_safe: bool = False,
    model_label: str | None = None,
) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    try:
        if prompt_safe:
            clean = strip_ansi(content)
            print_formatted_text("")
            heading = "🦀 hermitcrab"
            if model_label:
                heading += f" [{strip_ansi(model_label)}]"
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


async def consume_outbound_loop(
    bus: Any,
    agent_loop: Any,
    turn_done: asyncio.Event,
    turn_response: list[tuple[str, str | None]],
    *,
    render_markdown: bool,
    console: Console,
) -> None:
    """Consume outbound bus messages, render progress, and collect turn responses."""
    while True:
        try:
            msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            if msg.metadata.get("_progress"):
                if not msg.content or not msg.content.strip():
                    continue
                is_tool_hint = msg.metadata.get("_tool_hint", False)
                if should_render_progress(
                    agent_loop.channels_config,
                    is_tool_hint=is_tool_hint,
                ):
                    console.print(f"  [dim]↳ {msg.content}[/dim]")
            elif not turn_done.is_set():
                if msg.content:
                    turn_response.append((msg.content, msg.metadata.get("_active_model_label")))
                turn_done.set()
            elif msg.content:
                print_agent_response(
                    msg.content,
                    render_markdown=render_markdown,
                    console=console,
                    prompt_safe=True,
                    model_label=msg.metadata.get("_active_model_label"),
                )
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break


def is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit."""
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def build_interactive_intro() -> str:
    """Build the interactive CLI intro shown on startup."""
    return (
        f"{__logo__} Interactive mode "
        "(type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit; press [bold]Esc[/bold] "
        "while working to stop the current task)\n"
        "  [dim]/help shows chat commands. Lines prefixed with ↳ are live progress updates while "
        "HermitCrab is gathering context, resuming work, or running tools.[/dim]\n"
    )


def run_interactive_mode(
    *,
    bus: Any,
    agent_loop: Any,
    timeout_monitor: Any,
    session_id: str,
    markdown: bool,
    thinking_ctx: Callable[[], Any],
    console: Console,
) -> None:
    """Run the interactive terminal chat loop."""
    if get_tty_stdin_fd() is None:
        console.print("[red]Error: Interactive mode requires a TTY on stdin.[/red]")
        console.print(
            "Use [cyan]hermitcrab agent -m \"...\"[/cyan] for one-shot mode or run from a terminal."
        )
        raise typer.Exit(1)

    init_prompt_session()
    console.print(build_interactive_intro())

    if ":" in session_id:
        cli_channel, cli_chat_id = session_id.split(":", 1)
    else:
        cli_channel, cli_chat_id = "cli", session_id

    def _exit_on_sigint(signum, frame):
        restore_terminal()
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
            consume_outbound_loop(
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
                    flush_pending_tty_input()
                    user_input = await read_interactive_input_async()
                    command = user_input.strip()
                    if not command:
                        continue

                    if is_exit_command(command):
                        console.print("[dim]Finalizing session before exit...[/dim]")
                        try:
                            await agent_loop.process_direct(
                                "/new",
                                session_key=f"{cli_channel}:{cli_chat_id}",
                                channel=cli_channel,
                                chat_id=cli_chat_id,
                            )
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
                        restore_terminal()
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
                        console.print("  [yellow]Esc pressed - stopping active work...[/yellow]")
                        cancelled = await agent_loop.cancel_active_work(
                            f"{cli_channel}:{cli_chat_id}",
                            cancel_background=True,
                        )
                        if not cancelled:
                            console.print("  [dim]No active work to stop.[/dim]")

                    escape_task = asyncio.create_task(watch_for_escape(_stop_active_turn))
                    try:
                        with thinking_ctx():
                            await turn_done.wait()
                    finally:
                        escape_task.cancel()
                        await asyncio.gather(escape_task, return_exceptions=True)

                    if turn_response:
                        content, model_label = turn_response[0]
                        print_agent_response(
                            content,
                            render_markdown=markdown,
                            console=console,
                            model_label=model_label,
                        )
                except KeyboardInterrupt:
                    restore_terminal()
                    console.print("\nGoodbye!")
                    break
                except EOFError:
                    restore_terminal()
                    console.print("\nGoodbye!")
                    break
        finally:
            timeout_monitor.stop()
            agent_loop.stop()
            outbound_task.cancel()
            await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
            await agent_loop.close()

    asyncio.run(run_interactive())
