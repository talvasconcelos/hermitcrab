"""
Reflection: Meta-analysis of agent behavior.

Reflection identifies:
- Mistakes and failures
- Uncertainty patterns
- Repeated user corrections
- Tool usage inefficiencies
- Opportunities for improvement

Output:
- Reflection candidates (stored in memory/reflections/)
- Suggestions for prompt/heuristic improvements
- Pattern summaries for long-term learning

Unlike distillation (which extracts facts/tasks/goals), reflection
is about the agent's own behavior and performance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ReflectionType(str, Enum):
    """Types of reflection."""

    MISTAKE = "mistake"  # Something went wrong
    UNCERTAINTY = "uncertainty"  # Agent was unsure
    PATTERN = "pattern"  # Repeated behavior observed
    IMPROVEMENT = "improvement"  # Suggestion for improvement
    INSIGHT = "insight"  # General insight about work


@dataclass
class ReflectionCandidate:
    """
    Reflection candidate extracted from session analysis.

    Reflections are meta-observations about agent behavior,
    not domain knowledge (that's distillation's job).
    """

    type: ReflectionType
    title: str
    content: str
    confidence: float = 1.0
    source_session: str = ""
    tags: list[str] = field(default_factory=list)

    # Analysis metadata
    tool_involved: str | None = None  # Which tool was involved (if any)
    error_pattern: str | None = None  # Specific error pattern (for mistakes)
    frequency: str | None = None  # How often this occurs (for patterns)
    impact: str | None = None  # Impact level: low/medium/high
    suggestion: str | None = None  # Suggested improvement

    # Context
    session_context: str | None = None  # What was happening
    user_correction: bool = False  # Was user correction involved?

    created_at: datetime = field(default_factory=datetime.now)
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Validate reflection structure."""
        errors = []

        if not self.title or not self.title.strip():
            errors.append("Title is required")
        if not self.content or not self.content.strip():
            errors.append("Content is required")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append("Confidence must be between 0.0 and 1.0")

        # Type-specific validation
        if self.type == ReflectionType.MISTAKE:
            if not self.error_pattern:
                errors.append("Error pattern required for mistakes")

        if self.type == ReflectionType.PATTERN:
            if not self.frequency:
                errors.append("Frequency required for patterns")

        return errors

    def to_memory_params(self) -> dict[str, Any]:
        """Convert to memory.write_reflection() parameters."""
        # Build rich context from reflection data
        context_parts = []

        if self.session_context:
            context_parts.append(f"Context: {self.session_context}")
        if self.tool_involved:
            context_parts.append(f"Tool: {self.tool_involved}")
        if self.error_pattern:
            context_parts.append(f"Error: {self.error_pattern}")
        if self.frequency:
            context_parts.append(f"Frequency: {self.frequency}")
        if self.impact:
            context_parts.append(f"Impact: {self.impact}")
        if self.suggestion:
            context_parts.append(f"Suggestion: {self.suggestion}")
        if self.user_correction:
            context_parts.append("User correction: yes")

        context = "\n".join(context_parts) if context_parts else None

        return {
            "title": self.title,
            "content": self.content,
            "tags": self.tags + [self.type.value, "reflection"],
            "context": context,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type.value,
            "title": self.title,
            "content": self.content,
            "confidence": self.confidence,
            "source_session": self.source_session,
            "tags": self.tags,
            "tool_involved": self.tool_involved,
            "error_pattern": self.error_pattern,
            "frequency": self.frequency,
            "impact": self.impact,
            "suggestion": self.suggestion,
            "session_context": self.session_context,
            "user_correction": self.user_correction,
            "created_at": self.created_at.isoformat(),
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReflectionCandidate":
        """Create from dictionary (JSON deserialization)."""
        created_at = datetime.now()
        if data.get("created_at"):
            try:
                created_at = datetime.fromisoformat(data["created_at"])
            except (ValueError, TypeError):
                pass

        return cls(
            type=ReflectionType(data["type"]),
            title=data["title"],
            content=data["content"],
            confidence=data.get("confidence", 1.0),
            source_session=data.get("source_session", ""),
            tags=data.get("tags", []),
            tool_involved=data.get("tool_involved"),
            error_pattern=data.get("error_pattern"),
            frequency=data.get("frequency"),
            impact=data.get("impact"),
            suggestion=data.get("suggestion"),
            session_context=data.get("session_context"),
            user_correction=data.get("user_correction", False),
            created_at=created_at,
            extra=data.get("extra", {}),
        )


# JSON Schema for LLM extraction
REFLECTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "reflections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["mistake", "uncertainty", "pattern", "improvement", "insight"],
                        "description": "Type of reflection",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short descriptive title",
                    },
                    "content": {
                        "type": "string",
                        "description": "Reflection content (meta-observation)",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence in observation",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags",
                    },
                    "tool_involved": {
                        "type": "string",
                        "description": "Tool involved (if applicable)",
                    },
                    "error_pattern": {
                        "type": "string",
                        "description": "Specific error pattern (for mistakes)",
                    },
                    "frequency": {
                        "type": "string",
                        "description": "How often this occurs (for patterns)",
                    },
                    "impact": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Impact level",
                    },
                    "suggestion": {
                        "type": "string",
                        "description": "Suggested improvement",
                    },
                    "user_correction": {
                        "type": "boolean",
                        "description": "Was user correction involved?",
                    },
                },
                "required": ["type", "title", "content"],
            },
        },
    },
    "required": ["reflections"],
}


# Reflection prompts for different analysis types
REFLECTION_PROMPTS = {
    "mistakes": """Analyze this session for MISTAKES and FAILURES:
- Tool errors or exceptions
- Failed operations
- Incorrect assumptions
- Misunderstandings of user intent
- Repeated failed attempts

For each mistake, identify:
- What went wrong
- Which tool was involved (if any)
- The error pattern
- How it could be avoided""",

    "uncertainty": """Analyze this session for UNCERTAINTY:
- Places where the agent seemed unsure
- Requests for clarification
- Hedged responses ("might", "could", "possibly")
- Areas where the agent lacked knowledge

For each uncertainty, note:
- What the agent was uncertain about
- Why the uncertainty existed
- How it could be resolved in future""",

    "patterns": """Analyze this session for PATTERNS:
- Repeated behaviors or operations
- Recurring tool usage
- Common types of requests
- User behavior patterns

For each pattern, identify:
- What pattern was observed
- How frequently it appears
- Whether it's efficient or wasteful""",

    "improvements": """Based on this session, suggest IMPROVEMENTS:
- Prompt or instruction changes
- Tool usage optimizations
- Workflow improvements
- Knowledge gaps to fill

For each improvement:
- What should change
- Why it would help
- Expected impact (low/medium/high)""",
}
