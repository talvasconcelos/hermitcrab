"""
Strict, deterministic, category-based memory system.

Memory is stored as atomic markdown files with YAML frontmatter.
Each file represents exactly one memory item.

Categories (enforced):
- facts: Long-term truths, rarely updated
- decisions: Locked choices, immutable
- goals: Outcome-oriented objectives
- tasks: Concrete actionable items with lifecycle
- reflections: Subjective observations, append-only

Wikilinks:
- Memory content supports Obsidian-style wikilinks [[Like This]]
- Wikilinks create connections between related memories
- Enables graph visualization and navigation in Obsidian
- Example: A task might link to its parent [[Goal]] or related [[Decision]]
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import frontmatter
from loguru import logger

from hermitcrab.utils.helpers import ensure_dir


class MemoryCategory(str, Enum):
    """Valid memory categories - fixed and enforced."""

    FACTS = "facts"
    DECISIONS = "decisions"
    GOALS = "goals"
    TASKS = "tasks"
    REFLECTIONS = "reflections"


class TaskStatus(str, Enum):
    """Task lifecycle states (per spec)."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DEFERRED = "deferred"


@dataclass
class MemoryItem:
    """Represents a single memory item with metadata."""

    id: str
    category: MemoryCategory
    title: str
    content: str
    created_at: datetime
    updated_at: datetime
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def file_path(self) -> Path:
        """Get the file path for this memory item."""
        return Path(self.metadata.get("_file_path", ""))

    def to_frontmatter(self) -> frontmatter.Post:
        """Convert to frontmatter Post for writing."""
        post = frontmatter.Post(self.content)
        post.metadata.update(
            {
                "id": self.id,
                "title": self.title,
                "created_at": self.created_at.strftime("%Y-%m-%dT%H-%M-%S"),
                "updated_at": self.updated_at.strftime("%Y-%m-%dT%H-%M-%S"),
                "type": self.category.value,
                "tags": self.tags,
            }
        )
        # Add category-specific metadata
        if self.category == MemoryCategory.TASKS:
            post.metadata["status"] = self.metadata.get("status", TaskStatus.OPEN.value)
            post.metadata["assignee"] = self.metadata.get("assignee", "")
            if "deadline" in self.metadata:
                post.metadata["deadline"] = self.metadata["deadline"]
            if "priority" in self.metadata:
                post.metadata["priority"] = self.metadata["priority"]
            if "related_goal" in self.metadata:
                post.metadata["related_goal"] = self.metadata["related_goal"]
        elif self.category == MemoryCategory.GOALS:
            post.metadata["status"] = self.metadata.get("status", "active")
            if "priority" in self.metadata:
                post.metadata["priority"] = self.metadata["priority"]
            if "horizon" in self.metadata:
                post.metadata["horizon"] = self.metadata["horizon"]
        elif self.category == MemoryCategory.FACTS:
            if "source" in self.metadata:
                post.metadata["source"] = self.metadata["source"]
        elif self.category == MemoryCategory.DECISIONS:
            post.metadata["status"] = self.metadata.get("status", "active")
            if "supersedes" in self.metadata:
                post.metadata["supersedes"] = self.metadata["supersedes"]
            if "rationale" in self.metadata:
                post.metadata["rationale"] = self.metadata["rationale"]
            if "scope" in self.metadata:
                post.metadata["scope"] = self.metadata["scope"]
        elif self.category == MemoryCategory.REFLECTIONS:
            if "context" in self.metadata:
                post.metadata["context"] = self.metadata["context"]

        return post


