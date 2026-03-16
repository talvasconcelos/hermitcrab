"""Utility functions for hermitcrab."""

from dataclasses import dataclass
from pathlib import Path

from hermitcrab.config.schema import ModelAliasConfig, NamedModelConfig


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


@dataclass(frozen=True)
class ResolvedModelAlias:
    """Resolved model alias with optional reasoning override."""

    model: str | None
    reasoning_effort: str | None = None
    alias: str | None = None


def resolve_named_model(
    model: str | None,
    models: dict[str, NamedModelConfig] | None,
) -> ResolvedModelAlias:
    """Resolve a named model reference to its configured model and metadata."""
    if model is None:
        return ResolvedModelAlias(model=None)

    if not models:
        return ResolvedModelAlias(model=model)

    model_ref = model.strip()
    if not model_ref:
        return ResolvedModelAlias(model=model_ref)

    named = models.get(model_ref)
    if not named:
        return ResolvedModelAlias(model=model)

    return ResolvedModelAlias(
        model=named.model,
        reasoning_effort=named.reasoning_effort,
        alias=model_ref,
    )


def resolve_model_alias_config(
    alias_or_model: str | None,
    aliases: dict[str, str | ModelAliasConfig],
    named_models: dict[str, NamedModelConfig] | None = None,
) -> ResolvedModelAlias:
    """
    Resolve a model alias to its full model name and optional reasoning override.

    Args:
        alias_or_model: Model alias or full model name.
        aliases: Dictionary mapping aliases to model strings or structured alias configs.

    Returns:
        Resolved alias selection.
    """
    if alias_or_model is None:
        return ResolvedModelAlias(model=None)

    alias_lower = alias_or_model.lower().strip()
    if alias_lower in aliases:
        resolved = aliases[alias_lower]
        if isinstance(resolved, ModelAliasConfig):
            named = resolve_named_model(resolved.model, named_models)
            return ResolvedModelAlias(
                model=named.model,
                reasoning_effort=resolved.effective_reasoning_effort() or named.reasoning_effort,
                alias=alias_lower,
            )
        named = resolve_named_model(resolved, named_models)
        return ResolvedModelAlias(
            model=named.model,
            reasoning_effort=named.reasoning_effort,
            alias=alias_lower,
        )

    return resolve_named_model(alias_or_model, named_models)


def resolve_model_alias(
    alias_or_model: str | None,
    aliases: dict[str, str | ModelAliasConfig],
    named_models: dict[str, NamedModelConfig] | None = None,
) -> str | None:
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
        resolved = aliases[alias_lower]
        if isinstance(resolved, ModelAliasConfig):
            return resolve_named_model(resolved.model, named_models).model
        return resolve_named_model(resolved, named_models).model

    # Not an alias, return as-is (assumed to be a full model name)
    return resolve_named_model(alias_or_model, named_models).model
