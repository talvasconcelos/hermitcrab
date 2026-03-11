"""Tests for the journal system."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.loop import AgentLoop


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def journal_store(temp_workspace: Path) -> JournalStore:
    """Create a journal store for testing."""
    return JournalStore(temp_workspace)


class TestJournalStore:
    """Test JournalStore functionality."""

    def test_init_creates_journal_directory(self, temp_workspace: Path):
        """Journal directory is created on initialization."""
        store = JournalStore(temp_workspace)
        assert store.journal_dir.exists()
        assert store.journal_dir.name == "journal"

    def test_write_entry_creates_file(self, journal_store: JournalStore):
        """Writing an entry creates a new file."""
        content = "Test journal entry"
        path = journal_store.write_entry(content)

        assert path.exists()
        assert path.name.startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        assert path.suffix == ".md"

    def test_write_entry_with_frontmatter(self, journal_store: JournalStore):
        """New entries include YAML frontmatter."""
        content = "Test entry"
        path = journal_store.write_entry(
            content,
            session_keys=["cli:test"],
            tags=["test", "session"],
        )

        text = path.read_text()
        assert text.startswith("---")
        assert "date:" in text
        assert "session_keys:" in text
        assert "cli:test" in text
        assert "tags:" in text
        assert "test" in text

    def test_write_entry_appends_to_existing_file(self, journal_store: JournalStore):
        """Subsequent entries append without overwriting."""
        content1 = "First entry"
        content2 = "Second entry"

        path1 = journal_store.write_entry(content1)
        path2 = journal_store.write_entry(content2)

        # Same file (same day)
        assert path1 == path2

        text = path1.read_text()
        assert "First entry" in text
        assert "Second entry" in text
        # Frontmatter appears only once
        assert text.count("---") == 2  # Opening and closing

    def test_write_entry_empty_content_raises(self, journal_store: JournalStore):
        """Empty content raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            journal_store.write_entry("")

        with pytest.raises(ValueError, match="cannot be empty"):
            journal_store.write_entry("   ")

    def test_write_entry_with_custom_date(self, journal_store: JournalStore):
        """Entries can be written for specific dates."""
        custom_date = datetime(2026, 2, 23, tzinfo=timezone.utc)
        content = "Historical entry"

        path = journal_store.write_entry(content, date=custom_date)

        assert path.name == "2026-02-23.md"
        assert path.exists()

    def test_read_entry_returns_full_content(self, journal_store: JournalStore):
        """Reading returns full file content including frontmatter."""
        content = "Test content"
        journal_store.write_entry(content, tags=["test"])

        read_content = journal_store.read_entry()
        assert read_content is not None
        assert "Test content" in read_content
        assert "---" in read_content

    def test_read_entry_not_found_returns_none(self, journal_store: JournalStore):
        """Reading non-existent entry returns None."""
        past_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = journal_store.read_entry(past_date)
        assert result is None

    def test_read_entry_body_excludes_frontmatter(self, journal_store: JournalStore):
        """Reading body returns content without frontmatter."""
        content = "Body content here"
        journal_store.write_entry(content, tags=["test"])

        body = journal_store.read_entry_body()
        assert body is not None
        assert "Body content here" in body
        assert "---" not in body
        assert "tags:" not in body

    def test_list_entries_returns_sorted(self, journal_store: JournalStore):
        """Entries are listed newest first."""
        # Write entries for different dates
        dates = [
            datetime(2026, 2, 21, tzinfo=timezone.utc),
            datetime(2026, 2, 23, tzinfo=timezone.utc),
            datetime(2026, 2, 22, tzinfo=timezone.utc),
        ]

        for date in dates:
            journal_store.write_entry(f"Entry for {date.strftime('%Y-%m-%d')}", date=date)

        entries = journal_store.list_entries()

        # Should be sorted by filename (date string) descending
        assert len(entries) == 3
        assert entries[0].stem == "2026-02-23"
        assert entries[1].stem == "2026-02-22"
        assert entries[2].stem == "2026-02-21"

    def test_list_entries_with_limit(self, journal_store: JournalStore):
        """Limit restricts number of entries returned."""
        for i in range(5):
            date = datetime(2026, 2, 21 + i, tzinfo=timezone.utc)
            journal_store.write_entry(f"Entry {i}", date=date)

        entries = journal_store.list_entries(limit=3)
        assert len(entries) == 3

    def test_has_entry_returns_true_for_existing(self, journal_store: JournalStore):
        """has_entry returns True for existing entries."""
        journal_store.write_entry("Test")
        assert journal_store.has_entry() is True

    def test_has_entry_returns_false_for_missing(self, journal_store: JournalStore):
        """has_entry returns False for missing entries."""
        past_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert journal_store.has_entry(past_date) is False

    def test_get_entry_metadata_parses_frontmatter(self, journal_store: JournalStore):
        """Metadata is correctly parsed from frontmatter."""
        journal_store.write_entry(
            "Content",
            session_keys=["cli:test", "nostr:abc"],
            tags=["session", "nostr"],
        )

        metadata = journal_store.get_entry_metadata()

        assert metadata is not None
        assert "date" in metadata
        assert "session_keys" in metadata
        assert "tags" in metadata
        assert len(metadata["session_keys"]) == 2
        assert len(metadata["tags"]) == 2

    def test_get_entry_metadata_no_file_returns_none(self, journal_store: JournalStore):
        """Metadata returns None for non-existent file."""
        past_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = journal_store.get_entry_metadata(past_date)
        assert result is None

    def test_journal_isolated_from_memory(self, temp_workspace: Path):
        """Journal does not create memory directories or files."""
        store = JournalStore(temp_workspace)
        store.write_entry("Test entry")

        # Journal directory exists
        assert (temp_workspace / "journal").exists()

        # Memory directory should NOT be created by journal
        memory_dir = temp_workspace / "memory"
        assert not memory_dir.exists()

    def test_append_mode_preserves_existing_content(self, journal_store: JournalStore):
        """Appending preserves all existing content."""
        # Write first entry with tags
        path1 = journal_store.write_entry(
            "First content",
            tags=["tag1"],
            session_keys=["session1"],
        )

        # Write second entry
        path2 = journal_store.write_entry(
            "Second content",
            tags=["tag2"],
        )

        assert path1 == path2
        content = path1.read_text()

        # Both contents present
        assert "First content" in content
        assert "Second content" in content

        # Original frontmatter preserved
        assert "session1" in content
        assert "tag1" in content


