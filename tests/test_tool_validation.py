from typing import Any

from hermitcrab.agent.tools.base import Tool
from hermitcrab.agent.tools.policy import ToolMetadata, ToolPermissionLevel, ToolPermissionPolicy
from hermitcrab.agent.tools.registry import ToolRegistry


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


async def test_registry_denies_tool_when_policy_blocks_it() -> None:
    reg = ToolRegistry(
        default_policy=ToolPermissionPolicy(
            actor="subagent",
            allowed_permissions=frozenset({ToolPermissionLevel.READ_ONLY}),
            allowed_tool_names=frozenset({"sample"}),
            allow_subagent_tools_only=True,
            profile_name="explore",
        )
    )
    reg.register(
        SampleTool(),
        metadata=ToolMetadata(
            permission_level=ToolPermissionLevel.COORDINATOR,
            available_to_subagents=False,
        ),
    )

    result = await reg.execute("sample", {"query": "hi", "count": 2})

    assert "not allowed" in result
    assert "reserved for the main agent" in result


def test_registry_filters_definitions_using_policy() -> None:
    reg = ToolRegistry(
        default_policy=ToolPermissionPolicy(
            actor="subagent",
            allowed_permissions=frozenset({ToolPermissionLevel.READ_ONLY}),
            allowed_tool_names=frozenset({"sample"}),
            allow_subagent_tools_only=True,
            profile_name="explore",
        )
    )
    reg.register(
        SampleTool(),
        metadata=ToolMetadata(
            permission_level=ToolPermissionLevel.READ_ONLY,
            available_to_subagents=True,
        ),
    )

    definitions = reg.get_definitions()

    assert len(definitions) == 1
    assert definitions[0]["function"]["name"] == "sample"
