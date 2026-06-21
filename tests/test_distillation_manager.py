from __future__ import annotations

from pathlib import Path

from hermitcrab.agent.distillation import AtomicCandidate, CandidateType
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
    audit_path = tmp_path / "memory" / "distillation_audit.jsonl"
    assert audit_path.exists()
    assert "Ephemeral current-state request" in audit_path.read_text(encoding="utf-8")
