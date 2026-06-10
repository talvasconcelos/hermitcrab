from hermitcrab.providers.attribution_headers import merge_provider_headers


def test_openrouter_provider_gets_hermitcrab_attribution_headers() -> None:
    headers = merge_provider_headers(
        provider_name="openrouter",
        api_base="https://openrouter.ai/api/v1",
        configured_headers=None,
    )

    assert headers == {
        "HTTP-Referer": "https://github.com/talvasconcelos/hermitcrab",
        "X-Title": "HermitCrab",
        "X-OpenRouter-Categories": "productivity,agent",
    }


def test_openrouter_detection_by_api_base_covers_custom_provider() -> None:
    headers = merge_provider_headers(
        provider_name="custom",
        api_base="https://openrouter.ai/api/v1",
        configured_headers=None,
    )

    assert headers["X-Title"] == "HermitCrab"


def test_non_openrouter_provider_gets_no_default_headers() -> None:
    headers = merge_provider_headers(
        provider_name="anthropic",
        api_base="https://api.anthropic.com",
        configured_headers=None,
    )

    assert headers is None


def test_configured_headers_override_app_attribution_defaults() -> None:
    headers = merge_provider_headers(
        provider_name="openrouter",
        api_base="https://openrouter.ai/api/v1",
        configured_headers={"X-Title": "My App", "X-Custom": "yes"},
    )

    assert headers["HTTP-Referer"] == "https://github.com/talvasconcelos/hermitcrab"
    assert headers["X-Title"] == "My App"
    assert headers["X-Custom"] == "yes"


def test_custom_provider_passes_extra_headers_to_openai_client(monkeypatch) -> None:
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("hermitcrab.providers.custom_provider.AsyncOpenAI", FakeAsyncOpenAI)

    from hermitcrab.providers.custom_provider import CustomProvider

    CustomProvider(
        api_key="test-key",
        api_base="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini",
        extra_headers={"X-Title": "HermitCrab"},
    )

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["default_headers"]["X-Title"] == "HermitCrab"
    assert "x-session-affinity" in captured["default_headers"]
