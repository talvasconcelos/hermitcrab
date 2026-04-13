"""LLM provider abstraction module."""

from hermitcrab.providers.base import LLMProvider, LLMResponse
from hermitcrab.providers.litellm_provider import LiteLLMProvider
from hermitcrab.providers.ollama_provider import OllamaProvider
from hermitcrab.providers.openai_codex_provider import OpenAICodexProvider
from hermitcrab.providers.qwen_oauth_provider import QwenOAuthProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LiteLLMProvider",
    "OllamaProvider",
    "OpenAICodexProvider",
    "QwenOAuthProvider",
]
