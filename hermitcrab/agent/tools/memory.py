"""Memory tools for typed memory operations."""

from typing import Any

from loguru import logger

from hermitcrab.agent.memory import MemoryItem, MemoryStore
from hermitcrab.agent.tools.base import Tool

_MAX_MEMORY_ITEM_CHARS = 400


def _truncate(text: str, max_chars: int = _MAX_MEMORY_ITEM_CHARS) -> str:
    """Truncate long memory content for tool results."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...(truncated)"


def _format_memory_items(items: list[MemoryItem], *, limit: int | None = None) -> str:
    """Render memory items into compact text for tool results."""
    if limit is not None:
        items = items[:limit]

    if not items:
        return "No memory items found."

    lines: list[str] = []
    for item in items:
        lines.append(f"[{item.category.value}] {item.title} (id={item.id})")
        if item.content:
            lines.append(_truncate(item.content))
    return "\n".join(lines)


class ReadMemoryTool(Tool):
    """Tool to read memory items from a specific category."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def description(self) -> str:
        return (
            "Read memory items from one category. "
            "Use this when you need user-specific or project-specific long-term context."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": sorted(self.memory.VALID_CATEGORIES),
                    "description": "Memory category to read",
                },
                "id": {
                    "type": "string",
                    "description": "Optional exact memory item ID",
                },
                "query": {
                    "type": "string",
                    "description": "Optional title/content substring filter",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of items to return",
                },
            },
            "required": ["category"],
        }

    async def execute(
        self,
        category: str,
        id: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            logger.info(
                "read_memory start: category={}, id={}, query={}, limit={}",
                category,
                id,
                query,
                limit,
            )
            items = self.memory.read_memory(category=category, id=id, query=query)
            logger.info("read_memory found {} item(s) in category={}", len(items), category)
            return _format_memory_items(items, limit=limit)
        except Exception as e:
            logger.exception("read_memory failed")
            return f"Error reading memory: {str(e)}"


class SearchMemoryTool(Tool):
    """Tool to search memory across categories."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "search_memory"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory across categories. "
            "Use this before answering when historical or user-specific context may matter."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for titles, tags, and content",
                },
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": sorted(self.memory.VALID_CATEGORIES),
                    },
                    "description": "Optional categories to search; omit for all",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of results to return",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        categories: list[str] | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            logger.info(
                "search_memory start: query={}, categories={}, limit={}",
                query,
                categories,
                limit,
            )
            items = self.memory.search_memory(query=query, categories=categories, limit=limit)
            logger.info("search_memory found {} item(s) for query={}", len(items), query)
            return _format_memory_items(items, limit=limit)
        except Exception as e:
            logger.exception("search_memory failed")
            return f"Error searching memory: {str(e)}"


class WriteFactTool(Tool):
    """Tool to write facts to memory."""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    @property
    def name(self) -> str:
        return "write_fact"

    @property
    def description(self) -> str:
        return (
            "Save a long-term fact to memory (user preferences, established truths, project context). "
            "Use wikilinks [[Like This]] to connect related memories when it makes sense "
            "(e.g., 'User prefers [[Python]] for backend development')."
        )

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
                    "description": "The fact content. Use wikilinks [[Like This]] to connect related memories."
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
        return (
            "Save a decision to memory (explicit user-confirmed locked choices only). "
            "Do not use this for assistant recommendations, analysis, reports, or tentative options. "
            "Use wikilinks [[Like This]] to connect to related [[Goals]], [[Facts]], or other [[Decisions]]."
        )

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
                    "description": (
                        "Decision content describing the committed choice. "
                        "Do not store recommendations or reports here. "
                        "Use wikilinks [[Like This]] to connect related memories."
                    )
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
                    "description": "Reasoning behind the committed decision"
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
        return (
            "Save a goal to memory (objectives, outcomes the user wants to achieve). "
            "Use wikilinks [[Like This]] to connect related [[Tasks]], [[Decisions]], or other [[Goals]]."
        )

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
                    "description": "Goal content. Use wikilinks [[Like This]] to connect related memories."
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
        return (
            "Save a task to memory (action items, todos, things to do). "
            "Use wikilinks [[Like This]] to connect to related [[Goals]], [[Decisions]], or other [[Tasks]]. "
            "Example: 'Implement feature X for [[Project Alpha]] to achieve [[Q2 Goals]].'"
        )

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
                    "description": "Task content. Use wikilinks [[Like This]] to connect related memories."
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
                    "enum": ["open", "in_progress", "done", "deferred"],
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
        status: str = "open",
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
        return (
            "Save a reflection to memory (meta-observations, patterns, insights). "
            "Use wikilinks [[Like This]] to connect to related memories, sessions, or concepts."
        )

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
                    "description": "Reflection content. Use wikilinks [[Like This]] to connect related memories."
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
