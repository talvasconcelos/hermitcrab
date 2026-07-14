"""Onboarding profile sync for workspace bootstrap files."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import json_repair
from loguru import logger


async def _noop_chat_callable(**_kwargs: Any) -> Any:
    return None


class OnboardingProfileService:
    """Extract durable onboarding insights and sync bootstrap profile files."""

    ONBOARDING_FLAG_FILE = ".onboarding_mode"
    STATE_FILE = Path("onboarding/state.json")
    ACTIVE_STATUSES = {"active", "ready_to_confirm"}
    TERMINAL_STATUSES = {"completed", "paused"}
    VALID_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
    MIN_CONFIDENCE = 0.75
    MAX_CONTEXT_MESSAGES = 12
    MAX_BULLETS_PER_FILE = 6
    SECTION_TITLE = "## Onboarding Insights"
    TARGET_FILES = {
        "user_md": "USER.md",
        "soul_md": "SOUL.md",
        "identity_md": "IDENTITY.md",
    }

    SYSTEM_PROMPT = """You extract durable onboarding insights from conversation.

Return JSON only with this exact shape:
{
  "skip": false,
  "insights": [
    {
      "target": "USER.md",
      "bullet": "- ...",
      "evidence": "exact supporting text from the conversation",
      "confidence": 0.0,
      "reason": "why this is durable"
    }
  ],
  "observed_domains": [],
  "pending_assumptions": []
}

Rules:
- Keep only durable high-signal insights.
- `target` must be one of USER.md, SOUL.md, or IDENTITY.md.
- Prefer concrete facts/preferences/constraints in USER.md.
- Put values, motivations, and behavior patterns in SOUL.md.
- Put how the assistant should behave for this user in IDENTITY.md.
- Every insight must include exact evidence from the supplied conversation.
- Do not include temporary details or one-off requests.
- Use short bullet-ready lines. No markdown headers.
- If nothing durable is grounded in the conversation, return:
  {"skip": true, "insights": [], "observed_domains": [], "pending_assumptions": []}
