from hermitcrab.agent.loop import AgentLoop
from hermitcrab.config.schema import Config
from hermitcrab.providers.base import LLMProvider, LLMResponse
from hermitcrab.providers.litellm_provider import LiteLLMProvider
from hermitcrab.providers.ollama_provider import OllamaProvider
from hermitcrab.providers.openai_codex_auth import codex_cloudflare_headers
from hermitcrab.providers.registry import normalize_provider_name
from hermitcrab.providers.routing_provider import RoutingProvider


def test_provider_name_normalization_accepts_config_alias_shapes() -> None:
    assert normalize_provider_name("openaiOauth") == "openai_codex"
    assert normalize_provider_name("openaiOAuth") == "openai_codex"
    assert normalize_provider_name("openai-oauth") == "openai_codex"
    assert normalize_provider_name("openai_oauth") == "openai_codex"
    assert normalize_provider_name("openaiCodex") == "openai_codex"
    assert normalize_provider_name("githubCopilot") == "github_copilot"
    assert normalize_provider_name("nvidiaNim") == "nvidia_nim"


def test_camel_case_oauth_prefix_does_not_fall_back_to_openrouter(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {
                "gpt-5.4": {
                    "model": "openaiOauth/gpt-5.4-mini",
                }
            },
            "providers": {
                "openrouter": {
                    "apiKey": "sk-or-test",
                    "apiBase": "https://openrouter.ai/api/v1",
                }
            },
        }
    )

    assert config.resolve_model_config("gpt-5.4").model == "openaiOauth/gpt-5.4-mini"
    assert config.get_provider_name("gpt-5.4") == "openai_codex"


def test_camel_case_codex_prefix_does_not_fall_back_to_openrouter(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {
                "gpt-5.4": {
                    "model": "openaiCodex/gpt-5.4-mini",
                }
            },
            "providers": {
                "openrouter": {
                    "apiKey": "sk-or-test",
                    "apiBase": "https://openrouter.ai/api/v1",
                }
            },
        }
    )

    assert config.get_provider_name("gpt-5.4") == "openai_codex"


class _FallbackProvider(LLMProvider):
    async def chat(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(content="fallback")

    def get_default_model(self) -> str:
        return "fallback-model"


def test_routing_provider_dispatches_codex_directly(monkeypatch) -> None:
    calls = []

    class FakeCodexProvider(LLMProvider):
        def __init__(self, default_model: str):
            super().__init__()
            self.default_model = default_model

        async def chat(self, *args, **kwargs) -> LLMResponse:
            calls.append((self.default_model, kwargs.get("model")))
            return LLMResponse(content="codex")

        def get_default_model(self) -> str:
            return self.default_model

    monkeypatch.setattr(
        "hermitcrab.providers.routing_provider.OpenAICodexProvider",
        FakeCodexProvider,
    )
    provider = RoutingProvider(
        fallback_provider=_FallbackProvider(),
        request_config_resolver=lambda model: {
            "model": "openaiCodex/gpt-5.4-mini",
            "provider_name": "openai_codex",
        },
    )

    import asyncio

    response = asyncio.run(provider.chat(messages=[], model="gpt-5.4"))

    assert response.content == "codex"
    assert calls == [("openaiCodex/gpt-5.4-mini", "openaiCodex/gpt-5.4-mini")]


def test_codex_model_discovery_has_fallbacks_without_local_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    models = AgentLoop._read_local_codex_models()

    assert "gpt-5.4-mini" in models
    assert "gpt-5.3-codex" in models


def test_ollama_named_model_provider_options_override_request_defaults(tmp_path) -> None:
    config = Config.model_validate(
        {
            "root": str(tmp_path),
            "models": {
                "granite4": {
                    "model": "ollama/granite4:350m",
                    "providerOptions": {
                        "num_ctx": 32000,
                        "num_thread": 6,
                        "temperature": 0.05,
                        "max_tokens": 512,
                    },
                }
            },
            "providers": {"ollama": {"apiBase": "http://localhost:11434"}},
        }
    )

    provider = OllamaProvider(
        default_model="ollama/gemma4",
        request_config_resolver=lambda model: {
            "model": config.resolve_model_config(model).model or model,
            "api_base": config.get_api_base(model),
            "provider_name": config.get_provider_name(model),
            "provider_options": config.resolve_model_config(model).provider_options or {},
        },
    )

    body, _, _ = provider._prepare_request(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        model="granite4",
        max_tokens=8192,
        temperature=0.7,
        reasoning_effort=None,
    )

    assert body["model"] == "granite4:350m"
    assert body["options"]["num_ctx"] == 32000
    assert body["options"]["num_thread"] == 6
    assert body["options"]["temperature"] == 0.05
    assert body["options"]["num_predict"] == 512
    assert "max_tokens" not in body["options"]


def test_codex_headers_use_codex_originator() -> None:
    headers = codex_cloudflare_headers("not-a-jwt")

    assert headers["originator"] == "codex_cli_rs"
    assert headers["User-Agent"].startswith("codex_cli_rs/")


def test_named_provider_selector_is_not_sent_to_openrouter() -> None:
    provider = LiteLLMProvider(
        default_model="openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        provider_name="openrouter",
        request_config_resolver=lambda model: {
            "model": "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "provider_name": "openrouter",
            "api_base": "https://openrouter.ai/api/v1",
            "provider_options": {"provider": "openrouter"},
        },
    )

    kwargs, _ = provider._build_completion_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        model="main",
        max_tokens=32,
        temperature=0.0,
        reasoning_effort=None,
    )

    assert kwargs["model"] == "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    assert "provider" not in kwargs
