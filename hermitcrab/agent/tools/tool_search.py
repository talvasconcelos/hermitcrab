"""Tool discovery for deferrable tools whose full schemas are withheld until needed."""

from __future__ import annotations

import json
from typing import Any

from hermitcrab.agent.tools.base import Tool


class ToolSearchTool(Tool):
    """Search deferrable tools by keyword and activate them for the conversation."""

    def __init__(self, registry: Any):
        self._registry = registry

    @property
    def name(self) -> str:
        return "tool_search"

    @staticmethod
    def _one_line(text: str, limit: int = 90) -> str:
        first = text.split(". ")[0].strip()
        return first if len(first) <= limit else first[: limit - 3].rstrip() + "..."

    @property
    def description(self) -> str:
        entries = self._registry.unactivated_deferred()
        if not entries:
            return "No additional tools are available to discover."
        lines = [
            "Discover and activate an additional capability by keyword. "
            "Use this when you need a tool that is not already listed. "
            "Available capabilities:",
        ]
        for entry in entries:
            lines.append(f"- {entry.tool.name}: {self._one_line(entry.tool.description)}")
        lines.append("After a successful search, the matched tool becomes callable in this conversation.")
        return "\n".join(lines)

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing the capability you need (e.g. 'schedule reminder', 'search web')",
                    "minLength": 2,
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Maximum number of tools to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str = "", max_results: int = 5, **kwargs: Any) -> str:
        if not query.strip():
            return "Provide a query describing the capability you need."
        matches = self._registry.search_deferred(query, limit=max_results)
        if not matches:
            return f"No matching tools found for '{query}'."
        for entry in matches:
            self._registry.activate(entry.tool.name)
        lines = [f"Found {len(matches)} tool(s); they are now callable:", ""]
        for entry in matches:
            lines.append(json.dumps(entry.tool.to_schema(), ensure_ascii=False))
        return "\n".join(lines)
