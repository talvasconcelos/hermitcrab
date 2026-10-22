"""Exercise the MCP SDK interfaces used by real server connections."""

import sys
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import httpx2
import mcp
from mcp import types

from hermitcrab.agent.tools.mcp import connect_mcp_servers
from hermitcrab.agent.tools.registry import ToolRegistry
from hermitcrab.config.schema import MCPServerConfig


async def test_stdio_server_registers_and_executes_a_real_sdk_tool(tmp_path) -> None:
    server = tmp_path / "server.py"
    server.write_text(
        "from mcp.server import MCPServer\n"
        "server = MCPServer('smoke')\n"
        "@server.tool()\n"
        "def echo(value: str) -> str:\n"
        "    return value\n"
        "server.run()\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    config = MCPServerConfig(command=sys.executable, args=[str(server)], tool_timeout=10)

    async with AsyncExitStack() as stack:
        await connect_mcp_servers({"smoke": config}, registry, stack)

        assert registry.has("mcp_smoke_echo")
        assert "value" in registry.get("mcp_smoke_echo").parameters["properties"]
        assert await registry.execute("mcp_smoke_echo", {"value": "SMOKE-OK"}) == "SMOKE-OK"


async def test_http_connection_uses_sdk_client_type_and_two_streams(monkeypatch) -> None:
    from mcp.client import streamable_http

    read, write = object(), object()
    captured = {}

    @asynccontextmanager
    async def transport(url, *, http_client):
        captured["client"] = http_client
        assert isinstance(http_client, httpx2.AsyncClient)
        assert http_client.timeout.read is None
        assert http_client.headers["X-Smoke"] == "yes"
        yield read, write

    class Session:
        def __init__(self, actual_read, actual_write):
            assert actual_read is read and actual_write is write

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[types.Tool(name="echo", input_schema={"type": "object"})])

        async def call_tool(self, name, arguments):
            return types.CallToolResult(content=[types.TextContent(type="text", text="HTTP-OK")])

    monkeypatch.setattr(streamable_http, "streamable_http_client", transport)
    monkeypatch.setattr(mcp, "ClientSession", Session)
    registry = ToolRegistry()
    config = MCPServerConfig(url="https://example.com/mcp", headers={"X-Smoke": "yes"})

    async with AsyncExitStack() as stack:
        await connect_mcp_servers({"http": config}, registry, stack)
        assert await registry.execute("mcp_http_echo", {}) == "HTTP-OK"

    assert captured["client"].is_closed
