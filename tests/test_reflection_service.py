from __future__ import annotations

from dataclasses import dataclass
import json

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


def make_service(tmp_path, content: str, *, auto_promote: bool = False) -> ReflectionService:
    async def fake_chat_callable(**_kwargs):
        return FakeResponse(content=content)

    return ReflectionService(
        memory=MemoryStore(tmp_path),
        chat_callable=fake_chat_callable,
        model="test-model",
        auto_promote=auto_promote,
        allowed_targets=["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
        max_file_lines=200,
    )


def reflection_audit_events(tmp_path) -> list[dict]:
    audit_path = tmp_path / "memory" / "reflection_audit.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


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


@pytest.mark.asyncio
async def test_reflection_audits_saved_decision(tmp_path):
    service = make_service(
        tmp_path,
        """{
          "title": "Use Shell For Deletions",
          "observation": "The user corrected the agent about shell deletion.",
          "impact": "This avoids false refusal of simple file operations.",
          "lesson": "I should use available shell execution for explicit scoped filesystem operations.",
          "recommended_behavior": "When exec is available and the user gives exact file paths, use explicit rm paths and verify.",
          "scope": "tool_usage",
          "confidence": 0.9,
          "evidence": "shell exec is available; use rm with exact paths",
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
    events = reflection_audit_events(tmp_path)
    assert events[-1]["event"] == "saved"
    assert events[-1]["reason"] == "saved"
    assert events[-1]["session_key"] == "telegram:tal"
    assert events[-1]["title"] == "Use Shell For Deletions"
    assert events[-1]["scope"] == "tool_usage"
    assert events[-1]["evidence"] == "shell exec is available; use rm with exact paths"


@pytest.mark.asyncio
async def test_reflection_audits_validation_failure(tmp_path):
    service = make_service(
        tmp_path,
        """{
          "title": "Weak",
          "observation": "The user corrected the agent.",
          "impact": "It matters.",
          "lesson": "I should improve.",
          "recommended_behavior": "Improve.",
          "scope": "assistant_behavior",
          "confidence": 0.2,
          "evidence": "shell exec is available; use rm with exact paths"
        }""",
    )

    outcome = await service.reflect_on_session(
        messages=[{"role": "user", "content": "shell exec is available; use rm with exact paths"}],
        session_key="telegram:tal",
        digest=make_digest(),
    )

    assert outcome.status == "skipped"
    assert service.memory.list_memories("reflections") == []
    events = reflection_audit_events(tmp_path)
    assert events[-1]["event"] == "validation_failed"
    assert events[-1]["reason"] == "low_or_invalid_confidence"


@pytest.mark.asyncio
async def test_reflection_audits_skip_response(tmp_path):
    service = make_service(tmp_path, '{"skip": true, "reason": "No new insights"}')

    outcome = await service.reflect_on_session(
        messages=[{"role": "user", "content": "shell exec is available; use rm with exact paths"}],
        session_key="telegram:tal",
        digest=make_digest(),
    )

    assert outcome.status == "skipped"
    events = reflection_audit_events(tmp_path)
    assert events[-1]["event"] == "skipped"
    assert events[-1]["reason"] == "No new insights"


@pytest.mark.asyncio
async def test_reflection_audits_duplicate(tmp_path):
    content = """{
      "title": "Use Shell For Deletions",
      "observation": "The user corrected the agent about shell deletion.",
      "impact": "This avoids false refusal of simple file operations.",
      "lesson": "I should use available shell execution for explicit scoped filesystem operations.",
      "recommended_behavior": "When exec is available and the user gives exact file paths, use explicit rm paths and verify.",
      "scope": "tool_usage",
      "confidence": 0.9,
      "evidence": "shell exec is available; use rm with exact paths",
      "should_promote": false,
      "promotion_target": "none",
      "promote_content": ""
    }"""
    service = make_service(tmp_path, content)
    digest = make_digest()

    first = await service.reflect_on_session(
        messages=[{"role": "user", "content": "shell exec is available; use rm with exact paths"}],
        session_key="telegram:tal",
        digest=digest,
    )
    second = await service.reflect_on_session(
        messages=[{"role": "user", "content": "shell exec is available; use rm with exact paths"}],
        session_key="telegram:tal",
        digest=digest,
    )

    assert first.status == "saved"
    assert second.status == "skipped"
    events = reflection_audit_events(tmp_path)
    assert events[-1]["event"] == "duplicate"
    assert events[-1]["reason"] == "duplicate_or_contradictory"
    assert len(service.memory.list_memories("reflections")) == 1


@pytest.mark.asyncio
async def test_reflection_audits_promotion_skipped_when_not_viable(tmp_path):
    service = make_service(
        tmp_path,
        """{
          "title": "Use Shell For Deletions",
          "observation": "The user corrected the agent about shell deletion.",
          "impact": "This avoids false refusal of simple file operations.",
          "lesson": "I should use available shell execution for explicit scoped filesystem operations.",
          "recommended_behavior": "When exec is available and the user gives exact file paths, use explicit rm paths and verify.",
          "scope": "tool_usage",
          "confidence": 0.7,
          "evidence": "shell exec is available; use rm with exact paths",
          "should_promote": true,
          "promotion_target": "TOOLS.md",
          "promote_content": "Use explicit rm paths for scoped deletions."
        }""",
        auto_promote=True,
    )

    outcome = await service.reflect_on_session(
        messages=[{"role": "user", "content": "shell exec is available; use rm with exact paths"}],
        session_key="telegram:tal",
        digest=make_digest(),
    )

    assert outcome.status == "saved"
    events = reflection_audit_events(tmp_path)
    assert any(
        event["event"] == "promotion_skipped"
        and event["reason"] == "promotion_not_viable"
        and event["promotion_target"] == "none"
        for event in events
    )
