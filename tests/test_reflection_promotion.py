"""Tests for reflection promotion system."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.reflection import (
    BOOTSTRAP_SECTIONS,
    BootstrapEditProposal,
    ReflectionCandidate,
    ReflectionPromoter,
    ReflectionType,
)
from hermitcrab.providers.base import LLMProvider, LLMResponse


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_provider():
    """Create a mock LLM provider."""
    provider = MagicMock(spec=LLMProvider)
    provider.chat = AsyncMock()
    provider.get_default_model = MagicMock(return_value="test-model")
    return provider


@pytest.fixture
def promoter(temp_workspace, mock_provider):
    """Create a ReflectionPromoter instance."""
    return ReflectionPromoter(
        workspace=temp_workspace,
        provider=mock_provider,
        model="test-model",
        target_files=["AGENTS.md", "SOUL.md"],
        max_file_lines=100,
    )


class TestBootstrapEditProposal:
    """Tests for BootstrapEditProposal validation."""

    def test_valid_proposal(self):
        """Test valid proposal passes validation."""
        proposal = BootstrapEditProposal(
            target_file="AGENTS.md",
            section="## Self-Improvements",
            content="Always read files before editing",
            reason="Test reflection",
            reflection_type="mistake",
            confidence=0.9,
        )
        assert proposal.validate() == []

    def test_invalid_target_file(self):
        """Test invalid target file fails validation."""
        proposal = BootstrapEditProposal(
            target_file="INVALID.md",
            section="## Section",
            content="Test content",
            reason="Test",
            reflection_type="mistake",
        )
        errors = proposal.validate()
        assert any("Invalid target file" in error for error in errors)

    def test_missing_content(self):
        """Test missing content fails validation."""
        proposal = BootstrapEditProposal(
            target_file="AGENTS.md",
            section="## Section",
            content="",
            reason="Test",
            reflection_type="mistake",
        )
        errors = proposal.validate()
        assert any("Content is required" in error for error in errors)

    def test_missing_reason(self):
        """Test missing reason fails validation."""
        proposal = BootstrapEditProposal(
            target_file="AGENTS.md",
            section="## Section",
            content="Test content",
            reason="",
            reflection_type="mistake",
        )
        errors = proposal.validate()
        assert any("Reason is required" in error for error in errors)


class TestReflectionPromoter:
    """Tests for ReflectionPromoter class."""

    def test_init_default_target_files(self, temp_workspace, mock_provider):
        """Test promoter initializes with default target files."""
        promoter = ReflectionPromoter(
            workspace=temp_workspace,
            provider=mock_provider,
            model="test-model",
        )
        assert promoter.target_files == list(BOOTSTRAP_SECTIONS.keys())

    def test_init_custom_target_files(self, temp_workspace, mock_provider):
        """Test promoter initializes with custom target files."""
        promoter = ReflectionPromoter(
            workspace=temp_workspace,
            provider=mock_provider,
            model="test-model",
            target_files=["AGENTS.md"],
        )
        assert promoter.target_files == ["AGENTS.md"]

    def test_get_bootstrap_file_path(self, promoter, temp_workspace):
        """Test bootstrap file path resolution."""
        path = promoter._get_bootstrap_file_path("AGENTS.md")
        assert path == temp_workspace / "AGENTS.md"

    def test_read_nonexistent_file(self, promoter):
        """Test reading nonexistent file returns empty string."""
        content = promoter._read_bootstrap_file("NONEXISTENT.md")
        assert content == ""

    def test_write_and_read_file(self, promoter, temp_workspace):
        """Test writing and reading bootstrap file."""
        test_content = "# Test Content\n\nTest body"
        promoter._write_bootstrap_file("TEST.md", test_content)
        
        read_content = promoter._read_bootstrap_file("TEST.md")
        assert read_content == test_content

    def test_append_to_new_section(self, promoter, temp_workspace):
        """Test appending to a new section creates it."""
        # Start with empty file
        promoter._write_bootstrap_file("AGENTS.md", "")
        
        content = "New instruction here"
        result = promoter._append_to_section(
            "AGENTS.md",
            "## Self-Improvements from Reflection",
            content,
        )
        
        assert "## Self-Improvements from Reflection" in result
        assert content in result

    def test_append_to_existing_section(self, promoter, temp_workspace):
        """Test appending to existing section."""
        initial = """# Agents

## Self-Improvements from Reflection

