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

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "Optionally specify a model name or alias (e.g., 'qwen', 'local', 'claude') "
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
                    "description": "Optional model name or alias (e.g., 'qwen', 'local', 'claude'). "
                    "If not specified, uses the default subagent model.",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, label: str | None = None, model: str | None = None, **kwargs: Any) -> str:
        """Spawn a subagent to execute the given task."""
        return await self._manager.spawn(
            task=task,
            label=label,
            model=model,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
        )