class MemoryStore:
    """
    Category-based memory store with atomic file-backed storage.

    All memory is stored as individual markdown files with YAML frontmatter
    in workspace/memory/{category}/ directories.

    No LLM-based consolidation. No global memory files. No hidden state.
    """

    VALID_CATEGORIES = {cat.value for cat in MemoryCategory}

    def __init__(self, workspace: Path):
        """
        Initialize memory store.

        Args:
            workspace: Path to workspace directory.
        """
        self.workspace = workspace
        self.memory_dir = ensure_dir(workspace / "memory")
        self._write_lock = threading.RLock()

        # Create category directories
        self.category_dirs: dict[MemoryCategory, Path] = {}
        for category in MemoryCategory:
            self.category_dirs[category] = ensure_dir(self.memory_dir / category.value)

    def _generate_id(self, title: str, content: str) -> str:
        """Generate a unique, deterministic ID for a memory item."""
        hash_input = f"{title}:{content}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:8]

    @staticmethod
    def _tokenize_search(text: str) -> set[str]:
        """Tokenize text into lowercase alphanumeric search terms."""
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token}

    @classmethod
    def _search_match_score(cls, item: MemoryItem, file_path: Path, query: str) -> int:
        """Score a memory item for deterministic search ranking."""
        query_lower = query.lower().strip()
        if not query_lower:
            return 0

        query_tokens = cls._tokenize_search(query_lower)
        title_lower = item.title.lower()
        stem_lower = file_path.stem.lower()
        content_lower = item.content.lower()
        tag_tokens = {tag.lower() for tag in item.tags}
        item_tokens = cls._tokenize_search(" ".join([item.title, item.content, " ".join(item.tags)]))

        score = 0
        if stem_lower == query_lower:
            score += 140
        elif query_lower in stem_lower:
            score += 90

        if title_lower == query_lower:
            score += 180
        elif query_lower in title_lower:
            score += 120

        if any(query_lower == tag for tag in tag_tokens):
            score += 100
        elif any(query_lower in tag for tag in tag_tokens):
            score += 70

        if query_lower in content_lower:
            score += 40

        token_overlap = len(query_tokens & item_tokens)
        score += token_overlap * 12

        if item.category == MemoryCategory.TASKS:
            status = item.metadata.get("status", TaskStatus.OPEN.value)
            if status == TaskStatus.IN_PROGRESS.value:
                score += 8
            elif status == TaskStatus.OPEN.value:
                score += 6

        return score

    @staticmethod
    def _context_priority(item: MemoryItem) -> int:
        """Prioritize memory items that are more likely to help active work."""
        if item.category == MemoryCategory.TASKS:
            status = item.metadata.get("status", TaskStatus.OPEN.value)
            priorities = {
                TaskStatus.IN_PROGRESS.value: 4,
                TaskStatus.OPEN.value: 3,
                TaskStatus.DEFERRED.value: 1,
                TaskStatus.DONE.value: 0,
            }
            return priorities.get(status, 0)
        if item.category == MemoryCategory.GOALS:
            return 2 if item.metadata.get("status", "active") == "active" else 0
        if item.category == MemoryCategory.DECISIONS:
            return 2 if item.metadata.get("status", "active") == "active" else 1
        if item.category == MemoryCategory.REFLECTIONS:
            title = " ".join(re.sub(r"[^a-z0-9\s]+", " ", item.title.lower()).split())
            if title in {"short descriptive title", "descriptive title", "insight", "learning"}:
                return -1
            return 1
        return 1

    def _slugify(self, text: str) -> str:
        """Convert text to a safe URL-friendly slug."""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[-\s]+", "-", text)
        return text[:50]

    def _generate_filename(
        self,
        title: str,
        category: MemoryCategory,
        created_at: datetime | None = None,
    ) -> str:
        """
        Generate a unique, collision-resistant filename.

        Format: {timestamp}-{uuid_short}-{category}-{slug}.md

        Args:
            title: Memory title (used for slug).
            category: Memory category.
            created_at: Creation timestamp (defaults to now).

        Returns:
            Safe filename string.
        """
        timestamp = (created_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H-%M-%S")
        uuid_short = os.urandom(6).hex()  # 12-char hex string
        slug = self._slugify(title)
        return f"{timestamp}-{uuid_short}-{category.value}-{slug}.md"

    def _parse_frontmatter(self, post: frontmatter.Post, file_path: Path) -> MemoryItem:
        """Parse a frontmatter post into a MemoryItem.

        Validates required fields per category.
        """
        meta = post.metadata
        category_str = meta.get("type", "")

        # Validate category
        if category_str not in self.VALID_CATEGORIES:
            raise ValueError(f"Invalid memory category: {category_str}")

        category = MemoryCategory(category_str)

        # Validate required fields
        self._validate_required_fields(meta, category)

        # Parse timestamps
        created_at, updated_at = self._parse_timestamps(meta)

        # Extract category-specific metadata
        extra_meta = self._extract_category_metadata(meta, category, file_path)

        return MemoryItem(
            id=meta.get("id", self._generate_id(meta.get("title", ""), post.content)),
            category=category,
            title=meta.get("title", "Untitled"),
            content=post.content.strip(),
            created_at=created_at,
            updated_at=updated_at,
            tags=meta.get("tags", []),
            metadata=extra_meta,
        )

    def _validate_required_fields(self, meta: dict, category: MemoryCategory) -> None:
        """Validate required fields for a memory category."""
        # Common required fields (id is optional - auto-generated if missing)
        for field_name in ["created_at", "type"]:
            if field_name not in meta:
                raise ValueError(f"Missing required field '{field_name}' in {category.value} memory")

        # Category-specific required fields
        if category in {MemoryCategory.DECISIONS, MemoryCategory.GOALS, MemoryCategory.TASKS}:
            if "status" not in meta:
                raise ValueError(f"Missing required field 'status' in {category.value[:-1]} memory")

        if category == MemoryCategory.TASKS:
            if "assignee" not in meta or not meta.get("assignee"):
                raise ValueError("Missing required field 'assignee' in task memory")

    def _parse_timestamps(self, meta: dict) -> tuple[datetime, datetime]:
        """Parse created_at and updated_at timestamps from metadata."""
        created_str = meta.get("created_at", datetime.now(timezone.utc).isoformat())
        updated_str = meta.get("updated_at", created_str)

        try:
            created_at = datetime.strptime(created_str[:19], "%Y-%m-%dT%H-%M-%S")
        except (ValueError, TypeError):
            created_at = datetime.now(timezone.utc)

        try:
            updated_at = datetime.strptime(updated_str[:19], "%Y-%m-%dT%H-%M-%S")
        except (ValueError, TypeError):
            updated_at = created_at

        return created_at, updated_at

    def _extract_category_metadata(
        self,
        meta: dict,
        category: MemoryCategory,
        file_path: Path,
    ) -> dict[str, Any]:
        """Extract category-specific metadata from frontmatter."""
        extra_meta: dict[str, Any] = {"_file_path": str(file_path)}

        # Task metadata
        if category == MemoryCategory.TASKS:
            extra_meta["status"] = meta.get("status", TaskStatus.OPEN.value)
            extra_meta["assignee"] = meta.get("assignee", "")
            for key in ["deadline", "priority", "related_goal"]:
                if key in meta:
                    extra_meta[key] = meta[key]

        # Goal metadata
        elif category == MemoryCategory.GOALS:
            extra_meta["status"] = meta.get("status", "active")
            for key in ["priority", "horizon"]:
                if key in meta:
                    extra_meta[key] = meta[key]

        # Fact metadata
        elif category == MemoryCategory.FACTS:
            if "source" in meta:
                extra_meta["source"] = meta["source"]

        # Decision metadata
        elif category == MemoryCategory.DECISIONS:
            extra_meta["status"] = meta.get("status", "active")
            for key in ["supersedes", "rationale", "scope"]:
                if key in meta:
                    extra_meta[key] = meta[key]

        # Reflection metadata
        elif category == MemoryCategory.REFLECTIONS:
            if "context" in meta:
                extra_meta["context"] = meta["context"]

        return extra_meta

    def _read_file(self, file_path: Path) -> MemoryItem | None:
        """Read a single memory file."""
        if not file_path.exists():
            return None

        try:
            post = frontmatter.load(file_path)
            return self._parse_frontmatter(post, file_path)
        except Exception as e:
            logger.error("Failed to read memory file {}: {}", file_path, e)
            return None

    def _write_file(self, item: MemoryItem, overwrite: bool = False) -> Path:
        """Write a memory item to file.

        Args:
            item: Memory item to write.
            overwrite: If True and item has existing file path, overwrite it directly.

        Returns:
            Path to written file.
        """
        with self._write_lock:
            # If overwriting and item has existing file path, use it directly
            if overwrite and item.metadata.get("_file_path"):
                file_path = Path(item.metadata["_file_path"])
                if file_path.exists():
                    post = item.to_frontmatter()
                    file_path.write_text(frontmatter.dumps(post), encoding="utf-8")
                    logger.info("Overwrote memory item: {}:{} -> {}", item.category.value, item.id, file_path)
                    return file_path

            # De-duplicate by deterministic ID: update existing file instead of creating another one
            existing = self.read_memory(item.category, id=item.id)
            if existing:
                existing_item = existing[0]
                existing_path = Path(existing_item.metadata.get("_file_path", ""))
                if existing_path.exists():
                    item.created_at = existing_item.created_at
                    item.metadata["_file_path"] = str(existing_path)
                    post = item.to_frontmatter()
                    existing_path.write_text(frontmatter.dumps(post), encoding="utf-8")
                    logger.info("Upserted memory item: {}:{} -> {}", item.category.value, item.id, existing_path)
                    return existing_path

            # Generate unique filename with timestamp, UUID, category, and slug
            filename = self._generate_filename(item.title, item.category, item.created_at)
            file_path = self.category_dirs[item.category] / filename

            # Handle rare filename collisions (same timestamp + UUID)
            counter = 0
            original_filename = filename
            while file_path.exists():
                existing_file_item = self._read_file(file_path)
                if existing_file_item and existing_file_item.id == item.id:
                    break  # Same item, overwrite
                counter += 1
                filename = f"{original_filename.rsplit('.', 1)[0]}_{counter}.md"
                file_path = self.category_dirs[item.category] / filename

            post = item.to_frontmatter()
            file_path.write_text(frontmatter.dumps(post), encoding="utf-8")

            # Update metadata with actual file path
            item.metadata["_file_path"] = str(file_path)

            logger.info("Wrote memory item: {}:{} -> {}", item.category.value, item.id, file_path)
            return file_path

    # ========================================================================
    # WRITE OPERATIONS - Category-specific with lifecycle enforcement
    # ========================================================================

    def write_fact(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        confidence: float | None = None,
        source: str | None = None,
    ) -> MemoryItem:
        """
        Write a new fact to memory.

        Facts are long-term truths. Written only if explicitly stated or
        unambiguous. May be updated only if contradicted. Rarely deleted.

        Args:
            title: Short descriptive title.
            content: Fact content.
            tags: Optional tags for categorization.
            confidence: Confidence level (0.0-1.0).
            source: Source of the fact.

        Returns:
            The created MemoryItem.
        """
        if not content.strip():
            raise ValueError("Fact content cannot be empty")

        item = MemoryItem(
            id=self._generate_id(title, content),
            category=MemoryCategory.FACTS,
            title=title,
            content=content.strip(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            tags=tags or [],
            metadata={
                "confidence": confidence,
                "source": source,
            },
        )

        self._write_file(item)
        logger.info("Wrote fact: {}", title)
        return item

    def write_decision(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        status: str = "active",
        supersedes: str | None = None,
        rationale: str | None = None,
        scope: str | None = None,
    ) -> MemoryItem:
        """
        Write a new decision to memory.

        Decisions are locked architectural or behavioral choices.
        Immutable - never edited. Only superseded by new decisions.
        Never deleted.

        Args:
            title: Short descriptive title.
            content: Decision content.
            tags: Optional tags.
            status: Decision status (active or superseded).
            supersedes: ID of decision this supersedes (if any).
            rationale: Reasoning behind the decision.
            scope: Scope of the decision.

        Returns:
            The created MemoryItem.

        Raises:
            ValueError: If status is invalid or content is empty.
        """
        if not content.strip():
            raise ValueError("Decision content cannot be empty")
        if status not in ("active", "superseded"):
            raise ValueError("Decision status must be 'active' or 'superseded'")

        item = MemoryItem(
            id=self._generate_id(title, content),
            category=MemoryCategory.DECISIONS,
            title=title,
            content=content.strip(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            tags=tags or [],
            metadata={
                "status": status,
                "supersedes": supersedes,
                "rationale": rationale,
                "scope": scope,
            },
        )

        self._write_file(item)
        logger.info("Wrote decision: {} (status: {})", title, status)
        return item

    def write_goal(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        status: str = "active",
        priority: str | None = None,
        horizon: str | None = None,
    ) -> MemoryItem:
        """
        Write a new goal to memory.

        Goals are outcome-oriented objectives. Durable beyond a single
        session. May be refined or marked achieved. Not silently removed.

        Args:
            title: Short descriptive title.
            content: Goal content.
            tags: Optional tags.
            status: Goal status (active, achieved, abandoned).
            priority: Priority level (e.g., "high", "medium", "low").
            horizon: Time horizon (e.g., "short-term", "long-term").

        Returns:
            The created MemoryItem.

        Raises:
            ValueError: If status is invalid or content is empty.
        """
        if not content.strip():
            raise ValueError("Goal content cannot be empty")
        if status not in ("active", "achieved", "abandoned"):
            raise ValueError("Goal status must be 'active', 'achieved', or 'abandoned'")

        item = MemoryItem(
            id=self._generate_id(title, content),
            category=MemoryCategory.GOALS,
            title=title,
            content=content.strip(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            tags=tags or [],
            metadata={
                "status": status,
                "priority": priority,
                "horizon": horizon,
            },
        )

        self._write_file(item)
        logger.info("Wrote goal: {} (status: {})", title, status)
        return item

    def write_task(
        self,
        title: str,
        content: str,
        assignee: str,
        tags: list[str] | None = None,
        status: TaskStatus | str = TaskStatus.OPEN,
        deadline: str | None = None,
        priority: str | None = None,
        related_goal: str | None = None,
    ) -> MemoryItem:
        """
        Write a new task to memory.

        Tasks are concrete, actionable items with lifecycle.
        One task per file. State transitions only.
        Completed tasks archived, not deleted.

        Args:
            title: Short descriptive title.
            content: Task content.
            assignee: Who the task is assigned to (REQUIRED).
            tags: Optional tags.
            status: Task status (open, in_progress, done, deferred).
            deadline: Deadline date string.
            priority: Priority level.
            related_goal: ID of related goal.

        Returns:
            The created MemoryItem.

        Raises:
            ValueError: If assignee is empty or content is empty.
        """
        if not content.strip():
            raise ValueError("Task content cannot be empty")
        if not assignee or not assignee.strip():
            raise ValueError("Task assignee is required")

        # Convert string status to TaskStatus enum if needed
        if isinstance(status, str):
            status = TaskStatus(status)

        item = MemoryItem(
            id=self._generate_id(title, content),
            category=MemoryCategory.TASKS,
            title=title,
            content=content.strip(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            tags=tags or [],
            metadata={
                "status": status.value,
                "assignee": assignee.strip(),
                "deadline": deadline,
                "priority": priority,
                "related_goal": related_goal,
            },
        )

        self._write_file(item)
        logger.info("Wrote task: {} (assignee: {})", title, assignee)
        return item

    def write_reflection(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        context: str | None = None,
    ) -> MemoryItem:
        """
        Write a new reflection to memory.

        Reflections are subjective observations.
        Append only - never edited or deleted.
        May contradict earlier reflections.

        Args:
            title: Short descriptive title.
            content: Reflection content.
            tags: Optional tags.
            context: Context for the reflection.

        Returns:
            The created MemoryItem.
        """
        if not content.strip():
            raise ValueError("Reflection content cannot be empty")

        item = MemoryItem(
            id=self._generate_id(title, content),
            category=MemoryCategory.REFLECTIONS,
            title=title,
            content=content.strip(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            tags=tags or [],
            metadata={
                "context": context,
            },
        )

        self._write_file(item)
        logger.info("Wrote reflection: {}", title)
        return item

    # ========================================================================
    # READ OPERATIONS
    # ========================================================================

    def read_memory(
        self,
        category: MemoryCategory | str,
        id: str | None = None,
        query: str | None = None,
    ) -> list[MemoryItem]:
        """
        Read memory items by category with optional filtering.

        Args:
            category: Category to read from.
            id: Optional specific item ID to find.
            query: Optional search query for title/content matching.

        Returns:
            List of matching MemoryItems.
        """
        if isinstance(category, str):
            if category not in self.VALID_CATEGORIES:
                raise ValueError(f"Invalid category: {category}")
            category = MemoryCategory(category)

        category_path = self.category_dirs[category]
        results: list[MemoryItem] = []

        if not category_path.exists():
            return results

        # Collect all items first, then sort deterministically by updated_at (newest first)
        all_items: list[MemoryItem] = []
        for file_path in sorted(category_path.glob("*.md")):  # Sort paths for deterministic iteration
            item = self._read_file(file_path)
            if item is None:
                continue

            # Filter by ID if provided
            if id and item.id != id:
                continue

            # Filter by query if provided
            if query:
                query_lower = query.lower()
                if query_lower not in item.title.lower() and query_lower not in item.content.lower():
                    continue

            all_items.append(item)

        # Sort by updated_at descending (newest first) for deterministic ordering
        all_items.sort(key=lambda x: x.updated_at, reverse=True)

        # If searching by ID, check for duplicates and self-heal
        if id:
            matching = [item for item in all_items if item.id == id]
            if len(matching) > 1:
                logger.warning(
                    "Duplicate IDs detected for '{}': {} items found. Keeping newest, removing {} old.",
                    id,
                    len(matching),
                    len(matching) - 1,
                )
                # Self-heal: remove duplicate files (keep newest)
                for duplicate in matching[1:]:
                    file_path = Path(duplicate.metadata.get("_file_path", ""))
                    if file_path.exists():
                        file_path.unlink()
                        logger.info("Removed duplicate memory file: {}", file_path)
                return matching[:1]
            return matching[:1] if matching else []

        return all_items

    def search_memory(
        self,
        query: str,
        categories: list[MemoryCategory | str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryItem]:
        """
        Search memory across categories.

        Deterministic search: filenames first, then frontmatter fields,
        then simple keyword matching in content.

        Args:
            query: Search query string.
            categories: Categories to search (None = all).
            limit: Maximum results to return.

        Returns:
            List of matching MemoryItems.
        """
        query_lower = query.lower()
        scored_results: dict[str, tuple[int, MemoryItem]] = {}

        # Determine which categories to search
        if categories is None:
            search_categories = list(MemoryCategory)
        else:
            search_categories = []
            for cat in categories:
                if isinstance(cat, str):
                    if cat not in self.VALID_CATEGORIES:
                        logger.warning("Skipping invalid category: {}", cat)
                        continue
                    search_categories.append(MemoryCategory(cat))
                else:
                    search_categories.append(cat)

        for category in search_categories:
            category_path = self.category_dirs[category]
            if not category_path.exists():
                continue

            for file_path in category_path.glob("*.md"):
                item = self._read_file(file_path)
                if item is None:
                    continue

                score = self._search_match_score(item, file_path, query_lower)
                if score <= 0:
                    continue

                existing = scored_results.get(item.id)
                if existing is None or score > existing[0] or (
                    score == existing[0] and item.updated_at > existing[1].updated_at
                ):
                    scored_results[item.id] = (score, item)

        results = [item for _, item in sorted(
            scored_results.values(),
            key=lambda pair: (pair[0], pair[1].updated_at),
            reverse=True,
        )]

        if limit:
            results = results[:limit]

        return results

    # ========================================================================
    # UPDATE OPERATIONS
    # ========================================================================

    def update_memory(
        self,
        category: MemoryCategory | str,
        id: str,
        content: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        **metadata_updates: Any,
    ) -> MemoryItem | None:
        """
        Update an existing memory item.

        Category-specific rules:
        - FACTS: May be updated only if contradicted
        - DECISIONS: Immutable - this will log a warning but allow for superseding
        - GOALS: May be refined or status changed
        - TASKS: State transitions only (status changes)
        - REFLECTIONS: Append only - updates not allowed

        Args:
            category: Category of the item.
            id: Item ID to update.
            content: New content (if allowed).
            title: New title.
            tags: New tags.
            **metadata_updates: Category-specific metadata updates.

        Returns:
            Updated MemoryItem, or None if not found.

        Raises:
            ValueError: If update violates category rules.
        """
        if isinstance(category, str):
            if category not in self.VALID_CATEGORIES:
                raise ValueError(f"Invalid category: {category}")
            category = MemoryCategory(category)

        # Find the item
        items = self.read_memory(category, id=id)
        if not items:
            logger.warning("Memory item not found: {}:{} ", category.value, id)
            return None

        item = items[0]

        # Enforce category-specific rules
        if category == MemoryCategory.REFLECTIONS:
            raise ValueError("Reflections are append-only and cannot be updated")

        if category == MemoryCategory.DECISIONS:
            logger.warning(
                "Updating decision {} - decisions should be immutable. "
                "Consider writing a new decision that supersedes this one.",
                id,
            )

        # Apply updates
        if title is not None:
            item.title = title

        if content is not None:
            item.content = content.strip()

        if tags is not None:
            item.tags = tags

        # Update metadata
        item.metadata.update(metadata_updates)
        item.updated_at = datetime.now(timezone.utc)

        # Category-specific metadata handling
        if category == MemoryCategory.TASKS:
            if "status" in metadata_updates:
                old_status = item.metadata.get("status", TaskStatus.OPEN.value)
                new_status = metadata_updates["status"]
                logger.info("Task {} status changed: {} -> {}", id, old_status, new_status)

        self._write_file(item, overwrite=True)
        logger.info("Updated memory item: {}:{} ", category.value, id)

        return item

    def update_task_status(
        self,
        task_id: str,
        new_status: TaskStatus | str,
    ) -> MemoryItem | None:
        """
        Update a task's status with lifecycle validation.

        Args:
            task_id: ID of the task to update.
            new_status: New status value.

        Returns:
            Updated MemoryItem, or None if not found.
        """
        if isinstance(new_status, str):
            if new_status not in {s.value for s in TaskStatus}:
                raise ValueError(f"Invalid task status: {new_status}")
            new_status = TaskStatus(new_status)

        items = self.read_memory(MemoryCategory.TASKS, id=task_id)
        if not items:
            logger.warning("Task not found: {}", task_id)
            return None

        item = items[0]
        old_status = item.metadata.get("status", TaskStatus.OPEN.value)

        # Validate status transitions
        valid_transitions = {
            TaskStatus.OPEN: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.DEFERRED},
            TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.DEFERRED},
            TaskStatus.DONE: set(),  # Terminal state
            TaskStatus.DEFERRED: {TaskStatus.OPEN, TaskStatus.IN_PROGRESS},
        }

        if new_status not in valid_transitions.get(TaskStatus(old_status), set()):
            logger.warning(
                "Unusual task status transition: {} -> {} for task {}",
                old_status,
                new_status.value,
                task_id,
            )

        return self.update_memory(
            MemoryCategory.TASKS,
            task_id,
            status=new_status.value,
        )

    # ========================================================================
    # DELETE OPERATIONS
    # ========================================================================

    def delete_memory(
        self,
        category: MemoryCategory | str,
        id: str,
    ) -> bool:
        """
        Delete a memory item.

        Category-specific rules:
        - FACTS: Rarely deleted - requires explicit confirmation
        - DECISIONS: Never deleted - this will refuse
        - GOALS: Not silently removed - archive instead
        - TASKS: Completed tasks archived, not deleted
        - REFLECTIONS: Never deleted

        Args:
            category: Category of the item.
            id: Item ID to delete.

        Returns:
            True if deleted, False otherwise.

        Raises:
            ValueError: If deletion violates category rules.
        """
        if isinstance(category, str):
            if category not in self.VALID_CATEGORIES:
                raise ValueError(f"Invalid category: {category}")
            category = MemoryCategory(category)

        # Find the item
        items = self.read_memory(category, id=id)
        if not items:
            logger.warning("Memory item not found: {}:{} ", category.value, id)
            return False

        item = items[0]

        # Enforce category-specific rules
        if category == MemoryCategory.DECISIONS:
            raise ValueError("Decisions are immutable and cannot be deleted")

        if category == MemoryCategory.REFLECTIONS:
            raise ValueError("Reflections are append-only and cannot be deleted")

        if category == MemoryCategory.TASKS:
            status = item.metadata.get("status", TaskStatus.OPEN.value)
            if status == TaskStatus.DONE.value:
                logger.warning(
                    "Task {} is completed. Archiving instead of deleting.",
                    id,
                )
                # Archive by moving to a subdirectory
                return self._archive_task(item)

        if category == MemoryCategory.GOALS:
            status = item.metadata.get("status", "active")
            if status == "achieved":
                logger.warning(
                    "Goal {} is achieved. Archiving instead of deleting.",
                    id,
                )
                return self._archive_goal(item)

        # Delete the file
        file_path = Path(item.metadata.get("_file_path", ""))
        if file_path.exists():
            file_path.unlink()
            logger.info("Deleted memory item: {}:{} ", category.value, id)
            return True

        return False

    def _archive_task(self, item: MemoryItem) -> bool:
        """Archive a completed task."""
        archive_dir = ensure_dir(self.category_dirs[MemoryCategory.TASKS] / "archived")
        new_path = archive_dir / item.file_path.name

        if item.file_path.exists():
            item.file_path.rename(new_path)
            item.metadata["_file_path"] = str(new_path)
            logger.info("Archived task: {} -> {}", item.title, new_path)
            return True
        return False

    def _archive_goal(self, item: MemoryItem) -> bool:
        """Archive an achieved goal."""
        archive_dir = ensure_dir(self.category_dirs[MemoryCategory.GOALS] / "archived")
        new_path = archive_dir / item.file_path.name

        if item.file_path.exists():
            item.file_path.rename(new_path)
            item.metadata["_file_path"] = str(new_path)
            logger.info("Archived goal: {} -> {}", item.title, new_path)
            return True
        return False

    # ========================================================================
    # CONTEXT BUILDING
    # ========================================================================

    def get_memory_context(
        self,
        max_chars: int | None = None,
        max_items_per_category: int | None = None,
        max_item_chars: int | None = None,
    ) -> str:
        """
        Build memory context for the system prompt.

        Returns all active memory items organized by category.
        This replaces the old MEMORY.md-based approach.
        """
        parts = []

        for category in MemoryCategory:
            items = self.read_memory(category)
            if not items:
                continue

            active_items = [item for item in items if "archived" not in str(item.file_path)]
            active_items = [item for item in active_items if self._context_priority(item) >= 0]
            active_items.sort(
                key=lambda item: (self._context_priority(item), item.updated_at),
                reverse=True,
            )
            if max_items_per_category is not None:
                active_items = active_items[:max_items_per_category]
            if not active_items:
                continue

            category_name = category.value.title()
            section_lines = [f"## {category_name}"]

            for item in active_items:
                formatted = self._format_memory_item(item, category)
                if max_item_chars is not None and len(formatted) > max_item_chars:
                    formatted = formatted[:max_item_chars].rstrip() + "\n...(truncated)"
                section_lines.append(formatted)

            parts.append("\n".join(section_lines))

        if parts:
            content = "\n\n---\n\n".join(parts)
            if max_chars is not None and len(content) > max_chars:
                return content[:max_chars].rstrip() + "\n\n_[Memory context truncated]_"
            return content

        return ""

    def get_relevant_context(
        self,
        query: str,
        *,
        limit: int = 6,
        max_chars: int | None = None,
        max_item_chars: int | None = None,
    ) -> str:
        """Build a compact context section from memories relevant to a live query."""
        if not query or not query.strip():
            return ""

        items = self.search_memory(query=query, limit=limit)
        if not items:
            return ""

        section_lines: list[str] = []
        for item in items:
            formatted = self._format_memory_item(item, item.category)
            if max_item_chars is not None and len(formatted) > max_item_chars:
                formatted = formatted[:max_item_chars].rstrip() + "\n...(truncated)"
            section_lines.append(formatted)

        content = "\n".join(section_lines)
        if max_chars is not None and len(content) > max_chars:
            return content[:max_chars].rstrip() + "\n\n_[Relevant memory truncated]_"
        return content

    def _format_memory_item(self, item: MemoryItem, category: MemoryCategory) -> str:
        """Format a single memory item for context display."""
        lines = [f"\n### {item.title}"]

        # Add relevant metadata as context
        meta_lines = self._build_metadata_lines(item, category)

        if meta_lines:
            lines.append("(" + " | ".join(meta_lines) + ")")

        lines.append(item.content)
        return "\n".join(lines)

    def _build_metadata_lines(self, item: MemoryItem, category: MemoryCategory) -> list[str]:
        """Build metadata lines for a memory item based on category."""
        meta_lines: list[str] = []

        if item.tags:
            meta_lines.append(f"Tags: {', '.join(item.tags)}")

        # Category-specific metadata
        if category == MemoryCategory.TASKS:
            meta_lines.extend(self._get_task_metadata(item))
        elif category == MemoryCategory.GOALS:
            meta_lines.extend(self._get_goal_metadata(item))
        elif category == MemoryCategory.FACTS:
            meta_lines.extend(self._get_fact_metadata(item))
        elif category == MemoryCategory.DECISIONS:
            meta_lines.extend(self._get_decision_metadata(item))

        return meta_lines

    def _get_task_metadata(self, item: MemoryItem) -> list[str]:
        """Get metadata lines for task items."""
        meta_lines: list[str] = []
        status = item.metadata.get("status", "todo")
        meta_lines.append(f"Status: {status}")
        if item.metadata.get("assignee"):
            meta_lines.append(f"Assignee: {item.metadata['assignee']}")
        if item.metadata.get("deadline"):
            meta_lines.append(f"Deadline: {item.metadata['deadline']}")
        return meta_lines

    def _get_goal_metadata(self, item: MemoryItem) -> list[str]:
        """Get metadata lines for goal items."""
        meta_lines: list[str] = []
        status = item.metadata.get("status", "active")
        meta_lines.append(f"Status: {status}")
        if item.metadata.get("priority"):
            meta_lines.append(f"Priority: {item.metadata['priority']}")
        return meta_lines

    def _get_fact_metadata(self, item: MemoryItem) -> list[str]:
        """Get metadata lines for fact items."""
        meta_lines: list[str] = []
        if item.metadata.get("confidence"):
            meta_lines.append(f"Confidence: {item.metadata['confidence']}")
        if item.metadata.get("source"):
            meta_lines.append(f"Source: {item.metadata['source']}")
        return meta_lines

    def _get_decision_metadata(self, item: MemoryItem) -> list[str]:
        """Get metadata lines for decision items."""
        meta_lines: list[str] = []
        if item.metadata.get("supersedes"):
            meta_lines.append(f"Supersedes: {item.metadata['supersedes']}")
        return meta_lines

    def list_memories(
        self,
        category: MemoryCategory | str | None = None,
        include_archived: bool = False,
    ) -> list[MemoryItem]:
        """
        List all memory items, optionally filtered by category.

        Args:
            category: Category to list (None = all).
            include_archived: Whether to include archived items.

        Returns:
            List of MemoryItems.
        """
        if category is None:
            categories = list(MemoryCategory)
        elif isinstance(category, str):
            if category not in self.VALID_CATEGORIES:
                raise ValueError(f"Invalid category: {category}")
            categories = [MemoryCategory(category)]
        else:
            categories = [category]

        results: list[MemoryItem] = []
        for cat in categories:
            # Read from main category directory
            items = self.read_memory(cat)
            for item in items:
                if not include_archived and "archived" in str(item.file_path):
                    continue
                results.append(item)

            # Also read from archived subdirectory if requested
            if include_archived:
                archive_dir = self.category_dirs[cat] / "archived"
                if archive_dir.exists():
                    for file_path in archive_dir.glob("*.md"):
                        item = self._read_file(file_path)
                        if item:
                            results.append(item)

        # Sort by category, then recency
        results.sort(key=lambda x: (x.category.value, x.updated_at), reverse=True)
        return results
