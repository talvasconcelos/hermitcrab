"""Onboarding profile sync for workspace bootstrap files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Awaitable, Callable

import json_repair
from loguru import logger


class OnboardingProfileService:
    """Extract durable onboarding insights and sync bootstrap profile files."""

    ONBOARDING_FLAG_FILE = ".onboarding_mode"
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
  "confidence": 0.0,
  "user_md": ["- ..."],
  "soul_md": ["- ..."],
  "identity_md": ["- ..."]
}

Rules:
- Keep only durable high-signal insights.
- Prefer concrete facts/preferences/constraints in user_md.
- Put values, motivations, and behavior patterns in soul_md.
- Put how the assistant should behave for this user in identity_md.
- Do not include temporary details or one-off requests.
- Use short bullet-ready lines. No markdown headers.
- If confidence is low or nothing durable, return:
  {"skip": true, "confidence": 0.0, "user_md": [], "soul_md": [], "identity_md": []}
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
        return (self.workspace / self.ONBOARDING_FLAG_FILE).exists()

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
            return False
        if payload.get("skip"):
            return False

        confidence = self._parse_confidence(payload.get("confidence"))
        if confidence < self.MIN_CONFIDENCE:
            return False

        changed = False
        for key, filename in self.TARGET_FILES.items():
            bullets = self._normalize_bullets(payload.get(key))
            if not bullets:
                continue
            path = self.workspace / filename
            if self._merge_section_bullets(path, bullets):
                changed = True

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
