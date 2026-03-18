"""
Reflection: First-person self-improvement.

After each session (or 30-min inactivity), the agent reflects:
- What did I learn about this user?
- How can I be more helpful next time?
- What patterns should I remember?

Output: 0-1 reflection file + optional bootstrap update.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import json_repair
from loguru import logger

if TYPE_CHECKING:
    from hermitcrab.agent.loop import SessionDigest
    from hermitcrab.agent.memory import MemoryStore


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
Do not log bugs, tool failures, or general summaries.
Do not produce more than one insight.
"""

    USER_PROMPT = """Review this session digest and extract one high-value learning for the future agent.

Session digest:
{digest}

{recent_reflections_section}

Respond with JSON:
{{
  "title": "Short, descriptive title",
  "content": "What did you learn? Write in first person: 'I learned...', 'I should...', 'The user prefers...'",
  "type": "preference|correction|pattern|insight|workflow",
  "evidence": "Optional concrete behavior, correction, or repeated pattern from this session that caused this learning",
  "should_promote": true,
  "promote_to": "AGENTS.md|TOOLS.md|SOUL.md|IDENTITY.md|none",
  "promote_content": "Specific instruction for your future self"
}}

If nothing is worth remembering, respond: {{"skip": true, "reason": "No new insights"}}

Rules:
- ONE insight only (pick the most valuable)
- First-person voice ("I learned...", not "The assistant should...")
- evidence must cite a concrete user behavior, correction, or repeated pattern from this session
- prioritize user corrections, preferences, and workflow expectations
- do not reflect on tool errors, missing files, provider failures, or generic project summaries
- avoid duplicating recent reflections
- promote_content should be actionable instruction for bootstrap files
"""

    def __init__(
        self,
        memory: MemoryStore,
        chat_callable: Callable[..., Awaitable[Any]],
        model: str,
        *,
        auto_promote: bool,
        allowed_targets: list[str],
        max_file_lines: int,
    ):
        """
        Initialize reflection service.

        Args:
            memory: Memory store for reading/writing reflections.
            chat_callable: Hardened chat function for generating reflections.
            model: Model to use for reflection generation.
        """
        self.memory = memory
        self.chat_callable = chat_callable
        self.model = model
        self.auto_promote = auto_promote
        self.allowed_targets = allowed_targets or ["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"]
        self.max_file_lines = max(50, max_file_lines)

    async def reflect_on_session(
        self,
        messages: list[dict],
        session_key: str,
        digest: SessionDigest,
    ) -> None:
        """
        Reflect on a session and extract learnings.

        Args:
            messages: Session messages to analyze.
            session_key: Session identifier.
            digest: Deterministic session digest.
        """
        try:
            # Skip empty sessions
            if not messages:
                logger.debug("Reflection skipped: empty session {}", session_key)
                return

            # 0. Deterministic high-priority corrections win over weak-model synthesis.
            priority_result = self._build_priority_reflection(digest)
            if priority_result:
                recent = self.memory.list_memories("reflections")[:10]
                if not self._is_duplicate_or_contradictory(priority_result, recent):
                    self._write_reflection(priority_result, session_key)
                    if (
                        self.auto_promote
                        and priority_result.get("should_promote")
                        and priority_result.get("promote_content")
                    ):
                        await self._promote(priority_result)
                    logger.info("Reflection complete (priority rule): {}", priority_result["title"])
                return

            # 1. Load recent reflections for dedup context
            recent = self.memory.list_memories("reflections")[:10]

            # 2. Build prompt
            digest_text = self._format_digest(digest)
            recent_section = self._format_recent_reflections(recent)

            user_prompt = self.USER_PROMPT.format(
                digest=digest_text,
                recent_reflections_section=recent_section,
            )

            # 3. Single LLM call
            response = await self.chat_callable(
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
            if result.get("skip") and result.get("reason") == "Invalid response format":
                repair_prompt = (
                    "Convert the previous reflection attempt into valid JSON only. "
                    "If there is no valid user-specific learning, return "
                    '{"skip": true, "reason": "No new insights"}.'
                )
                repaired = await self.chat_callable(
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                        {"role": "assistant", "content": response.content or ""},
                        {"role": "user", "content": repair_prompt},
                    ],
                    model=self.model,
                    temperature=0.0,
                    max_tokens=384,
                )
                result = self._parse_response(repaired.content)

            if result.get("skip"):
                logger.debug("Reflection skipped: {}", result.get("reason", "no insights"))
                return

            # 5. Validate required fields
            if not self._is_valid_result(result, digest):
                logger.warning("Reflection rejected by validation: {}", result)
                return

            if not result.get("evidence"):
                result["evidence"] = self._build_fallback_evidence(digest)

            if self._is_duplicate_or_contradictory(result, recent):
                logger.info("Reflection skipped after duplicate/contradiction guard: {}", result["title"])
                return

            # 6. Write reflection
            self._write_reflection(result, session_key)

            # 7. Auto-promote if flagged
            if self.auto_promote and result.get("should_promote") and result.get("promote_content"):
                await self._promote(result)

            logger.info("Reflection complete: {}", result.get("title", "unknown"))

        except Exception as e:
            logger.warning("Reflection failed (non-fatal): {}", e)

    def _format_digest(self, digest: SessionDigest) -> str:
        """Format deterministic digest data for reflection."""
        lines = [
            f"Session: {digest.session_key}",
            f"Channel: {digest.channel}",
            f"Time: {digest.first_timestamp} -> {digest.last_timestamp}",
            "",
            "User requests:",
        ]
        lines.extend(f"- {item}" for item in (digest.user_requests or ["None captured."]))
        lines.append("")
        lines.append("User corrections / expectations:")
        lines.extend(f"- {item}" for item in (digest.user_corrections or ["None captured."]))
        lines.append("")
        lines.append("Outcomes:")
        lines.extend(f"- {item}" for item in (digest.outcomes or ["None captured."]))
        if digest.failures:
            lines.append("")
            lines.append("Ignore these tool or provider failures:")
            lines.extend(f"- {item}" for item in digest.failures)
        return "\n".join(lines)

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

    def _build_priority_reflection(self, digest: SessionDigest) -> dict[str, Any] | None:
        """Extract a deterministic high-priority workflow lesson from explicit user corrections."""
        correction_markers = (
            "you delegated the entire thing",
            "you did it again",
            "make a plan",
            "break the task",
            "break it into smaller",
            "delegate those small tasks",
            "do it yourself",
            "you as the coordinator",
            "don't leave me waiting",
            "pick up where it failed",
            "retry it",
            "figure it out",
        )

        evidence = ""
        for item in reversed(digest.user_corrections):
            normalized = self._normalize_text(item)
            if any(marker in normalized for marker in correction_markers):
                evidence = item.strip()
                break

        if not evidence:
            return None

        return {
            "title": "Maintain ownership of delegated tasks",
            "content": (
                "I learned that for broad multi-step work I must keep ownership: make a plan, "
                "delegate only bounded subtasks to subagents, monitor failures, and either retry "
                "with tighter scope or take over myself instead of pushing the problem back to the user."
            ),
            "type": "workflow",
            "evidence": evidence,
            "should_promote": True,
            "promote_to": "AGENTS.md",
            "promote_content": (
                "For broad or strategic tasks, keep main-agent ownership. Plan first, delegate only "
                "bounded subtasks, track progress, retry/refine failed subagent work internally, and "
                "never surface raw subagent failure or refinement requests as the final user-facing answer."
            ),
        }

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

    def _is_valid_result(self, result: dict[str, Any], digest: SessionDigest) -> bool:
        """Reject malformed or low-value reflections."""
        required = ("title", "content", "type")
        if any(not result.get(field) for field in required):
            return False

        reflection_type = str(result.get("type", "")).strip().lower()
        if reflection_type not in {"preference", "correction", "pattern", "insight", "workflow"}:
            return False

        title = str(result.get("title", "")).strip()
        content = str(result.get("content", "")).strip()
        evidence = str(result.get("evidence", "")).strip()
        normalized = self._normalize_text(" ".join([title, content, evidence]))
        normalized_title = self._normalize_text(title)

        banned_markers = (
            "short descriptive title",
            "tool failure",
            "provider failure",
            "file not found",
            "invalid response format",
            "error calling llm",
            "generic summary",
        )
        if any(marker in normalized for marker in banned_markers):
            return False

        generic_titles = {
            "short descriptive title",
            "descriptive title",
            "user preference",
            "user preferences",
            "insight",
            "workflow insight",
            "learning",
        }
        if normalized_title in generic_titles:
            return False

        if any(marker in normalized for marker in ("tool error", "missing file", "read_file", "list_dir")):
            return False

        return self._is_grounded_in_digest(result, digest)

    def _build_fallback_evidence(self, digest: SessionDigest) -> str:
        """Create deterministic evidence text when the model omits it."""
        if digest.user_corrections:
            return digest.user_corrections[-1]
        if digest.user_requests:
            return digest.user_requests[-1]
        if digest.outcomes:
            return digest.outcomes[-1]
        return f"Grounded in session {digest.session_key}."

    def _is_grounded_in_digest(self, result: dict[str, Any], digest: SessionDigest) -> bool:
        """Ensure the reflection is supported by session evidence."""
        haystacks = [
            *digest.user_requests,
            *digest.user_corrections,
            *digest.outcomes,
            *digest.event_lines,
        ]
        digest_text = self._normalize_text(" ".join(haystacks))
        if not digest_text:
            return False

        combined_text = self._normalize_text(
            " ".join(
                [
                    str(result.get("title", "")),
                    str(result.get("content", "")),
                    str(result.get("evidence", "")),
                ]
            )
        )

        preference_markers = {
            "concise": ("short", "brief", "direct", "concise"),
            "brief": ("short", "brief", "concise"),
            "detailed": ("detail", "detailed", "verbose"),
            "verbose": ("detail", "detailed", "verbose"),
        }
        for marker, aliases in preference_markers.items():
            if marker in combined_text and any(alias in digest_text for alias in aliases):
                return True

        content_tokens = [
            token
            for token in combined_text.split()
            if len(token) >= 5 and token not in {"learned", "should", "future", "agent", "their", "about"}
        ]
        if not content_tokens:
            return True

        matched = sum(1 for token in content_tokens if token in digest_text)
        threshold = max(1, min(3, len(content_tokens) // 3 or 1))
        return matched >= threshold

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

        # Validate target file against config
        if target_file == "none":
            return
        if target_file not in self.allowed_targets:
            logger.warning("Invalid promote target: {}", target_file)
            return

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
        if file_path.exists():
            line_count = len(file_path.read_text(encoding="utf-8").splitlines())
            if line_count >= self.max_file_lines:
                logger.warning("Skipping reflection promotion; {} exceeds {} lines", target_file, self.max_file_lines)
                return
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
