"""Tests for the new reflection service."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.reflection import ReflectionService


def make_digest(**overrides):
    base = {
        "session_key": "test-session",
        "channel": "cli",
        "chat_id": "direct",
        "first_timestamp": "2026-03-11T10:00:00+00:00",
        "last_timestamp": "2026-03-11T10:05:00+00:00",
        "event_lines": ["- User: Keep answers short."],
        "user_requests": ["Keep answers short."],
        "user_corrections": ["Keep answers short."],
        "outcomes": [],
        "failures": [],
        "wikilinks": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def memory_store(temp_workspace):
    """Create a MemoryStore instance with a temporary workspace."""
    return MemoryStore(temp_workspace)


@pytest.fixture
def mock_provider():
    """Create a mock LLM provider."""
    provider = MagicMock()

    async def mock_chat(**kwargs):
        response = MagicMock()
        response.content = '{"skip": true, "reason": "Default skip for testing"}'
        return response

    provider.chat = AsyncMock(side_effect=mock_chat)
    return provider


class TestReflectionService:
    """Test ReflectionService."""

    @pytest.mark.asyncio
    async def test_reflect_on_session_skips_empty(self, memory_store, mock_provider):
        """Test that empty sessions are skipped."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        await service.reflect_on_session(
            messages=[],
            session_key="test-session",
            digest=make_digest(),
        )

        # LLM should not be called
        mock_provider.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_reflect_on_session_skips_when_llm_returns_skip(self, memory_store, mock_provider):
        """Test that sessions are skipped when LLM returns skip=true."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        # Mock LLM response with skip
        async def skip_chat(**kwargs):
            response = MagicMock()
            response.content = '{"skip": true, "reason": "No new insights"}'
            return response
        mock_provider.chat.side_effect = skip_chat

        await service.reflect_on_session(
            messages=[{"role": "user", "content": "Hello"}],
            session_key="test-session",
            digest=make_digest(user_requests=["Hello"], user_corrections=[]),
        )

        # LLM should be called but no reflection written
        assert mock_provider.chat.called

    @pytest.mark.asyncio
    async def test_reflect_on_session_writes_reflection(self, memory_store, mock_provider):
        """Test that reflections are written to memory."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        # Mock LLM response with reflection
        async def reflection_chat(**kwargs):
            response = MagicMock()
            response.content = '''
            {
                "title": "User prefers concise answers",
                "content": "I learned that the user prefers brief, direct answers rather than long explanations.",
                "type": "preference",
                "evidence": "The user explicitly asked for a short answer and responded positively to a brief reply.",
                "should_promote": false
            }
            '''
            return response
        mock_provider.chat.side_effect = reflection_chat

        await service.reflect_on_session(
            messages=[
                {"role": "user", "content": "Give me a short answer"},
                {"role": "assistant", "content": "Sure, brief response here."},
            ],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Give me a short answer"],
                user_corrections=["Give me a short answer"],
            ),
        )

        # Check reflection was written
        reflections = memory_store.list_memories("reflections")
        assert len(reflections) == 1
        assert reflections[0].title == "User prefers concise answers"
        assert "preference" in reflections[0].tags

    @pytest.mark.asyncio
    async def test_reflect_on_session_promotes_when_flagged(self, memory_store, mock_provider, temp_workspace):
        """Test that reflections are promoted when should_promote=true."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=True,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        # Mock LLM response with promotion
        async def promote_chat(**kwargs):
            response = MagicMock()
            response.content = '''
            {
                "title": "Task status values",
                "content": "I learned to use 'open' not 'pending' for task status.",
                "type": "correction",
                "evidence": "During the session I used status open and avoided the invalid pending variant.",
                "should_promote": true,
                "promote_to": "TOOLS.md",
                "promote_content": "Task status values: use 'open', 'in_progress', 'done', 'deferred'"
            }
            '''
            return response
        mock_provider.chat.side_effect = promote_chat

        await service.reflect_on_session(
            messages=[
                {"role": "user", "content": "Create a task"},
                {"role": "assistant", "content": "Using status: open"},
            ],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Create a task"],
                user_corrections=["Use status open, not pending."],
            ),
        )

        # Check reflection was written
        reflections = memory_store.list_memories("reflections")
        assert len(reflections) == 1

        # Check bootstrap file was created/updated
        tools_file = temp_workspace / "TOOLS.md"
        assert tools_file.exists()
        content = tools_file.read_text()
        assert "Task status values" in content

    @pytest.mark.asyncio
    async def test_parse_response_handles_invalid_json(self, memory_store, mock_provider):
        """Test that invalid JSON is handled gracefully."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        # Test parsing invalid JSON
        result = service._parse_response("This is not JSON")
        assert result["skip"] is True
        assert "Invalid response format" in result["reason"]

    @pytest.mark.asyncio
    async def test_parse_response_extracts_json_from_text(self, memory_store, mock_provider):
        """Test that JSON is extracted from surrounding text."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        # Test parsing JSON embedded in text
        text = '''
        Here's my reflection:
        {"title": "Test", "content": "Test content", "type": "insight"}
        Hope that helps!
        '''
        result = service._parse_response(text)
        assert result["title"] == "Test"
        assert result["content"] == "Test content"

    @pytest.mark.asyncio
    async def test_parse_response_handles_relaxed_json(self, memory_store, mock_provider):
        """Relaxed JSON with trailing commas should still parse."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        text = """
        ```json
        {
          "title": "Test",
          "content": "Test content",
          "type": "insight",
        }
        ```
        """
        result = service._parse_response(text)
        assert result["title"] == "Test"
        assert result["content"] == "Test content"

    @pytest.mark.asyncio
    async def test_reflect_on_session_skips_duplicate_reflection(self, memory_store, mock_provider):
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        memory_store.write_reflection(
            title="User prefers concise answers",
            content="I learned that the user prefers brief, direct answers rather than long explanations.",
            tags=["preference", "reflection", "learning"],
            context="Evidence: The user explicitly asked for short answers in the previous session.",
        )

        async def duplicate_chat(**kwargs):
            response = MagicMock()
            response.content = """
            {
                "title": "User prefers concise answers",
                "content": "I learned that the user prefers brief, direct answers instead of long explanations.",
                "type": "preference",
                "evidence": "The user again asked for a short answer in this session.",
                "should_promote": false
            }
            """
            return response

        mock_provider.chat.side_effect = duplicate_chat

        await service.reflect_on_session(
            messages=[{"role": "user", "content": "Short answer please"}],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Short answer please"],
                user_corrections=["Short answer please"],
            ),
        )

        reflections = memory_store.list_memories("reflections")
        assert len(reflections) == 1

    @pytest.mark.asyncio
    async def test_reflect_on_session_skips_contradictory_preference(self, memory_store, mock_provider):
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        memory_store.write_reflection(
            title="Answer length preference",
            content="I learned that the user prefers concise answers.",
            tags=["preference", "reflection", "learning"],
            context="Evidence: The user asked me to keep replies brief.",
        )

        async def contradictory_chat(**kwargs):
            response = MagicMock()
            response.content = """
            {
                "title": "Answer length preference",
                "content": "I learned that the user prefers detailed, verbose answers.",
                "type": "preference",
                "evidence": "The user asked for more detail this time.",
                "should_promote": false
            }
            """
            return response

        mock_provider.chat.side_effect = contradictory_chat

        await service.reflect_on_session(
            messages=[{"role": "user", "content": "Explain in detail"}],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Explain in detail"],
                user_corrections=["Explain in detail"],
            ),
        )

        reflections = memory_store.list_memories("reflections")
        assert len(reflections) == 1

    @pytest.mark.asyncio
    async def test_reflect_on_session_rejects_tool_failure_reflection(self, memory_store, mock_provider):
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        async def tool_failure_chat(**kwargs):
            response = MagicMock()
            response.content = """
            {
                "title": "Tool failure: read_file",
                "content": "I learned that read_file failed and should be retried.",
                "type": "insight",
                "evidence": "The session had a read_file tool error after a missing file.",
                "should_promote": false
            }
            """
            return response

        mock_provider.chat.side_effect = tool_failure_chat

        await service.reflect_on_session(
            messages=[{"role": "user", "content": "Please inspect the config"}],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Please inspect the config"],
                user_corrections=[],
                failures=["read_file: Error: File not found"],
            ),
        )

        reflections = memory_store.list_memories("reflections")
        assert reflections == []

    @pytest.mark.asyncio
    async def test_reflect_on_session_rejects_generic_placeholder_title(self, memory_store, mock_provider):
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        async def generic_title_chat(**kwargs):
            response = MagicMock()
            response.content = """
            {
                "title": "Short, descriptive title",
                "content": "I learned that the user prefers concise answers.",
                "type": "preference",
                "evidence": "The user asked me to keep the answer brief.",
                "should_promote": false
            }
            """
            return response

        mock_provider.chat.side_effect = generic_title_chat

        await service.reflect_on_session(
            messages=[{"role": "user", "content": "Keep it concise"}],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Keep it concise"],
                user_corrections=["Keep it concise"],
            ),
        )

        reflections = memory_store.list_memories("reflections")
        assert reflections == []

    @pytest.mark.asyncio
    async def test_reflect_on_session_accepts_missing_evidence_when_digest_supports_it(self, memory_store, mock_provider):
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        async def no_evidence_chat(**kwargs):
            response = MagicMock()
            response.content = """
            {
                "title": "User prefers concise answers",
                "content": "I learned that the user prefers concise answers and direct replies.",
                "type": "preference",
                "should_promote": false
            }
            """
            return response

        mock_provider.chat.side_effect = no_evidence_chat

        await service.reflect_on_session(
            messages=[{"role": "user", "content": "Please keep it concise"}],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Please keep it concise"],
                user_corrections=["Please keep it concise"],
            ),
        )

        reflections = memory_store.list_memories("reflections")
        assert len(reflections) == 1
        assert "Please keep it concise" in reflections[0].metadata.get("context", "")

    @pytest.mark.asyncio
    async def test_reflect_on_session_prioritizes_explicit_workflow_correction(
        self, memory_store, mock_provider
    ):
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        await service.reflect_on_session(
            messages=[{"role": "user", "content": "Do the whole project report."}],
            session_key="test-session",
            digest=make_digest(
                user_requests=["Create a full report and implementation plan."],
                user_corrections=[
                    (
                        "Instead of planning and delegating small tasks, you delegated the entire "
                        "thing to a weak subagent. Make a plan, break it into smaller tasks, and "
                        "do it yourself if needed."
                    )
                ],
                outcomes=["Compiled the final report after taking over failed subtask work."],
            ),
        )

        mock_provider.chat.assert_not_called()
        reflections = memory_store.list_memories("reflections")
        assert len(reflections) == 1
        assert reflections[0].title == "Maintain ownership of delegated tasks"
        assert "delegate only bounded subtasks" in reflections[0].content

    @pytest.mark.asyncio
    async def test_format_digest_includes_core_sections(self, memory_store, mock_provider):
        """Digest formatting preserves the structured reflection input."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        formatted = service._format_digest(
            make_digest(
                user_requests=["Short"],
                user_corrections=["Keep it short."],
                outcomes=["I answered briefly."],
                failures=["tool failure: ignore me"],
            )
        )
        assert "User requests:" in formatted
        assert "User corrections / expectations:" in formatted
        assert "Ignore these tool or provider failures:" in formatted

    @pytest.mark.asyncio
    async def test_format_recent_reflections_handles_empty(self, memory_store, mock_provider):
        """Test that empty recent reflections are handled."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        formatted = service._format_recent_reflections([])
        assert "No recent reflections" in formatted

    @pytest.mark.asyncio
    async def test_append_to_bootstrap_creates_new_file(self, memory_store, mock_provider, temp_workspace):
        """Test appending to non-existent bootstrap file."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        file_path = temp_workspace / "NEW_FILE.md"
        service._append_to_bootstrap(
            file_path=file_path,
            section="## New Section",
            content="New content here",
        )

        assert file_path.exists()
        content = file_path.read_text()
        assert "## New Section" in content
        assert "New content here" in content

    @pytest.mark.asyncio
    async def test_append_to_bootstrap_appends_to_existing_section(self, memory_store, mock_provider, temp_workspace):
        """Test appending to existing bootstrap section."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        # Create file with section
        file_path = temp_workspace / "TEST.md"
        file_path.write_text("## Existing Section\n\nOld content\n")

        service._append_to_bootstrap(
            file_path=file_path,
            section="## Existing Section",
            content="New content",
        )

        content = file_path.read_text()
        assert "Old content" in content
        assert "New content" in content
        # New content should come after old
        assert content.index("Old content") < content.index("New content")

    @pytest.mark.asyncio
    async def test_append_to_bootstrap_creates_new_section(self, memory_store, mock_provider, temp_workspace):
        """Test creating new section in existing file."""
        service = ReflectionService(
            memory=memory_store,
            chat_callable=mock_provider.chat,
            model="test-model",
            auto_promote=False,
            allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
            max_file_lines=500,
        )

        # Create file with different section
        file_path = temp_workspace / "TEST.md"
        file_path.write_text("## Other Section\n\nSome content\n")

        service._append_to_bootstrap(
            file_path=file_path,
            section="## New Section",
            content="New content",
        )

        content = file_path.read_text()
        assert "## Other Section" in content
        assert "## New Section" in content
        assert "New content" in content
