from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hermitcrab.providers.base import (
    LLMProvider,
    LLMResponse,
    ResponseDoneEvent,
    TextDeltaEvent,
    ToolCallEvent,
)
from hermitcrab.providers.custom_provider import CustomProvider
from hermitcrab.providers.litellm_provider import LiteLLMProvider
from hermitcrab.providers.ollama_provider import OllamaProvider


class StubProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__(api_key=None, api_base=None)
        self.responses = list(responses)
        self.calls = 0

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
        reasoning_effort=None,
    ):
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


def test_litellm_sanitize_messages_serializes_tool_call_arguments() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1234567890",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": {"path": "notes.md", "content": "hello"},
                    },
                }
            ],
        }
    ]

    sanitized = LiteLLMProvider._sanitize_messages(messages)

    assert sanitized[0]["tool_calls"][0]["function"]["arguments"] == (
        '{"path": "notes.md", "content": "hello"}'
    )


def test_litellm_sanitize_messages_repairs_malformed_tool_call_arguments() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1234567890",
                    "type": "function",
                    "function": {
                        "name": "spawn",
                        "arguments": '{"task":"do x","label":"fast","{\\"task":"broken"}',
                    },
                }
            ],
        }
    ]

    sanitized = LiteLLMProvider._sanitize_messages(messages)

    repaired = sanitized[0]["tool_calls"][0]["function"]["arguments"]
    assert repaired.startswith("{")
    assert '"task"' in repaired


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
async def test_litellm_stream_chat_accumulates_partial_tool_calls() -> None:
    provider = LiteLLMProvider(default_model="openai/gpt-4.1")

    class _Stream:
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content="Checking ", tool_calls=None, reasoning_content=None
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_1",
                                function=SimpleNamespace(
                                    name="read_memory",
                                    arguments='{"category"',
                                ),
                            )
                        ],
                        reasoning_content=None,
                    ),
                )
            ],
        ),
        SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13),
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(name=None, arguments=':"facts"}'),
                            )
                        ],
                        reasoning_content="step",
                    ),
                )
            ],
        ),
    ]

    with patch(
        "hermitcrab.providers.litellm_provider.acompletion",
        AsyncMock(return_value=_Stream(chunks)),
    ):
        events = [
            event
            async for event in provider.stream_chat(messages=[{"role": "user", "content": "hello"}])
        ]

    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].delta == "Checking "
    assert isinstance(events[1], ToolCallEvent)
    assert events[1].tool_call.name == "read_memory"
    assert events[1].tool_call.arguments == {"category": "facts"}
    assert isinstance(events[2], ResponseDoneEvent)
    assert events[2].finish_reason == "tool_calls"
    assert events[2].usage == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
    assert events[2].reasoning_content == "step"


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
    provider = CustomProvider(
        api_key="stub", api_base="http://localhost:11434/v1", default_model="kimi"
    )
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


@pytest.mark.asyncio
async def test_litellm_provider_uses_request_specific_config_for_cross_provider_job_models() -> (
    None
):
    provider = LiteLLMProvider(
        api_key="ollama",
        api_base="http://localhost:11434/v1",
        default_model="openai/minimax-m2.5:cloud",
        provider_name="openai",
        request_config_resolver=lambda model: {
            "api_key": "sk-or-test",
            "api_base": "https://openrouter.ai/api/v1",
            "extra_headers": None,
            "provider_name": "openrouter",
        }
        if model == "openrouter/nvidia/nemotron-3-nano-30b-a3b:free"
        else {
            "api_key": "ollama",
            "api_base": "http://localhost:11434/v1",
            "extra_headers": None,
            "provider_name": "openai",
        },
    )

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", tool_calls=None, reasoning_content=None),
            )
        ],
        usage=None,
    )

    with patch(
        "hermitcrab.providers.litellm_provider.acompletion", AsyncMock(return_value=response)
    ) as mock_completion:
        await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="openrouter/nvidia/nemotron-3-nano-30b-a3b:free",
        )

    assert (
        mock_completion.await_args.kwargs["model"]
        == "openrouter/nvidia/nemotron-3-nano-30b-a3b:free"
    )
    assert mock_completion.await_args.kwargs["api_key"] == "sk-or-test"
    assert mock_completion.await_args.kwargs["api_base"] == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_litellm_provider_applies_named_model_provider_options() -> None:
    provider = LiteLLMProvider(
        api_key="ollama",
        api_base="http://localhost:11434/v1",
        default_model="local_coder",
        provider_name="openai",
        request_config_resolver=lambda model: {
            "model": "ollama/qwen2.5-coder:7b",
            "api_key": "ollama",
            "api_base": "http://localhost:11434/v1",
            "extra_headers": None,
            "provider_name": "openai",
            "provider_options": {"num_ctx": 16384},
            "reasoning_effort": "low",
        }
        if model == "local_coder"
        else {
            "model": model,
            "api_key": "ollama",
            "api_base": "http://localhost:11434/v1",
            "extra_headers": None,
            "provider_name": "openai",
            "provider_options": {},
            "reasoning_effort": None,
        },
    )

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", tool_calls=None, reasoning_content=None),
            )
        ],
        usage=None,
    )

    with patch(
        "hermitcrab.providers.litellm_provider.acompletion", AsyncMock(return_value=response)
    ) as mock_completion:
        await provider.chat(messages=[{"role": "user", "content": "hello"}], model="local_coder")

    assert mock_completion.await_args.kwargs["model"] == "ollama/qwen2.5-coder:7b"
    assert mock_completion.await_args.kwargs["num_ctx"] == 16384
    assert mock_completion.await_args.kwargs["reasoning_effort"] == "low"


