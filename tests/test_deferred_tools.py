from hermitcrab.agent.tools.base import Tool
from hermitcrab.agent.tools.policy import ToolMetadata, ToolPermissionLevel
from hermitcrab.agent.tools.registry import ToolRegistry
from hermitcrab.agent.tools.tool_search import ToolSearchTool


class _FakeTool(Tool):
    def __init__(self, name: str, description: str):
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        return "ok"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        _FakeTool("core_read", "Read a file."),
        ToolMetadata(ToolPermissionLevel.READ_ONLY, True, ("core",)),
    )
    reg.register(
        _FakeTool("deferred_web", "Search the web for current information."),
        ToolMetadata(ToolPermissionLevel.NETWORK, True, ("web",), deferrable=True),
    )
    reg.register(ToolSearchTool(reg))
    return reg


def _names(definitions: list[dict]) -> set[str]:
    return {d["function"]["name"] for d in definitions}


def test_deferred_tool_schema_withheld_until_activated() -> None:
    names = _names(_registry().get_definitions())
    assert "core_read" in names
    assert "tool_search" in names
    assert "deferred_web" not in names


def test_search_deferred_ranks_by_keyword_match() -> None:
    reg = _registry()
    matches = reg.search_deferred("web search", limit=5)
    assert [m.tool.name for m in matches] == ["deferred_web"]


def test_activate_exposes_deferred_schema() -> None:
    reg = _registry()
    reg.activate("deferred_web")
    assert "deferred_web" in _names(reg.get_definitions())


def test_tool_search_execute_returns_schema_and_activates() -> None:
    import asyncio

    reg = _registry()
    output = asyncio.run(ToolSearchTool(reg).execute(query="web"))
    assert "deferred_web" in output
    assert "deferred_web" in _names(reg.get_definitions())


def test_tool_search_no_match_is_honest() -> None:
    import asyncio

    reg = _registry()
    output = asyncio.run(ToolSearchTool(reg).execute(query="zzzznothing"))
    assert "No matching" in output
