"""Memory tools for typed memory operations."""

from typing import Any

from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.tools.base import Tool


class WriteFactTool(Tool):
    """Tool to write facts to memory."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "write_fact"

    @property
    def description(self) -> str:
        return "Save a long-term fact to memory (user preferences, established truths, project context)"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title for this fact"
                },
                "content": {
                    "type": "string",
                    "description": "The fact content"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization"
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence level (0.0-1.0)"
                },
                "source": {
                    "type": "string",
                    "description": "Source of the fact (e.g., 'user statement', 'web search')"
                }
            },
            "required": ["title", "content"]
        }

    async def execute(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        confidence: float | None = None,
        source: str | None = None,
        **kwargs: Any
    ) -> str:
        try:
            item = self.memory.write_fact(
                title=title,
                content=content,
                tags=tags,
                confidence=confidence,
                source=source,
            )
            return f"Fact saved: {item.title}"
        except Exception as e:
            return f"Error saving fact: {str(e)}"


class WriteDecisionTool(Tool):
    """Tool to write decisions to memory."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "write_decision"

    @property
    def description(self) -> str:
        return "Save a decision to memory (architectural choices, trade-offs, locked decisions)"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title"
                },
                "content": {
                    "type": "string",
                    "description": "Decision content"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags"
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "superseded"],
                    "description": "Decision status"
                },
                "rationale": {
                    "type": "string",
                    "description": "Reasoning behind the decision"
                },
                "supersedes": {
                    "type": "string",
                    "description": "ID of decision this supersedes"
                }
            },
            "required": ["title", "content"]
        }

    async def execute(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        status: str = "active",
        rationale: str | None = None,
        supersedes: str | None = None,
        **kwargs: Any
    ) -> str:
        try:
            item = self.memory.write_decision(
                title=title,
                content=content,
                tags=tags,
                status=status,
                rationale=rationale,
                supersedes=supersedes,
            )
            return f"Decision saved: {item.title}"
        except Exception as e:
            return f"Error saving decision: {str(e)}"


class WriteGoalTool(Tool):
    """Tool to write goals to memory."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "write_goal"

    @property
    def description(self) -> str:
        return "Save a goal to memory (objectives, outcomes the user wants to achieve)"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title"
                },
                "content": {
                    "type": "string",
                    "description": "Goal content"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags"
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "achieved", "abandoned"],
                    "description": "Goal status"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Goal priority"
                }
            },
            "required": ["title", "content"]
        }

    async def execute(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        status: str = "active",
        priority: str | None = None,
        **kwargs: Any
    ) -> str:
        try:
            item = self.memory.write_goal(
                title=title,
                content=content,
                tags=tags,
                status=status,
                priority=priority,
            )
            return f"Goal saved: {item.title}"
        except Exception as e:
            return f"Error saving goal: {str(e)}"


class WriteTaskTool(Tool):
    """Tool to write tasks to memory."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "write_task"

    @property
    def description(self) -> str:
        return "Save a task to memory (action items, todos, things to do)"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title"
                },
                "content": {
                    "type": "string",
                    "description": "Task content"
                },
                "assignee": {
                    "type": "string",
                    "description": "Who is responsible for this task"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags"
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "archived"],
                    "description": "Task status"
                },
                "deadline": {
                    "type": "string",
                    "description": "Task deadline (e.g., '2026-03-01', 'next week')"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Task priority"
                }
            },
            "required": ["title", "content", "assignee"]
        }

    async def execute(
        self,
        title: str,
        content: str,
        assignee: str,
        tags: list[str] | None = None,
        status: str = "pending",
        deadline: str | None = None,
        priority: str | None = None,
        **kwargs: Any
    ) -> str:
        try:
            item = self.memory.write_task(
                title=title,
                content=content,
                assignee=assignee,
                tags=tags,
                status=status,
                deadline=deadline,
                priority=priority,
            )
            return f"Task saved: {item.title} (assigned to {assignee})"
        except Exception as e:
            return f"Error saving task: {str(e)}"


class WriteReflectionTool(Tool):
    """Tool to write reflections to memory."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "write_reflection"

    @property
    def description(self) -> str:
        return "Save a reflection to memory (meta-observations, patterns, insights)"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short descriptive title"
                },
                "content": {
                    "type": "string",
                    "description": "Reflection content"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags"
                },
                "context": {
                    "type": "string",
                    "description": "Context or situation that prompted this reflection"
                }
            },
            "required": ["title", "content"]
        }

    async def execute(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        context: str | None = None,
        **kwargs: Any
    ) -> str:
        try:
            item = self.memory.write_reflection(
                title=title,
                content=content,
                tags=tags,
                context=context,
            )
            return f"Reflection saved: {item.title}"
        except Exception as e:
            return f"Error saving reflection: {str(e)}"
