"""Session-history search tool."""

from __future__ import annotations

from typing import Any

from hermitcrab.agent.tools.base import Tool
from hermitcrab.session.manager import SessionManager


class SessionSearchTool(Tool):
    """Search recent and archived session transcripts for prior discussions."""

    def __init__(self, sessions: SessionManager):
        self.sessions = sessions

    @property
    def name(self) -> str:
        return "session_search"

    @property
    def description(self) -> str:
        return (
            "Search past conversation transcripts across active and archived sessions. "
            "Use when the user references something discussed earlier and recent chat context is not enough."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Phrase or keywords to search for in past conversations",
                    "minLength": 2,
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Maximum number of matching sessions to return",
                    "default": 3,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, max_results: int = 3, **kwargs: Any) -> str:
        results = self.sessions.search_history(query, max_results=max_results)
        if not results:
            return "No matching past conversations found."

        lines = [f"Found {len(results)} matching session(s):", ""]
        for index, result in enumerate(results, start=1):
            state = "archived" if result["archived"] else "active"
            lines.append(f"--- Session {index} ({state}) ---")
            lines.append(f"Session: {result['session_key']}")
            lines.append(f"Updated: {result['updated_at']}")
            for excerpt in result["excerpts"]:
                lines.append("")
                lines.append(excerpt)
            lines.append("")
        return "\n".join(lines).rstrip()
