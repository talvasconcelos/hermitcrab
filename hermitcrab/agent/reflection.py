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

    VALID_SCOPES = {
        "global_product",
        "assistant_behavior",
        "tool_usage",
        "user_preference",
        "session_tactic",
    }
    VALID_PROMOTION_TARGETS = {"AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md", "none"}

    SYSTEM_PROMPT = """Reflect on the recent conversation for your future self.

Focus on one concrete learning that would make you more useful next time.

Be specific, actionable, and grounded in this session.
Prefer learnings about user preferences, workflow expectations, coordination behavior, or tool-usage discipline.
Do not produce a narrative summary.
Do not produce more than one insight.
"""

    USER_PROMPT = """Review this session digest and extract one high-value learning for the future agent.

Session digest:
{digest}

{recent_reflections_section}

Respond with JSON:
{{
  "title": "Short, descriptive title",
  "observation": "What happened in this session that matters?",
  "impact": "Why does it matter for future behavior?",
  "lesson": "What should the future agent learn from it? Write in first person.",
  "recommended_behavior": "Concrete behavior to apply next time.",
  "scope": "global_product|assistant_behavior|tool_usage|user_preference|session_tactic",
  "confidence": 0.0,
  "evidence": "Concrete example from this session that supports the learning",
  "should_promote": true,
  "promotion_target": "AGENTS.md|TOOLS.md|SOUL.md|IDENTITY.md|none",
  "promote_content": "One concise instruction that belongs in the target file"
}}

If nothing is worth remembering, respond: {{"skip": true, "reason": "No new insights"}}

Rules:
- ONE insight only (pick the most valuable)
- lesson should use first-person voice
- evidence must cite a concrete user behavior, correction, or repeated pattern from this session
- prioritize user corrections, preferences, workflow expectations, and durable behavior changes
- do not produce a generic summary of the session
- avoid duplicating recent reflections
- only set should_promote=true if the learning is durable enough to belong in persistent context, not just this one session
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
        self.allowed_targets = allowed_targets or [
            "AGENTS.md",
            "TOOLS.md",
            "SOUL.md",
            "IDENTITY.md",
        ]
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
                logger.info(
                    "Reflection skipped after duplicate/contradiction guard: {}", result["title"]
                )
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
            f"User goal: {digest.user_goal or 'Unknown'}",
            "",
            "User requests:",
        ]
        lines.extend(f"- {item}" for item in (digest.user_requests or ["None captured."]))
        lines.append("")
        lines.append("Follow-up user turns / corrections:")
        lines.extend(f"- {item}" for item in (digest.user_corrections or ["None captured."]))
        lines.append("")
        lines.append("Outcomes:")
        lines.extend(f"- {item}" for item in (digest.outcomes or ["None captured."]))
        lines.append("")
        lines.append("Artifacts changed:")
        lines.extend(f"- {item}" for item in (digest.artifacts_changed or ["None captured."]))
        lines.append("")
        lines.append("Decisions made:")
        lines.extend(f"- {item}" for item in (digest.decisions_made or ["None captured."]))
        lines.append("")
        lines.append("Open loops:")
        lines.extend(f"- {item}" for item in (digest.open_loops or ["None captured."]))
        if digest.failures:
            lines.append("")
            lines.append("Failures or friction:")
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
        self._normalize_result_shape(result)

        required = (
            "title",
            "observation",
            "impact",
            "lesson",
            "recommended_behavior",
            "scope",
        )
        if any(not str(result.get(field, "")).strip() for field in required):
            return False

        scope = str(result.get("scope", "")).strip().lower()
        if scope not in self.VALID_SCOPES:
            return False

        confidence = self._parse_confidence(result.get("confidence"))
        if confidence is None or confidence < 0.55:
            return False
        result["confidence"] = confidence

        title = str(result.get("title", "")).strip()
        observation = str(result.get("observation", "")).strip()
        impact = str(result.get("impact", "")).strip()
        lesson = str(result.get("lesson", "")).strip()
        recommended_behavior = str(result.get("recommended_behavior", "")).strip()
        evidence = str(result.get("evidence", "")).strip()
        normalized = self._normalize_text(
            " ".join([title, observation, impact, lesson, recommended_behavior, evidence])
        )
        normalized_title = self._normalize_text(title)

        if len([token for token in normalized_title.split() if len(token) >= 4]) < 2:
            return False

        if self._looks_like_prompt_placeholder(title, lesson, recommended_behavior):
            return False

        if self._looks_like_failure_report(scope, normalized, digest):
            return False

        result["should_promote"] = self._coerce_bool(result.get("should_promote", False))
        result["promotion_target"] = self._normalize_promotion_target(
            result.get("promotion_target")
        )
        if result["should_promote"] and result["promotion_target"] == "none" and confidence >= 0.8:
            result["promotion_target"] = self._default_promotion_target(scope)
        if result["promotion_target"] not in self.VALID_PROMOTION_TARGETS:
            return False

        return self._is_grounded_in_digest(result, digest)

    def _build_fallback_evidence(self, digest: SessionDigest) -> str:
        """Create deterministic evidence text when the model omits it."""
        if digest.user_corrections:
            return digest.user_corrections[-1]
        if digest.open_loops:
            return digest.open_loops[-1]
        if digest.user_requests:
            return digest.user_requests[-1]
        if digest.outcomes:
            return digest.outcomes[-1]
        return f"Grounded in session {digest.session_key}."

    @staticmethod
    def _parse_confidence(value: Any) -> float | None:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        if confidence < 0.0 or confidence > 1.0:
            return None
        return confidence

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return bool(value)

    def _normalize_result_shape(self, result: dict[str, Any]) -> None:
        """Accept legacy reflection keys while preferring the newer schema."""
        if result.get("content") and not result.get("lesson"):
            result["lesson"] = result["content"]
        legacy_type = str(result.get("type", "")).strip().lower()
        if legacy_type and not result.get("scope"):
            legacy_scope_map = {
                "preference": "user_preference",
                "correction": "assistant_behavior",
                "pattern": "session_tactic",
                "insight": "assistant_behavior",
                "workflow": "global_product",
            }
            result["scope"] = legacy_scope_map.get(legacy_type, "assistant_behavior")
        if result.get("promote_to") and not result.get("promotion_target"):
            result["promotion_target"] = result["promote_to"]
        if result.get("lesson") and not result.get("recommended_behavior"):
            result["recommended_behavior"] = result["lesson"]
        if result.get("lesson") and not result.get("observation"):
            result["observation"] = result.get("evidence") or result["lesson"]
        if result.get("lesson") and not result.get("impact"):
            result["impact"] = "This changes how I should behave in similar situations."
        if result.get("confidence") is None:
            result["confidence"] = 0.8 if result.get("should_promote") else 0.7

    def _normalize_promotion_target(self, value: Any) -> str:
        target = str(value or "none").strip()
        if not target:
            return "none"
        if "|" in target:
            for option in target.split("|"):
                option = option.strip()
                if option in self.VALID_PROMOTION_TARGETS:
                    return option
            return "none"
        return target if target in self.VALID_PROMOTION_TARGETS else "none"

    @staticmethod
    def _default_promotion_target(scope: str) -> str:
        mapping = {
            "global_product": "AGENTS.md",
            "assistant_behavior": "SOUL.md",
            "tool_usage": "TOOLS.md",
            "session_tactic": "AGENTS.md",
        }
        return mapping.get(scope, "none")

    def _looks_like_prompt_placeholder(
        self, title: str, lesson: str, recommended_behavior: str
    ) -> bool:
        normalized_title = self._normalize_text(title)
        normalized_body = self._normalize_text(f"{lesson} {recommended_behavior}")
        if normalized_title in {
            "short descriptive title",
            "descriptive title",
            "learning",
            "insight",
        }:
            return True
        return any(
            marker in normalized_body
            for marker in {
                "what did you learn",
                "specific instruction for your future self",
                "respond with json",
            }
        )

    def _looks_like_failure_report(
        self, scope: str, normalized: str, digest: SessionDigest
    ) -> bool:
        if scope == "tool_usage":
            return False
        if digest.failures and not (digest.user_corrections or digest.outcomes):
            return True
        failure_tokens = {"tool", "error", "provider", "missing", "failed", "file", "response"}
        return sum(1 for token in failure_tokens if token in normalized) >= 3

    def _is_grounded_in_digest(self, result: dict[str, Any], digest: SessionDigest) -> bool:
        """Ensure the reflection is supported by session evidence."""
        haystacks = [
            *digest.user_requests,
            *digest.user_corrections,
            *digest.outcomes,
            *digest.event_lines,
            *digest.artifacts_changed,
            *digest.decisions_made,
            *digest.open_loops,
            *digest.assistant_responses,
        ]
        digest_text = self._normalize_text(" ".join(haystacks))
        if not digest_text:
            return False

        combined_text = self._normalize_text(
            " ".join(
                [
                    str(result.get("title", "")),
                    str(result.get("observation", "")),
                    str(result.get("impact", "")),
                    str(result.get("lesson", "")),
                    str(result.get("recommended_behavior", "")),
                    str(result.get("evidence", "")),
                ]
            )
        )

        content_tokens = [
            token
            for token in combined_text.split()
            if len(token) >= 5
            and token not in {"learned", "should", "future", "agent", "their", "about", "behavior"}
        ]
        if not content_tokens:
            return True

        matched = sum(1 for token in content_tokens if token in digest_text)
        threshold = max(1, min(3, len(content_tokens) // 3 or 1))
        return matched >= threshold

    def _write_reflection(self, result: dict, session_key: str) -> None:
        """Write reflection to memory."""
        reflection_scope = result.get("scope", "assistant_behavior")
        tags = [reflection_scope, "reflection", "learning"]
        content = (
            f"Observation: {result['observation']}\n"
            f"Impact: {result['impact']}\n"
            f"Lesson: {result['lesson']}\n"
            f"Recommended behavior: {result['recommended_behavior']}"
        )

        # Build context from promote info
        context_parts = [f"Evidence: {result.get('evidence', '').strip()}"]
        context_parts.append(f"Confidence: {result.get('confidence', 0.0)}")
        if result.get("should_promote"):
            context_parts.append(
                f"Marked for promotion to {result.get('promotion_target', 'unknown')}"
            )

        self.memory.write_reflection(
            title=result["title"],
            content=content,
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

    def _is_duplicate_or_contradictory(self, result: dict, recent: list) -> bool:
        """Reject duplicate or obviously contradictory reflections."""
        title = result.get("title")
        content = result.get("lesson") or result.get("content")
        evidence = result.get("evidence")
        reflection_scope = result.get("scope", "assistant_behavior")

        for item in recent:
            if item is None:
                continue

            title_similarity = self._similarity(title, item.title)
            content_similarity = self._similarity(content, item.content)
            evidence_similarity = self._similarity(evidence, item.metadata.get("context", ""))

            if title_similarity >= 0.88 and (
                content_similarity >= 0.82 or evidence_similarity >= 0.75
            ):
                return True

            if reflection_scope == "user_preference" and "user_preference" in (item.tags or []):
                if title_similarity >= 0.92 and content_similarity >= 0.68:
                    return True

        return False

    async def _promote(self, result: dict) -> None:
        """Auto-promote reflection to bootstrap file."""
        target_file = self._normalize_promotion_target(result.get("promotion_target", "none"))
        content = result.get("promote_content")

        if not content:
            return

        if target_file == "none":
            return
        if target_file not in self.allowed_targets:
            logger.warning("Invalid promote target: {}", target_file)
            return

        section_map = {
            "global_product": "## Product Learnings",
            "assistant_behavior": "## Behavior Learnings",
            "tool_usage": "## Tool Learnings",
            "user_preference": "## User Preference Learnings",
            "session_tactic": "## Session Tactics",
        }
        section = section_map.get(result.get("scope", "assistant_behavior"), "## Learnings")

        file_path = self.memory.workspace / target_file
        if file_path.exists():
            line_count = len(file_path.read_text(encoding="utf-8").splitlines())
            if line_count >= self.max_file_lines:
                logger.warning(
                    "Skipping reflection promotion; {} exceeds {} lines",
                    target_file,
                    self.max_file_lines,
                )
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
