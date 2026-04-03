"""
Knowledge Base layer for HermitCrab.

The knowledge base is a searchable reference library for external information:
- Articles, books, documentation, notes
- Human-editable, Obsidian-compatible (Markdown + wikilinks)
- Semi-structured (optional YAML frontmatter)
- NOT auto-distilled into memory
- NOT auto-loaded into context
- Only used when retrieval is explicitly triggered

Philosophical boundaries:
- Knowledge = Library (reference material)
- Memory = Identity (authoritative truths)
- Journal = History (narrative logs)
- Scratchpad = Working Memory (transient)

These layers must remain distinct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
from loguru import logger

from hermitcrab.utils.helpers import ensure_dir, safe_filename


@dataclass
class KnowledgeItem:
    """
    Represents a knowledge item (file) in the knowledge base.

    Unlike MemoryItem, this is semi-structured:
    - No enforced schema beyond basic metadata
    - Content can be arbitrary length/structure
    - Tags and metadata are optional
    """

    file_path: Path
    title: str
    content: str
    item_type: str = ""  # article, book, doc, note, etc.
    source: str = ""
    tags: list[str] = field(default_factory=list)
    ingested_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, file_path: Path) -> "KnowledgeItem | None":
        """
        Load a knowledge item from a markdown file.

        Args:
            file_path: Path to the markdown file.

        Returns:
            KnowledgeItem or None if file cannot be parsed.
        """
        try:
            if not file_path.exists() or not file_path.is_file():
                return None

            if file_path.suffix.lower() not in (".md", ".markdown"):
                return None

            post = frontmatter.load(file_path)
            content = post.content or ""
            meta = post.metadata or {}

            return cls(
                file_path=file_path,
                title=meta.get("title", file_path.stem),
                content=content,
                item_type=meta.get("type", ""),
                source=meta.get("source", ""),
                tags=meta.get("tags", []),
                ingested_at=cls._parse_date(meta.get("ingested_at")),
                metadata=meta,
            )
        except Exception as e:
            logger.warning(f"Failed to load knowledge file {file_path}: {e}")
            return None

    @staticmethod
    def _parse_date(value: Any) -> datetime | None:
        """Parse date from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None

    def to_context_snippet(self, max_chars: int = 2000) -> str:
        """
        Convert to a context snippet for LLM injection.

        Args:
            max_chars: Maximum characters to include.

        Returns:
            Formatted snippet with metadata and truncated content.
        """
        lines = [f"# {self.title}"]

        if self.item_type:
            lines.append(f"**Type:** {self.item_type}")
        if self.source:
            lines.append(f"**Source:** {self.source}")
        if self.tags:
            lines.append(f"**Tags:** {', '.join(self.tags)}")
        if self.ingested_at:
            lines.append(f"**Ingested:** {self.ingested_at.strftime('%Y-%m-%d')}")

        lines.append("")
        lines.append("---")
        lines.append("")

        content = self.content.strip()
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n[...truncated...]"

        lines.append(content)
        return "\n".join(lines)


@dataclass
class SearchResult:
    """Result from a knowledge search."""

    item: KnowledgeItem
    score: float
    match_reasons: list[str] = field(default_factory=list)


