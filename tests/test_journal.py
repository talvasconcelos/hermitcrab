"""Tests for the journal system."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.journal import JournalStore
from hermitcrab.agent.journal_background import JournalBackgroundManager
from hermitcrab.agent.loop import AgentLoop
from hermitcrab.agent.session_digest import SessionDigestBuilder


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


def test_journal_background_rejects_generic_body_when_digest_has_specifics():
    digest = SimpleNamespace(
        user_goal="Create the weekly grocery list",
        artifacts_changed=["knowledge/notes/checklists/grocery.md"],
        outcomes=["Created grocery checklist"],
        open_loops=["Need to add cleaning supplies later"],
    )

    assert (
        JournalBackgroundManager._is_usable_journal_body(
            "I worked on it. I helped the user. I completed the task.",
            digest,
        )
        is False
    )


def test_journal_prompt_requires_agent_point_of_view(temp_workspace: Path):
    journal = JournalStore(temp_workspace)
    reflection_service = MagicMock()
    manager = JournalBackgroundManager(
        journal=journal,
        reflection_service=reflection_service,
        digest_builder=SessionDigestBuilder(),
        chat_callable=AsyncMock(),
        get_model_for_job=lambda job_class: "test-model",
        strip_think=lambda text: text,
        reasoning_effort=None,
    )
    digest = SimpleNamespace(
        session_key="cli:direct",
        channel="cli",
        chat_id="direct",
        first_timestamp="2026-04-03T10:00:00+00:00",
        last_timestamp="2026-04-03T10:05:00+00:00",
        wikilinks=[],
        event_lines=["- User asked to update a preference", "- I updated memory"],
        user_goal="Update a shopping preference",
        artifacts_changed=["memory/facts/shopping-day.md"],
        outcomes=["Updated shopping preference in memory"],
        decisions_made=[],
        open_loops=["Need to verify the reminder time"],
    )

    prompt = manager._build_journal_prompt(digest)

    assert "assistant's own journal" in prompt
    assert "'I' always refers to the assistant" in prompt


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

        rendered = agent._format_journal_entry(
            digest, "I revised [[Release Notes]] for [[Ship v0.4]]."
        )

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
            [
                {
                    "role": "user",
                    "content": "Please summarize yesterday's work.",
                    "timestamp": "2026-03-11T10:00:00+00:00",
                }
            ],
            "cli:direct",
        )

        assert digest.user_requests == ["Please summarize yesterday's work."]
        assert digest.user_corrections == []

    def test_digest_ignores_synthetic_subagent_completion_prompt(self, temp_workspace: Path):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = agent._build_session_digest(
            [
                {
                    "role": "user",
                    "content": "Pull the open tasks with a subagent.",
                    "timestamp": "2026-03-18T11:58:09+00:00",
                },
                {
                    "role": "user",
                    "content": (
                        "[Subagent 'Get open tasks' completed successfully]\n\n"
                        'Task: Use read_memory(category="tasks") to get all tasks.\n\n'
                        "Result:\nTask completed but no final response was generated.\n\n"
                        "Write a user-facing completion update."
                    ),
                    "timestamp": "2026-03-18T11:58:20+00:00",
                },
            ],
            "cli:direct",
        )

        assert digest.user_requests == ["Pull the open tasks with a subagent."]
        assert any("Subagent reported back for task" in line for line in digest.event_lines)

    def test_fallback_journal_uses_real_user_request_not_subagent_prompt(
        self, temp_workspace: Path
    ):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = agent._build_session_digest(
            [
                {
                    "role": "user",
                    "content": "Pull the open tasks with a subagent.",
                    "timestamp": "2026-03-18T11:58:09+00:00",
                },
                {
                    "role": "assistant",
                    "content": "The subagent completed but didn't return useful output. Let me just check directly:",
                    "tool_calls": [
                        {"function": {"name": "read_memory", "arguments": '{"category": "tasks"}'}}
                    ],
                    "timestamp": "2026-03-18T11:58:20+00:00",
                },
            ],
            "cli:direct",
        )

        body = agent._build_fallback_journal_body(digest)

        assert "Pull the open tasks with a subagent." in body
        assert "didn't return useful output" not in body

    def test_digest_keeps_primary_goal_when_user_pings_for_status(self, temp_workspace: Path):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = agent._build_session_digest(
            [
                {
                    "role": "user",
                    "content": "Draft the accountant implementation flow and save it in the project folder.",
                    "timestamp": "2026-03-21T11:33:34+00:00",
                },
                {
                    "role": "assistant",
                    "content": "I'll pull the details and write it.",
                    "timestamp": "2026-03-21T11:34:00+00:00",
                },
                {
                    "role": "user",
                    "content": "what's the status on that research? any blockers?",
                    "timestamp": "2026-03-21T11:42:27+00:00",
                },
            ],
            "cli:direct",
        )

        assert (
            digest.user_goal
            == "Draft the accountant implementation flow and save it in the project folder."
        )
        assert digest.user_corrections == ["what's the status on that research? any blockers?"]

    def test_successful_write_tool_counts_as_outcome(self, temp_workspace: Path):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = agent._build_session_digest(
            [
                {
                    "role": "user",
                    "content": "Write the accountant implementation flow in the project folder.",
                    "timestamp": "2026-03-21T11:33:34+00:00",
                },
                {
                    "role": "assistant",
                    "content": "Let me write the doc now.",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "/tmp/accountant-implementation-flow.md"}',
                            }
                        }
                    ],
                    "timestamp": "2026-03-21T11:42:27+00:00",
                },
                {
                    "role": "tool",
                    "name": "write_file",
                    "content": "Successfully wrote 14631 bytes to /tmp/accountant-implementation-flow.md",
                    "timestamp": "2026-03-21T11:42:28+00:00",
                },
                {
                    "role": "user",
                    "content": "did you just died on me?",
                    "timestamp": "2026-03-21T11:43:10+00:00",
                },
            ],
            "cli:direct",
        )

        assert any("Successfully wrote 14631 bytes" in outcome for outcome in digest.outcomes)
        assert digest.open_loops == []

    def test_journal_event_trace_filters_raw_tool_mechanics(self, temp_workspace: Path):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = SimpleNamespace(
            session_key="cli:direct",
            channel="cli",
            chat_id="direct",
            first_timestamp="2026-03-18T11:58:09+00:00",
            last_timestamp="2026-03-18T11:58:20+00:00",
            event_lines=[
                "- User: Pull the open tasks with a subagent.",
                "- Assistant used spawn: Get open tasks",
                "- Assistant used read_memory: tasks",
                "- Assistant saved fact [[Fast local model]].",
            ],
            user_requests=["Pull the open tasks with a subagent."],
            user_corrections=[],
            outcomes=[],
            failures=[],
            wikilinks=["[[Fast local model]]"],
        )

        trace = agent._build_journal_event_trace(digest)

        assert "- User: Pull the open tasks with a subagent." in trace
        assert "- Assistant saved fact [[Fast local model]]." in trace
        assert not any("Assistant used spawn" in line for line in trace)
        assert not any("Assistant used read_memory" in line for line in trace)

    def test_digest_ignores_generic_assistant_self_description(self, temp_workspace: Path):
        bus = MagicMock()
        bus.consume_inbound = AsyncMock()
        bus.publish_outbound = AsyncMock()
        provider = MagicMock()
        provider.chat = AsyncMock()
        provider.get_default_model = MagicMock(return_value="test-model")

        agent = AgentLoop(bus=bus, provider=provider, workspace=temp_workspace)
        digest = agent._build_session_digest(
            [
                {
                    "role": "user",
                    "content": "Help me organize this product idea.",
                    "timestamp": "2026-03-18T16:30:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": (
                        "I am a helpful assistant, and I am here to assist you. "
                        "I am a knowledgeable assistant that can provide information and guidance."
                    ),
                    "timestamp": "2026-03-18T16:31:00+00:00",
                },
            ],
            "cli:direct",
        )

        assert digest.user_requests == ["Help me organize this product idea."]
        assert not any("helpful assistant" in line.lower() for line in digest.event_lines)

    def test_truncated_journal_body_is_rejected(self):
        digest = SimpleNamespace(
            user_goal="Write the implementation flow.",
            artifacts_changed=["law-firm-implementation-flow.md"],
            outcomes=[],
            open_loops=[],
        )

        assert not JournalBackgroundManager._is_usable_journal_body(
            (
                "I wrote the implementation flow after the original delegation path failed. "
                "The main artifact was [[law-firm-implementation-flow.md]] and I kept the work moving. "
                "The [[real-estate-im"
            ),
            digest,
        )
