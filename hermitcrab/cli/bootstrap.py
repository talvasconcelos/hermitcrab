"""Workspace and identity bootstrap helpers for CLI commands."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from hermitcrab.config.schema import Config


def atomic_write_text(path: Path, content: str) -> None:
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


def bootstrap_standard_layout(config: Config, announce: Callable[[str], None] | None = None) -> None:
    """Create or refresh the system and owner identity roots."""
    system_root = config.system_root_path
    owner_root = config.owner_identity_root_path

    ensure_root(system_root, "system root", announce=announce)
    create_template_files(system_root, ["AGENTS.md", "TOOLS.md"], announce=announce)
    (system_root / "logs").mkdir(exist_ok=True)
    (system_root / "indexes").mkdir(exist_ok=True)
    (system_root / "history").mkdir(exist_ok=True)

    ensure_root(owner_root, "owner identity root", announce=announce)
    create_template_files(
        owner_root,
        ["IDENTITY.md", "SOUL.md", "USER.md", "HEARTBEAT.md", "ONBOARDING_MODE.md"],
        announce=announce,
    )
    create_identity_directories(owner_root, announce=announce)


def ensure_root(
    root: Path,
    label: str,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Create one root directory if missing."""
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        if announce is not None:
            announce(f"[green]✓[/green] Created {label} at {root}")


def create_template_files(
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
            atomic_write_text(dest, (templates_dir / name).read_text(encoding="utf-8"))
            if announce is not None:
                announce(f"  [dim]Created {dest.name}[/dim]")


def create_identity_directories(
    identity_root: Path,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Create per-identity runtime directories."""
    for dirname in ["cron", "journal", "projects", "reports", "sessions", "skills"]:
        (identity_root / dirname).mkdir(exist_ok=True)
        if announce is not None:
            announce(f"  [dim]Created {dirname}/[/dim]")

    memory_dir = identity_root / "memory"
    memory_dir.mkdir(exist_ok=True)
    for category in ["facts", "decisions", "goals", "tasks", "reflections"]:
        (memory_dir / category).mkdir(exist_ok=True)
        if announce is not None:
            announce(f"  [dim]Created memory/{category}/[/dim]")

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
        atomic_write_text(
            onboarding_flag,
            (
                "Onboarding mode is enabled for this identity.\n"
                "Delete this file to disable onboarding prompt injection.\n"
            ),
        )
        if announce is not None:
            announce("  [dim]Enabled onboarding mode (.onboarding_mode)[/dim]")


def build_onboard_next_steps() -> list[str]:
    """Build concise first-run guidance based on the local environment."""
    lines = ["\nNext steps:"]

    if shutil.which("ollama"):
        lines.extend(
            [
                "  1. Recommended local setup detected: [cyan]ollama[/cyan] is installed",
                "     Start it with [cyan]ollama serve[/cyan] and pull a model like [cyan]ollama pull llama3.2:3b[/cyan]",
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
