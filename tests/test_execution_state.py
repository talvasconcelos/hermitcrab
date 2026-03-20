"""Tests for deterministic execution-state tracking."""

from hermitcrab.agent.execution_state import ExecutionPhase, ExecutionStateTracker


def test_execution_state_tracker_defaults_to_idle() -> None:
    tracker = ExecutionStateTracker()

    state = tracker.get("cli:direct")

    assert state.phase == ExecutionPhase.IDLE
    assert state.detail == ""


def test_execution_state_tracker_updates_and_clears() -> None:
    tracker = ExecutionStateTracker()

    updated = tracker.set("cli:direct", ExecutionPhase.RUNNING_TOOLS, "executing tools")

    assert updated.phase == ExecutionPhase.RUNNING_TOOLS
    assert tracker.get("cli:direct").detail == "executing tools"

    tracker.clear("cli:direct")

    assert tracker.get("cli:direct").phase == ExecutionPhase.IDLE
