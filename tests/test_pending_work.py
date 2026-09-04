"""Focused regressions for coordinator-owned pending-work resumption."""

from __future__ import annotations

from hermitcrab.agent.pending_work import PendingWork, should_resume_pending_work


def _pending(last_failure: str, planned_command: str | None = None) -> PendingWork:
    return PendingWork(
        origin_request="what's your nostr npub",
        latest_request="find the npub",
        source_excerpt="you have an nsec in the config",
        last_failure=last_failure,
        planned_command=planned_command,
        tools_used=["exec"],
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_approval_pending_work_resumes_on_confirmation() -> None:
    pending = _pending(
        "I can do that, but deleting files needs your approval first.",
        planned_command="which nak; nak --version 2>/dev/null",
    )

    assert should_resume_pending_work(pending, "yes")


def test_approval_pending_work_does_not_resume_on_question() -> None:
    pending = _pending("I can do that, but deleting files needs your approval first.")

    assert not should_resume_pending_work(pending, "what is destructive about that command?!")


def test_approval_pending_work_does_not_resume_on_refusal() -> None:
    pending = _pending("I can do that, but deleting files needs your approval first.")

    assert not should_resume_pending_work(pending, "no, don't do that")


def test_non_approval_pending_work_still_resumes_on_related_follow_up() -> None:
    pending = _pending("Tool timed out.")

    assert should_resume_pending_work(pending, "please finish finding the npub")
