from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermitcrab.agent.distillation import AtomicCandidate, CandidateType, DecisionStatus
from hermitcrab.agent.distillation_background import DistillationManager
from hermitcrab.agent.memory import MemoryStore


def make_manager(tmp_path: Path) -> DistillationManager:
    async def fake_chat_callable(**_kwargs):
        raise AssertionError("not used")

    return DistillationManager(
        workspace=tmp_path,
        memory=MemoryStore(tmp_path),
        chat_callable=fake_chat_callable,
        get_model_for_job=lambda _job: "test-model",
        strip_think=lambda content: content,
        reasoning_effort=None,
    )


def audit_events(tmp_path: Path) -> list[dict]:
    audit_path = tmp_path / "memory" / "distillation_audit.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]


def test_distilled_fact_commit_persists_evidence_metadata(tmp_path):
    manager = make_manager(tmp_path)
    candidate = AtomicCandidate(
        type=CandidateType.FACT,
        title="User prefers concise replies",
        content="User prefers concise replies for routine status updates.",
        confidence=0.9,
        evidence="User said: keep it brief unless I ask for detail.",
    )

    manager.commit_candidate_to_memory(candidate)

    facts = manager.memory.list_memories("facts")
    assert len(facts) == 1
    assert facts[0].metadata["evidence"] == "User said: keep it brief unless I ask for detail."
    assert audit_events(tmp_path)[-1]["event"] == "committed"


def test_distilled_ignored_candidate_is_audited_but_not_committed(tmp_path):
    manager = make_manager(tmp_path)
    candidate = AtomicCandidate(
        type=CandidateType.IGNORED,
        title="Current disk usage request",
        content="The user asked for current disk usage once.",
        confidence=1.0,
        skip_reason="Ephemeral current-state request, not durable memory.",
    )

    manager.commit_candidate_to_memory(candidate)

    assert manager.memory.list_memories("facts") == []
    events = audit_events(tmp_path)
    assert events[-1]["event"] == "filtered"
    assert events[-1]["reason"] == "Ephemeral current-state request, not durable memory."


def test_duplicate_distilled_fact_is_reused_not_duplicated_and_audited(tmp_path):
    manager = make_manager(tmp_path)
    first = AtomicCandidate(
        type=CandidateType.FACT,
        title="User prefers concise replies",
        content="User prefers concise replies for routine status updates.",
        confidence=0.9,
        evidence="User said: keep it brief.",
    )
    duplicate = AtomicCandidate(
        type=CandidateType.FACT,
        title="User prefers concise replies",
        content="User prefers concise replies for routine status updates.",
        confidence=0.95,
        evidence="User repeated: concise is best.",
    )

    manager.commit_candidate_to_memory(first)
    manager.commit_candidate_to_memory(duplicate)

    facts = manager.memory.list_memories("facts")
    assert len(facts) == 1
    assert audit_events(tmp_path)[-1]["reason"] == "duplicate"


def test_filter_reasons_are_specific_for_low_confidence_bootstrap_and_decisions(tmp_path):
    manager = make_manager(tmp_path)
    low_confidence = AtomicCandidate(
        type=CandidateType.GOAL,
        title="Maybe learn guitar",
        content="User might want to learn guitar someday.",
        confidence=0.4,
        evidence="User mentioned guitar once.",
    )
    bootstrap_fact = AtomicCandidate(
        type=CandidateType.FACT,
        title="Agent should use tools",
        content="The assistant should use tools before answering current facts.",
        confidence=0.95,
        tags=["agent"],
        evidence="User corrected tool use.",
    )
    weak_decision = AtomicCandidate(
        type=CandidateType.DECISION,
        title="Use SQLite",
        content="Use SQLite for sessions.",
        confidence=0.95,
        evidence="User approved SQLite sessions.",
    )

    manager.commit_candidate_to_memory(low_confidence)
    manager.commit_candidate_to_memory(bootstrap_fact)
    manager.commit_candidate_to_memory(weak_decision)

    reasons = [event["reason"] for event in audit_events(tmp_path)[-3:]]
    assert reasons == ["low_confidence", "bootstrap_instruction", "decision_missing_rationale_or_status"]


@pytest.mark.asyncio
async def test_distillation_response_rejects_ungrounded_evidence_and_audits_validation(tmp_path):
    manager = make_manager(tmp_path)
    response = json.dumps(
        {
            "candidates": [
                {
                    "type": "fact",
                    "title": "User loves Kubernetes",
                    "content": "User loves Kubernetes for every project.",
                    "confidence": 0.95,
                    "evidence": "User said Kubernetes is mandatory.",
                }
            ]
        }
    )
    messages = [{"role": "user", "content": "Please keep this app simple and inspectable."}]

    await manager._commit_distillation_response(response, "telegram:tal", messages)

    assert manager.memory.list_memories("facts") == []
    events = audit_events(tmp_path)
    assert events[-1]["event"] == "validation_failed"
    assert events[-1]["reason"] == "evidence_not_in_session"


@pytest.mark.asyncio
async def test_distillation_response_commits_user_preference_end_to_end(tmp_path):
    manager = make_manager(tmp_path)
    response = json.dumps(
        {
            "candidates": [
                {
                    "type": "fact",
                    "title": "User prefers inspectable fixes",
                    "content": "User prefers simple, inspectable fixes over clever abstractions.",
                    "confidence": 0.95,
                    "tags": ["preference"],
                    "evidence": "I prefer simple, inspectable fixes over clever abstractions.",
                }
            ]
        }
    )
    messages = [
        {
            "role": "user",
            "content": "I prefer simple, inspectable fixes over clever abstractions.",
        }
    ]

    await manager._commit_distillation_response(response, "telegram:tal", messages)

    facts = manager.memory.list_memories("facts")
    assert len(facts) == 1
    assert "inspectable fixes" in facts[0].content
    assert facts[0].metadata["evidence"] == "I prefer simple, inspectable fixes over clever abstractions."


@pytest.mark.asyncio
async def test_distillation_response_audits_parse_and_schema_failures(tmp_path):
    manager = make_manager(tmp_path)

    await manager._commit_distillation_response("not json", "telegram:tal", [])
    await manager._commit_distillation_response(
        json.dumps({"candidates": [{"type": "fact", "title": "Missing content"}]}),
        "telegram:tal",
        [{"role": "user", "content": "Missing content"}],
    )

    events = audit_events(tmp_path)
    assert events[-2]["event"] == "parse_failed"
    assert events[-1]["event"] == "validation_failed"
    assert events[-1]["reason"] == "Content is required; Evidence is required"


def test_correction_with_same_fact_title_updates_existing_fact_without_duplicate(tmp_path):
    manager = make_manager(tmp_path)
    old = AtomicCandidate(
        type=CandidateType.FACT,
        title="User response style",
        content="User prefers long detailed replies.",
        confidence=0.8,
        evidence="Earlier: explain everything in detail.",
    )
    correction = AtomicCandidate(
        type=CandidateType.FACT,
        title="User response style",
        content="User prefers concise replies unless they ask for detail.",
        confidence=0.95,
        evidence="Actually, keep replies concise unless I ask for detail.",
    )

    manager.commit_candidate_to_memory(old)
    manager.commit_candidate_to_memory(correction)

    facts = manager.memory.list_memories("facts")
    assert len(facts) == 1
    assert facts[0].content == "User prefers concise replies unless they ask for detail."
    assert facts[0].metadata["evidence"] == "Actually, keep replies concise unless I ask for detail."
