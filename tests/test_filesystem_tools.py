from __future__ import annotations

import pytest

from hermitcrab.agent.tools.filesystem import ReadFileTool
from hermitcrab.agent.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_read_file_returns_full_content_by_default(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("abcdef", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)

    result = await tool.execute("notes.md")

    assert result == "abcdef"


@pytest.mark.asyncio
async def test_read_file_supports_offset_and_limit(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("abcdef", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)

    result = await tool.execute("notes.md", offset=2, limit=3)

    assert "offset=2" in result
    assert "returned_chars=3" in result
    assert "next_offset=5" in result
    assert result.endswith("cde")


@pytest.mark.asyncio
async def test_read_file_registry_coerces_string_offsets(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("abcdef", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace=tmp_path))

    result = await registry.execute("read_file", {"path": "notes.md", "offset": "4", "limit": "2"})

    assert "offset=4" in result
    assert "next_offset=null" in result
    assert result.endswith("ef")
