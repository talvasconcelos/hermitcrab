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

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

if TYPE_CHECKING:
    from hermitcrab.providers.base import LLMProvider


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


# Reflection to bootstrap file mapping
REFLECTION_TO_BOOTSTRAP_MAP = {
    ReflectionType.MISTAKE: ["TOOLS.md", "AGENTS.md"],
    ReflectionType.UNCERTAINTY: ["IDENTITY.md", "AGENTS.md"],
    ReflectionType.PATTERN: ["AGENTS.md", "SOUL.md"],
    ReflectionType.IMPROVEMENT: ["AGENTS.md", "SOUL.md", "TOOLS.md"],
    ReflectionType.INSIGHT: ["SOUL.md", "IDENTITY.md"],
}


# Bootstrap file section headers for organized updates
BOOTSTRAP_SECTIONS = {
    "AGENTS.md": "## Self-Improvements from Reflection",
    "SOUL.md": "## Learned Values",
    "IDENTITY.md": "## Adapted Identity",
    "TOOLS.md": "## Learned Tool Behaviors",
}


@dataclass
class BootstrapEditProposal:
    """
    Proposed edit to a bootstrap file.

    Generated by LLM analysis of reflections.
    """

    target_file: str  # AGENTS.md, SOUL.md, etc.
    section: str  # Section header to append under
    content: str  # Content to add
    reason: str  # Why this edit is proposed (reflection title)
    reflection_type: str  # Type of reflection that triggered this
    confidence: float = 1.0

    def validate(self) -> list[str]:
        """Validate the proposal."""
        errors = []
        if not self.target_file or self.target_file not in BOOTSTRAP_SECTIONS:
            errors.append(f"Invalid target file: {self.target_file}")
        if not self.content or not self.content.strip():
            errors.append("Content is required")
        if not self.reason:
            errors.append("Reason is required")
        return errors


# JSON schema for bootstrap edit proposals
BOOTSTRAP_EDIT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "enum": ["AGENTS.md", "SOUL.md", "IDENTITY.md", "TOOLS.md"],
                        "description": "Bootstrap file to update",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section header to append under",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to add",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this edit is proposed",
                    },
                    "reflection_type": {
                        "type": "string",
                        "enum": ["mistake", "uncertainty", "pattern", "improvement", "insight"],
                        "description": "Type of reflection that triggered this",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence in this proposal",
                    },
                },
                "required": ["target_file", "content", "reason", "reflection_type"],
            },
        },
    },
    "required": ["edits"],
}


