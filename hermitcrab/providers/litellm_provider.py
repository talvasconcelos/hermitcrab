"""LiteLLM provider implementation for multi-provider support.

Includes Ollama-specific enhancements:
- :cloud suffix for remote Ollama routing with API key auth
- Reasoning model support (think parameter for DeepSeek, etc.)
- Tool call quirk handling (nested wrappers, tool. prefix)
- Multimodal support (IMAGE marker extraction)
"""

import hashlib
import json
import os
import re
import secrets
import string
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import json_repair
import litellm
from litellm import acompletion
from loguru import logger

from hermitcrab.providers.base import (
    LLMProvider,
    LLMResponse,
    ResponseDoneEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolCallRequest,
)
from hermitcrab.providers.registry import find_by_model, find_gateway

# Standard OpenAI chat-completion message keys; extras (e.g. reasoning_content) are stripped for strict providers.
_ALLOWED_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
# Ollama-specific message keys (multimodal support)
_OLLAMA_MSG_KEYS = frozenset({"images"})
_ALNUM = string.ascii_letters + string.digits

# Image marker pattern for multimodal support
# Matches: [IMAGE:data:image/png;base64,abcd==] or [IMAGE:base64data]
_IMAGE_MARKER_PATTERN = re.compile(r"\[IMAGE:([^\]]+)\]")


@dataclass(slots=True)
class _StreamingToolCallState:
    """In-progress streamed tool call assembled across SSE deltas."""

    id: str | None = None
    name: str = ""
    argument_parts: list[str] = field(default_factory=list)


