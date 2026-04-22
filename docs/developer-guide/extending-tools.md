# Extending tools

Add new tools to HermitCrab.

## Tool base class

All tools subclass `Tool` from `agent/tools/base.py`:

```python
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None

class Tool:
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name (snake_case, unique)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""

    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with validated parameters."""
```

## Permission levels

Tools are classified by risk in `agent/tools/policy.py`:

```python
class ToolPermissionLevel:
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    NETWORK = "network"
    DANGEROUS_EXEC = "dangerous_exec"
    COORDINATOR = "coordinator"
```

Choose the least-privileged level that makes sense for your tool.

## Registering a tool

Tools are registered in `agent/tools/registry.py`:

```python
from hermitcrab.agent.tools.registry import ToolRegistry

registry = ToolRegistry()
registry.register(MyNewTool())
```

Registration happens during `AgentLoop` initialization. The registry passes tools to the LLM as available functions.

## Example: adding a simple tool

```python
# hermitcrab/agent/tools/weather.py
from dataclasses import dataclass
from typing import Any
import httpx

from hermitcrab.agent.tools.base import Tool, ToolResult

class WeatherTool(Tool):
    @property
    def name(self) -> str:
        return "get_weather"

    @property
    def description(self) -> str:
        return "Get current weather for a city."

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name"
                }
            },
            "required": ["city"]
        }

    async def execute(self, city: str, **kwargs) -> ToolResult:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://wttr.in/{city}?format=%t+%C"
                )
                return ToolResult(success=True, output=resp.text)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
```

## Tool configuration

Tools can read config at runtime. For example, the web search tool reads the Brave Search API key:

```python
api_key = config.tools.web.search.api_key
```

Configuration is available via the tool's execution context.

## Testing tools

Write focused tests under `tests/`:

```python
# tests/test_weather_tool.py
import pytest
import asyncio

from hermitcrab.agent.tools.weather import WeatherTool

@pytest.mark.asyncio
async def test_weather_tool_success():
    tool = WeatherTool()
    result = await tool.execute(city="Lisbon")
    assert result.success
    assert "Lisbon" in result.output or result.output

@pytest.mark.asyncio
async def test_weather_tool_failure():
    tool = WeatherTool()
    result = await tool.execute(city="")
    assert not result.success
```

## Tool safety guidelines

1. **Validate all input** — use JSON Schema in `parameters()` 
2. **Use least-privilege permission** — `read_only` if the tool only reads
3. **Handle errors gracefully** — return `ToolResult(success=False, ...)` not exceptions
4. **Respect timeouts** — use `httpx.AsyncClient(timeout=...)` for network calls
5. **Don't expose secrets** — don't log or return API keys, tokens, or credentials
6. **Sanitize output** — strip sensitive data from outputs before returning
7. **Be deterministic** — same input should produce same output when possible

## Subagent availability

By default, new tools are available to subagents unless:

- They use `coordinator` permission level
- The subagent profile explicitly filters them out

If your tool should not be available to subagents, classify it as `coordinator`.

## Tool naming conventions

- Use `snake_case` for tool names: `read_file`, `web_search`, `person_profile`
- Names should be verb-noun: `write_fact`, `list_dir`, `knowledge_search`
- Prefix with domain if ambiguous: `knowledge_search` vs `search_memory`

## Complexity hotspots

- **Tool registry** (`agent/tools/registry.py`) — dynamic registration, parameter validation, result formatting
- **Tool policy** (`agent/tools/policy.py`) — permission enforcement, denial hints, subagent filtering
- **Exec tool** (`agent/tools/shell.py`) — risk classification, deny patterns, safety guards

When extending tools, study these files to understand the full contract.
