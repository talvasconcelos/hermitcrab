"""Spawn tool for creating background subagents."""

from typing import TYPE_CHECKING, Any

from hermitcrab.agent.tools.base import Tool

if TYPE_CHECKING:
    from hermitcrab.agent.subagent import SubagentManager


class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._brief: str | None = None

    def set_context(self, channel: str, chat_id: str, brief: str | None = None) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._brief = brief

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "Optionally specify a configured model name or shorthand alias (e.g., 'coder', 'local', 'claude') "
            "to use a specific model for this task."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
                "model": {
                    "type": "string",
                    "description": "Optional configured model name or shorthand alias "
                    "(e.g., 'coder', 'local', 'claude'). "
                    "If not specified, uses the default subagent model.",
                },
                "profile": {
                    "type": "string",
                    "description": "Optional subagent profile controlling allowed tools and "
                    "execution style. Use values such as 'implementation', 'verification', "
                    "'research', or 'explore'.",
                },
            },
            "required": ["task"],
        }

    async def execute(
        self,
        task: str,
        label: str | None = None,
        model: str | None = None,
        profile: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        return await self._manager.spawn(
            task=task,
            label=label,
            model=model,
            profile=profile,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            brief=self._brief,
        )
