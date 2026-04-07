"""Generic checklist tools for updateable household and personal lists."""

from __future__ import annotations

from typing import Any

from hermitcrab.agent.lists import ListStore
from hermitcrab.agent.tools.base import Tool


class ShowListTool(Tool):
    """Show a stored checklist or find likely matching checklists."""

    def __init__(self, lists: ListStore):
        self.lists = lists

    @property
    def name(self) -> str:
        return "list_show"

    @property
    def description(self) -> str:
        return (
            "Show a generic user list stored in knowledge, such as groceries, car parts, gift ideas, "
            "packing lists, school supplies, or errands. These are stored as updateable checklists. "
            "Use for requests like 'what's still needed', "
            "'show my list', or 'I'm at the supermarket, what do I need?'."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "Exact checklist title when already known.",
                },
                "query": {
                    "type": "string",
                    "description": "Checklist title or search query. Leave empty to browse recent checklists.",
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "Whether to include completed items in the output.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matches to show when the query is ambiguous.",
                    "default": 5,
                },
            },
        }

    async def execute(
        self,
        list_name: str = "",
        query: str = "",
        include_completed: bool = True,
        max_results: int = 5,
        **kwargs: Any,
    ) -> str:
        lookup = list_name.strip() or query.strip()
        if lookup:
            if item := self.lists.find_list(lookup):
                return self.lists.render_list(item, include_completed=include_completed)
            matches = self.lists.search_lists(lookup, max_results=max_results)
        else:
            matches = self.lists.list_lists()[:max_results]

        if not matches:
            return "No matching checklists found."

        lines = [f"Found {len(matches)} checklist(s):", ""]
        for item in matches:
            lines.append(self.lists.render_summary(item))
        return "\n".join(lines)


class AddListItemsTool(Tool):
    """Add items to an existing checklist or create it if missing."""

    def __init__(self, lists: ListStore):
        self.lists = lists

    @property
    def name(self) -> str:
        return "list_add_items"

    @property
    def description(self) -> str:
        return (
            "Add items to a generic checklist in knowledge. Creates the checklist if it does not exist yet. "
            "Use for groceries, gift ideas, BBQ supplies, car parts, packing lists, and similar updateable lists."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "Checklist title, e.g. 'Groceries' or 'Xmas gifts'",
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Items to add to the checklist",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags to help later retrieval",
                },
            },
            "required": ["list_name", "items"],
        }

    async def execute(
        self,
        list_name: str,
        items: list[str],
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        stored, added = self.lists.add_items(list_name, items, tags=tags)
        added_text = ", ".join(added) if added else "no new items"
        return (
            f"Checklist updated: {stored.title}\n"
            f"Path: {stored.file_path}\n"
            f"Added: {added_text}\n"
            f"Remaining: {stored.remaining_count}\n"
            f"Completed: {stored.completed_count}"
        )


class SetListItemStatusTool(Tool):
    """Mark checklist items completed or pending."""

    def __init__(self, lists: ListStore):
        self.lists = lists

    @property
    def name(self) -> str:
        return "list_set_item_status"

    @property
    def description(self) -> str:
        return (
            "Mark checklist items as completed or pending in a generic checklist. Use when the user says they got "
            "something, finished it, still need it, or wants an item unchecked."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "list_name": {"type": "string", "description": "Checklist title"},
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Item names or close matches to update",
                },
                "done": {
                    "type": "boolean",
                    "description": "True to mark completed, false to mark still needed",
                },
            },
            "required": ["list_name", "items", "done"],
        }

    async def execute(self, list_name: str, items: list[str], done: bool, **kwargs: Any) -> str:
        stored, changed, unmatched = self.lists.set_item_status(list_name, items, done=done)
        status_text = "completed" if done else "pending"
        lines = [
            f"Updated checklist: {stored.title}",
            f"Marked {status_text}: {', '.join(changed)}",
            f"Remaining: {stored.remaining_count}",
            f"Completed: {stored.completed_count}",
        ]
        if unmatched:
            lines.append(f"Unmatched: {', '.join(unmatched)}")
        return "\n".join(lines)


class RemoveListItemsTool(Tool):
    """Remove items from a checklist."""

    def __init__(self, lists: ListStore):
        self.lists = lists

    @property
    def name(self) -> str:
        return "list_remove_items"

    @property
    def description(self) -> str:
        return (
            "Remove items from a generic checklist when the user wants them deleted instead of marked done. "
            "Useful for drafts, gift ideas, or anything that should disappear from the list."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "list_name": {"type": "string", "description": "Checklist title"},
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Item names or close matches to remove",
                },
            },
            "required": ["list_name", "items"],
        }

    async def execute(self, list_name: str, items: list[str], **kwargs: Any) -> str:
        stored, removed, unmatched = self.lists.remove_items(list_name, items)
        lines = [
            f"Updated checklist: {stored.title}",
            f"Removed: {', '.join(removed)}",
            f"Remaining: {stored.remaining_count}",
            f"Completed: {stored.completed_count}",
        ]
        if unmatched:
            lines.append(f"Unmatched: {', '.join(unmatched)}")
        return "\n".join(lines)


class DeleteListTool(Tool):
    """Delete a whole checklist when it is no longer needed."""

    def __init__(self, lists: ListStore):
        self.lists = lists

    @property
    def name(self) -> str:
        return "list_delete"

    @property
    def description(self) -> str:
        return (
            "Delete a generic checklist from knowledge when the user explicitly says the whole list is done, "
            "obsolete, or should be removed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "list_name": {"type": "string", "description": "Checklist title"},
            },
            "required": ["list_name"],
        }

    async def execute(self, list_name: str, **kwargs: Any) -> str:
        deleted = self.lists.delete_list(list_name, missing_ok=True)
        if deleted is None:
            return f"Checklist not found: {list_name}"
        return f"Deleted checklist: {deleted.title}\nPath: {deleted.file_path}"