Old instruction
"""
        promoter._write_bootstrap_file("AGENTS.md", initial)
        
        new_content = "New instruction"
        result = promoter._append_to_section(
            "AGENTS.md",
            "## Self-Improvements from Reflection",
            new_content,
        )
        
        assert "Old instruction" in result
        assert "New instruction" in result
        assert result.index("Old instruction") < result.index("New instruction")

    @pytest.mark.asyncio
    async def test_propose_edits_empty_reflections(self, promoter):
        """Test proposing edits with no reflections returns empty list."""
        proposals = await promoter.propose_edits_from_reflections([])
        assert proposals == []

    @pytest.mark.asyncio
    async def test_propose_edits_from_reflections(self, promoter, mock_provider):
        """Test proposing edits from reflections."""
        # Mock LLM response
        mock_response = LLMResponse(
            content='''{
                "edits": [
                    {
                        "target_file": "AGENTS.md",
                        "content": "Always verify before editing",
                        "reason": "Test reflection",
                        "reflection_type": "mistake"
                    }
                ]
            }''',
        )
        mock_provider.chat.return_value = mock_response
        
        reflections = [
            ReflectionCandidate(
                type=ReflectionType.MISTAKE,
                title="Test reflection",
                content="Test content",
                tool_involved="edit_file",
            )
        ]
        
        proposals = await promoter.propose_edits_from_reflections(reflections)
        
        assert len(proposals) == 1
        assert proposals[0].target_file == "AGENTS.md"
        assert proposals[0].reason == "Test reflection"

    @pytest.mark.asyncio
    async def test_propose_edits_filters_by_target_files(self, promoter, mock_provider):
        """Test that proposals are filtered by target files."""
        # Mock LLM response with non-target file
        mock_response = LLMResponse(
            content='''{
                "edits": [
                    {
                        "target_file": "TOOLS.md",
                        "content": "Test content",
                        "reason": "Test",
                        "reflection_type": "mistake"
                    }
                ]
            }''',
        )
        mock_provider.chat.return_value = mock_response
        
        reflections = [
            ReflectionCandidate(
                type=ReflectionType.MISTAKE,
                title="Test",
                content="Test content",
            )
        ]
        
        proposals = await promoter.propose_edits_from_reflections(reflections)
        
        # TOOLS.md not in target_files (only AGENTS.md, SOUL.md)
        assert len(proposals) == 0

    @pytest.mark.asyncio
    async def test_apply_edits(self, promoter, temp_workspace):
        """Test applying edit proposals."""
        # Create empty file
        promoter._write_bootstrap_file("AGENTS.md", "")
        
        proposals = [
            BootstrapEditProposal(
                target_file="AGENTS.md",
                section="## Self-Improvements from Reflection",
                content="Test instruction",
                reason="Test reflection",
                reflection_type="mistake",
            )
        ]
        
        applied = await promoter.apply_edits(proposals, use_smart_insert=False)
        
        assert "AGENTS.md" in applied
        assert len(applied["AGENTS.md"]) == 1
        assert applied["AGENTS.md"][0] == "Test reflection"
        
        # Verify file content
        content = promoter._read_bootstrap_file("AGENTS.md")
        assert "Test instruction" in content

    @pytest.mark.asyncio
    async def test_promote_reflections_full_pipeline(self, promoter, mock_provider):
        """Test full promotion pipeline."""
        # Mock LLM response for proposal generation
        mock_response = LLMResponse(
            content='''{
                "edits": [
                    {
                        "target_file": "AGENTS.md",
                        "section": "## Self-Improvements from Reflection",
                        "content": "Always read before editing",
                        "reason": "Edit without read error",
                        "reflection_type": "mistake"
                    }
                ]
            }''',
        )
        mock_provider.chat.return_value = mock_response
        
        reflections = [
            ReflectionCandidate(
                type=ReflectionType.MISTAKE,
                title="Edit without read error",
                content="edit_file failed because file wasn't read first",
                tool_involved="edit_file",
                error_pattern="file not found",
            )
        ]
        
        applied = await promoter.promote_reflections(reflections)
        
        assert "AGENTS.md" in applied
        assert len(applied["AGENTS.md"]) == 1

    @pytest.mark.asyncio
    async def test_promote_reflections_with_notification(self, promoter, mock_provider):
        """Test promotion with user notification."""
        # Mock LLM response
        mock_response = LLMResponse(
            content='''{
                "edits": [
                    {
                        "target_file": "AGENTS.md",
                        "section": "## Self-Improvements from Reflection",
                        "content": "Test content",
                        "reason": "Test reason",
                        "reflection_type": "mistake"
                    }
                ]
            }''',
        )
        mock_provider.chat.return_value = mock_response
        
        # Mock notification callback
        notify_callback = AsyncMock()
        
        reflections = [
            ReflectionCandidate(
                type=ReflectionType.MISTAKE,
                title="Test",
                content="Test content",
            )
        ]
        
        await promoter.promote_reflections(reflections, notify_callback=notify_callback)
        
        notify_callback.assert_called_once()
        call_args = notify_callback.call_args[0][0]
        assert "🧠 Self-Improvement" in call_args
        assert "AGENTS.md" in call_args

    @pytest.mark.asyncio
    async def test_promote_reflections_no_edits_generated(self, promoter, mock_provider):
        """Test promotion when LLM generates no edits."""
        # Mock LLM response with no edits
        mock_response = LLMResponse(content='{"edits": []}')
        mock_provider.chat.return_value = mock_response
        
        reflections = [
            ReflectionCandidate(
                type=ReflectionType.INSIGHT,
                title="Test",
                content="Test content",
            )
        ]
        
        notify_callback = AsyncMock()
        applied = await promoter.promote_reflections(reflections, notify_callback=notify_callback)
        
        assert applied == {}
        notify_callback.assert_not_called()

    def test_check_file_size_no_archive_needed(self, promoter, temp_workspace):
        """Test file size check when no archiving needed."""
        # Create file under limit
        lines = ["# Test"] + [f"Line {i}" for i in range(50)]
        content = "\n".join(lines)
        promoter._write_bootstrap_file("AGENTS.md", content)
        
        # Should not archive (under 100 line limit)
        promoter._check_file_size_and_archive("AGENTS.md")
        
        # File should be unchanged
        read_content = promoter._read_bootstrap_file("AGENTS.md")
        assert read_content == content

    def test_check_file_size_archive_needed(self, promoter, temp_workspace):
        """Test file size check triggers archiving."""
        # Create file over limit
        lines = ["# Test"] + [f"Line {i}" for i in range(150)]
        content = "\n".join(lines)
        promoter._write_bootstrap_file("AGENTS.md", content)
        
        # Should archive (over 100 line limit)
        promoter._check_file_size_and_archive("AGENTS.md")
        
        # Archive file should exist
        archive_files = list(temp_workspace.glob("AGENTS.md.archived.*"))
        assert len(archive_files) == 1
        
        # Main file should be trimmed
        read_content = promoter._read_bootstrap_file("AGENTS.md")
        assert len(read_content.split("\n")) < 100
