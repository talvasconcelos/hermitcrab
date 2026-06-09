"""Tests for session-history search tooling."""

from __future__ import annotations

import pytest

from hermitcrab.agent.tools.session_search import SessionSearchTool
from hermitcrab.session.manager import SessionManager


@pytest.mark.asyncio
async def test_session_search_recent_returns_tail_without_keyword_match(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("nostr:tal")
    session.add_message("user", "can you search what i asked you about dinner tonight?")
    session.add_message("assistant", "I found no matching dinner plans.")
    session.add_message("user", "dude WTF!! you said you didn't find anything like 2 messages ago!")
    manager.save(session)

    tool = SessionSearchTool(manager)
    result = await tool.execute(recent=True, max_results=1)

    assert "Found 1 recent session" in result
    assert "dinner tonight" in result
    assert "2 messages ago" in result


@pytest.mark.asyncio
async def test_session_search_keyword_still_returns_matches(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("nostr:tal")
    session.add_message("user", "i need some ideas for dinner")
    session.add_message("assistant", "try quesadillas")
    manager.save(session)

    tool = SessionSearchTool(manager)
    result = await tool.execute(query="quesadillas", max_results=1)

    assert "Found 1 matching session" in result
    assert "quesadillas" in result
