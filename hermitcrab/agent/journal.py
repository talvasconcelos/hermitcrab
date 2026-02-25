"""
Journal system for HermitCrab.

The journal is a daily narrative log written by the agent that answers:
"What happened today?"

It exists to:
- Help the user review past activity
- Help the agent reorient itself temporally
- Provide narrative context without storing truth

The journal is NOT memory:
- Never treated as authoritative knowledge
- Never automatically distilled into memory
- Never injected into prompts by default
- Does not affect decisions, facts, goals, tasks, or reflections
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from hermitcrab.utils.helpers import ensure_dir


class JournalStore:
    """
    Daily journal store with append-only semantics.

    All journal entries are stored as markdown files in workspace/journal/
    with one file per calendar day.

    Journal entries are:
    - Narrative and descriptive
    - Short and factual in tone
    - Append-only (never overwritten)
    - Separate from memory system
    """

    def __init__(self, workspace: Path):
        """
        Initialize journal store.

        Args:
            workspace: Path to workspace directory.
        """
        self.workspace = workspace
        self.journal_dir = ensure_dir(workspace / "journal")

    def _get_today_path(self) -> Path:
        """Get the journal file path for today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.journal_dir / f"{today}.md"

    def _get_date_path(self, date: datetime) -> Path:
        """Get the journal file path for a specific date."""
        date_str = date.strftime("%Y-%m-%d")
        return self.journal_dir / f"{date_str}.md"

    def _build_frontmatter(
        self,
        date: datetime,
        session_keys: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        Build YAML frontmatter for journal entry.

        Args:
            date: Entry date.
            session_keys: Optional list of session identifiers.
            tags: Optional freeform tags.

        Returns:
            YAML frontmatter string (including --- delimiters).
        """
        lines = ["---", f"date: {date.strftime('%Y-%m-%d')}"]

        if session_keys:
            lines.append("session_keys:")
            for key in session_keys:
                lines.append(f"  - {key}")

        if tags:
            lines.append("tags:")
            for tag in tags:
                lines.append(f"  - {tag}")

        lines.append("---")
        return "\n".join(lines)

    def write_entry(
        self,
        content: str,
        session_keys: list[str] | None = None,
        tags: list[str] | None = None,
        date: datetime | None = None,
    ) -> Path:
        """
        Write a journal entry for the specified date (defaults to today).

        Appends to existing file if entry already exists for the day.
        Never overwrites existing content.

        Args:
            content: Narrative journal content (markdown).
            session_keys: Optional session identifiers involved.
            tags: Optional freeform tags.
            date: Entry date (defaults to today UTC).

        Returns:
            Path to the journal file.

        Raises:
            ValueError: If content is empty.
        """
        if not content or not content.strip():
            raise ValueError("Journal content cannot be empty")

        date = date or datetime.now(timezone.utc)
        file_path = self._get_date_path(date)

        # Build the entry
        frontmatter = self._build_frontmatter(date, session_keys, tags)

        # Check if file exists to determine if we need to add frontmatter
        needs_frontmatter = not file_path.exists()

        # Build full content
        if needs_frontmatter:
            full_content = f"{frontmatter}\n\n{content.strip()}\n"
        else:
            # Append mode - just add content
            full_content = f"\n{content.strip()}\n"

        # Append to file (create if needed)
        mode = "a" if not needs_frontmatter else "w"
        with open(file_path, mode, encoding="utf-8") as f:
            f.write(full_content)

        logger.info(
            "Wrote journal entry: {} ({} bytes, {})",
            file_path.name,
            len(content),
            "new file" if needs_frontmatter else "appended",
        )

        return file_path

    def read_entry(self, date: datetime | None = None) -> str | None:
        """
        Read a journal entry for the specified date.

        Args:
            date: Date to read (defaults to today).

        Returns:
            Full file content including frontmatter, or None if not found.
        """
        date = date or datetime.now(timezone.utc)
        file_path = self._get_date_path(date)

        if not file_path.exists():
            return None

        try:
            return file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Failed to read journal entry {}: {}", file_path, e)
            return None

    def read_entry_body(self, date: datetime | None = None) -> str | None:
        """
        Read just the body content (excluding frontmatter) for a date.

        Args:
            date: Date to read (defaults to today).

        Returns:
            Body content only, or None if not found.
        """
        date = date or datetime.now(timezone.utc)
        file_path = self._get_date_path(date)

        if not file_path.exists():
            return None

        try:
            content = file_path.read_text(encoding="utf-8")

            # Skip frontmatter (between --- markers)
            if content.startswith("---"):
                end_marker = content.find("\n---", 3)
                if end_marker != -1:
                    return content[end_marker + 4 :].strip()

            return content.strip()
        except Exception as e:
            logger.error("Failed to read journal entry {}: {}", file_path, e)
            return None

    def list_entries(self, limit: int | None = None) -> list[Path]:
        """
        List journal entries sorted by date (newest first).

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of journal file paths.
        """
        if not self.journal_dir.exists():
            return []

        entries = sorted(
            self.journal_dir.glob("*.md"),
            key=lambda p: p.stem,
            reverse=True,
        )

        if limit:
            entries = entries[:limit]

        return entries

    def has_entry(self, date: datetime | None = None) -> bool:
        """
        Check if a journal entry exists for the specified date.

        Args:
            date: Date to check (defaults to today).

        Returns:
            True if entry exists.
        """
        date = date or datetime.now(timezone.utc)
        file_path = self._get_date_path(date)
        return file_path.exists()

    def get_entry_metadata(
        self,
        date: datetime | None = None,
    ) -> dict[str, Any] | None:
        """
        Parse and return metadata from a journal entry.

        Args:
            date: Date to read (defaults to today).

        Returns:
            Metadata dict with date, session_keys, tags; or None if not found.
        """
        date = date or datetime.now(timezone.utc)
        file_path = self._get_date_path(date)

        if not file_path.exists():
            return None

        try:
            content = file_path.read_text(encoding="utf-8")

            if not content.startswith("---"):
                return {"date": date.strftime("%Y-%m-%d")}

            # Parse frontmatter
            end_marker = content.find("\n---", 3)
            if end_marker == -1:
                return {"date": date.strftime("%Y-%m-%d")}

            frontmatter = content[4:end_marker]
            metadata: dict[str, Any] = {"date": date.strftime("%Y-%m-%d")}

            current_list: str | None = None
            for line in frontmatter.split("\n"):
                line_stripped = line.strip()

                if line_stripped.startswith("session_keys:"):
                    metadata["session_keys"] = []
                    current_list = "session_keys"
                elif line_stripped.startswith("tags:"):
                    metadata["tags"] = []
                    current_list = "tags"
                elif line_stripped.startswith("- "):
                    # List item
                    value = line_stripped[2:].strip()
                    if current_list and current_list in metadata:
                        metadata[current_list].append(value)
                elif line_stripped and not line.startswith(" "):
                    # New top-level field
                    current_list = None

            return metadata
        except Exception as e:
            logger.error("Failed to parse journal metadata {}: {}", file_path, e)
            return None
