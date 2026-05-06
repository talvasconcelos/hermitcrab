"""Request-level provider routing for mixed model configurations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from hermitcrab.providers.base import (
    LLMProvider,
    LLMResponse,
    ResponseDoneEvent,
    TextDeltaEvent,
    ToolCallEvent,
)
from hermitcrab.providers.openai_codex_provider import OpenAICodexProvider


class RoutingProvider(LLMProvider):
    """Delegate direct OAuth requests before falling back to a base provider."""

    def __init__(
        self,
        fallback_provider: LLMProvider,
        request_config_resolver: Callable[[str], dict[str, Any]],
    ):
        super().__init__(fallback_provider.api_key, fallback_provider.api_base)
        self.fallback_provider = fallback_provider
        self._request_config_resolver = request_config_resolver

    def get_default_model(self) -> str:
        get_default = getattr(self.fallback_provider, "get_default_model", None)
        if callable(get_default):
            return get_default()
        return getattr(self.fallback_provider, "default_model", None) or ""

    def _direct_provider(self, model: str | None) -> tuple[LLMProvider | None, str | None]:
        if not model:
            return None, None
        request_config = self._request_config_resolver(model) or {}
        resolved_model = request_config.get("model") or model
        provider_name = request_config.get("provider_name")

        if provider_name in {"openai_oauth", "openai_codex"}:
            return OpenAICodexProvider(default_model=resolved_model), resolved_model
        return None, resolved_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        direct_provider, resolved_model = self._direct_provider(model)
        if direct_provider is not None:
            return await direct_provider.chat(
                messages=messages,
                tools=tools,
                model=resolved_model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
        return await self.fallback_provider.chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | ResponseDoneEvent]:
        direct_provider, resolved_model = self._direct_provider(model)
        provider = direct_provider or self.fallback_provider
        async for event in provider.stream_chat(
            messages=messages,
            tools=tools,
            model=resolved_model if direct_provider else model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        ):
            yield event

    async def close(self) -> None:
        await self.fallback_provider.close()
