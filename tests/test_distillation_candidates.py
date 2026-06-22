from __future__ import annotations

from hermitcrab.agent.distillation import AtomicCandidate, CandidateType


def test_atomic_candidate_requires_evidence_for_saveable_learning():
    candidate = AtomicCandidate(
        type=CandidateType.FACT,
        title="User prefers concise replies",
        content="User prefers concise replies for routine status updates.",
        confidence=0.9,
    )

    assert "Evidence is required" in candidate.validate()


def test_atomic_candidate_accepts_grounded_evidence():
    candidate = AtomicCandidate(
        type=CandidateType.FACT,
        title="User prefers concise replies",
        content="User prefers concise replies for routine status updates.",
        confidence=0.9,
        evidence="User said: 'keep it brief unless I ask for detail.'",
    )

    assert candidate.validate() == []
    params = candidate.to_memory_params()
    assert params["evidence"] == "User said: 'keep it brief unless I ask for detail.'"


def test_ignored_candidate_requires_skip_reason_instead_of_evidence():
    candidate = AtomicCandidate(
        type=CandidateType.IGNORED,
        title="One-off command output",
        content="The user asked for current disk usage once.",
        skip_reason="Ephemeral task chatter, not durable memory.",
    )

    assert candidate.validate() == []
    as_dict = candidate.to_dict()
    assert as_dict["type"] == "ignored"
    assert as_dict["skip_reason"] == "Ephemeral task chatter, not durable memory."
