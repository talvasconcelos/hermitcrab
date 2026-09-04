"""AgentLoop construction settings shared by CLI entrypoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermitcrab.config.schema import Config, ModelAliasConfig


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
        return None

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


def _build_reflection_config(config: Config) -> dict[str, Any]:
    """Build reflection promotion settings for AgentLoop."""
    return {
        "auto_promote": config.reflection.promotion.auto_promote,
        "target_files": config.reflection.promotion.target_files,
        "max_file_lines": config.reflection.promotion.max_file_lines,
        "notify_user": config.reflection.promotion.notify_user,
    }


def build_agent_loop_kwargs(
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
    # Budget the system-prompt + memory + history surface against the effective
    # context window, reserving headroom for tool schemas and the generated reply
    # (both are sent outside this surface). Keeps history retention proportional
    # to the window instead of a fixed 6000-token planning budget.
    context_window = config.resolve_context_window()
    prompt_token_budget = max(6000, context_window - 6000 - config.agents.defaults.max_tokens)
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
        "prompt_token_budget": prompt_token_budget,
        "reflection_config": _build_reflection_config(config),
    }
