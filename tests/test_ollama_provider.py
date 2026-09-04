from hermitcrab.providers.ollama_provider import OllamaProvider


def _prepare(reasoning_effort: str | None) -> dict:
    provider = OllamaProvider()
    body, _headers, _meta = provider._prepare_request(
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        model="qwen3.8-4b:latest",
        max_tokens=100,
        temperature=0.1,
        reasoning_effort=reasoning_effort,
    )
    return body


def test_ollama_prepare_request_disables_thinking_when_reasoning_none() -> None:
    assert _prepare("none")["think"] is False


def test_ollama_prepare_request_maps_reasoning_levels_to_think_strings() -> None:
    assert _prepare("low")["think"] == "low"
    assert _prepare("medium")["think"] == "medium"
    assert _prepare("high")["think"] == "high"


def test_ollama_prepare_request_omits_think_when_no_reasoning_effort() -> None:
    assert "think" not in _prepare(None)
