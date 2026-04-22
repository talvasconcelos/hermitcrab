"""Tool registry for dynamic tool management and runtime authorization."""

from __future__ import annotations

from dataclasses import asdict, dataclass
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


@dataclass(slots=True)
class PolicyDenialHint:
    """Structured hint emitted when runtime policy blocks a tool."""

    tool_name: str
    permission_level: str
    denial: str
    actor: str
    profile_name: str | None
    allowed_permissions: tuple[str, ...]
    alternative_tools: tuple[str, ...]
    safe_fallback_tool: str | None = None


class ToolRegistry:
    """Registry for tools plus the policy used to expose and execute them."""

    def __init__(
        self,
        default_policy: ToolPermissionPolicy | None = None,
        audit_event: Any | None = None,
    ):
        self._tools: dict[str, RegisteredTool] = {}
        self._default_policy = default_policy
        self._audit_event = audit_event
        self._last_policy_hint: PolicyDenialHint | None = None

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
            self._last_policy_hint = None
            logger.warning("Tool policy denied '{}' ({})", name, denial)
            if callable(self._audit_event):
                self._audit_event(
                    "tool.policy_denied",
                    tool_name=name,
                    permission_level=entry.metadata.permission_level.value,
                    denial=denial,
                )
            redirect_result = await self._execute_policy_redirect(
                denied_name=name,
                denied_metadata=entry.metadata,
                denied_params=params,
                policy=policy or self._default_policy,
            )
            self._last_policy_hint = self._build_policy_hint(
                name=name,
                metadata=entry.metadata,
                denial=denial,
                policy=policy or self._default_policy,
                redirect_result=redirect_result,
            )
            return self._format_policy_denial(
                name=name,
                metadata=entry.metadata,
                denial=denial,
                policy=policy or self._default_policy,
                redirect_result=redirect_result,
            ) + hint

        self._last_policy_hint = None

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

    def consume_policy_hint(self) -> dict[str, Any] | None:
        """Return and clear the last structured policy-denial hint."""
        hint = self._last_policy_hint
        self._last_policy_hint = None
        if hint is None:
            return None
        return asdict(hint)

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

    def _format_policy_denial(
        self,
        *,
        name: str,
        metadata: ToolMetadata,
        denial: str,
        policy: ToolPermissionPolicy | None,
        redirect_result: tuple[str, str] | None,
    ) -> str:
        lines = [f"Error: Tool '{name}' is blocked by runtime policy.", f"Reason: {denial}"]

        if policy is not None and policy.allowed_permissions:
            allowed_permissions = ", ".join(
                sorted(level.value for level in policy.allowed_permissions)
            )
            lines.append(f"Allowed permissions here: {allowed_permissions}.")

        alternatives = self._policy_alternatives(name, metadata, policy)
        if alternatives:
            lines.append(f"Try instead: {', '.join(alternatives)}.")

        if redirect_result is not None:
            redirect_name, redirect_output = redirect_result
            lines.append(f"Safe fallback used: `{redirect_name}`.")
            lines.append(
                self._format_policy_redirect_guidance(
                    denied_name=name,
                    redirect_name=redirect_name,
                )
            )
            lines.append(redirect_output)
        else:
            lines.append(
                self._format_permission_guidance(
                    permission_level=metadata.permission_level.value,
                )
            )

        return "\n".join(lines)

    @staticmethod
    def _format_policy_redirect_guidance(*, denied_name: str, redirect_name: str) -> str:
        if redirect_name == "read_file" and denied_name in {"write_file", "edit_file"}:
            return "Next safe step: inspect the current file content above, then ask a higher-permission agent to apply the edit."
        if redirect_name == "list_show" and denied_name.startswith("list_"):
            return "Next safe step: inspect the current list state above, then ask a higher-permission agent to make the change."
        return "Next safe step: use the safe fallback output above to decide the next allowed action."

    @staticmethod
    def _format_permission_guidance(*, permission_level: str) -> str:
        if permission_level == "workspace_write":
            return "Next safe step: gather the needed read-only context first, then hand the write back to a higher-permission agent."
        if permission_level == "dangerous_exec":
            return "Next safe step: ask for explicit approval before retrying the destructive action."
        if permission_level == "network":
            return "Next safe step: continue with local/read-only work or switch to a profile that allows network access."
        if permission_level == "coordinator":
            return "Next safe step: continue within the current profile or hand coordination back to the main agent."
        return "Next safe step: use one of the allowed tools above or hand the blocked action back to a higher-permission agent."

    def _build_policy_hint(
        self,
        *,
        name: str,
        metadata: ToolMetadata,
        denial: str,
        policy: ToolPermissionPolicy | None,
        redirect_result: tuple[str, str] | None,
    ) -> PolicyDenialHint:
        return PolicyDenialHint(
            tool_name=name,
            permission_level=metadata.permission_level.value,
            denial=denial,
            actor=policy.actor if policy is not None else "unknown",
            profile_name=policy.profile_name if policy is not None else None,
            allowed_permissions=tuple(
                sorted(level.value for level in policy.allowed_permissions)
            )
            if policy is not None and policy.allowed_permissions
            else (),
            alternative_tools=tuple(self._policy_alternatives(name, metadata, policy)),
            safe_fallback_tool=redirect_result[0] if redirect_result is not None else None,
        )

    def _policy_alternatives(
        self,
        denied_name: str,
        denied_metadata: ToolMetadata,
        policy: ToolPermissionPolicy | None,
    ) -> list[str]:
        alternatives: list[str] = []
        for name, entry in self._tools.items():
            if name == denied_name:
                continue
            if self._check_policy(name, entry.metadata, policy) is not None:
                continue
            if denied_metadata.tags and set(entry.metadata.tags).intersection(denied_metadata.tags):
                alternatives.append(name)

        if alternatives:
            return sorted(alternatives)[:4]

        allowed = sorted(self.tool_names(policy))
        return allowed[:4]

    async def _execute_policy_redirect(
        self,
        *,
        denied_name: str,
        denied_metadata: ToolMetadata,
        denied_params: dict[str, Any],
        policy: ToolPermissionPolicy | None,
    ) -> tuple[str, str] | None:
        redirect = self._build_policy_redirect(denied_name, denied_params)
        if redirect is None:
            return None

        fallback_name, fallback_params = redirect
        fallback_entry = self._tools.get(fallback_name)
        if fallback_entry is None:
            return None
        if self._check_policy(fallback_name, fallback_entry.metadata, policy) is not None:
            return None

        try:
            normalized = self._normalize_params(fallback_entry.tool, fallback_params)
            errors = fallback_entry.tool.validate_params(normalized)
            if errors:
                return None
            result = await fallback_entry.tool.execute(**normalized)
        except Exception:
            logger.exception(
                "Policy redirect from '{}' to '{}' failed",
                denied_name,
                fallback_name,
            )
            return None

        if self._is_error_result(result):
            return None
        logger.info("Policy redirect ran '{}' instead of denied '{}'", fallback_name, denied_name)
        if callable(self._audit_event):
            self._audit_event(
                "tool.policy_redirected",
                tool_name=denied_name,
                redirect_tool_name=fallback_name,
                permission_level=denied_metadata.permission_level.value,
            )
        return fallback_name, result

    @staticmethod
    def _build_policy_redirect(
        denied_name: str,
        denied_params: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        if denied_name in {"write_file", "edit_file"}:
            path = denied_params.get("path")
            if isinstance(path, str) and path.strip():
                return "read_file", {"path": path}

        if denied_name in {"list_add_items", "list_set_item_status", "list_remove_items", "list_delete"}:
            list_name = denied_params.get("list_name")
            if isinstance(list_name, str) and list_name.strip():
                return "list_show", {"list_name": list_name, "include_completed": True}

        return None

    @staticmethod
    def _is_error_result(result: Any) -> bool:
        return isinstance(result, str) and result.strip().lower().startswith("error")

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
