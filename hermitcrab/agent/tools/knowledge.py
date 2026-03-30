"""Knowledge base tools for retrieval and ingestion."""

from typing import Any

from hermitcrab.agent.knowledge import KnowledgeStore
from hermitcrab.agent.tools.base import Tool


class KnowledgeSearchTool(Tool):
    """Tool to search the knowledge base."""

    def __init__(self, knowledge: KnowledgeStore):
        self.knowledge = knowledge

    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return (
            "Search the knowledge base for reference material (articles, docs, notes). "
            "Use when the user query requires external reference information or domain knowledge. "
            "Returns ranked results with snippets. Does NOT modify memory."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query - keywords, topics, or concepts to find",
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["articles", "books", "docs", "notes"]},
                    "description": "Limit search to specific categories (optional)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self, query: str, categories: list[str] | None = None, max_results: int = 5, **kwargs: Any
    ) -> str:
        try:
            results = self.knowledge.search(
                query=query,
                categories=categories,
                max_results=max_results,
            )

            if not results:
                return "No relevant knowledge items found."

            # Format results
            output_lines = [f"Found {len(results)} relevant item(s):\n"]

            for i, result in enumerate(results, 1):
                output_lines.append(f"--- Result {i} (score: {result.score:.2f}) ---")
                output_lines.append(f"**{result.item.title}**")
                output_lines.append(f"Type: {result.item.item_type or 'N/A'}")
                if result.item.source:
                    output_lines.append(f"Source: {result.item.source}")
                if result.item.tags:
                    output_lines.append(f"Tags: {', '.join(result.item.tags)}")
                output_lines.append("")
                output_lines.append(f"Match reasons: {', '.join(result.match_reasons)}")
                output_lines.append("")
                # Include truncated content snippet
                snippet = result.item.content[:500]
                if len(result.item.content) > 500:
                    snippet += "..."
                output_lines.append(snippet)
                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error searching knowledge base: {str(e)}"


class KnowledgeIngestTool(Tool):
    """Tool to ingest new content into the knowledge base."""

    def __init__(self, knowledge: KnowledgeStore):
        self.knowledge = knowledge

    @property
    def name(self) -> str:
        return "knowledge_ingest"

    @property
    def description(self) -> str:
        return (
            "Ingest new content into the knowledge base. "
            "Use to save articles, documentation, notes, reference material, reusable checklists, or shopping lists for future retrieval. "
            "Content is stored as markdown files with optional metadata. "
            "This does NOT create memory items - it only adds to the reference library."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title for the knowledge item"},
                "content": {
                    "type": "string",
                    "description": "Content to ingest (summary, notes, or full text)",
                },
                "category": {
                    "type": "string",
                    "enum": ["articles", "books", "docs", "notes"],
                    "description": "Category for organization (default: notes)",
                },
                "item_type": {
                    "type": "string",
                    "description": "Type metadata (e.g., 'article', 'tutorial', 'reference')",
                },
                "source": {"type": "string", "description": "Source URL or reference (optional)"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for organization (optional)",
                },
            },
            "required": ["title", "content"],
        }

    async def execute(
        self,
        title: str,
        content: str,
        category: str = "notes",
        item_type: str = "note",
        source: str = "",
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            item = self.knowledge.ingest(
                content=content,
                title=title,
                category=category,
                item_type=item_type,
                source=source,
                tags=tags,
            )

            if item:
                return f"Knowledge item ingested: `{item.file_path}`\nTitle: {item.title}\nCategory: {category}"
            else:
                return "Failed to ingest knowledge item."

        except Exception as e:
            return f"Error ingesting knowledge item: {str(e)}"


class KnowledgeIngestURLTool(Tool):
    """Tool to ingest content from a URL into the knowledge base."""

    def __init__(self, knowledge: KnowledgeStore):
        self.knowledge = knowledge

    @property
    def name(self) -> str:
        return "knowledge_ingest_url"

    @property
    def description(self) -> str:
        return (
            "Ingest content from a URL into the knowledge base. "
            "Fetches the URL, extracts readable content, and saves it as a knowledge item. "
            "Use for saving articles, documentation pages, or blog posts for future reference. "
            "This is explicit ingestion - not automatic scraping."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to ingest"},
                "category": {
                    "type": "string",
                    "enum": ["articles", "books", "docs", "notes"],
                    "description": "Category for organization (default: articles)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for organization (optional)",
                },
            },
            "required": ["url"],
        }

    async def execute(
        self, url: str, category: str = "articles", tags: list[str] | None = None, **kwargs: Any
    ) -> str:
        try:
            item = self.knowledge.ingest_from_url(
                url=url,
                category=category,
                tags=tags,
            )

            if item:
                return f"URL ingested: `{item.file_path}`\nTitle: {item.title}\nSource: {url}"
            else:
                return "Failed to ingest URL - content could not be fetched or extracted."

        except Exception as e:
            return f"Error ingesting URL: {str(e)}"


class KnowledgeListTool(Tool):
    """Tool to list knowledge items with optional filtering."""

    def __init__(self, knowledge: KnowledgeStore):
        self.knowledge = knowledge

    @property
    def name(self) -> str:
        return "knowledge_list"

    @property
    def description(self) -> str:
        return (
            "List knowledge items in the knowledge base with optional filtering. "
            "Use to browse available reference material or find items by tag/type. "
            "Returns metadata only - use knowledge_search for content retrieval."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["articles", "books", "docs", "notes"],
                    "description": "Filter by category",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (must have all specified tags)",
                },
                "item_type": {"type": "string", "description": "Filter by item type"},
            },
        }

    async def execute(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        item_type: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            items = self.knowledge.list_items(
                category=category,
                tags=tags,
                item_type=item_type,
            )

            if not items:
                filters = []
                if category:
                    filters.append(f"category={category}")
                if tags:
                    filters.append(f"tags={tags}")
                if item_type:
                    filters.append(f"type={item_type}")
                filter_str = f" with {', '.join(filters)}" if filters else ""
                return f"No knowledge items found{filter_str}."

            # Format output
            output_lines = [f"Found {len(items)} knowledge item(s):\n"]

            for item in items:
                output_lines.append(f"**{item.title}**")
                output_lines.append(f"  File: `{item.file_path.name}`")
                output_lines.append(f"  Type: {item.item_type or 'N/A'}")
                if item.source:
                    output_lines.append(f"  Source: {item.source}")
                if item.tags:
                    output_lines.append(f"  Tags: {', '.join(item.tags)}")
                if item.ingested_at:
                    output_lines.append(f"  Ingested: {item.ingested_at.strftime('%Y-%m-%d')}")
                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"Error listing knowledge items: {str(e)}"


class KnowledgeStatsTool(Tool):
    """Tool to get knowledge base statistics."""

    def __init__(self, knowledge: KnowledgeStore):
        self.knowledge = knowledge

    @property
    def name(self) -> str:
        return "knowledge_stats"

    @property
    def description(self) -> str:
        return (
            "Get statistics about the knowledge base - item counts per category and total. "
            "Use to understand the size and scope of the reference library."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        try:
            stats = self.knowledge.get_stats()

            lines = [
                "Knowledge Base Statistics",
                "=" * 25,
                f"Total items: {stats['total']}",
                "",
                "By category:",
            ]

            for category, count in stats["by_category"].items():
                lines.append(f"  {category}: {count}")

            return "\n".join(lines)

        except Exception as e:
            return f"Error getting knowledge stats: {str(e)}"
