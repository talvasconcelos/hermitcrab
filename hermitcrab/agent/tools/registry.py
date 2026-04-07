"""Tool registry for dynamic tool management and runtime authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from hermitcrab.agent.tool_call_recovery import normalize_tool_argument_values
from hermitcrab.agent.tools.base import Tool
from hermitcrab.agent.tools.policy import ToolMetadata, ToolPermissionPolicy


@dataclass(slots=True)
class RegisteredTool:
    """One registered tool plus its static metadata."""

    tool: Tool
    metadata: ToolMetadata


class ToolRegistry:
    """Registry for tools plus the policy used to expose and execute them."""

    def __init__(self, default_policy: ToolPermissionPolicy | None = None):
        self._tools: dict[str, RegisteredTool] = {}
        self._default_policy = default_policy

    def register(self, tool: Tool, metadata: ToolMetadata | None = None) -> None:
        """Register a tool and its metadata."""
        self._tools[tool.name] = RegisteredTool(tool=tool, metadata=metadata or tool.metadata)

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        entry = self._tools.get(name)
        return entry.tool if entry else None

    def get_metadata(self, name: str) -> ToolMetadata | None:
        """Get tool metadata by name."""
        entry = self._tools.get(name)
        return entry.metadata if entry else None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def tool_names(self, policy: ToolPermissionPolicy | None = None) -> list[str]:
        """Return registered tool names, optionally filtered by policy."""
        effective_policy = policy or self._default_policy
        names: list[str] = []
        for name, entry in self._tools.items():
            if self._check_policy(name, entry.metadata, effective_policy) is None:
                names.append(name)
        return names

    def get_definitions(self, policy: ToolPermissionPolicy | None = None) -> list[dict[str, Any]]:
        """Get tool definitions in OpenAI format, filtered by policy when provided."""
        effective_policy = policy or self._default_policy
        definitions: list[dict[str, Any]] = []
        for name, entry in self._tools.items():
            if self._check_policy(name, entry.metadata, effective_policy) is None:
                definitions.append(entry.tool.to_schema())
        return definitions

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        policy: ToolPermissionPolicy | None = None,
    ) -> str:
        """Execute a tool by name with validation and policy enforcement."""
        hint = "\n\n[Analyze the error above and try a different approach.]"

        entry = self._tools.get(name)
        if not entry:
            available = ", ".join(self.tool_names(policy))
            return (
                f"Error: Tool '{name}' not found. Available: {available}"
                if available
                else (f"Error: Tool '{name}' not found.")
            )

        denial = self._check_policy(name, entry.metadata, policy or self._default_policy)
        if denial is not None:
            logger.warning("Tool policy denied '{}' ({})", name, denial)
            return f"Error: Tool '{name}' is not allowed: {denial}" + hint

        try:
            logger.info(
                "Tool registry executing '{}' with params keys={}", name, sorted(params.keys())
            )
            params = self._normalize_params(entry.tool, params)
            errors = entry.tool.validate_params(params)
            if errors:
                logger.warning("Tool validation failed for '{}': {}", name, errors)
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + hint
            result = await entry.tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                logger.warning("Tool '{}' returned error result: {}", name, result[:200])
                return result + hint
            logger.info("Tool registry completed '{}'", name)
            return result
        except Exception as exc:
            logger.exception("Tool registry raised while executing '{}'", name)
            return f"Error executing {name}: {exc}" + hint

    @staticmethod
    def _check_policy(
        name: str,
        metadata: ToolMetadata,
        policy: ToolPermissionPolicy | None,
    ) -> str | None:
        if policy is None:
            return None
        return policy.check(name, metadata)

    @classmethod
    def _normalize_params(cls, tool: Tool, params: dict[str, Any]) -> dict[str, Any]:
        schema = tool.parameters or {}
        properties = schema.get("properties", {})
        normalized: dict[str, Any] = {}
        for key, value in params.items():
            item_schema = properties.get(key, {})
            normalized[key] = cls._coerce_value(normalize_tool_argument_values(value), item_schema)
        return normalized

    @classmethod
    def _coerce_value(cls, value: Any, schema: dict[str, Any]) -> Any:
        expected = schema.get("type")
        if isinstance(expected, list):
            for option in expected:
                coerced = cls._coerce_value(value, {**schema, "type": option})
                if cls._matches_type(coerced, option):
                    return coerced
            return value

        if expected == "integer" and isinstance(value, str):
            return cls._coerce_number(value, integer_only=True)
        if expected == "number" and isinstance(value, str):
            return cls._coerce_number(value, integer_only=False)
        if expected == "boolean" and isinstance(value, str):
            return cls._coerce_boolean(value)
        if expected == "array":
            item_schema = schema.get("items", {})
            if isinstance(value, str):
                return [cls._coerce_value(value, item_schema)]
            if isinstance(value, list):
                return [cls._coerce_value(item, item_schema) for item in value]
        if expected == "object" and isinstance(value, dict):
            properties = schema.get("properties", {})
            return {
                key: cls._coerce_value(item, properties.get(key, {}))
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _matches_type(value: Any, expected: str) -> bool:
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "array":
            return isinstance(value, list)
        if expected == "object":
            return isinstance(value, dict)
        if expected == "string":
            return isinstance(value, str)
        return False

    @staticmethod
    def _coerce_number(value: str, *, integer_only: bool) -> Any:
        text = value.strip()
        if not text:
            return value
        try:
            parsed = float(text)
        except ValueError:
            return value
        if integer_only:
            return int(parsed) if parsed.is_integer() else value
        return int(parsed) if parsed.is_integer() else parsed

    @staticmethod
    def _coerce_boolean(value: str) -> Any:
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return value

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
