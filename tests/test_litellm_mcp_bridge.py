"""LiteLLM's real MCP bridge must import with the declared runtime dependencies.

Never skip this because FastAPI is present: that hid a separate MCP major-version
mismatch on fresh installs even though the locked local environment could import.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest


def test_mcp_bridge_imports_without_the_proxy_extra() -> None:
    from litellm.responses.mcp.chat_completions_handler import acompletion_with_mcp

    assert callable(acompletion_with_mcp)


def test_mcp_has_types_required_by_current_litellm() -> None:
    from mcp.types import CacheableResult

    assert CacheableResult is not None


@pytest.mark.parametrize("stream", [False, True])
async def test_real_litellm_tool_request_reaches_http_transport(stream, monkeypatch) -> None:
    from hermitcrab.providers.base import ToolCallEvent
    from hermitcrab.providers.litellm_provider import LiteLLMProvider, _litellm_module

    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("OPENROUTER_API_KEY", "smoke-test-key")
    litellm = _litellm_module()
    monkeypatch.setattr(litellm, "api_base", litellm.api_base)
    requests = []
    tool_call = {
        "id": "call_smoke", "type": "function",
        "function": {"name": "read_memory", "arguments": '{"category":"tasks"}'},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            response = {
                "id": "chatcmpl-smoke", "object": "chat.completion", "created": 0,
                "model": "openrouter/auto",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [tool_call],
                }}],
            }
            if payload.get("stream"):
                response["object"] = "chat.completion.chunk"
                response["choices"] = [{"index": 0, "finish_reason": None, "delta": {
                    "role": "assistant", "tool_calls": [{"index": 0, **tool_call}],
                }}]
                body = f"data: {json.dumps(response)}\n\ndata: [DONE]\n\n".encode()
                content_type = "text/event-stream"
            else:
                body = json.dumps(response).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = LiteLLMProvider(
            api_key="smoke-test-key", api_base=f"http://127.0.0.1:{server.server_port}/v1",
            default_model="openrouter/openrouter/auto", provider_name="openrouter",
        )
        kwargs = {
            "messages": [{"role": "user", "content": "Show my tasks"}],
            "tools": [{"type": "function", "function": {
                "name": "read_memory", "description": "Read stored tasks",
                "parameters": {"type": "object", "properties": {
                    "category": {"type": "string"},
                }},
            }}],
        }
        if stream:
            calls = [event.tool_call async for event in provider.stream_chat(**kwargs)
                     if isinstance(event, ToolCallEvent)]
        else:
            calls = (await provider.chat(**kwargs)).tool_calls
        assert len(calls) == 1
        assert calls[0].name == "read_memory"
        assert calls[0].arguments == {"category": "tasks"}
        assert requests[0]["tools"][0]["function"]["name"] == "read_memory"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
