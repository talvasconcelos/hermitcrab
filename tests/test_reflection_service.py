from __future__ import annotations

from dataclasses import dataclass

import pytest

from hermitcrab.agent.memory import MemoryStore
from hermitcrab.agent.reflection import ReflectionService
from hermitcrab.agent.session_digest import SessionDigest


@dataclass
class FakeResponse:
    content: str


def make_digest() -> SessionDigest:
    return SessionDigest(
        session_key="telegram:tal",
        channel="telegram",
        chat_id="tal",
        first_timestamp="2026-01-01T00:00:00Z",
        last_timestamp="2026-01-01T00:05:00Z",
        event_lines=[
            "- User: please delete these files",
            "- Assistant: I cannot delete files because I have no deletion tool",
            "- User: shell exec is available; use rm with exact paths",
        ],
        user_requests=["please delete these files"],
        user_corrections=["shell exec is available; use rm with exact paths"],
        outcomes=["User clarified the agent should use shell exec for exact file deletion"],
        failures=[],
        wikilinks=[],
        user_goal="Delete listed files safely",
        artifacts_changed=[],
        decisions_made=[],
        open_loops=[],
        assistant_responses=["I cannot delete files because I have no deletion tool"],
        signals={"user_turn_count": 2, "followup_user_turn_count": 1},
    )


def make_service(tmp_path, content: str) -> ReflectionService:
    async def fake_chat_callable(**_kwargs):
        return FakeResponse(content=content)

    return ReflectionService(
        memory=MemoryStore(tmp_path),
        chat_callable=fake_chat_callable,
        model="test-model",
        auto_promote=False,
        allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
        max_file_lines=200,
    )


@pytest.mark.asyncio
async def test_reflection_salvages_partial_but_grounded_json(tmp_path):
    service = make_service(
        tmp_path,
        '{"title":"Use shell for deletions","lesson":"I should treat exec/shell as a valid file operation path when explicit safe paths are provided.","scope":"tool","confidence":"80%"}',
    )

    outcome = await service.reflect_on_session(
        messages=[{"role": "user", "content": "shell exec is available; use rm with exact paths"}],
        session_key="telegram:tal",
        digest=make_digest(),
    )

    assert outcome.status == "saved"
    assert outcome.title == "Use shell for deletions"
    reflections = service.memory.list_memories("reflections")
    assert len(reflections) == 1
    assert "shell exec is available" in reflections[0].metadata["context"]


@pytest.mark.asyncio
async def test_reflection_uses_fallback_evidence_before_grounding(tmp_path):
    service = make_service(
        tmp_path,
        """{
          "title": "Delete Tool Confusion",
          "observation": "The assistant confused a missing delete API with inability to delete files.",
          "impact": "This blocks simple user requests unnecessarily.",
          "lesson": "I should use available shell execution for explicit scoped filesystem operations instead of claiming deletion is impossible.",
          "recommended_behavior": "When exec is available and the user gives exact file paths, use rm with explicit paths and verify each result.",
          "scope": "tool_usage",
          "confidence": 0.9,
          "should_promote": false,
          "promotion_target": "none",
          "promote_content": ""
        }""",
    )

    outcome = await service.reflect_on_session(
        messages=[{"role": "user", "content": "shell exec is available; use rm with exact paths"}],
        session_key="telegram:tal",
        digest=make_digest(),
    )

    assert outcome.status == "saved"
    reflections = service.memory.list_memories("reflections")
    assert "Evidence: shell exec is available; use rm with exact paths" in reflections[0].metadata["context"]


def test_labeled_reflection_response_is_accepted_with_aliases(tmp_path):
    service = make_service(tmp_path, "")
    parsed = service._parse_response(
        """
        Title: Use shell for exact deletions
        Observation: User corrected the agent about exec/shell deletion.
        Impact: Refusing safe deletion creates unnecessary manual work.
        Lesson: I should recognize shell exec as the filesystem operation tool when exact paths are provided.
        Recommended behavior: Use explicit rm paths, then verify with test -e.
        Scope: tools
        Confidence: 85%
        Evidence: shell exec is available; use rm with exact paths
        Should promote: no
        Promotion target: none
        """
    )

    valid, reason = service._validate_result(parsed, make_digest())

    assert valid, reason
    assert parsed["scope"] == "tool_usage"
    assert parsed["confidence"] == 0.85