def _short_tool_id() -> str:
    """Generate a provider-safe short tool call ID."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


def _tool_function_parts(function: Any) -> tuple[str | None, Any]:
    """Extract tool function name and arguments from object- or dict-shaped payloads."""
    if isinstance(function, dict):
        return function.get("name"), function.get("arguments")
    return getattr(function, "name", None), getattr(function, "arguments", None)


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.

    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        request_config_resolver: Callable[[str], dict[str, Any]] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._request_config_resolver = request_config_resolver

        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)

        if api_base:
            litellm.api_base = api_base

        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True

        # Ollama-specific configuration
        self._ollama_cloud_api_key = api_key  # For :cloud suffix routing
        self._ollama_reasoning_enabled = False  # For reasoning models (DeepSeek, etc.)

    def _get_request_config(self, model: str) -> dict[str, Any]:
        """Resolve per-request provider credentials/base from the target model."""
        if not self._request_config_resolver:
            return {
                "model": model,
                "api_key": self.api_key,
                "api_base": self.api_base,
                "extra_headers": self.extra_headers,
                "provider_options": {},
                "provider_name": None,
                "reasoning_effort": None,
            }

        resolved = self._request_config_resolver(model) or {}
        return {
            "model": resolved.get("model", model),
            "api_key": resolved.get("api_key", self.api_key),
            "api_base": resolved.get("api_base", self.api_base),
            "extra_headers": resolved.get("extra_headers", self.extra_headers),
            "provider_options": resolved.get("provider_options", {}),
            "provider_name": resolved.get("provider_name"),
            "reasoning_effort": resolved.get("reasoning_effort"),
        }

    def _is_ollama_model(self, model: str) -> bool:
        """Detect if model should use Ollama-specific handling.

        Matches:
        - ollama/llama3.1 (LiteLLM prefix)
        - ollama:llama3.1 (alternative syntax)
        - llama3.1:ollama (suffix syntax)
        """
        model_lower = model.lower()
        return (
            model_lower.startswith("ollama/")
            or model_lower.startswith("ollama:")
            or model_lower.endswith(":ollama")
        )

    def _resolve_ollama_cloud_routing(self, model: str) -> tuple[str, bool]:
        """Handle Ollama :cloud suffix for cloud model routing.

        The :cloud suffix signals Ollama to route the request to cloud models
        (e.g., llama3.1:cloud → llama3.1 via Ollama cloud). The call still goes
        through the configured api_base (usually local Ollama at localhost:11434).

        API key is only required if no local Ollama is running.

        Args:
            model: Model name, potentially with :cloud suffix

        Returns:
            Tuple of (model_name_for_api, should_use_cloud)
            - For local Ollama: keeps :cloud suffix (Ollama needs it for routing)
            - For direct cloud: strips :cloud suffix (API handles routing)

        Raises:
            ValueError: If :cloud requested but no local Ollama and no API key
        """
        requests_cloud = model.endswith(":cloud")
        normalized_model = model[:-6] if requests_cloud else model

        if not requests_cloud:
            return normalized_model, False

        # :cloud suffix: prefer local Ollama, fallback to API key
        is_local_ollama = self.api_base and any(
            host in self.api_base.lower() for host in ["localhost", "127.0.0.1", "::1"]
        )

        # If local Ollama is available, keep :cloud suffix for Ollama to route
        if is_local_ollama:
            return model, True

        # No local Ollama - need API key for direct cloud access
        if not self._ollama_cloud_api_key:
            raise ValueError(
                f"Model '{model}' requested cloud routing, but no local Ollama is running "
                f"({self.api_base}) and no API key is configured. "
                f"Start Ollama locally or set api_key in provider config."
            )

        # Use API key for direct cloud access (stripped model name)
        return normalized_model, True

    def _extract_ollama_images(self, content: str) -> tuple[str | None, list[str]]:
        """Extract image markers from content for Ollama multimodal support.

        Parses [IMAGE:data:image/png;base64,abcd==] markers and extracts base64 data.

        Args:
            content: Message content potentially containing image markers

        Returns:
            Tuple of (cleaned_text_content, list_of_base64_image_strings)
        """
        matches = _IMAGE_MARKER_PATTERN.findall(content)

        if not matches:
            return content if content.strip() else None, []

        # Extract base64 data from markers
        images = []
        for match in matches:
            # Handle both full data URIs and raw base64
            if match.startswith("data:image/"):
                # data:image/png;base64,abcd==
                parts = match.split(",", 1)
                if len(parts) == 2:
                    images.append(parts[1])
            else:
                # Raw base64
                images.append(match)

        # Remove markers from text
        cleaned = _IMAGE_MARKER_PATTERN.sub("", content).strip()

        return cleaned if cleaned else None, images

    def _apply_ollama_multimodal(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert image markers in user messages to Ollama format.

        Args:
            messages: List of message dicts

        Returns:
            Modified messages with images array for Ollama
        """
        result = []
        for msg in messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                content, images = self._extract_ollama_images(msg["content"])
                new_msg = dict(msg)
                if content is not None:
                    new_msg["content"] = content
                if images:
                    new_msg["images"] = images
                result.append(new_msg)
            else:
                result.append(msg)
        return result

    def _extract_ollama_tool_name(self, name: str, arguments: Any) -> tuple[str, Any]:
        """Handle Ollama tool call naming quirks.

        Ollama models sometimes emit tool calls in non-standard formats:
        1. Nested wrapper: {"name": "tool_call", "arguments": {"name": "shell", ...}}
        2. Prefixed name: "tool.shell" → "shell"

        Args:
            name: Tool name from model response
            arguments: Tool arguments (dict or str)

        Returns:
            Tuple of (cleaned_tool_name, arguments)
        """
        # Pattern 1: Nested tool_call wrapper
        if name == "tool_call" or name.startswith("tool_call>") or name.startswith("tool_call<"):
            if isinstance(arguments, dict):
                nested_name = arguments.get("name")
                nested_args = arguments.get("arguments", {})
                if nested_name:
                    return str(nested_name), nested_args

        # Pattern 2: Prefixed tool name (tool.shell, tool.file_read, etc.)
        if name.startswith("tool."):
            return name[5:], arguments

        # Pattern 3: Normal tool call
        return name, arguments

    def _parse_ollama_tool_calls(self, message: Any) -> list[ToolCallRequest]:
        """Parse tool calls from Ollama response with quirk handling.

        Args:
            message: LiteLLM response message object

        Returns:
            List of ToolCallRequest objects
        """
        tool_calls = []

        if not hasattr(message, "tool_calls") or not message.tool_calls:
            return tool_calls

        for tc in message.tool_calls:
            # Get raw name and arguments
            raw_name = tc.function.name if hasattr(tc.function, "name") else ""
            args = tc.function.arguments if hasattr(tc.function, "arguments") else "{}"

            # Parse arguments from JSON string if needed
            if isinstance(args, str):
                try:
                    args = json_repair.loads(args)
                except Exception:
                    args = {}

            # Apply quirk handling
            clean_name, clean_args = self._extract_ollama_tool_name(raw_name, args)

            # Ensure arguments are serialized as JSON string for internal parser
            args_str = json.dumps(clean_args) if isinstance(clean_args, dict) else str(clean_args)

            tool_calls.append(
                ToolCallRequest(
                    id=getattr(tc, "id", None) or f"call_{len(tool_calls)}",
                    name=clean_name,
                    arguments=args_str,
                )
            )

        return tool_calls

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return
        if not spec.env_key:
            # OAuth/provider-only specs (for example: openai_codex)
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # Standard mode: respect explicit provider prefixes first.
        if "/" in model:
            from hermitcrab.providers.registry import find_by_name

            explicit_prefix = model.split("/", 1)[0]
            explicit_spec = find_by_name(explicit_prefix)
            if explicit_spec and explicit_spec.litellm_prefix:
                return self._canonicalize_explicit_prefix(
                    model,
                    explicit_spec.name,
                    explicit_spec.litellm_prefix,
                )

        # Otherwise, auto-prefix for known providers inferred from model name.
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            model = self._canonicalize_explicit_prefix(model, spec.name, spec.litellm_prefix)
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    @staticmethod
    def _canonicalize_explicit_prefix(model: str, spec_name: str, canonical_prefix: str) -> str:
        """Normalize explicit provider prefixes like `github-copilot/...`."""
        if "/" not in model:
            return model
        prefix, remainder = model.split("/", 1)
        if prefix.lower().replace("-", "_") != spec_name:
            return model
        return f"{canonical_prefix}/{remainder}"

    def _supports_cache_control(self, model: str) -> bool:
        """Return True when the provider supports cache_control on content blocks."""
        if self._gateway is not None:
            return self._gateway.supports_prompt_caching
        spec = find_by_model(model)
        return spec is not None and spec.supports_prompt_caching

    def _apply_cache_control(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Return copies of messages and tools with cache_control injected."""
        new_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg["content"]
                if isinstance(content, str):
                    new_content = [
                        {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                    ]
                else:
                    new_content = list(content)
                    new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
                new_messages.append({**msg, "content": new_content})
            else:
                new_messages.append(msg)

        new_tools = tools
        if tools:
            new_tools = list(tools)
            new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}

        return new_messages, new_tools

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

    @staticmethod
    def _sanitize_messages(
        messages: list[dict[str, Any]], is_ollama: bool = False
    ) -> list[dict[str, Any]]:
        """Strip non-standard keys and ensure assistant messages have a content key.

        Args:
            messages: List of message dicts to sanitize
            is_ollama: If True, allow Ollama-specific keys like 'images'
        """
        allowed_keys = _ALLOWED_MSG_KEYS | _OLLAMA_MSG_KEYS if is_ollama else _ALLOWED_MSG_KEYS
        sanitized = LLMProvider._sanitize_request_messages(messages, allowed_keys)
        id_map: dict[str, str] = {}

        def map_id(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            return id_map.setdefault(value, LiteLLMProvider._normalize_tool_call_id(value))

        for clean in sanitized:
            if isinstance(clean.get("tool_calls"), list):
                normalized_tool_calls = []
                for tc in clean["tool_calls"]:
                    if not isinstance(tc, dict):
                        normalized_tool_calls.append(tc)
                        continue
                    tc_clean = dict(tc)
                    tc_clean["id"] = map_id(tc_clean.get("id"))
                    function = tc_clean.get("function")
                    if isinstance(function, dict):
                        function_clean = dict(function)
                        arguments = function_clean.get("arguments")
                        if arguments is not None:
                            if isinstance(arguments, str):
                                try:
                                    repaired = json_repair.loads(arguments)
                                except Exception:
                                    logger.warning(
                                        "Dropping unrecoverable tool-call arguments from outbound history for tool {}",
                                        function_clean.get("name", "<unknown>"),
                                    )
                                    repaired = {}
                                function_clean["arguments"] = json.dumps(
                                    repaired, ensure_ascii=False
                                )
                            else:
                                function_clean["arguments"] = json.dumps(
                                    arguments, ensure_ascii=False
                                )
                        tc_clean["function"] = function_clean
                    normalized_tool_calls.append(tc_clean)
                clean["tool_calls"] = normalized_tool_calls

            if "tool_call_id" in clean and clean["tool_call_id"]:
                clean["tool_call_id"] = map_id(clean["tool_call_id"])
        return sanitized

    @staticmethod
    def _normalize_tool_call_id(tool_call_id: Any) -> Any:
        """Normalize tool_call_id to a strict-provider-safe alphanumeric form."""
        if not isinstance(tool_call_id, str):
            return tool_call_id
        if len(tool_call_id) == 9 and tool_call_id.isalnum():
            return tool_call_id
        return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]

    def _build_completion_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: Literal["none", "low", "medium", "high"] | None,
    ) -> tuple[dict[str, Any], str]:
        """Build normalized LiteLLM request kwargs and return resolved model."""
        original_model = model or self.default_model
        request_config = self._get_request_config(original_model)
        resolved_model = request_config.get("model") or original_model
        request_provider_name = request_config.get("provider_name")
        request_gateway = find_gateway(
            request_provider_name,
            request_config.get("api_key"),
            request_config.get("api_base"),
        )

        original_gateway = self._gateway
        self._gateway = request_gateway
        try:
            api_model = self._resolve_model(resolved_model)
        finally:
            self._gateway = original_gateway

        use_ollama_cloud = False
        ollama_model = api_model
        if self._is_ollama_model(api_model):
            ollama_model, use_ollama_cloud = self._resolve_ollama_cloud_routing(ollama_model)

        if self._supports_cache_control(resolved_model):
            messages, tools = self._apply_cache_control(messages, tools)

        is_ollama = self._is_ollama_model(api_model)
        if is_ollama:
            messages = self._apply_ollama_multimodal(messages)

        max_tokens = max(1, max_tokens)
        kwargs: dict[str, Any] = {
            "model": ollama_model,
            "messages": self._sanitize_messages(
                self._sanitize_empty_content(messages),
                is_ollama=is_ollama,
            ),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        self._apply_model_overrides(ollama_model, kwargs)

        provider_options = request_config.get("provider_options") or {}
        reserved_option_keys = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "api_key",
            "api_base",
            "extra_headers",
        }
        for key, value in provider_options.items():
            if key not in reserved_option_keys:
                kwargs[key] = value

        request_api_key = request_config.get("api_key")
        if request_api_key:
            kwargs["api_key"] = request_api_key

        request_api_base = request_config.get("api_base")
        if request_api_base:
            kwargs["api_base"] = request_api_base

        request_extra_headers = request_config.get("extra_headers")
        if request_extra_headers:
            kwargs["extra_headers"] = request_extra_headers

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        effective_reasoning_effort = reasoning_effort or request_config.get("reasoning_effort")
        if effective_reasoning_effort:
            kwargs["reasoning_effort"] = effective_reasoning_effort

        if self._is_ollama_model(api_model) and self._ollama_reasoning_enabled:
            kwargs["think"] = True

        return kwargs, ollama_model

    @staticmethod
    def _iter_stream_choices(chunk: Any) -> list[Any]:
        choices = getattr(chunk, "choices", None)
        if isinstance(choices, list):
            return choices
        return []

    @staticmethod
    def _extract_delta_text(delta: Any) -> str:
        content = (
            delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
        )
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                else:
                    text = getattr(item, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    @staticmethod
    def _iter_stream_tool_call_fragments(delta: Any) -> list[tuple[int, str | None, str, str]]:
        raw_tool_calls = (
            delta.get("tool_calls")
            if isinstance(delta, dict)
            else getattr(delta, "tool_calls", None)
        )
        if not isinstance(raw_tool_calls, list):
            raw_tool_calls = []

        fragments: list[tuple[int, str | None, str, str]] = []
        for idx, tc in enumerate(raw_tool_calls):
            if isinstance(tc, dict):
                index = tc.get("index", idx)
                function = tc.get("function") or {}
                identifier = tc.get("id")
                name = function.get("name") or ""
                arguments = function.get("arguments") or ""
            else:
                index = getattr(tc, "index", idx)
                function = getattr(tc, "function", None)
                identifier = getattr(tc, "id", None)
                name = getattr(function, "name", "") if function is not None else ""
                arguments = getattr(function, "arguments", "") if function is not None else ""
            fragments.append((int(index), identifier, str(name or ""), str(arguments or "")))

        legacy = (
            delta.get("function_call")
            if isinstance(delta, dict)
            else getattr(delta, "function_call", None)
        )
        if legacy:
            name, arguments = _tool_function_parts(legacy)
            fragments.append((0, None, str(name or ""), str(arguments or "")))
        return fragments

    @staticmethod
    def _update_stream_tool_states(
        states: dict[int, _StreamingToolCallState],
        fragments: list[tuple[int, str | None, str, str]],
    ) -> None:
        for index, identifier, name, arguments in fragments:
            state = states.setdefault(index, _StreamingToolCallState())
            if identifier:
                state.id = identifier
            if name:
                state.name = name
            if arguments:
                state.argument_parts.append(arguments)

    @staticmethod
    def _finalize_stream_tool_states(
        states: dict[int, _StreamingToolCallState],
    ) -> list[ToolCallRequest]:
        tool_calls: list[ToolCallRequest] = []
        for index in sorted(states):
            state = states[index]
            if not state.name:
                continue
            argument_text = "".join(state.argument_parts).strip()
            parsed_arguments: Any = {}
            if argument_text:
                try:
                    parsed_arguments = json_repair.loads(argument_text)
                except Exception:
                    logger.warning(
                        "Failed to repair streamed tool-call arguments for {}",
                        state.name,
                    )
                    parsed_arguments = {}
            if not isinstance(parsed_arguments, dict):
                parsed_arguments = {}
            tool_calls.append(
                ToolCallRequest(
                    id=state.id or _short_tool_id(),
                    name=state.name,
                    arguments=parsed_arguments,
                )
            )
        return tool_calls

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: Literal["none", "low", "medium", "high"] | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            reasoning_effort: Control reasoning/thinking effort for supported models.
                Values: "none", "low", "medium", "high". Silently ignored if unsupported.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        kwargs, ollama_model = self._build_completion_kwargs(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response, model=ollama_model)
        except Exception as e:
            # Return error as content for graceful handling
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: Literal["none", "low", "medium", "high"] | None = None,
    ):
        """Yield typed streaming events from LiteLLM when supported."""
        kwargs, _ = self._build_completion_kwargs(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        kwargs["stream"] = True

        finish_reason = "stop"
        usage: dict[str, int] = {}
        reasoning_parts: list[str] = []
        tool_states: dict[int, _StreamingToolCallState] = {}

        stream = await acompletion(**kwargs)
        async for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                usage = {
                    "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(chunk_usage, "completion_tokens", 0),
                    "total_tokens": getattr(chunk_usage, "total_tokens", 0),
                }

            for choice in self._iter_stream_choices(chunk):
                finish_reason = getattr(choice, "finish_reason", None) or finish_reason
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                text = self._extract_delta_text(delta)
                if text:
                    yield TextDeltaEvent(delta=text)

                reasoning = (
                    delta.get("reasoning_content")
                    if isinstance(delta, dict)
                    else getattr(delta, "reasoning_content", None)
                )
                if isinstance(reasoning, str) and reasoning:
                    reasoning_parts.append(reasoning)

                fragments = self._iter_stream_tool_call_fragments(delta)
                if fragments:
                    self._update_stream_tool_states(tool_states, fragments)

        for tool_call in self._finalize_stream_tool_states(tool_states):
            yield ToolCallEvent(tool_call=tool_call)
        yield ResponseDoneEvent(
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )

    def _parse_response(self, response: Any, model: str | None = None) -> LLMResponse:
        """Parse LiteLLM response into our standard format.

        Args:
            response: LiteLLM response object
            model: Optional model name for Ollama-specific handling
        """
        choice = response.choices[0]
        message = choice.message
        content = message.content
        finish_reason = choice.finish_reason
        raw_tool_calls: list[Any] = []

        for ch in response.choices:
            msg = ch.message
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                raw_tool_calls.extend(msg.tool_calls)
                if ch.finish_reason in ("tool_calls", "stop"):
                    finish_reason = ch.finish_reason
            if not content and getattr(msg, "content", None):
                content = msg.content

        if len(response.choices) > 1:
            logger.debug(
                "LiteLLM response has {} choices, merged {} tool_calls",
                len(response.choices),
                len(raw_tool_calls),
            )

        # Use Ollama-specific tool call parsing if applicable
        is_ollama = model and self._is_ollama_model(model)

        if is_ollama:
            if raw_tool_calls:
                tool_calls = self._parse_ollama_tool_calls_from_list(raw_tool_calls)
            else:
                tool_calls = self._parse_ollama_tool_calls(message)
        else:
            tool_calls = []
            for tc in raw_tool_calls:
                function = (
                    tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                )
                name, args = _tool_function_parts(function)

                if not name:
                    continue

                if args is None:
                    args = {}

                if isinstance(args, str):
                    args = json_repair.loads(args)

                tool_calls.append(
                    ToolCallRequest(
                        id=(tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None))
                        or _short_tool_id(),
                        name=name,
                        arguments=args,
                    )
                )

            if not raw_tool_calls and hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    # Parse arguments from JSON string if needed
                    function = (
                        tc.get("function")
                        if isinstance(tc, dict)
                        else getattr(tc, "function", None)
                    )
                    name, args = _tool_function_parts(function)
                    if not name:
                        continue
                    if isinstance(args, str):
                        args = json_repair.loads(args)

                    tool_calls.append(
                        ToolCallRequest(
                            id=(tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None))
                            or _short_tool_id(),
                            name=name,
                            arguments=args,
                        )
                    )

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        reasoning_content = getattr(message, "reasoning_content", None) or None

        if not tool_calls:
            legacy_function_call = getattr(message, "function_call", None)
            legacy_name, legacy_args = _tool_function_parts(legacy_function_call)
            if legacy_name:
                legacy_args = legacy_args or {}
                if isinstance(legacy_args, str):
                    try:
                        legacy_args = json_repair.loads(legacy_args)
                    except Exception:
                        legacy_args = {}
                if isinstance(legacy_args, dict):
                    tool_calls = [
                        ToolCallRequest(
                            id=_short_tool_id(),
                            name=legacy_name,
                            arguments=legacy_args,
                        )
                    ]

        normalized_finish_reason = finish_reason or "stop"
        if normalized_finish_reason == "tool_calls" and not tool_calls:
            logger.warning(
                "LiteLLM returned finish_reason=tool_calls but no tool calls were parsed; downgrading to stop"
            )
            normalized_finish_reason = "stop"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=normalized_finish_reason,
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def _parse_ollama_tool_calls_from_list(self, tool_calls: list[Any]) -> list[ToolCallRequest]:
        """Parse Ollama tool calls from an aggregated list across choices."""
        parsed: list[ToolCallRequest] = []

        for tc in tool_calls:
            function = getattr(tc, "function", None)
            raw_name = getattr(function, "name", "") or ""
            args = getattr(function, "arguments", "{}")

            if isinstance(args, str):
                try:
                    args = json_repair.loads(args)
                except Exception:
                    args = {}

            clean_name, clean_args = self._extract_ollama_tool_name(raw_name, args)
            args_str = json.dumps(clean_args) if isinstance(clean_args, dict) else str(clean_args)

            parsed.append(
                ToolCallRequest(
                    id=getattr(tc, "id", None) or _short_tool_id(),
                    name=clean_name,
                    arguments=args_str,
                )
            )

        return parsed

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
