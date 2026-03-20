"""Direct OpenAI-compatible provider — bypasses LiteLLM."""

from __future__ import annotations

import uuid
from typing import Any

import json_repair
from openai import AsyncOpenAI

from hermitcrab.providers.base import LLMProvider, LLMResponse, ToolCallRequest


def _function_parts(function: Any) -> tuple[str | None, Any]:
    if isinstance(function, dict):
        return function.get("name"), function.get("arguments")
    return getattr(function, "name", None), getattr(function, "arguments", None)


class CustomProvider(LLMProvider):

    def __init__(self, api_key: str = "no-key", api_base: str = "http://localhost:8000/v1", default_model: str = "default"):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            default_headers={"x-session-affinity": uuid.uuid4().hex},
        )

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                   model: str | None = None, max_tokens: int = 4096, temperature: float = 0.7,
                   reasoning_effort: str | None = None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": self._sanitize_empty_content(messages),
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        try:
            return self._parse(await self._client.chat.completions.create(**kwargs))
        except Exception as e:
            return LLMResponse(content=f"Error: {e}", finish_reason="error")

    def _parse(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        msg = choice.message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            call_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            function = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
            name, arguments = _function_parts(function)
            if not name:
                continue
            if isinstance(arguments, str):
                arguments = json_repair.loads(arguments)
            tool_calls.append(
                ToolCallRequest(
                    id=call_id or str(uuid.uuid4())[:8],
                    name=name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        if not tool_calls and getattr(msg, "function_call", None):
            function_call = msg.function_call
            name, args = _function_parts(function_call)
            if not name:
                name = None
            args = args or {}
            if isinstance(args, str):
                args = json_repair.loads(args)
            if name:
                tool_calls = [
                    ToolCallRequest(
                        id=str(uuid.uuid4())[:8],
                        name=name,
                        arguments=args if isinstance(args, dict) else {},
                    )
                ]
        u = response.usage
        finish_reason = choice.finish_reason or "stop"
        if finish_reason == "tool_calls" and not tool_calls:
            finish_reason = "stop"

        return LLMResponse(
            content=msg.content, tool_calls=tool_calls, finish_reason=finish_reason,
            usage={"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens, "total_tokens": u.total_tokens} if u else {},
            reasoning_content=getattr(msg, "reasoning_content", None) or None,
        )

    def get_default_model(self) -> str:
        return self.default_model

    async def close(self) -> None:
        await self._client.close()
