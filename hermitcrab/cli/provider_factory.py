"""Provider construction for CLI runtime entrypoints."""

from __future__ import annotations

from typing import Any

import typer

from hermitcrab.config.schema import Config


def make_provider(config: Config, console: Any):
    """Create the appropriate LLM provider from config."""
    from hermitcrab.providers.attribution_headers import merge_provider_headers
    from hermitcrab.providers.custom_provider import CustomProvider
    from hermitcrab.providers.litellm_provider import LiteLLMProvider
    from hermitcrab.providers.ollama_provider import OllamaProvider
    from hermitcrab.providers.openai_codex_provider import OpenAICodexProvider
    from hermitcrab.providers.registry import find_by_name, normalize_provider_name
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

    if provider_name == "openai_codex" or (
        "/" in model and normalize_provider_name(model.split("/", 1)[0]) == "openai_codex"
    ):
        return OpenAICodexProvider(default_model=resolved_model.model or model)

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
    resolved_model_name = resolved_model.model or model

    if provider_name == "ollama" or "ollama" in resolved_model_name.lower():
        ollama_config = config.providers.ollama if hasattr(config.providers, "ollama") else None
        api_base = config.get_api_base(model)

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
