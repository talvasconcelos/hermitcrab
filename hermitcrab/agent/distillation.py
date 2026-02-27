"""
Distillation: Extract atomic candidates from session data.

Distillation produces proposals only (not authoritative).
Uses strict JSON schema for extraction.
Validation and commit happen elsewhere (Tier 0).
Local only, skip if unavailable.

Candidate Types:
- fact: Long-term truths (preferences, project context)
- decision: Locked choices (immutable, never deleted)
- goal: Outcome-oriented objectives
- task: Actionable items with lifecycle
- reflection: Subjective observations (append-only)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CandidateType(str, Enum):
    """Atomic candidate types (must match memory categories)."""

    FACT = "fact"
    DECISION = "decision"
    GOAL = "goal"
    TASK = "task"
    REFLECTION = "reflection"


class TaskStatus(str, Enum):
    """Task lifecycle status."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DEFERRED = "deferred"


class GoalStatus(str, Enum):
    """Goal status."""

    ACTIVE = "active"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


class DecisionStatus(str, Enum):
    """Decision status."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"


@dataclass
class AtomicCandidate:
    """
    Atomic candidate extracted from session.

    This is a proposal only - not authoritative.
    Must be validated and committed by Tier 0 logic.
    """

    type: CandidateType
    title: str
    content: str
    confidence: float = 1.0  # 0.0-1.0, confidence in extraction
    source_session: str = ""  # Session key where extracted from
    tags: list[str] = field(default_factory=list)

    # Category-specific metadata (optional, depends on type)
    # For TASK:
    task_status: TaskStatus | None = None
    task_assignee: str | None = None
    task_deadline: str | None = None
    task_priority: str | None = None

    # For GOAL:
    goal_status: GoalStatus | None = None
    goal_priority: str | None = None
    goal_horizon: str | None = None

    # For DECISION:
    decision_status: DecisionStatus | None = None
    decision_rationale: str | None = None
    decision_supersedes: str | None = None  # ID of superseded decision

    # For FACT:
    fact_source: str | None = None

    # For REFLECTION:
    reflection_context: str | None = None

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_memory_params(self) -> dict[str, Any]:
        """
        Convert to memory.write_*() parameters.

        Returns:
            Dict of parameters for the appropriate memory write method.
        """
        base_params = {
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
        }

        if self.type == CandidateType.FACT:
            return {
                **base_params,
                "confidence": self.confidence,
                "source": self.fact_source,
            }
        elif self.type == CandidateType.DECISION:
            return {
                **base_params,
                "status": self.decision_status.value if self.decision_status else "active",
                "rationale": self.decision_rationale,
                "supersedes": self.decision_supersedes,
            }
        elif self.type == CandidateType.GOAL:
            return {
                **base_params,
                "status": self.goal_status.value if self.goal_status else "active",
                "priority": self.goal_priority,
                "horizon": self.goal_horizon,
            }
        elif self.type == CandidateType.TASK:
            return {
                **base_params,
                "assignee": self.task_assignee or "unassigned",
                "status": self.task_status or TaskStatus.OPEN,
                "deadline": self.task_deadline,
                "priority": self.task_priority,
            }
        elif self.type == CandidateType.REFLECTION:
            return {
                **base_params,
                "context": self.reflection_context,
            }

        return base_params

    def validate(self) -> list[str]:
        """
        Validate candidate structure.

        Returns:
            List of validation errors (empty if valid).
        """
        errors = []

        # Common validation
        if not self.title or not self.title.strip():
            errors.append("Title is required")
        if not self.content or not self.content.strip():
            errors.append("Content is required")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append("Confidence must be between 0.0 and 1.0")

        # Type-specific validation
        if self.type == CandidateType.TASK:
            if not self.task_assignee or not self.task_assignee.strip():
                errors.append("Task assignee is required")

        if self.type == CandidateType.DECISION:
            if self.decision_supersedes and not self.decision_rationale:
                errors.append("Rationale required when superseding another decision")

        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type.value,
            "title": self.title,
            "content": self.content,
            "confidence": self.confidence,
            "source_session": self.source_session,
            "tags": self.tags,
            "task_status": self.task_status.value if self.task_status else None,
            "task_assignee": self.task_assignee,
            "task_deadline": self.task_deadline,
            "task_priority": self.task_priority,
            "goal_status": self.goal_status.value if self.goal_status else None,
            "goal_priority": self.goal_priority,
            "goal_horizon": self.goal_horizon,
            "decision_status": self.decision_status.value if self.decision_status else None,
            "decision_rationale": self.decision_rationale,
            "decision_supersedes": self.decision_supersedes,
            "fact_source": self.fact_source,
            "reflection_context": self.reflection_context,
            "created_at": self.created_at.isoformat(),
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AtomicCandidate":
        """Create from dictionary (JSON deserialization)."""
        # Parse enums
        task_status = None
        if data.get("task_status"):
            task_status = TaskStatus(data["task_status"])

        goal_status = None
        if data.get("goal_status"):
            goal_status = GoalStatus(data["goal_status"])

        decision_status = None
        if data.get("decision_status"):
            decision_status = DecisionStatus(data["decision_status"])

        created_at = datetime.now(timezone.utc)
        if data.get("created_at"):
            try:
                created_at = datetime.fromisoformat(data["created_at"])
            except (ValueError, TypeError):
                pass

        return cls(
            type=CandidateType(data["type"].lower()),  # Case-insensitive
            title=data["title"],
            content=data["content"],
            confidence=data.get("confidence", 1.0),
            source_session=data.get("source_session", ""),
            tags=data.get("tags", []),
            task_status=task_status,
            task_assignee=data.get("task_assignee"),
            task_deadline=data.get("task_deadline"),
            task_priority=data.get("task_priority"),
            goal_status=goal_status,
            goal_priority=data.get("goal_priority"),
            goal_horizon=data.get("goal_horizon"),
            decision_status=decision_status,
            decision_rationale=data.get("decision_rationale"),
            decision_supersedes=data.get("decision_supersedes"),
            fact_source=data.get("fact_source"),
            reflection_context=data.get("reflection_context"),
            created_at=created_at,
            extra=data.get("extra", {}),
        )


# JSON Schema for LLM extraction
DISTILLATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["fact", "decision", "goal", "task", "reflection"],
                        "description": "Candidate type (must match memory categories)",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short descriptive title (max 100 chars)",
                        "maxLength": 100,
                    },
                    "content": {
                        "type": "string",
                        "description": "Candidate content (atomic, single concern)",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence in extraction accuracy (0.0-1.0)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for categorization",
                    },
                    # Task-specific fields
                    "task_status": {
                        "type": "string",
                        "enum": ["open", "in_progress", "done", "deferred"],
                        "description": "Task status (only for type=task)",
                    },
                    "task_assignee": {
                        "type": "string",
                        "description": "Who the task is assigned to (required for type=task)",
                    },
                    "task_deadline": {
                        "type": "string",
                        "description": "Deadline date (YYYY-MM-DD format)",
                    },
                    "task_priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "Task priority",
                    },
                    # Goal-specific fields
                    "goal_status": {
                        "type": "string",
                        "enum": ["active", "achieved", "abandoned"],
                        "description": "Goal status (only for type=goal)",
                    },
                    "goal_priority": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "Goal priority",
                    },
                    "goal_horizon": {
                        "type": "string",
                        "enum": ["short-term", "medium-term", "long-term"],
                        "description": "Goal time horizon",
                    },
                    # Decision-specific fields
                    "decision_status": {
                        "type": "string",
                        "enum": ["active", "superseded"],
                        "description": "Decision status (only for type=decision)",
                    },
                    "decision_rationale": {
                        "type": "string",
                        "description": "Reasoning behind the decision",
                    },
                    "decision_supersedes": {
                        "type": "string",
                        "description": "ID/title of decision this supersedes",
                    },
                    # Fact-specific fields
                    "fact_source": {
                        "type": "string",
                        "description": "Source of the fact",
                    },
                    # Reflection-specific fields
                    "reflection_context": {
                        "type": "string",
                        "description": "Context for the reflection",
                    },
                },
                "required": ["type", "title", "content"],
                # Conditional requirements based on type
                "allOf": [
                    {
                        "if": {"properties": {"type": {"const": "task"}}},
                        "then": {
                            "required": ["task_assignee"],
                        },
                    },
                ],
            },
        },
    },
    "required": ["candidates"],
}
