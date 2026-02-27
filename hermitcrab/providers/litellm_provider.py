"""LiteLLM provider implementation for multi-provider support.

Includes Ollama-specific enhancements:
- :cloud suffix for remote Ollama routing with API key auth
- Reasoning model support (think parameter for DeepSeek, etc.)
- Tool call quirk handling (nested wrappers, tool. prefix)
- Multimodal support (IMAGE marker extraction)
"""

import json
import os
import re
from typing import Any

import json_repair
import litellm
from litellm import acompletion

from hermitcrab.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from hermitcrab.providers.registry import find_by_model, find_gateway

# Standard OpenAI chat-completion message keys; extras (e.g. reasoning_content) are stripped for strict providers.
_ALLOWED_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
# Ollama-specific message keys (multimodal support)
_OLLAMA_MSG_KEYS = frozenset({"images"})

# Image marker pattern for multimodal support
# Matches: [IMAGE:data:image/png;base64,abcd==] or [IMAGE:base64data]
_IMAGE_MARKER_PATTERN = re.compile(r'\[IMAGE:([^\]]+)\]')


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
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}

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

    def _is_ollama_model(self, model: str) -> bool:
        """Detect if model should use Ollama-specific handling.

        Matches:
        - ollama/llama3.1 (LiteLLM prefix)
        - ollama:llama3.1 (alternative syntax)
        - llama3.1:ollama (suffix syntax)
        """
        model_lower = model.lower()
        return (
            model_lower.startswith("ollama/") or
            model_lower.startswith("ollama:") or
            model_lower.endswith(":ollama")
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
            Tuple of (stripped_model_name, should_use_cloud)

        Raises:
            ValueError: If :cloud requested but no local Ollama and no API key
        """
        requests_cloud = model.endswith(":cloud")
        normalized_model = model[:-6] if requests_cloud else model

        if not requests_cloud:
            return normalized_model, False

        # :cloud suffix: prefer local Ollama, fallback to API key
        is_local_ollama = (
            self.api_base and
            any(host in self.api_base.lower() for host in ['localhost', '127.0.0.1', '::1'])
        )

        # If local Ollama is available, :cloud just routes through it
        if is_local_ollama:
            return normalized_model, True

        # No local Ollama - need API key for direct cloud access
        if not self._ollama_cloud_api_key:
            raise ValueError(
                f"Model '{model}' requested cloud routing, but no local Ollama is running "
                f"({self.api_base}) and no API key is configured. "
                f"Start Ollama locally or set api_key in provider config."
            )

        # Use API key for direct cloud access
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
            if match.startswith('data:image/'):
                # data:image/png;base64,abcd==
                parts = match.split(',', 1)
                if len(parts) == 2:
                    images.append(parts[1])
            else:
                # Raw base64
                images.append(match)

        # Remove markers from text
        cleaned = _IMAGE_MARKER_PATTERN.sub('', content).strip()

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

            tool_calls.append(ToolCallRequest(
                id=getattr(tc, "id", None) or f"call_{len(tool_calls)}",
                name=clean_name,
                arguments=args_str,
            ))

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

        # Standard mode: auto-prefix for known providers
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
                    new_content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
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
    def _sanitize_messages(messages: list[dict[str, Any]], is_ollama: bool = False) -> list[dict[str, Any]]:
        """Strip non-standard keys and ensure assistant messages have a content key.

        Args:
            messages: List of message dicts to sanitize
            is_ollama: If True, allow Ollama-specific keys like 'images'
        """
        allowed_keys = _ALLOWED_MSG_KEYS | _OLLAMA_MSG_KEYS if is_ollama else _ALLOWED_MSG_KEYS
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed_keys}
            # Strict providers require "content" even when assistant only has tool_calls
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        original_model = model or self.default_model
        model = self._resolve_model(original_model)

        # Check for Ollama :cloud routing
        use_ollama_cloud = False
        ollama_model = model
        if self._is_ollama_model(model):
            ollama_model, use_ollama_cloud = self._resolve_ollama_cloud_routing(ollama_model)

        if self._supports_cache_control(original_model):
            messages, tools = self._apply_cache_control(messages, tools)

        # Apply Ollama multimodal support (image markers → images array)
        is_ollama = self._is_ollama_model(model)
        if is_ollama:
            messages = self._apply_ollama_multimodal(messages)

        # Clamp max_tokens to at least 1 — negative or zero values cause
        # LiteLLM to reject the request with "max_tokens must be at least 1".
        max_tokens = max(1, max_tokens)

        kwargs: dict[str, Any] = {
            "model": ollama_model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages), is_ollama=is_ollama),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(ollama_model, kwargs)

        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base

        # Pass extra headers (e.g. APP-Code for AiHubMix)
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Ollama reasoning model support (think parameter)
        # Some Ollama models (DeepSeek, etc.) support internal reasoning
        if self._is_ollama_model(model) and self._ollama_reasoning_enabled:
            kwargs["think"] = True

        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response, model=ollama_model)
        except Exception as e:
            # Return error as content for graceful handling
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    def _parse_response(self, response: Any, model: str | None = None) -> LLMResponse:
        """Parse LiteLLM response into our standard format.

        Args:
            response: LiteLLM response object
            model: Optional model name for Ollama-specific handling
        """
        choice = response.choices[0]
        message = choice.message

        # Use Ollama-specific tool call parsing if applicable
        is_ollama = model and self._is_ollama_model(model)

        if is_ollama:
            tool_calls = self._parse_ollama_tool_calls(message)
        else:
            tool_calls = []
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    # Parse arguments from JSON string if needed
                    args = tc.function.arguments
                    if isinstance(args, str):
                        args = json_repair.loads(args)

                    tool_calls.append(ToolCallRequest(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        reasoning_content = getattr(message, "reasoning_content", None) or None

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
