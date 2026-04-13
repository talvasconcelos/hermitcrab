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
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib.resources import files as package_files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import json_repair
from loguru import logger

if TYPE_CHECKING:
    from hermitcrab.agent.loop import SessionDigest
    from hermitcrab.agent.memory import MemoryStore


@dataclass(slots=True)
class ReflectionOutcome:
    status: str
    reason: str
    title: str | None = None
    file_path: str | None = None


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
    TARGET_SCOPE_MAP = {
        "AGENTS.md": {"global_product", "session_tactic", "assistant_behavior"},
        "TOOLS.md": {"tool_usage"},
        "SOUL.md": {"assistant_behavior"},
        "IDENTITY.md": {"assistant_behavior"},
    }

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
  "promote_content": "One concise bullet-ready instruction that belongs in the target file"
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
- prefer `AGENTS.md` for product/workflow policy, `TOOLS.md` for tool discipline, `SOUL.md` for stable behavior style, and `IDENTITY.md` only for durable self-model constraints
- user-specific preferences usually stay in memory; only promote them when they clearly belong in durable assistant-wide context
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
        notify_user: bool = True,
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
        self.notify_user = notify_user

    async def reflect_on_session(
        self,
        messages: list[dict],
        session_key: str,
        digest: SessionDigest,
    ) -> ReflectionOutcome:
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
                return ReflectionOutcome(status="skipped", reason="empty_session")

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
                reason = str(result.get("reason", "no_insights"))
                logger.debug("Reflection skipped: {}", reason)
                return ReflectionOutcome(status="skipped", reason=reason)

            # 5. Validate required fields
            valid, reason = self._validate_result(result, digest)
            if not valid:
                logger.debug("Reflection rejected by validation (reason={}): {}", reason, result)
                return ReflectionOutcome(status="skipped", reason=reason)

            if not result.get("evidence"):
                result["evidence"] = self._build_fallback_evidence(digest)

            if self._is_duplicate_or_contradictory(result, recent):
                logger.info(
                    "Reflection skipped after duplicate/contradiction guard: {}", result["title"]
                )
                return ReflectionOutcome(
                    status="skipped",
                    reason="duplicate_or_contradictory",
                    title=result.get("title"),
                )

            # 6. Write reflection
            item = self._write_reflection(result, session_key)

            # 7. Auto-promote if flagged
            if self.auto_promote and result.get("should_promote") and result.get("promote_content"):
                await self._promote(result)

            logger.info("Reflection complete: {}", result.get("title", "unknown"))
            return ReflectionOutcome(
                status="saved",
                reason="saved",
                title=item.title,
                file_path=str(item.file_path),
            )

        except Exception as e:
            logger.warning("Reflection failed (non-fatal): {}", e)
            return ReflectionOutcome(status="failed", reason=str(e))

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

        parsed = self._parse_json_like_response(content)
        if parsed is not None:
            return parsed

        parsed = self._parse_labeled_response(content)
        if parsed is not None:
            return parsed

        return {"skip": True, "reason": "Invalid response format"}

    def _parse_json_like_response(self, content: str) -> dict[str, Any] | None:
        candidates = [content.strip()]

        fenced = re.findall(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", content, flags=re.DOTALL)
        candidates.extend(candidate.strip() for candidate in fenced if candidate.strip())

        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            candidates.append(content[start:end])

        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            candidates.append(content[start:end])

        for candidate in candidates:
            try:
                result = json_repair.loads(candidate)
            except Exception as exc:
                logger.debug("Reflection JSON candidate parse failed: {}", exc)
                continue
            normalized = self._normalize_parsed_result(result)
            if normalized is not None:
                return normalized
        return None

    def _normalize_parsed_result(self, result: Any) -> dict[str, Any] | None:
        if isinstance(result, dict):
            if isinstance(result.get("reflection"), dict):
                return dict(result["reflection"])
            return dict(result)
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return dict(result[0])
        return None

    def _parse_labeled_response(self, content: str) -> dict[str, Any] | None:
        field_patterns = {
            "title": r"(?im)^\s*(?:[-*]\s*)?(?:title)\s*:\s*(.+)$",
            "observation": r"(?im)^\s*(?:[-*]\s*)?(?:observation)\s*:\s*(.+)$",
            "impact": r"(?im)^\s*(?:[-*]\s*)?(?:impact)\s*:\s*(.+)$",
            "lesson": r"(?im)^\s*(?:[-*]\s*)?(?:lesson)\s*:\s*(.+)$",
            "recommended_behavior": r"(?im)^\s*(?:[-*]\s*)?(?:recommended behavior|recommended_behavior)\s*:\s*(.+)$",
            "scope": r"(?im)^\s*(?:[-*]\s*)?(?:scope)\s*:\s*(.+)$",
            "evidence": r"(?im)^\s*(?:[-*]\s*)?(?:evidence)\s*:\s*(.+)$",
            "promotion_target": r"(?im)^\s*(?:[-*]\s*)?(?:promotion target|promotion_target)\s*:\s*(.+)$",
            "promote_content": r"(?im)^\s*(?:[-*]\s*)?(?:promote content|promote_content)\s*:\s*(.+)$",
        }
        result: dict[str, Any] = {}
        for field, pattern in field_patterns.items():
            match = re.search(pattern, content)
            if match:
                result[field] = match.group(1).strip().strip('"')

        confidence_match = re.search(
            r"(?im)^\s*(?:[-*]\s*)?(?:confidence)\s*:\s*([0-9.]+%?)\s*$",
            content,
        )
        if confidence_match:
            result["confidence"] = confidence_match.group(1)

        should_promote_match = re.search(
            r"(?im)^\s*(?:[-*]\s*)?(?:should promote|should_promote)\s*:\s*(true|false|yes|no)\s*$",
            content,
        )
        if should_promote_match:
            result["should_promote"] = should_promote_match.group(1)

        return result or None

    def _validate_result(self, result: dict[str, Any], digest: SessionDigest) -> tuple[bool, str]:
        """Reject malformed or low-value reflections and report the reason."""
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
            return False, "missing_required_fields"

        scope = str(result.get("scope", "")).strip().lower()
        if scope not in self.VALID_SCOPES:
            return False, "invalid_scope"
        result["scope"] = scope

        confidence = self._parse_confidence(result.get("confidence"))
        if confidence is None or confidence < 0.55:
            return False, "low_or_invalid_confidence"
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
            return False, "weak_title"

        if self._looks_like_prompt_placeholder(title, lesson, recommended_behavior):
            return False, "prompt_placeholder"

        if self._looks_like_failure_report(scope, normalized, digest):
            return False, "failure_report"

        result["should_promote"] = self._coerce_bool(result.get("should_promote", False))
        result["scope"] = self._normalize_learning_scope(result, digest)
        result["promotion_target"] = self._resolve_promotion_target(result, digest)
        if result["promotion_target"] not in self.VALID_PROMOTION_TARGETS:
            return False, "invalid_promotion_target"
        if not self._promotion_is_viable(result):
            result["should_promote"] = False
            result["promotion_target"] = "none"

        if not self._is_grounded_in_digest(result, digest):
            return False, "not_grounded_in_digest"
        return True, "ok"

    def _is_valid_result(self, result: dict[str, Any], digest: SessionDigest) -> bool:
        """Backward-compatible validation hook used by tests and call sites."""
        valid, _reason = self._validate_result(result, digest)
        return valid

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
            raw = str(value).strip()
            if not raw:
                return None
            if raw.endswith("%"):
                confidence = float(raw[:-1].strip()) / 100.0
            else:
                confidence = float(raw)
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
        text_fields = (
            "title",
            "observation",
            "impact",
            "lesson",
            "recommended_behavior",
            "evidence",
            "promote_content",
        )
        for field in text_fields:
            if field in result:
                result[field] = self._normalize_text_field(result.get(field))

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
        result["scope"] = self._normalize_learning_scope(result, None)
        result["promotion_target"] = self._normalize_promotion_target(
            result.get("promotion_target")
        )

    def _normalize_promotion_target(self, value: Any) -> str:
        target = str(value or "none").strip()
        if not target:
            return "none"
        normalized = re.sub(r"\s+", "", target).lower()
        alias_map = {
            "agents": "AGENTS.md",
            "agents.md": "AGENTS.md",
            "tools": "TOOLS.md",
            "tools.md": "TOOLS.md",
            "soul": "SOUL.md",
            "soul.md": "SOUL.md",
            "identity": "IDENTITY.md",
            "identity.md": "IDENTITY.md",
            "none": "none",
        }
        if normalized in alias_map:
            return alias_map[normalized]
        if "|" in target:
            for option in target.split("|"):
                option = option.strip()
                normalized_option = self._normalize_promotion_target(option)
                if normalized_option in self.VALID_PROMOTION_TARGETS:
                    return normalized_option
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

    def _resolve_promotion_target(self, result: dict[str, Any], digest: SessionDigest) -> str:
        target = self._normalize_promotion_target(result.get("promotion_target"))
        if not result.get("should_promote"):
            return target

        inferred = self._infer_bootstrap_target(result, digest)
        if inferred != "none":
            return inferred
        if target == "none" and float(result.get("confidence", 0.0) or 0.0) >= 0.8:
            return self._default_promotion_target(str(result.get("scope", "")))
        return target

    def _normalize_learning_scope(self, result: dict[str, Any], _digest: SessionDigest | None) -> str:
        scope = str(result.get("scope", "")).strip().lower().replace(" ", "_")
        alias_map = {
            "global": "global_product",
            "global_product": "global_product",
            "product": "global_product",
            "workflow": "global_product",
            "assistant": "assistant_behavior",
            "assistant_behavior": "assistant_behavior",
            "behavior": "assistant_behavior",
            "tool": "tool_usage",
            "tools": "tool_usage",
            "tool_usage": "tool_usage",
            "preference": "user_preference",
            "user_preference": "user_preference",
            "session": "session_tactic",
            "session_tactic": "session_tactic",
            "tactic": "session_tactic",
        }
        return alias_map.get(scope, scope)

    def _infer_bootstrap_target(self, result: dict[str, Any], digest: SessionDigest) -> str:
        scope = str(result.get("scope", "")).strip().lower()
        if (
            scope == "assistant_behavior"
            and digest.user_corrections
            and (
                digest.outcomes or digest.open_loops or digest.artifacts_changed or digest.failures
            )
        ):
            return "AGENTS.md"
        return self._default_promotion_target(scope) if scope in self.VALID_SCOPES else "none"

    def _promotion_audit_path(self) -> Path:
        """Return the audit log path for bootstrap promotions."""
        return self.memory.workspace / "bootstrap_promotion_log.md"

    def _promotion_is_viable(self, result: dict[str, Any]) -> bool:
        """Allow promotion only for durable, target-appropriate learnings."""
        if not result.get("should_promote"):
            return True

        confidence = float(result.get("confidence", 0.0) or 0.0)
        if confidence < 0.8:
            return False

        scope = str(result.get("scope", "")).strip().lower()
        target = str(result.get("promotion_target", "none")).strip()
        if target == "none":
            return False

        if scope == "user_preference":
            return False

        allowed_scopes = self.TARGET_SCOPE_MAP.get(target)
        if allowed_scopes and scope not in allowed_scopes:
            return False

        promote_content = self._normalize_promote_content(str(result.get("promote_content", "")))
        if not promote_content:
            return False
        if len(promote_content) > 220:
            return False
        if promote_content.count("-") > 1:
            return False
        result["promote_content"] = promote_content
        return True

    @staticmethod
    def _normalize_promote_content(content: str) -> str:
        """Normalize promoted instructions into one concise durable bullet."""
        stripped = " ".join(content.strip().split())
        if not stripped:
            return ""
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        return f"- {stripped}"

    @staticmethod
    def _normalize_text_field(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith(("```", "`")) and text.endswith(("```", "`")):
            text = text.strip("`").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1].strip()
        text = re.sub(r"^\s*[-*]\s+", "", text)
        return " ".join(text.split())

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
        if digest.user_corrections:
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

    def _write_reflection(self, result: dict, session_key: str):
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

        return self.memory.write_reflection(
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
        content = self._normalize_promote_content(str(result.get("promote_content", "")))

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
        if self._bootstrap_conflict_exists(target_file, content):
            logger.info(
                "Skipping bootstrap promotion for {} due to duplicate/conflict in existing bootstrap guidance",
                result["title"],
            )
            return
        self._ensure_bootstrap_file(file_path)
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
        self._append_promotion_audit(result, target_file, content)

        logger.info("Auto-promoted reflection to {}: {}", target_file, result["title"])

    def _ensure_bootstrap_file(self, file_path: Path) -> None:
        """Seed missing bootstrap files from bundled templates when possible."""
        if file_path.exists():
            return

        template_resource = package_files("hermitcrab") / "templates" / file_path.name
        if template_resource.is_file():
            file_path.write_text(template_resource.read_text(encoding="utf-8"), encoding="utf-8")

    def _append_to_bootstrap(self, file_path: Path, section: str, content: str) -> None:
        """Append content to bootstrap file section."""
        if not file_path.exists():
            file_path.write_text(f"{section}\n\n{content}\n")
            return

        existing = file_path.read_text(encoding="utf-8")
        if self._bootstrap_already_contains(existing, content):
            return

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

    def _bootstrap_already_contains(self, existing: str, content: str) -> bool:
        """Avoid appending effectively duplicate promoted guidance."""
        normalized_content = self._normalize_text(content)
        if not normalized_content:
            return True

        for line in existing.splitlines():
            normalized_line = self._normalize_text(line)
            if not normalized_line:
                continue
            if normalized_line == normalized_content:
                return True
            if SequenceMatcher(None, normalized_line, normalized_content).ratio() >= 0.92:
                return True
        return False

    def _bootstrap_conflict_exists(self, target_file: str, content: str) -> bool:
        """Reject promotions that already exist or nearly exist in bootstrap files."""
        normalized_content = self._normalize_text(content)
        if not normalized_content:
            return True

        for candidate_name in self.allowed_targets:
            candidate_path = self.memory.workspace / candidate_name
            if not candidate_path.exists():
                continue
            existing = candidate_path.read_text(encoding="utf-8")
            for line in existing.splitlines():
                normalized_line = self._normalize_text(line)
                if not normalized_line:
                    continue
                similarity = SequenceMatcher(None, normalized_line, normalized_content).ratio()
                if similarity >= 0.92:
                    return True
                if candidate_name != target_file and similarity >= 0.84:
                    return True
        return False

    def _append_promotion_audit(
        self, result: dict[str, Any], target_file: str, content: str
    ) -> None:
        """Append an auditable record of bootstrap promotions."""
        audit_path = self._promotion_audit_path()
        entry = (
            f"## {result['title']}\n\n"
            f"- target: {target_file}\n"
            f"- scope: {result.get('scope', 'unknown')}\n"
            f"- confidence: {result.get('confidence', 0.0)}\n"
            f"- notify_user: {self.notify_user}\n"
            f"- content: {content}\n"
        )
        existing = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
        separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
        audit_path.write_text(f"{existing}{separator}{entry}", encoding="utf-8")
