"""Generic checklist storage in the knowledge layer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
from loguru import logger

from hermitcrab.utils.helpers import ensure_dir, safe_filename

CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$")
BARE_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")


@dataclass
class ListEntry:
    """A single list item."""

    text: str
    done: bool = False


@dataclass
class StoredList:
    """A stored generic list."""

    file_path: Path
    title: str
    slug: str
    items: list[ListEntry] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    updated_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def remaining_count(self) -> int:
        return sum(1 for item in self.items if not item.done)

    @property
    def completed_count(self) -> int:
        return sum(1 for item in self.items if item.done)


class ListStore:
    """Manage updateable user checklists under the knowledge tree."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.lists_dir = ensure_dir(workspace / "knowledge" / "notes" / "checklists")

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized or safe_filename(value).strip().lower() or "list"

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())

    @staticmethod
    def _dedupe_items(items: list[str | ListEntry]) -> list[ListEntry]:
        deduped: list[ListEntry] = []
        seen: set[str] = set()
        for item in items:
            entry = item if isinstance(item, ListEntry) else ListEntry(text=str(item).strip())
            if not entry.text.strip():
                continue
            normalized = ListStore._normalize(entry.text)
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(ListEntry(text=entry.text.strip(), done=bool(entry.done)))
        return deduped

    def _list_path(self, title: str) -> Path:
        return self.lists_dir / f"{self._slugify(title)}.md"

    def _iter_list_paths(self) -> list[Path]:
        return sorted(self.lists_dir.rglob("*.md"))

    def _load_list_file(self, file_path: Path) -> StoredList | None:
        try:
            post = frontmatter.load(file_path)
        except Exception as exc:
            logger.warning("Failed to load list file {}: {}", file_path, exc)
            return None

        metadata = post.metadata or {}
        title = str(metadata.get("title") or file_path.stem)
        tags = [str(tag) for tag in metadata.get("tags", []) if str(tag).strip()]
        updated_at = self._parse_date(metadata.get("updated_at"))

        entries: list[ListEntry] = []
        for line in (post.content or "").splitlines():
            match = CHECKBOX_RE.match(line)
            if match:
                entries.append(
                    ListEntry(
                        text=match.group("text").strip(), done=match.group("mark").lower() == "x"
                    )
                )
                continue
            match = BARE_BULLET_RE.match(line)
            if match:
                entries.append(ListEntry(text=match.group("text").strip(), done=False))

        return StoredList(
            file_path=file_path,
            title=title,
            slug=self._slugify(title),
            items=self._dedupe_items(entries),
            tags=tags,
            updated_at=updated_at,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(value: object) -> datetime | None:
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

    def list_lists(self) -> list[StoredList]:
        items = [
            loaded for path in self._iter_list_paths() if (loaded := self._load_list_file(path))
        ]
        items.sort(key=lambda item: item.updated_at or datetime.min, reverse=True)
        return items

    def get_list(self, name: str) -> StoredList | None:
        query = name.strip()
        if not query:
            return None

        direct_path = self._list_path(query)
        if direct_path.exists():
            return self._load_list_file(direct_path)

        normalized_query = self._normalize(query)
        for item in self.list_lists():
            if item.slug == self._slugify(query):
                return item
            if self._normalize(item.title) == normalized_query:
                return item
        return None

    def search_lists(self, query: str, max_results: int = 5) -> list[StoredList]:
        normalized_query = self._normalize(query)
        if not normalized_query:
            return self.list_lists()[:max_results]

        query_terms = set(normalized_query.split())
        scored: list[tuple[float, StoredList]] = []
        for item in self.list_lists():
            title_norm = self._normalize(item.title)
            slug_norm = self._normalize(item.slug)
            tag_norms = {self._normalize(tag) for tag in item.tags}
            item_text = " ".join(self._normalize(entry.text) for entry in item.items)

            score = 0.0
            if title_norm == normalized_query or slug_norm == normalized_query:
                score += 8.0
            if normalized_query in title_norm or normalized_query in slug_norm:
                score += 4.0

            title_terms = set(title_norm.split()) | set(slug_norm.split())
            term_matches = query_terms & title_terms
            score += len(term_matches) * 0.75
            score += len(query_terms & tag_norms) * 1.25

            content_matches = sum(1 for term in query_terms if term and term in item_text)
            score += min(content_matches * 0.25, 2.0)

            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda pair: (pair[0], pair[1].updated_at or datetime.min), reverse=True)
        return [item for _, item in scored[:max_results]]

    def find_list(self, query: str) -> StoredList | None:
        if exact := self.get_list(query):
            return exact
        matches = self.search_lists(query, max_results=2)
        if len(matches) == 1:
            return matches[0]
        return None

    def save_list(
        self,
        title: str,
        items: list[str | ListEntry],
        tags: list[str] | None = None,
        existing_path: Path | None = None,
    ) -> StoredList:
        clean_title = title.strip() or "List"
        deduped_items = self._dedupe_items(items)
        file_path = existing_path or self._list_path(clean_title)

        metadata = {
            "title": clean_title,
            "type": "checklist",
            "tags": [str(tag).strip() for tag in (tags or []) if str(tag).strip()],
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        lines = [f"- [{'x' if entry.done else ' '}] {entry.text}" for entry in deduped_items]
        post = frontmatter.Post("\n".join(lines) + ("\n" if lines else ""), **metadata)
        file_path.write_text(frontmatter.dumps(post), encoding="utf-8")

        stored = self._load_list_file(file_path)
        if stored is None:
            raise ValueError(f"Failed to read stored list: {file_path}")
        return stored

    def add_items(
        self, list_name: str, items: list[str], tags: list[str] | None = None
    ) -> tuple[StoredList, list[str]]:
        existing = self.get_list(list_name)
        existing_items = existing.items if existing else []
        updated = self.save_list(
            title=existing.title if existing else list_name,
            items=[*existing_items, *items],
            tags=tags or (existing.tags if existing else None),
            existing_path=existing.file_path if existing else None,
        )

        existing_norms = {self._normalize(item.text) for item in existing_items}
        added = [
            entry.text
            for entry in updated.items
            if self._normalize(entry.text) not in existing_norms
        ]
        return updated, added

    def set_item_status(
        self, list_name: str, item_names: list[str], *, done: bool
    ) -> tuple[StoredList, list[str], list[str]]:
        existing = self.get_list(list_name)
        if existing is None:
            raise ValueError(f"List not found: {list_name}")

        matched_indices, unmatched = self._match_item_indices(existing.items, item_names)
        if not matched_indices:
            raise ValueError(f"No matching items found in list '{existing.title}'")

        updated_items: list[ListEntry] = []
        changed: list[str] = []
        for index, entry in enumerate(existing.items):
            if index in matched_indices:
                updated_items.append(ListEntry(text=entry.text, done=done))
                changed.append(entry.text)
            else:
                updated_items.append(entry)

        updated = self.save_list(
            title=existing.title,
            items=updated_items,
            tags=existing.tags,
            existing_path=existing.file_path,
        )
        return updated, changed, unmatched

    def remove_items(
        self, list_name: str, item_names: list[str]
    ) -> tuple[StoredList, list[str], list[str]]:
        existing = self.get_list(list_name)
        if existing is None:
            raise ValueError(f"List not found: {list_name}")

        matched_indices, unmatched = self._match_item_indices(existing.items, item_names)
        if not matched_indices:
            raise ValueError(f"No matching items found in list '{existing.title}'")

        removed = [
            entry.text for index, entry in enumerate(existing.items) if index in matched_indices
        ]
        kept = [entry for index, entry in enumerate(existing.items) if index not in matched_indices]
        updated = self.save_list(
            title=existing.title,
            items=kept,
            tags=existing.tags,
            existing_path=existing.file_path,
        )
        return updated, removed, unmatched

    def delete_list(self, list_name: str) -> StoredList:
        existing = self.get_list(list_name)
        if existing is None:
            raise ValueError(f"List not found: {list_name}")
        existing.file_path.unlink(missing_ok=False)
        return existing

    def _match_item_indices(
        self, items: list[ListEntry], queries: list[str]
    ) -> tuple[set[int], list[str]]:
        matched: set[int] = set()
        unmatched: list[str] = []

        normalized_items = [self._normalize(item.text) for item in items]
        for query in queries:
            normalized_query = self._normalize(query)
            if not normalized_query:
                unmatched.append(query)
                continue

            exact_hits = {
                index
                for index, candidate in enumerate(normalized_items)
                if candidate == normalized_query
            }
            if exact_hits:
                matched.update(exact_hits)
                continue

            fuzzy_hits = {
                index
                for index, candidate in enumerate(normalized_items)
                if normalized_query in candidate or candidate in normalized_query
            }
            if fuzzy_hits:
                matched.update(fuzzy_hits)
                continue

            unmatched.append(query)

        return matched, unmatched

    @staticmethod
    def render_list(item: StoredList, *, include_completed: bool = True) -> str:
        lines = [f"List: {item.title}", f"Path: {item.file_path}"]
        if item.tags:
            lines.append(f"Tags: {', '.join(item.tags)}")
        lines.append(f"Remaining: {item.remaining_count}")
        lines.append(f"Completed: {item.completed_count}")
        lines.append("")

        remaining = [entry for entry in item.items if not entry.done]
        completed = [entry for entry in item.items if entry.done]

        if remaining:
            lines.append("Open items:")
            lines.extend(f"- [ ] {entry.text}" for entry in remaining)
            lines.append("")

        if include_completed and completed:
            lines.append("Completed items:")
            lines.extend(f"- [x] {entry.text}" for entry in completed)
            lines.append("")

        if not item.items:
            lines.append("(empty list)")

        return "\n".join(lines).strip()

    def render_summary(self, item: StoredList) -> str:
        """Render a one-line summary for list browsing."""
        return (
            f"- {item.title} ({item.remaining_count} open, {item.completed_count} completed)"
            f" - {item.file_path}"
        )
