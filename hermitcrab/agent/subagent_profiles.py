"""Named subagent profiles with explicit tool surfaces and execution limits."""

from __future__ import annotations

from dataclasses import dataclass

from hermitcrab.agent.tools.policy import ToolPermissionLevel, ToolPermissionPolicy


@dataclass(frozen=True, slots=True)
class SubagentProfile:
    """A bounded subagent runtime profile."""

    name: str
    description: str
    allowed_tools: tuple[str, ...]
    allowed_permissions: tuple[ToolPermissionLevel, ...]
    max_iterations: int
    guidance: tuple[str, ...] = ()

    def build_policy(self) -> ToolPermissionPolicy:
        return ToolPermissionPolicy(
            actor="subagent",
            profile_name=self.name,
            allowed_permissions=frozenset(self.allowed_permissions),
            allowed_tool_names=frozenset(self.allowed_tools),
            allow_subagent_tools_only=True,
        )


SUBAGENT_PROFILES: dict[str, SubagentProfile] = {
    "research": SubagentProfile(
        name="research",
        description="Read and investigate without making workspace changes.",
        allowed_tools=("read_file", "list_dir", "web_search", "web_fetch"),
        allowed_permissions=(ToolPermissionLevel.READ_ONLY, ToolPermissionLevel.NETWORK),
        max_iterations=10,
        guidance=(
            "Prefer gathering evidence over proposing edits.",
            "Do not claim work was implemented unless files were actually changed.",
        ),
    ),
    "explore": SubagentProfile(
        name="explore",
        description="Inspect the workspace and gather local evidence.",
        allowed_tools=("read_file", "list_dir"),
        allowed_permissions=(ToolPermissionLevel.READ_ONLY,),
        max_iterations=8,
        guidance=("Stay local to the workspace unless the task explicitly requires web research.",),
    ),
    "implementation": SubagentProfile(
        name="implementation",
        description="Implement bounded code or content changes inside the workspace.",
        allowed_tools=("read_file", "write_file", "edit_file", "list_dir", "exec"),
        allowed_permissions=(
            ToolPermissionLevel.READ_ONLY,
            ToolPermissionLevel.WORKSPACE_WRITE,
            ToolPermissionLevel.DANGEROUS_EXEC,
        ),
        max_iterations=15,
        guidance=(
            "Read relevant files before editing them.",
            "Use shell execution only when it materially advances the task.",
        ),
    ),
    "verification": SubagentProfile(
        name="verification",
        description="Run checks, tests, and validation steps with minimal editing.",
        allowed_tools=("read_file", "list_dir", "exec"),
        allowed_permissions=(
            ToolPermissionLevel.READ_ONLY,
            ToolPermissionLevel.DANGEROUS_EXEC,
        ),
        max_iterations=10,
        guidance=(
            "Prefer verification over modification.",
            "If the task requires edits, report that clearly instead of making unplanned changes.",
        ),
    ),
}


DEFAULT_SUBAGENT_PROFILE = "implementation"


def get_subagent_profile(name: str | None) -> SubagentProfile:
    """Resolve a profile name to a concrete profile, falling back safely."""
    if name is None:
        return SUBAGENT_PROFILES[DEFAULT_SUBAGENT_PROFILE]
    normalized = name.strip().lower().replace("-", "_")
    return SUBAGENT_PROFILES.get(normalized, SUBAGENT_PROFILES[DEFAULT_SUBAGENT_PROFILE])


def suggest_subagent_escalation(
    current_profile: str,
    *,
    blocked_tool: str,
    required_permission: str,
    safe_fallback_tool: str | None = None,
) -> dict[str, str] | None:
    """Suggest a bounded next step after a subagent permission denial."""
    if safe_fallback_tool:
        return {
            "action": "continue_read_only",
            "target": safe_fallback_tool,
            "reason": "A safe read-only fallback already ran; coordinator can decide whether that is enough.",
        }

    if required_permission == ToolPermissionLevel.COORDINATOR.value:
        return {
            "action": "escalate_to_main_agent",
            "target": "main_agent",
            "reason": f"`{blocked_tool}` is coordinator-only.",
        }

    if required_permission == ToolPermissionLevel.DANGEROUS_EXEC.value:
        return {
            "action": "escalate_to_main_agent",
            "target": "main_agent",
            "reason": f"`{blocked_tool}` needs dangerous execution authority.",
        }

    if required_permission == ToolPermissionLevel.WORKSPACE_WRITE.value:
        if current_profile != "implementation":
            return {
                "action": "retry_with_profile",
                "target": "implementation",
                "reason": f"`{blocked_tool}` needs workspace write permission.",
            }
        return {
            "action": "escalate_to_main_agent",
            "target": "main_agent",
            "reason": f"`{blocked_tool}` was still blocked under implementation profile.",
        }

    if required_permission == ToolPermissionLevel.NETWORK.value:
        if current_profile in {"explore", "verification"}:
            return {
                "action": "retry_with_profile",
                "target": "research",
                "reason": f"`{blocked_tool}` needs network permission.",
            }
        return {
            "action": "escalate_to_main_agent",
            "target": "main_agent",
            "reason": f"`{blocked_tool}` needs network permission not available in profile `{current_profile}`.",
        }

    if current_profile != "implementation":
        return {
            "action": "escalate_to_main_agent",
            "target": "main_agent",
            "reason": f"`{blocked_tool}` was blocked under profile `{current_profile}`.",
        }
    return None