class TestJournalFormatting:
    def test_formatted_entry_includes_timestamp_channel_and_wikilinks(self, temp_workspace: Path):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = SimpleNamespace(
            session_key="nostr:abc123",
            channel="nostr",
            chat_id="abc123",
            first_timestamp="2026-03-11T10:00:00+00:00",
            last_timestamp="2026-03-11T10:05:00+00:00",
            event_lines=["- User: Please fix [[Release Notes]]"],
            user_requests=["Please fix release notes"],
            user_corrections=[],
            outcomes=[],
            failures=[],
            wikilinks=["[[Release Notes]]", "[[Ship v0.4]]"],
        )

        rendered = agent._format_journal_entry(digest, "I revised [[Release Notes]] for [[Ship v0.4]].")

        assert "10:05 UTC" in rendered
        assert "nostr" in rendered
        assert "nostr:abc123" in rendered
        assert "[[Release Notes]]" in rendered
        assert "[[Ship v0.4]]" in rendered

    def test_polite_request_is_not_treated_as_correction(self, temp_workspace: Path):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = agent._build_session_digest(
            [{"role": "user", "content": "Please summarize yesterday's work.", "timestamp": "2026-03-11T10:00:00+00:00"}],
            "cli:direct",
        )

        assert digest.user_requests == ["Please summarize yesterday's work."]
        assert digest.user_corrections == []