"""

    def __init__(
        self,
        workspace: Path,
        *,
        chat_callable: Callable[..., Awaitable[Any]],
        model: str,
    ):
        self.workspace = workspace
        self.chat_callable = chat_callable
        self.model = model

    def is_enabled(self) -> bool:
        if not (self.workspace / self.ONBOARDING_FLAG_FILE).exists():
            return False
        return self.read_state().get("status") in self.ACTIVE_STATUSES

    @property
    def state_path(self) -> Path:
        return self.workspace / self.STATE_FILE

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _default_state(self, status: str = "active") -> dict[str, Any]:
        now = self._now()
        return {
            "status": status,
            "started_at": now,
            "last_updated_at": now,
            "observed_domains": [],
            "pending_assumptions": [],
            "confirmed_insights": [],
            "suggested_use_cases": [],
        }

    def read_state(self) -> dict[str, Any]:
        """Read or initialize the inspectable onboarding state artifact."""
        if not (self.workspace / self.ONBOARDING_FLAG_FILE).exists():
            return self._default_state("completed")
        path = self.state_path
        if not path.exists():
            state = self._default_state("active")
            self._write_state(state)
            return state
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Onboarding state is corrupt; resetting to active: {}", exc)
            state = self._default_state("active")
            self._write_state(state)
            return state
        if not isinstance(state, dict) or state.get("status") not in self.VALID_STATUSES:
            state = self._default_state("active")
            self._write_state(state)
            return state
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        state = dict(state)
        state["last_updated_at"] = self._now()
        if not state.get("started_at"):
            state["started_at"] = state["last_updated_at"]
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def set_status(self, status: str) -> None:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid onboarding status: {status}")
        state = self.read_state()
        state["status"] = status
        self._write_state(state)

    def pause(self) -> None:
        self.set_status("paused")

    def resume(self) -> None:
        self.set_status("active")

    def complete(self) -> None:
        self.set_status("completed")

    @classmethod
    def is_workspace_onboarding_active(cls, workspace: Path) -> bool:
        service = cls(workspace, chat_callable=_noop_chat_callable, model="")
        return service.is_enabled()

    async def maybe_sync_from_messages(self, messages: list[dict[str, Any]]) -> bool:
        """Extract onboarding insights and persist bootstrap profile updates."""
        if not self.is_enabled():
            return False

        context = self._build_conversation_context(messages)
        if not context:
            return False

        try:
            response = await self.chat_callable(
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": f"Conversation:\n\n{context}"},
                ],
                model=self.model,
                temperature=0.0,
                max_tokens=500,
            )
        except Exception as exc:
            logger.debug("Onboarding profile extraction failed: {}", exc)
            return False

        payload = self._parse_payload(response.content if response else None)
        if not payload:
            self._audit_onboarding("skipped", reason="parse_failed")
            return False
        if payload.get("skip"):
            self._audit_onboarding("skipped", reason="no_durable_insight")
            return False

        insight_items = self._normalize_insights(payload)
        if not insight_items:
            self._audit_onboarding("validation_failed", reason="invalid_schema")
            return False

        changed = False
        conversation_text = self._normalize_line(context)
        for insight in insight_items:
            target = insight.get("target", "")
            bullet = insight.get("bullet", "")
            evidence = insight.get("evidence", "")
            insight_confidence = self._parse_confidence(insight.get("confidence"))
            if target not in self.TARGET_FILES.values():
                self._audit_onboarding("validation_failed", reason="invalid_target", insight=insight)
                continue
            if insight_confidence < self.MIN_CONFIDENCE:
                self._audit_onboarding("validation_failed", reason="low_confidence", insight=insight)
                continue
            if not evidence:
                self._audit_onboarding("validation_failed", reason="missing_evidence", insight=insight)
                continue
            if self._normalize_line(evidence) not in conversation_text:
                self._audit_onboarding(
                    "validation_failed",
                    reason="evidence_not_in_conversation",
                    insight=insight,
                )
                continue
            path = self.workspace / target
            if self._merge_section_bullets(path, [bullet]):
                self._audit_onboarding("written", reason=insight.get("reason") or "written", insight=insight)
                changed = True
            else:
                self._audit_onboarding("duplicate", reason="duplicate", insight=insight)
        self._update_state_from_payload(payload)
        return changed

    def _build_conversation_context(self, messages: list[dict[str, Any]]) -> str:
        recent: list[str] = []
        for msg in reversed(messages):
            role = str(msg.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            cleaned = " ".join(content.strip().split())
            if not cleaned:
                continue
            recent.append(f"{role}: {cleaned}")
            if len(recent) >= self.MAX_CONTEXT_MESSAGES:
                break
        recent.reverse()
        return "\n".join(recent)

    @staticmethod
    def _parse_payload(content: str | None) -> dict[str, Any] | None:
        if not content:
            return None
        try:
            parsed = json_repair.loads(content)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _parse_confidence(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return numeric if 0.0 <= numeric <= 1.0 else 0.0

    def _normalize_insights(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        raw_insights = payload.get("insights")
        if not isinstance(raw_insights, list):
            return []
        insights: list[dict[str, str]] = []
        for item in raw_insights:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or "").strip()
            bullet_list = self._normalize_bullets([item.get("bullet")])
            bullet = bullet_list[0] if bullet_list else ""
            insights.append(
                {
                    "target": target,
                    "bullet": bullet,
                    "evidence": str(item.get("evidence") or "").strip(),
                    "confidence": item.get("confidence"),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
        return insights

    def _audit_onboarding(
        self,
        event: str,
        *,
        reason: str,
        insight: dict[str, Any] | None = None,
    ) -> None:
        insight = insight or {}
        payload = {
            "timestamp": self._now(),
            "event": event,
            "reason": reason,
            "target": insight.get("target"),
            "bullet": insight.get("bullet"),
            "evidence": insight.get("evidence"),
        }
        audit_path = self.workspace / "onboarding" / "audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def _update_state_from_payload(self, payload: dict[str, Any]) -> None:
        state = self.read_state()
        if isinstance(payload.get("observed_domains"), list):
            existing = set(state.get("observed_domains") or [])
            for domain in payload["observed_domains"]:
                text = str(domain).strip()
                if text:
                    existing.add(text)
            state["observed_domains"] = sorted(existing)
        if isinstance(payload.get("pending_assumptions"), list):
            state["pending_assumptions"] = [
                str(item).strip() for item in payload["pending_assumptions"] if str(item).strip()
            ]
        self._write_state(state)

    def _normalize_bullets(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        bullets: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            text = " ".join(item.strip().split())
            if not text:
                continue
            if not text.startswith("- "):
                text = f"- {text.lstrip('-').strip()}"
            bullets.append(text)
            if len(bullets) >= self.MAX_BULLETS_PER_FILE:
                break
        return bullets

    def _merge_section_bullets(self, path: Path, new_bullets: list[str]) -> bool:
        if not path.exists():
            return False

        original = path.read_text(encoding="utf-8")
        section_lines, has_section = self._extract_existing_section_bullets(original)
        existing_norm = {self._normalize_line(line) for line in section_lines}
        additions = [line for line in new_bullets if self._normalize_line(line) not in existing_norm]
        if not additions:
            return False

        if has_section:
            updated = self._append_to_existing_section(original, additions)
        else:
            updated = original.rstrip() + "\n\n" + self.SECTION_TITLE + "\n" + "\n".join(additions) + "\n"

        path.write_text(updated, encoding="utf-8")
        return True

    def _extract_existing_section_bullets(self, content: str) -> tuple[list[str], bool]:
        lines = content.splitlines()
        in_section = False
        bullets: list[str] = []
        has_section = False
        for line in lines:
            if line.strip() == self.SECTION_TITLE:
                in_section = True
                has_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section and line.strip().startswith("- "):
                bullets.append(" ".join(line.strip().split()))
        return bullets, has_section

    def _append_to_existing_section(self, content: str, additions: list[str]) -> str:
        lines = content.splitlines()
        out: list[str] = []
        in_section = False
        inserted = False
        for line in lines:
            if line.strip() == self.SECTION_TITLE:
                in_section = True
                out.append(line)
                continue
            if in_section and line.startswith("## "):
                if not inserted:
                    out.extend(additions)
                    inserted = True
                in_section = False
                out.append(line)
                continue
            out.append(line)

        if in_section and not inserted:
            out.extend(additions)

        return "\n".join(out).rstrip() + "\n"

    @staticmethod
    def _normalize_line(line: str) -> str:
        return re.sub(r"\s+", " ", line.strip().lower())
