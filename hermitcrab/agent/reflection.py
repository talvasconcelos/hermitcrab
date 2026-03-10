"""
Reflection: First-person self-improvement.

After each session (or 30-min inactivity), the agent reflects:
- What did I learn about this user?
- How can I be more helpful next time?
- What patterns should I remember?

Output: 0-1 reflection file + optional bootstrap update.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import json_repair
from loguru import logger

if TYPE_CHECKING:
    from hermitcrab.agent.memory import MemoryStore
    from hermitcrab.providers.base import LLMProvider


class ReflectionService:
    """
    First-person reflection service.

    Single LLM call → 0-1 reflection → auto-promote if pattern.
    """

    SYSTEM_PROMPT = """Reflect on the recent conversation for your future self.

Focus on one concrete user-specific learning:
- a preference
- a correction
- a repeated pattern
- a workflow lesson

Be specific and actionable.
Do not log bugs or tool failures.
Do not produce more than one insight.
"""

    USER_PROMPT = """Review this conversation and extract one high-value learning.

Recent conversation:
{messages}

{recent_reflections_section}

Respond with JSON:
{{
  "title": "Short, descriptive title",
  "content": "What did you learn? Write in first person: 'I learned...', 'I should...', 'The user prefers...'",
  "type": "preference|correction|pattern|insight|workflow",
  "evidence": "Concrete behavior, correction, or repeated pattern from this session that caused this learning",
  "should_promote": true,
  "promote_to": "AGENTS.md|TOOLS.md|SOUL.md|IDENTITY.md|none",
  "promote_content": "Specific instruction for your future self"
}}

If nothing is worth remembering, respond: {{"skip": true, "reason": "No new insights"}}

