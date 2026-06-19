from __future__ import annotations

import pytest

from hermitcrab.providers.openai_codex_provider import (
    _consume_sse,
    _parse_usage,
    _prompt_cache_key,
)


class _FakeSSE:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def test_codex_prompt_cache_key_is_stable_for_same_model() -> None:
    assert _prompt_cache_key("openai-codex/gpt-5.5") == _prompt_cache_key("gpt-5.5")
    assert _prompt_cache_key("openai-codex/gpt-5.5") != _prompt_cache_key("gpt-5.4")


def test_codex_usage_parser_exposes_cached_tokens() -> None:
    usage = _parse_usage(
        {
            "input_tokens": 2006,
            "output_tokens": 300,
            "total_tokens": 2306,
            "input_tokens_details": {"cached_tokens": 1920},
        }
    )

    assert usage == {
        "prompt_tokens": 2006,
        "completion_tokens": 300,
        "total_tokens": 2306,
        "cached_tokens": 1920,
    }


@pytest.mark.asyncio
async def test_codex_sse_completion_returns_usage() -> None:
    response = _FakeSSE(
        [
            'data: {"type":"response.output_text.delta","delta":"done"}',
            "",
            (
                'data: {"type":"response.completed","response":{"status":"completed",'
                '"usage":{"prompt_tokens":1500,"completion_tokens":12,"total_tokens":1512,'
                '"prompt_tokens_details":{"cached_tokens":1024}}}}'
            ),
            "",
        ]
    )

    content, tool_calls, finish_reason, usage = await _consume_sse(response)  # type: ignore[arg-type]

    assert content == "done"
    assert tool_calls == []
    assert finish_reason == "stop"
    assert usage["cached_tokens"] == 1024
