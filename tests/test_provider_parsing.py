from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermitcrab.providers.base import LLMProvider, LLMResponse
from hermitcrab.providers.custom_provider import CustomProvider
from hermitcrab.providers.litellm_provider import LiteLLMProvider


class StubProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__(api_key=None, api_base=None)
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7, reasoning_effort=None):
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response

    def get_default_model(self) -> str:
        return "stub"


def test_litellm_sanitize_messages_normalizes_tool_call_ids() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1234567890",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path":"."}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1234567890",
            "name": "list_dir",
            "content": "ok",
        },
    ]

    sanitized = LiteLLMProvider._sanitize_messages(messages)

    tool_call_id = sanitized[0]["tool_calls"][0]["id"]
    assert tool_call_id == sanitized[1]["tool_call_id"]
    assert len(tool_call_id) == 9
    assert tool_call_id.isalnum()


def test_litellm_parse_response_merges_tool_calls_across_choices() -> None:
    provider = LiteLLMProvider(default_model="openai/gpt-4.1")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="", tool_calls=None, reasoning_content=None),
            ),
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(
                                name="list_dir",
                                arguments='{"path":"/tmp"}',
                            ),
                        )
                    ],
                    reasoning_content=None,
                ),
            ),
        ],
        usage=None,
    )

    parsed = provider._parse_response(response, model="openai/gpt-4.1")

    assert parsed.finish_reason == "tool_calls"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "list_dir"
    assert parsed.tool_calls[0].arguments == {"path": "/tmp"}


def test_litellm_parse_response_downgrades_empty_tool_call_finish_reason() -> None:
    provider = LiteLLMProvider(default_model="openai/gpt-4.1")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(content=None, tool_calls=None, reasoning_content=None),
            )
        ],
        usage=None,
    )

    parsed = provider._parse_response(response, model="openai/gpt-4.1")

    assert parsed.finish_reason == "stop"
    assert parsed.tool_calls == []


def test_litellm_parse_response_supports_legacy_function_call() -> None:
    provider = LiteLLMProvider(default_model="openai/gpt-4.1")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=None,
                    function_call=SimpleNamespace(
                        name="read_memory",
                        arguments='{"category":"facts"}',
                    ),
                    reasoning_content=None,
                ),
            )
        ],
        usage=None,
    )

    parsed = provider._parse_response(response, model="openai/gpt-4.1")

    assert parsed.finish_reason == "tool_calls"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "read_memory"
    assert parsed.tool_calls[0].arguments == {"category": "facts"}


def test_litellm_parse_response_supports_dict_shaped_tool_calls() -> None:
    provider = LiteLLMProvider(default_model="openai/gpt-4.1")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {
                                "name": "read_memory",
                                "arguments": '{"category":"facts"}',
                            },
                        }
                    ],
                    reasoning_content=None,
                ),
            )
        ],
        usage=None,
    )

    parsed = provider._parse_response(response, model="openai/gpt-4.1")

    assert parsed.finish_reason == "tool_calls"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "read_memory"
    assert parsed.tool_calls[0].arguments == {"category": "facts"}


@pytest.mark.asyncio
async def test_provider_chat_with_retry_retries_transient_exception() -> None:
    provider = StubProvider(
        [
            RuntimeError("500 Internal Server Error"),
            LLMResponse(content="done"),
        ]
    )

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "done"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_provider_chat_with_retry_retries_transient_error_response() -> None:
    provider = StubProvider(
        [
            LLMResponse(
                content="Error calling LLM: litellm.InternalServerError: 500 Internal Server Error",
                finish_reason="error",
            ),
            LLMResponse(content="done"),
        ]
    )

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "done"
    assert provider.calls == 2




@pytest.mark.asyncio
async def test_custom_provider_passes_reasoning_effort() -> None:
    provider = CustomProvider(api_key="stub", api_base="http://localhost:11434/v1", default_model="kimi")
    provider._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", tool_calls=[]),
                )
            ],
            usage=None,
        )
    )

    await provider.chat(
        messages=[{"role": "user", "content": "hello"}],
        reasoning_effort="high",
    )

    assert provider._client.chat.completions.create.await_args.kwargs["reasoning_effort"] == "high"


def test_custom_provider_supports_legacy_function_call() -> None:
    provider = CustomProvider(api_key="stub", api_base="http://localhost:11434/v1", default_model="kimi")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[],
                    function_call=SimpleNamespace(
                        name="read_memory",
                        arguments='{"category":"facts"}',
                    ),
                    reasoning_content=None,
                ),
            )
        ],
        usage=None,
    )

    parsed = provider._parse(response)

    assert parsed.finish_reason == "tool_calls"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "read_memory"
    assert parsed.tool_calls[0].arguments == {"category": "facts"}


def test_custom_provider_supports_dict_shaped_tool_calls() -> None:
    provider = CustomProvider(api_key="stub", api_base="http://localhost:11434/v1", default_model="kimi")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {
                                "name": "read_memory",
                                "arguments": '{"category":"facts"}',
                            },
                        }
                    ],
                    function_call=None,
                    reasoning_content=None,
                ),
            )
        ],
        usage=None,
    )

    parsed = provider._parse(response)

    assert parsed.finish_reason == "tool_calls"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "read_memory"
    assert parsed.tool_calls[0].arguments == {"category": "facts"}
