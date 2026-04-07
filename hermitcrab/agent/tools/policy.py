"""Runtime tool metadata and authorization policy helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ToolPermissionLevel(str, Enum):
    """Coarse capability classes used for runtime authorization."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    NETWORK = "network"
    DANGEROUS_EXEC = "dangerous_exec"
    COORDINATOR = "coordinator"


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    """Static metadata describing one tool's capability and exposure."""

    permission_level: ToolPermissionLevel = ToolPermissionLevel.READ_ONLY
    available_to_subagents: bool = True
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolPermissionPolicy:
    """Policy applied by the registry before a tool executes."""

    actor: str = "main_agent"
    allowed_permissions: frozenset[ToolPermissionLevel] | None = None
    allowed_tool_names: frozenset[str] | None = None
    denied_tool_names: frozenset[str] = field(default_factory=frozenset)
    allow_subagent_tools_only: bool = False
    profile_name: str | None = None

    def check(self, tool_name: str, metadata: ToolMetadata) -> str | None:
        """Return None when allowed, otherwise a short denial reason."""
        if tool_name in self.denied_tool_names:
            return f"'{tool_name}' is disabled for this runtime policy"

        if self.allow_subagent_tools_only and not metadata.available_to_subagents:
            return f"'{tool_name}' is reserved for the main agent"

        if self.allowed_tool_names is not None and tool_name not in self.allowed_tool_names:
            profile = f" profile '{self.profile_name}'" if self.profile_name else " policy"
            return f"'{tool_name}' is not available in subagent{profile}"

        if (
            self.allowed_permissions is not None
            and metadata.permission_level not in self.allowed_permissions
        ):
            return (
                f"'{tool_name}' requires permission '{metadata.permission_level.value}', "
                f"which is not allowed for {self.actor}"
            )

        return None


_DEFAULT_TOOL_METADATA: dict[str, ToolMetadata] = {
    "read_file": ToolMetadata(ToolPermissionLevel.READ_ONLY, True, ("filesystem", "read")),
    "list_dir": ToolMetadata(ToolPermissionLevel.READ_ONLY, True, ("filesystem", "read")),
    "read_memory": ToolMetadata(ToolPermissionLevel.READ_ONLY, True, ("memory", "read")),
    "search_memory": ToolMetadata(ToolPermissionLevel.READ_ONLY, True, ("memory", "read")),
    "session_search": ToolMetadata(ToolPermissionLevel.READ_ONLY, False, ("sessions", "read")),
    "knowledge_search": ToolMetadata(ToolPermissionLevel.READ_ONLY, False, ("knowledge", "read")),
    "knowledge_list": ToolMetadata(ToolPermissionLevel.READ_ONLY, False, ("knowledge", "read")),
    "knowledge_stats": ToolMetadata(ToolPermissionLevel.READ_ONLY, False, ("knowledge", "read")),
    "list_show": ToolMetadata(ToolPermissionLevel.READ_ONLY, False, ("lists", "read")),
    "person_profile": ToolMetadata(
        ToolPermissionLevel.WORKSPACE_WRITE, False, ("people", "write")
    ),
    "reminder": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, False, ("reminders", "write")),
    "write_file": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, True, ("filesystem", "write")),
    "edit_file": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, True, ("filesystem", "write")),
    "write_fact": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, False, ("memory", "write")),
    "write_decision": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, False, ("memory", "write")),
    "write_goal": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, False, ("memory", "write")),
    "write_task": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, False, ("memory", "write")),
    "write_reflection": ToolMetadata(
        ToolPermissionLevel.WORKSPACE_WRITE, False, ("memory", "write")
    ),
    "knowledge_ingest": ToolMetadata(
        ToolPermissionLevel.WORKSPACE_WRITE, False, ("knowledge", "write")
    ),
    "knowledge_ingest_url": ToolMetadata(
        ToolPermissionLevel.NETWORK, False, ("knowledge", "write", "network")
    ),
    "list_add_items": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, False, ("lists", "write")),
    "list_set_item_status": ToolMetadata(
        ToolPermissionLevel.WORKSPACE_WRITE, False, ("lists", "write")
    ),
    "list_remove_items": ToolMetadata(
        ToolPermissionLevel.WORKSPACE_WRITE, False, ("lists", "write")
    ),
    "list_delete": ToolMetadata(ToolPermissionLevel.WORKSPACE_WRITE, False, ("lists", "write")),
    "web_search": ToolMetadata(ToolPermissionLevel.NETWORK, True, ("web", "network")),
    "web_fetch": ToolMetadata(ToolPermissionLevel.NETWORK, True, ("web", "network")),
    "exec": ToolMetadata(ToolPermissionLevel.DANGEROUS_EXEC, True, ("shell", "exec")),
    "message": ToolMetadata(ToolPermissionLevel.COORDINATOR, False, ("coordination",)),
    "spawn": ToolMetadata(ToolPermissionLevel.COORDINATOR, False, ("coordination",)),
    "cron": ToolMetadata(ToolPermissionLevel.COORDINATOR, False, ("coordination",)),
}


def get_default_tool_metadata(tool_name: str) -> ToolMetadata:
    """Return default metadata for a tool name."""
    if tool_name.startswith("mcp_"):
        return ToolMetadata(ToolPermissionLevel.NETWORK, False, ("mcp", "network"))
    return _DEFAULT_TOOL_METADATA.get(
        tool_name,
        ToolMetadata(ToolPermissionLevel.READ_ONLY, False),
    )


def build_main_agent_policy() -> ToolPermissionPolicy:
    """Main agent policy: allow all registered tools unless explicitly denied."""
    return ToolPermissionPolicy(actor="main_agent")