Rules:
- ONE insight only (pick the most valuable)
- First-person voice ("I learned...", not "The assistant should...")
- evidence must cite a concrete user behavior, correction, or repeated pattern from this session
- avoid duplicating recent reflections
- promote_content should be actionable instruction for bootstrap files
"""

    def __init__(
        self,
        memory: MemoryStore,
        provider: LLMProvider,
        model: str,
    ):
        """
        Initialize reflection service.

        Args:
            memory: Memory store for reading/writing reflections.
            provider: LLM provider for generating reflections.
            model: Model to use for reflection generation.
        """
        self.memory = memory
        self.provider = provider
        self.model = model

    async def reflect_on_session(
        self,
        messages: list[dict],
        session_key: str,
    ) -> None:
        """
        Reflect on a session and extract learnings.

        Args:
            messages: Session messages to analyze.
            session_key: Session identifier.
        """
        try:
            # Skip empty sessions
            if not messages:
                logger.debug("Reflection skipped: empty session {}", session_key)
                return

            # 1. Load recent reflections for dedup context
            recent = self.memory.list_memories("reflections")[:10]

            # 2. Build prompt
            messages_text = self._format_messages(messages)
            recent_section = self._format_recent_reflections(recent)

            user_prompt = self.USER_PROMPT.format(
                messages=messages_text,
                recent_reflections_section=recent_section,
            )

            # 3. Single LLM call
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=0.1,
                max_tokens=512,
            )

            # 4. Parse response
            result = self._parse_response(response.content)

            if result.get("skip"):
                logger.debug("Reflection skipped: {}", result.get("reason", "no insights"))
                return

            # 5. Validate required fields
            if not result.get("title") or not result.get("content") or not result.get("evidence"):
                logger.warning("Reflection missing required fields: {}", result)
                return

            if self._is_duplicate_or_contradictory(result, recent):
                logger.info("Reflection skipped after duplicate/contradiction guard: {}", result["title"])
                return

            # 6. Write reflection
            self._write_reflection(result, session_key)

            # 7. Auto-promote if flagged
            if result.get("should_promote") and result.get("promote_content"):
                await self._promote(result)

            logger.info("Reflection complete: {}", result.get("title", "unknown"))

        except Exception as e:
            logger.warning("Reflection failed (non-fatal): {}", e)

    def _format_messages(self, messages: list[dict]) -> str:
        """Format messages for prompt (truncated if needed)."""
        # Keep last 20 messages to stay in context
        recent = messages[-20:]
        lines = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = (msg.get("content") or "")[:500]  # Truncate long messages
            lines.append(f"{role}: {content}")
        return "\n\n".join(lines)

    def _format_recent_reflections(self, recent: list) -> str:
        """Format recent reflections for dedup context."""
        if not recent:
            return "No recent reflections."

        lines = ["Recent reflections (avoid duplicating):"]
        for i, ref in enumerate(recent[:5], 1):
            if ref is None:
                continue
            content_preview = (ref.content or "")[:100].replace("\n", " ")
            lines.append(f"{i}. {ref.title}: {content_preview}...")
        return "\n".join(lines)

    def _parse_response(self, content: str | None) -> dict:
        """Parse LLM JSON response."""
        if not content:
            return {"skip": True, "reason": "Invalid response format"}

        try:
            # Extract JSON from response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                result = json_repair.loads(json_str)
                if isinstance(result, dict):
                    return result
        except Exception as e:
            logger.warning("Failed to parse reflection JSON: {}", e)

        return {"skip": True, "reason": "Invalid response format"}

    def _write_reflection(self, result: dict, session_key: str) -> None:
        """Write reflection to memory."""
        reflection_type = result.get("type", "insight")
        tags = [reflection_type, "reflection", "learning"]

        # Build context from promote info
        context_parts = [f"Evidence: {result.get('evidence', '').strip()}"]
        if result.get("should_promote"):
            context_parts.append(f"Marked for promotion to {result.get('promote_to', 'unknown')}")

        self.memory.write_reflection(
            title=result["title"],
            content=result["content"],
            tags=tags,
            context="\n".join(context_parts) if context_parts else None,
        )

    @staticmethod
    def _normalize_text(text: str | None) -> str:
        """Normalize text for conservative duplicate checks."""
        return " ".join(re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower()).split())

    @classmethod
    def _similarity(cls, a: str | None, b: str | None) -> float:
        return SequenceMatcher(None, cls._normalize_text(a), cls._normalize_text(b)).ratio()

    @classmethod
    def _extract_preference_polarity(cls, text: str | None) -> str | None:
        normalized = cls._normalize_text(text)
        positive_markers = ("prefer", "likes", "wants", "values", "more ", "concise", "brief")
        negative_markers = ("avoid", "dislike", "does not like", "less ", "detailed", "verbose")
        has_positive = any(marker in normalized for marker in positive_markers)
        has_negative = any(marker in normalized for marker in negative_markers)
        if has_positive and not has_negative:
            return "positive"
        if has_negative and not has_positive:
            return "negative"
        return None

    def _is_duplicate_or_contradictory(self, result: dict, recent: list) -> bool:
        """Reject duplicate or obviously contradictory reflections."""
        title = result.get("title")
        content = result.get("content")
        evidence = result.get("evidence")
        reflection_type = result.get("type", "insight")

        for item in recent:
            if item is None:
                continue

            title_similarity = self._similarity(title, item.title)
            content_similarity = self._similarity(content, item.content)
            evidence_similarity = self._similarity(evidence, item.metadata.get("context", ""))

            if title_similarity >= 0.88 and (content_similarity >= 0.82 or evidence_similarity >= 0.75):
                return True

            if reflection_type == "preference" and "preference" in (item.tags or []):
                same_subject = title_similarity >= 0.75
                new_polarity = self._extract_preference_polarity(content)
                old_polarity = self._extract_preference_polarity(item.content)
                if same_subject and new_polarity and old_polarity and new_polarity != old_polarity:
                    return True

        return False

    async def _promote(self, result: dict) -> None:
        """Auto-promote reflection to bootstrap file."""
        target_file = result.get("promote_to", "AGENTS.md")
        content = result.get("promote_content")

        if not content:
            return

        # Handle pipe-separated values or "none" (LLM may return multiple options)
        if "|" in target_file:
            # Extract first valid option from pipe-separated list
            for option in target_file.split("|"):
                option = option.strip()
                if option in ["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"]:
                    target_file = option
                    break
            else:
                target_file = "AGENTS.md"

        # Validate target file
        valid_files = ["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"]
        if target_file not in valid_files:
            logger.warning("Invalid promote target: {}", target_file)
            target_file = "AGENTS.md"

        # Map reflection type to section header
        section_map = {
            "preference": "## User Preferences",
            "correction": "## Corrections & Learnings",
            "pattern": "## Observed Patterns",
            "insight": "## Insights",
            "workflow": "## Workflow Notes",
        }
        section = section_map.get(result.get("type", "insight"), "## Learnings")

        # Append to bootstrap file
        file_path = self.memory.workspace / target_file
        self._append_to_bootstrap(file_path, section, content)

        logger.info("Auto-promoted reflection to {}: {}", target_file, result["title"])

    def _append_to_bootstrap(self, file_path: Path, section: str, content: str) -> None:
        """Append content to bootstrap file section."""
        if not file_path.exists():
            file_path.write_text(f"{section}\n\n{content}\n")
            return

        existing = file_path.read_text(encoding="utf-8")

        if section in existing:
            # Append to existing section
            lines = existing.split("\n")
            new_lines = []
            in_section = False
            inserted = False

            for i, line in enumerate(lines):
                new_lines.append(line)

                # Detect section end (next ## header or EOF)
                if line.strip() == section:
                    in_section = True
                elif in_section and line.startswith("## "):
                    # Insert before next section
                    if not inserted:
                        new_lines.insert(-1, "")
                        new_lines.insert(-1, content)
                        new_lines.insert(-1, "")
                        inserted = True
                    in_section = False

            # If still in section at EOF, append
            if in_section and not inserted:
                new_lines.append("")
                new_lines.append(content)
                new_lines.append("")

            file_path.write_text("\n".join(new_lines), encoding="utf-8")
        else:
            # Create new section at end
            separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
            file_path.write_text(f"{existing}{separator}{section}\n\n{content}\n", encoding="utf-8")