class KnowledgeStore:
    """
    Knowledge base store with search capabilities.

    The knowledge base is organized as:
    workspace/knowledge/
    ├── articles/
    ├── books/
    ├── docs/
    └── notes/

    Search is lightweight and file-based:
    - Filename matching
    - Tag matching from YAML frontmatter
    - Keyword search in content
    - Optional lightweight semantic matching (future)

    Knowledge is NEVER:
    - Auto-distilled into memory
    - Auto-loaded into context
    - Treated as authoritative
    - Scanned by reflection
    """

    DEFAULT_CATEGORIES = ["articles", "books", "docs", "notes"]

    def __init__(self, workspace: Path):
        """
        Initialize knowledge store.

        Args:
            workspace: Path to workspace directory.
        """
        self.workspace = workspace
        self.knowledge_dir = ensure_dir(workspace / "knowledge")

        # Create default category directories
        self.category_dirs: dict[str, Path] = {}
        for category in self.DEFAULT_CATEGORIES:
            self.category_dirs[category] = ensure_dir(self.knowledge_dir / category)

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize text for stable duplicate checks."""
        return " ".join(value.casefold().split())

    def _find_existing_by_title(self, title: str, category: str) -> list[KnowledgeItem]:
        """Find existing knowledge items with the same normalized title in a category."""
        normalized_title = self._normalize_text(title)
        category_path = self.category_dirs.get(category)
        if not category_path:
            return []

        matches: list[KnowledgeItem] = []
        for file_path in sorted(category_path.rglob("*.md")):
            item = KnowledgeItem.from_file(file_path)
            if not item:
                continue
            if self._normalize_text(item.title) == normalized_title:
                matches.append(item)
        return matches

    def _cleanup_duplicate_title_items(
        self, canonical: KnowledgeItem, candidates: list[KnowledgeItem]
    ) -> None:
        """Remove exact duplicate files that share the same normalized title and content."""
        canonical_content = self._normalize_text(canonical.content)
        for item in candidates:
            if item.file_path == canonical.file_path:
                continue
            if self._normalize_text(item.content) != canonical_content:
                continue
            try:
                item.file_path.unlink()
                logger.info("Removed duplicate knowledge item: {}", item.file_path)
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("Failed to remove duplicate knowledge item {}: {}", item.file_path, exc)

    def get_item(self, file_path: Path | str) -> KnowledgeItem | None:
        """
        Load a specific knowledge item by path.

        Args:
            file_path: Path to the knowledge file.

        Returns:
            KnowledgeItem or None if not found.
        """
        if isinstance(file_path, str):
            file_path = Path(file_path)

        # Ensure path is within knowledge directory
        try:
            file_path.resolve().relative_to(self.knowledge_dir.resolve())
        except ValueError:
            logger.warning(f"Attempted to access file outside knowledge directory: {file_path}")
            return None

        return KnowledgeItem.from_file(file_path)

    def search(
        self,
        query: str,
        categories: list[str] | None = None,
        max_results: int = 5,
        min_score: float = 0.1,
    ) -> list[SearchResult]:
        """
        Search the knowledge base.

        Search is explicit and conditional:
        - Only searches when agent decides it needs domain knowledge
        - Never pre-loads or indexes automatically
        - Returns ranked results with match reasons

        Args:
            query: Search query string.
            categories: Limit search to specific categories (default: all).
            max_results: Maximum number of results to return.
            min_score: Minimum score threshold.

        Returns:
            List of SearchResult objects, ranked by relevance.
        """
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        results: list[SearchResult] = []

        # Determine which categories to search
        search_categories = categories or self.DEFAULT_CATEGORIES

        for category in search_categories:
            category_path = self.category_dirs.get(category)
            if not category_path or not category_path.exists():
                continue

            for file_path in category_path.rglob("*.md"):
                item = KnowledgeItem.from_file(file_path)
                if not item:
                    continue

                score, reasons = self._score_item(item, query_lower, query_terms)
                if score >= min_score:
                    results.append(SearchResult(item=item, score=score, match_reasons=reasons))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:max_results]

    def _score_item(
        self, item: KnowledgeItem, query_lower: str, query_terms: set[str]
    ) -> tuple[float, list[str]]:
        """
        Score a knowledge item for relevance.

        Lightweight scoring (no embeddings by default):
        - Title match: high weight
        - Tag match: medium weight
        - Content keyword match: lower weight
        - Filename match: bonus

        Args:
            item: Knowledge item to score.
            query_lower: Lowercased query string.
            query_terms: Set of individual query terms.

        Returns:
            Tuple of (score, list of match reasons).
        """
        score = 0.0
        reasons: list[str] = []

        title_lower = item.title.lower()
        content_lower = item.content.lower()
        filename_lower = item.file_path.name.lower()

        # Title exact match (highest weight)
        if query_lower in title_lower:
            score += 3.0
            reasons.append("Query in title")

        # Title term matches
        title_terms = set(title_lower.split())
        title_matches = query_terms & title_terms
        if title_matches:
            score += len(title_matches) * 0.5
            reasons.append(f"Title terms: {', '.join(title_matches)}")

        # Tag matches (medium weight)
        item_tags_lower = [t.lower() for t in item.tags]
        tag_matches = query_terms & set(item_tags_lower)
        if tag_matches:
            score += len(tag_matches) * 1.0
            reasons.append(f"Tag matches: {', '.join(tag_matches)}")

        # Filename match (bonus)
        if query_lower in filename_lower:
            score += 1.5
            reasons.append("Query in filename")
        else:
            # Check for individual term matches in filename
            filename_terms = set(filename_lower.replace("_", " ").replace("-", " ").split())
            filename_term_matches = query_terms & filename_terms
            if filename_term_matches:
                score += len(filename_term_matches) * 0.5
                reasons.append(f"Filename terms: {', '.join(filename_term_matches)}")

        # Content keyword matches (lower weight, capped)
        content_matches = 0
        for term in query_terms:
            if term in content_lower:
                content_matches += 1

        if content_matches > 0:
            # Cap content contribution to avoid long documents dominating
            content_score = min(content_matches * 0.1, 2.0)
            score += content_score
            reasons.append(f"Content matches: {content_matches} terms")

        return score, reasons

    def list_items(
        self,
        category: str | None = None,
        tags: list[str] | None = None,
        item_type: str | None = None,
    ) -> list[KnowledgeItem]:
        """
        List knowledge items with optional filtering.

        Args:
            category: Filter by category (articles, books, docs, notes).
            tags: Filter by tags (must have all specified tags).
            item_type: Filter by item type from frontmatter.

        Returns:
            List of matching KnowledgeItem objects.
        """
        items: list[KnowledgeItem] = []

        if category:
            category_path = self.category_dirs.get(category)
            if not category_path:
                return items
            paths = list(category_path.rglob("*.md"))
        else:
            paths = []
            for cat_path in self.category_dirs.values():
                paths.extend(cat_path.rglob("*.md"))

        for file_path in paths:
            item = KnowledgeItem.from_file(file_path)
            if not item:
                continue

            # Apply filters
            if tags:
                tags_lower = set(t.lower() for t in tags)
                item_tags_lower = set(t.lower() for t in item.tags)
                if not tags_lower.issubset(item_tags_lower):
                    continue

            if item_type and item.item_type.lower() != item_type.lower():
                continue

            items.append(item)

        return items

    def ingest(
        self,
        content: str,
        title: str,
        category: str = "notes",
        item_type: str = "note",
        source: str = "",
        tags: list[str] | None = None,
        generate_summary: bool = False,
    ) -> KnowledgeItem | None:
        """
        Ingest new content into the knowledge base.

        Ingestion is explicit:
        - No automatic web scraping
        - No background crawling
        - No self-expanding corpus

        Args:
            content: Content to ingest.
            title: Title for the knowledge item.
            category: Target category (articles, books, docs, notes).
            item_type: Type metadata.
            source: Optional source URL or reference.
            tags: Optional tags.
            generate_summary: If True, attempt to generate summary (requires LLM).

        Returns:
            Created KnowledgeItem or None on failure.
        """
        if category not in self.DEFAULT_CATEGORIES:
            logger.warning(f"Unknown category '{category}', using 'notes'")
            category = "notes"

        category_path = self.category_dirs.get(category)
        if not category_path:
            logger.error(f"Category directory not found: {category}")
            return None

        existing_items = self._find_existing_by_title(title, category)

        # Reuse the canonical path for an existing note with the same title.
        file_path = category_path / f"{safe_filename(title)}.md"
        if existing_items:
            canonical_existing = next(
                (item for item in existing_items if item.file_path == file_path),
                existing_items[0],
            )
            file_path = canonical_existing.file_path
        else:
            counter = 1
            while file_path.exists():
                filename = safe_filename(title) + f"_{counter}.md"
                file_path = category_path / filename
                counter += 1

        # Build frontmatter
        metadata: dict[str, Any] = {
            "title": title,
            "type": item_type,
            "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

        if source:
            metadata["source"] = source
        if tags:
            metadata["tags"] = tags

        # Write file
        try:
            post = frontmatter.Post(content, **metadata)
            file_path.write_text(frontmatter.dumps(post), encoding="utf-8")
            logger.info(f"Ingested knowledge item: {file_path}")
            item = KnowledgeItem.from_file(file_path)
            if item is None:
                return None

            item.metadata["_write_action"] = "updated_existing" if existing_items else "created"
            if existing_items:
                self._cleanup_duplicate_title_items(item, existing_items)
            return item
        except Exception as e:
            logger.error(f"Failed to ingest knowledge item: {e}")
            return None

    def ingest_from_url(
        self,
        url: str,
        category: str = "articles",
        tags: list[str] | None = None,
    ) -> KnowledgeItem | None:
        """
        Ingest content from a URL.

        This is explicit user-triggered ingestion:
        - Not automatic scraping
        - Requires explicit tool call
        - Uses readability for extraction

        Args:
            url: URL to ingest.
            category: Target category (default: articles).
            tags: Optional tags.

        Returns:
            Created KnowledgeItem or None on failure.
        """
        # Import here to avoid circular dependency
        from hermitcrab.agent.tools.web import WebFetchTool

        # Fetch and extract content
        tool = WebFetchTool()
        result = tool._fetch_url(url)

        if not result or "error" in result:
            logger.error(f"Failed to fetch URL: {result}")
            return None

        # Extract title and content from result
        title = result.get("title", url)
        content = result.get("content", result.get("text", ""))

        if not content:
            logger.error("No content extracted from URL")
            return None

        return self.ingest(
            content=content,
            title=title,
            category=category,
            item_type="article",
            source=url,
            tags=tags,
        )

    def get_stats(self) -> dict[str, Any]:
        """
        Get knowledge base statistics.

        Returns:
            Dict with item counts per category and total.
        """
        stats: dict[str, Any] = {"total": 0, "by_category": {}}

        for category, path in self.category_dirs.items():
            count = len(list(path.rglob("*.md")))
            stats["by_category"][category] = count
            stats["total"] += count

        return stats
