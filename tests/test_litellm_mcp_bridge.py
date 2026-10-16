"""Tool-calling requests must not drag LiteLLM's fastapi-dependent MCP bridge in.

Regression: LiteLLM 1.92 imports ``litellm.responses.mcp.chat_completions_handler`` whenever
``tools`` is passed, which imports ``litellm/proxy/litellm_pre_call_utils.py`` →
``from fastapi import HTTPException, Request``. ``fastapi`` is declared only under LiteLLM's
``proxy`` extra, so on a plain install *every* tool-calling request failed with
"APIConnectionError: No module named 'fastapi'" — misclassified as transient, retried, and
finally reported as the model "acknowledging without doing the work".
"""

import inspect

import litellm
import pytest

from hermitcrab.providers.litellm_provider import LiteLLMProvider

_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Return the current time.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def _provider() -> LiteLLMProvider:
    return LiteLLMProvider(
        api_key="sk-test",
        default_model="deepseek-flash",
        provider_name="deepseek",
    )


def _build(tools: list[dict] | None) -> dict:
    kwargs, _ = _provider()._build_completion_kwargs(
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="deepseek-flash",
        max_tokens=16,
        temperature=0.0,
        reasoning_effort=None,
    )
    return kwargs


def test_tool_requests_skip_litellm_mcp_bridge() -> None:
    kwargs = _build([_TOOL])
    assert kwargs["tools"] == [_TOOL]
    assert kwargs["_skip_mcp_handler"] is True


def test_plain_requests_do_not_set_the_flag() -> None:
    assert "_skip_mcp_handler" not in _build(tools=None)


def test_litellm_still_accepts_the_mcp_skip_escape_hatch() -> None:
    """We depend on a private LiteLLM kwarg; fail loudly if upstream renames it."""
    assert "_skip_mcp_handler" in inspect.getsource(litellm.main)


def test_mcp_bridge_module_is_the_one_that_needs_fastapi() -> None:
    """Document the upstream coupling: this import is what fails without fastapi."""
    with pytest.raises(ModuleNotFoundError, match="fastapi"):
        __import__("litellm.responses.mcp.chat_completions_handler")
