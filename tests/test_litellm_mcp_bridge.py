"""Guard: LiteLLM's MCP bridge must stay importable without the `proxy` extra.

LiteLLM <=1.92.0 imported ``litellm.proxy.litellm_pre_call_utils`` (which imports fastapi) at
module scope from ``litellm.responses.mcp.litellm_proxy_mcp_handler``, and ``main.py`` imports
that chain whenever ``tools`` is passed. fastapi is declared only under LiteLLM's ``proxy``
extra, so on a plain install *every* tool-calling request failed with
"APIConnectionError: No module named 'fastapi'" — for every provider, streaming and not.

Upstream moved that import into a function in 1.93.0; pyproject floors litellm at the newest
release its 7-day cooldown allows. This test fails if the coupling ever comes back.
"""

import importlib.util

import pytest


def test_mcp_bridge_imports_without_the_proxy_extra() -> None:
    if importlib.util.find_spec("fastapi") is not None:
        pytest.skip("proxy extra installed; the coupling is not observable here")

    from litellm.responses.mcp.chat_completions_handler import acompletion_with_mcp

    assert callable(acompletion_with_mcp)
