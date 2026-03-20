"""Native Ollama provider using the /api/chat transport."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import json_repair
from loguru import logger

from hermitcrab.providers.base import LLMProvider, LLMResponse, ToolCallRequest

_OLLAMA_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})


def _extract_image_data(url: str) -> str | None:
    if not url.startswith("data:image/") or "," not in url:
        return None
    _, data = url.split(",", 1)
    return data or None


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        parsed = json_repair.loads(arguments)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _normalize_ollama_api_base(api_base: str | None) -> str:
    if not api_base:
        return "http://localhost:11434"
    trimmed = api_base.rstrip("/")
    if trimmed.lower().endswith("/v1"):
        trimmed = trimmed[:-3]
    return trimmed or "http://localhost:11434"


def _strip_ollama_model_prefix(model: str) -> str:
    if model.startswith("ollama/"):
        return model.split("/", 1)[1]
    if model.startswith("ollama:"):
        return model.split(":", 1)[1]
    if model.endswith(":ollama"):
        return model.rsplit(":", 1)[0]
    return model


def _is_ollama_model(model: str | None) -> bool:
    if not model:
        return False
    lowered = model.lower()
    return (
        lowered.startswith("ollama/")
        or lowered.startswith("ollama:")
        or lowered.endswith(":ollama")
    )


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for tool in tools:
        fn = (tool.get("function") or {}) if tool.get("type") == "function" else tool
        name = fn.get("name")
        if not name:
            continue
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": fn.get("description") or "",
                    "parameters": fn.get("parameters") or {},
                },
            }
        )
    return converted


def _convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role not in _OLLAMA_ALLOWED_ROLES:
            continue

        out: dict[str, Any] = {"role": role}
        content = msg.get("content")

        if role == "user" and isinstance(content, list):
            text_parts: list[str] = []
            images: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                    continue
                if item.get("type") == "image_url":
                    image = _extract_image_data((item.get("image_url") or {}).get("url", ""))
                    if image:
                        images.append(image)
            out["content"] = "".join(text_parts)
            if images:
                out["images"] = images
            converted.append(out)
            continue

        out["content"] = content if isinstance(content, str) else json.dumps(content or "")

        if role == "assistant" and isinstance(msg.get("tool_calls"), list):
            tool_calls: list[dict[str, Any]] = []
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name")
                if not name:
                    continue
                tool_calls.append(
                    {
                        "function": {
                            "name": name,
                            "arguments": _parse_tool_arguments(fn.get("arguments")),
                        }
                    }
                )
            if tool_calls:
                out["tool_calls"] = tool_calls

        if role == "tool" and msg.get("name"):
            out["tool_name"] = msg["name"]

        converted.append(out)

    return converted


def _format_ollama_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out while waiting for Ollama; local model loads can take a while"
    text = str(exc).strip()
    if text:
        return text
    return exc.__class__.__name__


def _consume_ndjson_bytes(chunks: list[bytes]) -> tuple[str, list[ToolCallRequest], str, dict[str, int], str | None]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    finish_reason = "stop"
    usage: dict[str, int] = {}
    next_tool_index = 0

    for raw in chunks:
        line = raw.decode("utf-8", "ignore").strip()
        if not line:
            continue
        event = json.loads(line)
        message = event.get("message") or {}

        text = message.get("content")
        if isinstance(text, str) and text:
            content_parts.append(text)

        reasoning = message.get("thinking") or message.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            reasoning_parts.append(reasoning)

        raw_tool_calls = message.get("tool_calls") or []
        for tc in raw_tool_calls:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not name:
                continue
            tool_calls.append(
                ToolCallRequest(
                    id=f"call_{next_tool_index}",
                    name=name,
                    arguments=_parse_tool_arguments(fn.get("arguments")),
                )
            )
            next_tool_index += 1

        if event.get("done"):
            finish_reason = event.get("done_reason") or ("tool_calls" if tool_calls else "stop")
            usage = {
                "prompt_tokens": int(event.get("prompt_eval_count") or 0),
                "completion_tokens": int(event.get("eval_count") or 0),
                "total_tokens": int((event.get("prompt_eval_count") or 0) + (event.get("eval_count") or 0)),
            }

    return "".join(content_parts) or None, tool_calls, finish_reason, usage, "".join(reasoning_parts) or None


@dataclass
class OllamaDiscoveryResult:
    reachable: bool
    models: list[str]


class OllamaProvider(LLMProvider):
    """Native Ollama /api/chat provider with LiteLLM fallback for non-Ollama models."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "ollama/llama3.1",
        extra_headers: dict[str, str] | None = None,
        request_config_resolver: Callable[[str], dict[str, Any]] | None = None,
        fallback_provider: LLMProvider | None = None,
    ):
        super().__init__(api_key=api_key, api_base=_normalize_ollama_api_base(api_base))
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._request_config_resolver = request_config_resolver
        self._fallback_provider = fallback_provider

    def _get_request_config(self, model: str) -> dict[str, Any]:
        if not self._request_config_resolver:
            return {
                "model": model,
                "api_key": self.api_key,
                "api_base": self.api_base,
                "extra_headers": self.extra_headers,
                "provider_name": "ollama",
                "provider_options": {},
                "reasoning_effort": None,
            }
        resolved = self._request_config_resolver(model) or {}
        return {
            "model": resolved.get("model", model),
            "api_key": resolved.get("api_key", self.api_key),
            "api_base": _normalize_ollama_api_base(resolved.get("api_base", self.api_base)),
            "extra_headers": resolved.get("extra_headers", self.extra_headers) or {},
            "provider_name": resolved.get("provider_name"),
            "provider_options": resolved.get("provider_options", {}),
            "reasoning_effort": resolved.get("reasoning_effort"),
        }

    @staticmethod
    def normalize_api_base(api_base: str | None) -> str:
        return _normalize_ollama_api_base(api_base)

    @staticmethod
    def parse_ndjson_events(chunks: list[bytes]) -> tuple[str | None, list[ToolCallRequest], str, dict[str, int], str | None]:
        return _consume_ndjson_bytes(chunks)

    @staticmethod
    async def discover_models(api_base: str | None) -> OllamaDiscoveryResult:
        base = _normalize_ollama_api_base(api_base)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{base}/api/tags")
                if not response.is_success:
                    return OllamaDiscoveryResult(reachable=True, models=[])
                data = response.json()
        except Exception:
            return OllamaDiscoveryResult(reachable=False, models=[])

        models = []
        for item in data.get("models", []):
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                models.append(item["name"])
        return OllamaDiscoveryResult(reachable=True, models=models)

    @staticmethod
    async def query_context_window(api_base: str | None, model_name: str) -> int | None:
        base = _normalize_ollama_api_base(api_base)
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.post(f"{base}/api/show", json={"name": model_name})
                if not response.is_success:
                    return None
                data = response.json()
        except Exception:
            return None

        model_info = data.get("model_info")
        if not isinstance(model_info, dict):
            return None
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                return value
        return None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        requested_model = model or self.default_model
        request_config = self._get_request_config(requested_model)
        resolved_model = request_config.get("model") or requested_model
        provider_name = request_config.get("provider_name")
        effective_reasoning_effort = reasoning_effort or request_config.get("reasoning_effort")

        if provider_name and provider_name != "ollama" and self._fallback_provider:
            return await self._fallback_provider.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=effective_reasoning_effort,
            )
        if not provider_name and not _is_ollama_model(resolved_model) and self._fallback_provider:
            return await self._fallback_provider.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=effective_reasoning_effort,
            )

        provider_options = dict(request_config.get("provider_options") or {})
        options: dict[str, Any] = {
            **provider_options,
            "temperature": temperature,
            "num_predict": max(1, max_tokens),
        }

        body: dict[str, Any] = {
            "model": _strip_ollama_model_prefix(resolved_model),
            "messages": _convert_messages(self._sanitize_empty_content(messages)),
            "stream": True,
            "options": options,
        }
        if tools:
            body["tools"] = _convert_tools(tools)

        headers = {"Content-Type": "application/json", **(request_config.get("extra_headers") or {})}
        if request_config.get("api_key"):
            headers.setdefault("Authorization", f"Bearer {request_config['api_key']}")

        try:
            logger.debug(
                "Ollama native request prepared (model={}, options={}, tools={})",
                body["model"],
                body["options"],
                len(tools or []),
            )
            timeout = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=60.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{request_config['api_base']}/api/chat",
                    headers=headers,
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        text = (await response.aread()).decode("utf-8", "ignore")
                        return LLMResponse(
                            content=f"Error calling Ollama: {text or response.reason_phrase}",
                            finish_reason="error",
                        )
                    chunks = [line async for line in response.aiter_lines() if line]
        except Exception as exc:
            return LLMResponse(
                content=f"Error calling Ollama: {_format_ollama_error(exc)}",
                finish_reason="error",
            )

        content, tool_calls, finish_reason, usage, reasoning = _consume_ndjson_bytes(
            [line.encode("utf-8") for line in chunks]
        )
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content=reasoning,
        )

    def get_default_model(self) -> str:
        return self.default_model

    async def close(self) -> None:
        if self._fallback_provider is not None:
            await self._fallback_provider.close()
