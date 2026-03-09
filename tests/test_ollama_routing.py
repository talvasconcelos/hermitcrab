import pytest

from hermitcrab.providers.litellm_provider import LiteLLMProvider


def test_resolve_model_respects_explicit_ollama_prefix() -> None:
    provider = LiteLLMProvider(
        api_key="",
        api_base="http://localhost:11434",
        default_model="ollama/kimi-k2.5:cloud",
        provider_name="ollama",
    )

    assert provider._resolve_model("ollama/kimi-k2.5:cloud") == "ollama/kimi-k2.5:cloud"
    assert provider._resolve_model("ollama/qwen3.5:4b") == "ollama/qwen3.5:4b"


def test_resolve_ollama_cloud_keeps_suffix_for_local() -> None:
    provider = LiteLLMProvider(
        api_key="",
        api_base="http://localhost:11434",
        default_model="ollama/llama3.1:cloud",
        provider_name="ollama",
    )

    model, use_cloud = provider._resolve_ollama_cloud_routing("llama3.1:cloud")

    assert model == "llama3.1:cloud"
    assert use_cloud is True


def test_resolve_ollama_cloud_strips_suffix_for_remote_with_key() -> None:
    provider = LiteLLMProvider(
        api_key="test-key",
        api_base="https://ollama.example.com",
        default_model="ollama/llama3.1:cloud",
        provider_name="ollama",
    )

    model, use_cloud = provider._resolve_ollama_cloud_routing("llama3.1:cloud")

    assert model == "llama3.1"
    assert use_cloud is True


def test_resolve_ollama_cloud_requires_key_when_remote() -> None:
    provider = LiteLLMProvider(
        api_key="",
        api_base="https://ollama.example.com",
        default_model="ollama/llama3.1:cloud",
        provider_name="ollama",
    )

    with pytest.raises(ValueError, match="no local Ollama is running"):
        provider._resolve_ollama_cloud_routing("llama3.1:cloud")
