"""Utility functions for hermitcrab."""

from pathlib import Path


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    """Get the hermitcrab data directory (~/.hermitcrab)."""
    return ensure_dir(Path.home() / ".hermitcrab")


def get_workspace_path(workspace: str | None = None) -> Path:
    """
    Get the workspace path.

    Args:
        workspace: Optional workspace path. Defaults to ~/.hermitcrab/workspace.

    Returns:
        Expanded and ensured workspace path.
    """
    if workspace:
        path = Path(workspace).expanduser()
    else:
        path = Path.home() / ".hermitcrab" / "workspace"
    return ensure_dir(path)


def get_skills_path(workspace: Path | None = None) -> Path:
    """Get the skills directory within the workspace."""
    ws = workspace or get_workspace_path()
    return ensure_dir(ws / "skills")


def safe_filename(name: str) -> str:
    """Convert a string to a safe filename."""
    # Replace unsafe characters
    unsafe = '<>:"/\\|?*'
    for char in unsafe:
        name = name.replace(char, "_")
    return name.strip()