class ReflectionPromoter:
    """
    Promotes reflections to bootstrap file updates.

    Analyzes reflections and proposes edits to AGENTS.md, SOUL.md,
    IDENTITY.md, and TOOLS.md to help the agent improve over time.

    Promotion strategy:
    1. Analyze reflection patterns
    2. Generate bootstrap edit proposals via LLM
    3. Apply edits (append or smart insert)
    4. Notify user of changes
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        target_files: list[str] | None = None,
        max_file_lines: int = 500,
    ):
        """
        Initialize reflection promoter.

        Args:
            workspace: Workspace path for bootstrap files.
            provider: LLM provider for generating proposals.
            model: Model to use for proposal generation.
            target_files: List of bootstrap files to update.
            max_file_lines: Max lines before archiving old sections.
        """
        self.workspace = workspace
        self.provider = provider
        self.model = model
        self.target_files = target_files or list(BOOTSTRAP_SECTIONS.keys())
        self.max_file_lines = max_file_lines

    def _get_bootstrap_file_path(self, filename: str) -> Path:
        """Get full path to bootstrap file."""
        return self.workspace / filename

    def _read_bootstrap_file(self, filename: str) -> str:
        """Read bootstrap file content."""
        file_path = self._get_bootstrap_file_path(filename)
        if not file_path.exists():
            return ""
        return file_path.read_text(encoding="utf-8")

    def _write_bootstrap_file(self, filename: str, content: str) -> None:
        """Write content to bootstrap file."""
        file_path = self._get_bootstrap_file_path(filename)
        file_path.write_text(content, encoding="utf-8")
        logger.info("Bootstrap file updated: {}", filename)

    def _append_to_section(
        self,
        filename: str,
        section: str,
        content: str,
    ) -> str:
        """
        Append content to a section in bootstrap file.

        If section doesn't exist, creates it at the end.
        This is the safe, append-only strategy.

        Args:
            filename: Bootstrap file name.
            section: Section header to append under.
            content: Content to append.

        Returns:
            Updated file content.
        """
        existing_content = self._read_bootstrap_file(filename)

        # Check if section exists
        if section in existing_content:
            # Append to existing section (at the end of section content)
            lines = existing_content.split("\n")
            new_lines = []
            in_section = False

            for i, line in enumerate(lines):
                # Check if we're entering the target section
                if line.strip() == section:
                    in_section = True
                    new_lines.append(line)
                    continue

                # Check if we're leaving the section (new ## header or end)
                if in_section and line.startswith("## ") and line.strip() != section:
                    # We've reached the next section, insert before it
                    new_lines.append("")
                    new_lines.append(content)
                    new_lines.append("")
                    in_section = False

                new_lines.append(line)

            # If we were still in section at EOF, append at end
            if in_section:
                new_lines.append("")
                new_lines.append(content)
                new_lines.append("")

            return "\n".join(new_lines)
        else:
            # Create new section at end
            separator = "\n\n" if existing_content else ""
            return f"{existing_content}{separator}{section}\n\n{content}\n"

    async def _smart_insert(
        self,
        filename: str,
        section: str,
        content: str,
        reflection_type: str,
    ) -> str:
        """
        Smart insert: LLM decides where to place content.

        LLM analyzes existing content and determines optimal placement.
        Falls back to append if LLM fails.

        Args:
            filename: Bootstrap file name.
            section: Suggested section header.
            content: Content to insert.
            reflection_type: Type of reflection triggering this.

        Returns:
            Updated file content.
        """
        existing_content = self._read_bootstrap_file(filename)

        if not existing_content:
            # Empty file, just create section
            return f"{section}\n\n{content}\n"

        # Ask LLM to decide placement
        prompt = (
            f"You are updating a bootstrap file '{filename}'.\n\n"
            f"Current content:\n{existing_content[:2000]}\n\n"  # Truncate for context
            f"New content to insert:\n{content}\n\n"
            f"Reflection type: {reflection_type}\n\n"
            f"Decide: Should this content:\n"
            f"1. Be appended to existing section '{section}'\n"
            f"2. Create a new section '{section}' at the end\n"
            f"3. Be inserted elsewhere (specify location)\n\n"
            f"Return ONLY the updated file content. No explanations."
        )

        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.1,
                max_tokens=2048,
            )

            updated_content = response.content
            if updated_content and updated_content.strip():
                return updated_content
        except Exception as e:
            logger.warning("Smart insert LLM failed, falling back to append: {}", e)

        # Fallback: append to section
        return self._append_to_section(filename, section, content)

    def _check_file_size_and_archive(self, filename: str) -> None:
        """
        Check file size and archive old sections if needed.

        Args:
            filename: Bootstrap file name.
        """
        file_path = self._get_bootstrap_file_path(filename)
        if not file_path.exists():
            return

        lines = file_path.read_text(encoding="utf-8").split("\n")

        if len(lines) <= self.max_file_lines:
            return  # No archiving needed

        # Archive old content
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{filename}.archived.{timestamp}"
        archive_path = self.workspace / archive_name

        shutil.copy(file_path, archive_path)
        logger.info("Archived oversized bootstrap file: {} -> {}", filename, archive_path)

        # Keep only recent sections (last N lines)
        # Simple strategy: keep last max_file_lines * 0.8 lines
        keep_lines = int(self.max_file_lines * 0.8)
        recent_content = "\n".join(lines[-keep_lines:])

        self._write_bootstrap_file(filename, recent_content)
        logger.info("Trimmed bootstrap file to {} lines", keep_lines)

    async def propose_edits_from_reflections(
        self,
        reflections: list[ReflectionCandidate],
    ) -> list[BootstrapEditProposal]:
        """
        Generate bootstrap edit proposals from reflections.

        Args:
            reflections: List of reflection candidates.

        Returns:
            List of bootstrap edit proposals.
        """
        if not reflections:
            return []

        # Build prompt with reflection context
        reflection_context = []
        for i, ref in enumerate(reflections, 1):
            reflection_context.append(
                f"{i}. [{ref.type.value}] {ref.title}\n"
                f"   Content: {ref.content}\n"
                f"   Tool: {ref.tool_involved or 'N/A'}\n"
                f"   Suggestion: {ref.suggestion or 'N/A'}\n"
            )

        prompt = (
            "Analyze these reflections and propose bootstrap file updates.\n\n"
            "Reflections:\n" + "\n".join(reflection_context) + "\n\n"
            "For each reflection, decide:\n"
            "- Which bootstrap file should be updated (AGENTS.md, SOUL.md, IDENTITY.md, TOOLS.md)\n"
            "- What instruction/value/behavior should be added\n"
            "- Be specific and actionable\n\n"
            "Target files and their purposes:\n"
            "- AGENTS.md: Agent instructions and behavior guidelines\n"
            "- SOUL.md: Core values and principles\n"
            "- IDENTITY.md: Agent identity and interaction style\n"
            "- TOOLS.md: Tool usage notes and caveats\n\n"
            "Return proposals as JSON with 'edits' array.\n"
            "Each edit must have: target_file, content, reason, reflection_type.\n"
            "Optional: section, confidence."
        )

        try:
            response = await self.provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.1,
                max_tokens=2048,
            )

            # Parse JSON response
            content = response.content
            if not content:
                return []

            # Extract JSON from response
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                data = json.loads(json_str)

                proposals = []
                for edit_data in data.get("edits", []):
                    try:
                        proposal = BootstrapEditProposal(
                            target_file=edit_data["target_file"],
                            section=edit_data.get(
                                "section",
                                BOOTSTRAP_SECTIONS.get(edit_data["target_file"], ""),
                            ),
                            content=edit_data["content"],
                            reason=edit_data["reason"],
                            reflection_type=edit_data["reflection_type"],
                            confidence=edit_data.get("confidence", 1.0),
                        )

                        errors = proposal.validate()
                        if errors:
                            logger.warning(
                                "Bootstrap edit proposal validation failed: {}: {}",
                                proposal.reason,
                                errors,
                            )
                            continue

                        # Filter by target files
                        if proposal.target_file not in self.target_files:
                            logger.debug(
                                "Skipping edit for non-target file: {}",
                                proposal.target_file,
                            )
                            continue

                        proposals.append(proposal)

                    except Exception as e:
                        logger.warning(
                            "Failed to parse bootstrap edit proposal: {}: {}",
                            edit_data.get("reason", "unknown"),
                            e,
                        )

                return proposals

        except json.JSONDecodeError as e:
            logger.warning("Bootstrap edit proposal response not valid JSON: {}", e)
        except Exception as e:
            logger.warning("Bootstrap edit proposal generation failed: {}", e)

        return []

    async def apply_edits(
        self,
        proposals: list[BootstrapEditProposal],
        use_smart_insert: bool = True,
    ) -> dict[str, list[str]]:
        """
        Apply bootstrap edit proposals.

        Args:
            proposals: List of edit proposals.
            use_smart_insert: Use LLM to decide placement (vs. append-only).

        Returns:
            Dict mapping filename to list of applied edits.
        """
        applied_edits: dict[str, list[str]] = {}

        for proposal in proposals:
            try:
                if use_smart_insert:
                    updated_content = await self._smart_insert(
                        filename=proposal.target_file,
                        section=proposal.section,
                        content=proposal.content,
                        reflection_type=proposal.reflection_type,
                    )
                else:
                    updated_content = self._append_to_section(
                        filename=proposal.target_file,
                        section=proposal.section,
                        content=proposal.content,
                    )

                self._write_bootstrap_file(proposal.target_file, updated_content)

                # Track applied edits
                if proposal.target_file not in applied_edits:
                    applied_edits[proposal.target_file] = []
                applied_edits[proposal.target_file].append(proposal.reason)

                # Check file size and archive if needed
                self._check_file_size_and_archive(proposal.target_file)

            except Exception as e:
                logger.error(
                    "Failed to apply bootstrap edit for {}: {}",
                    proposal.reason,
                    e,
                )

        return applied_edits

    async def promote_reflections(
        self,
        reflections: list[ReflectionCandidate],
        notify_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, list[str]]:
        """
        Full promotion pipeline: propose edits, apply them, notify user.

        Args:
            reflections: Reflections to promote.
            notify_callback: Optional callback to notify user of changes.

        Returns:
            Dict mapping filename to list of applied edits.
        """
        if not reflections:
            return {}

        # Step 1: Generate proposals
        proposals = await self.propose_edits_from_reflections(reflections)

        if not proposals:
            logger.debug("No bootstrap edit proposals generated from {} reflections", len(reflections))
            return {}

        # Step 2: Apply edits
        applied_edits = await self.apply_edits(proposals)

        # Step 3: Notify user (if callback provided)
        if notify_callback and applied_edits:
            for filename, edits in applied_edits.items():
                notification = (
                    f"🧠 Self-Improvement: Updated {filename}\n\n"
                    f"Based on recent reflections:\n"
                    + "\n".join(f"• {edit}" for edit in edits)
                )
                try:
                    await notify_callback(notification)
                except Exception as e:
                    logger.warning("Failed to send bootstrap update notification: {}", e)

        return applied_edits
