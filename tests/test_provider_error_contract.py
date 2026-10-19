"""Providers must raise failures, not return them as message content.

Regression: every provider caught the exception and returned
``LLMResponse(content="Error calling LLM: ...", finish_reason="error")``. Callers parsed that
text as model output, retries were decided by substring-matching the formatted string, and the
user was told the model "kept acknowledging" instead of being shown a configuration failure.
"""

import pytest

from hermitcrab.providers.base import LLMProvider, LLMResponse, ProviderError


class FakeProvider(LLMProvider):
    """Minimal provider whose scripted outcomes are either exceptions or content."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def chat(
        self,
        *,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
        reasoning_effort=None,
    ) -> LLMResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(content=str(outcome))

    def get_default_model(self) -> str:
        return "fake-model"


def _no_backoff(monkeypatch) -> int:
    monkeypatch.setattr(LLMProvider, "_CHAT_RETRY_DELAYS", (0, 0))
    return len(LLMProvider._CHAT_RETRY_DELAYS) + 1


@pytest.mark.asyncio
async def test_transient_failure_is_retried_then_succeeds(monkeypatch) -> None:
    _no_backoff(monkeypatch)
    provider = FakeProvider([ProviderError("429 rate limit", transient=True), "ok"])

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_non_transient_failure_is_raised_on_the_first_call(monkeypatch) -> None:
    _no_backoff(monkeypatch)
    provider = FakeProvider([ProviderError("invalid api key")])

    with pytest.raises(ProviderError) as excinfo:
        await provider.chat_with_retry(messages=[{"role": "user", "content": "hi"}])

    assert excinfo.value.transient is False
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_exhausted_retries_raise_instead_of_returning_error_text(monkeypatch) -> None:
    attempts = _no_backoff(monkeypatch)
    provider = FakeProvider(
        [ProviderError("503 service unavailable", transient=True) for _ in range(attempts)]
    )

    with pytest.raises(ProviderError):
        await provider.chat_with_retry(messages=[{"role": "user", "content": "hi"}])

    assert provider.calls == attempts


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("429 rate limit exceeded", True),
        ("503 service unavailable", True),
        ("Connection reset by peer", True),
        ("Authentication Fails, Your api key: ****test is invalid", False),
        ('authentication_error: {"message": "invalid api key"}', False),
        ("invalid_request_error: bad request", False),
    ],
)
def test_transient_classification_never_retries_configuration_failures(
    message: str, expected: bool
) -> None:
    assert LLMProvider._is_transient_error(message) is expected


@pytest.mark.asyncio
async def test_litellm_provider_raises_instead_of_fabricating_content(monkeypatch) -> None:
    from hermitcrab.providers import litellm_provider as module

    async def boom(**_kwargs):
        raise RuntimeError("invalid api key")

    monkeypatch.setattr(module, "acompletion", boom)
    provider = module.LiteLLMProvider(
        api_key="sk-test",
        default_model="deepseek/deepseek-flash",
        provider_name="deepseek",
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="deepseek/deepseek-flash",
        )

    assert "invalid api key" in str(excinfo.value)
    assert excinfo.value.transient is False
