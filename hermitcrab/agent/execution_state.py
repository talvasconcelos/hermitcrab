"""Deterministic execution-state tracking for coordinator progress."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ExecutionPhase(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    RUNNING_TOOLS = "running_tools"
    DELEGATED = "delegated"
    WAITING_BACKGROUND = "waiting_background"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class ExecutionState:
    phase: ExecutionPhase = ExecutionPhase.IDLE
    detail: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionStateTracker:
    """Track the latest execution phase for each session."""

    def __init__(self) -> None:
        self._states: dict[str, ExecutionState] = {}

    def set(self, session_key: str, phase: ExecutionPhase, detail: str = "") -> ExecutionState:
        state = ExecutionState(phase=phase, detail=detail)
        self._states[session_key] = state
        return state

    def get(self, session_key: str) -> ExecutionState:
        return self._states.get(session_key, ExecutionState())

    def clear(self, session_key: str) -> None:
        self._states.pop(session_key, None)
