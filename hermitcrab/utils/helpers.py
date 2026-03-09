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


def resolve_model_alias(alias_or_model: str | None, aliases: dict[str, str]) -> str | None:
    """
    Resolve a model alias to its full model name.

    Args:
        alias_or_model: Model alias (e.g., "qwen", "local") or full model name.
        aliases: Dictionary mapping aliases to full model names.

    Returns:
        Full model name if alias found, otherwise the original string (or None).

    Examples:
        >>> aliases = {"qwen": "ollama/qwen2.5:7b", "local": "ollama/llama3.2:3b"}
        >>> resolve_model_alias("qwen", aliases)
        "ollama/qwen2.5:7b"
        >>> resolve_model_alias("anthropic/claude-3", aliases)
        "anthropic/claude-3"
        >>> resolve_model_alias(None, aliases)
        None
    """
    if alias_or_model is None:
        return None

    # Check if it's a known alias (case-insensitive)
    alias_lower = alias_or_model.lower().strip()
    if alias_lower in aliases:
        return aliases[alias_lower]

    # Not an alias, return as-is (assumed to be a full model name)
    return alias_or_model