def test_ollama_provider_normalizes_native_api_base() -> None:
    assert (
        OllamaProvider.normalize_api_base("http://localhost:11434/v1/") == "http://localhost:11434"
    )
    assert (
        OllamaProvider.normalize_api_base("http://localhost:11434///") == "http://localhost:11434"
    )


def test_ollama_provider_preserves_large_integer_tool_arguments() -> None:
    content, tool_calls, finish_reason, usage, reasoning = OllamaProvider.parse_ndjson_events(
        [
            b'{"message":{"tool_calls":[{"function":{"name":"read_memory","arguments":{"chat_id":9223372036854775808}}}]},"done":false}',
            b'{"message":{"content":"ok"},"done":true,"done_reason":"stop","prompt_eval_count":10,"eval_count":2}',
        ]
    )

    assert content == "ok"
    assert finish_reason == "stop"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    assert reasoning is None
    assert tool_calls[0].arguments["chat_id"] == 9223372036854775808
    assert isinstance(tool_calls[0].arguments["chat_id"], int)


@pytest.mark.asyncio
async def test_ollama_provider_formats_timeout_errors() -> None:
    provider = OllamaProvider(
        api_base="http://localhost:11434", default_model="ollama/qwen0.8:latest"
    )

    class _TimeoutStream:
        async def __aenter__(self):
            raise httpx.ReadTimeout("timed out")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _StreamClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return _TimeoutStream()

    with patch(
        "hermitcrab.providers.ollama_provider.httpx.AsyncClient", return_value=_StreamClient()
    ):
        response = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert response.finish_reason == "error"
    assert response.content == (
        "Error calling Ollama: request timed out while waiting for Ollama; "
        "local model loads can take a while"
    )


@pytest.mark.asyncio
async def test_ollama_provider_forwards_provider_options_into_ollama_options() -> None:
    captured_json = {}

    class _Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            yield (
                '{"message":{"content":"ok"},"done":true,"done_reason":"stop",'
                '"prompt_eval_count":1,"eval_count":1}'
            )

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None, json=None):
            captured_json["body"] = json
            return _Response()

    provider = OllamaProvider(
        api_base="http://localhost:11434",
        default_model="fast_local",
        request_config_resolver=lambda model: {
            "model": "ollama/qwen0.8:latest",
            "api_base": "http://localhost:11434",
            "provider_name": "ollama",
            "provider_options": {"num_ctx": 8192, "num_thread": 4},
            "reasoning_effort": None,
        },
    )

    with patch("hermitcrab.providers.ollama_provider.httpx.AsyncClient", _Client):
        response = await provider.chat(
            messages=[{"role": "user", "content": "hello"}], model="fast_local"
        )

    assert response.content == "ok"
    assert captured_json["body"]["options"]["num_ctx"] == 8192
    assert captured_json["body"]["options"]["num_thread"] == 4


def test_custom_provider_supports_legacy_function_call() -> None:
    provider = CustomProvider(
        api_key="stub", api_base="http://localhost:11434/v1", default_model="kimi"
    )

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
    provider = CustomProvider(
        api_key="stub", api_base="http://localhost:11434/v1", default_model="kimi"
    )

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
