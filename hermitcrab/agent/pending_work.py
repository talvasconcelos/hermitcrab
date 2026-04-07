"""Coordinator-owned pending-work tracking for actionable turns."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(text: str | None) -> str:
    return " ".join((text or "").strip().split())


def snippet(text: str | None, max_chars: int = 280) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def has_structured_payload(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    lower = raw.lower()
    if len(raw) >= 180 or raw.count("\n") >= 3:
        return True
    if any(token in raw for token in ("```", "`", "~/", "./", "../", "/", "\\")):
        return True
    if any(token in raw for token in ("{", "}", "[", "]", "<", ">")):
        return True
    if any(token in lower for token in (".md", ".py", ".json", ".yaml", ".yml", ".toml")):
        return True
    if raw.count(":") >= 2 or "- " in raw or "* " in raw:
        return True
    return False


def is_short_follow_up(text: str | None) -> bool:
    raw = (text or "").strip()
    return bool(raw) and len(raw) <= 120 and not has_structured_payload(raw)


def looks_like_confirmation(text: str | None) -> bool:
    """Detect short follow-ups that are likely approval/confirmation replies."""
    normalized = normalize_text(text).lower()
    return normalized in {
        "yes",
        "yes please",
        "yes do it",
        "yes delete them",
        "yes remove them",
        "ok",
        "ok do it",
        "okay",
        "okay do it",
        "please do",
        "go ahead",
        "go ahead and do it",
        "do it",
        "delete them",
        "remove them",
        "proceed",
        "approved",
    }


def _keywords(text: str | None) -> set[str]:
    return {token for token in re.findall(r"\w+", (text or "").lower()) if len(token) >= 4}


@dataclass(slots=True)
class PendingWork:
    """Durable unresolved work owned by the coordinator rather than the model."""

    origin_request: str
    latest_request: str
    source_excerpt: str
    last_failure: str
    tools_used: list[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> PendingWork | None:
        payload = (metadata or {}).get("pending_work")
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                origin_request=str(payload.get("origin_request") or ""),
                latest_request=str(payload.get("latest_request") or ""),
                source_excerpt=str(payload.get("source_excerpt") or ""),
                last_failure=str(payload.get("last_failure") or ""),
                tools_used=[str(name) for name in payload.get("tools_used") or []],
                created_at=str(payload.get("created_at") or _utcnow_iso()),
                updated_at=str(payload.get("updated_at") or _utcnow_iso()),
            )
        except Exception:
            return None

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def find_action_source(messages: list[dict[str, Any]]) -> str:
    """Pick the strongest recent user request to anchor pending work."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        if has_structured_payload(content):
            return snippet(content, max_chars=700)
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            return snippet(msg["content"], max_chars=280)
    return ""


def build_pending_work_hint(pending: PendingWork, current_request: str) -> str:
    """Build a deterministic context reminder for unresolved work."""
    lines = [
        "You have unresolved pending work from this session.",
        f"Original request: {snippet(pending.origin_request, max_chars=240)}",
    ]
    if pending.source_excerpt:
        lines.append(f"Source content: {snippet(pending.source_excerpt, max_chars=320)}")
    if pending.last_failure:
        lines.append(f"Last failure: {snippet(pending.last_failure, max_chars=240)}")
    lines.append(f"Current user message: {snippet(current_request, max_chars=180)}")
    lines.append(
        "If the user is continuing that task, resume and complete it now. If the user clearly changed topics, follow the new request instead."
    )
    return "\n".join(lines)


def relates_to_pending_work(pending: PendingWork, text: str | None) -> bool:
    """Check whether a follow-up appears to refer to existing pending work."""
    current = _keywords(text)
    if not current:
        return False
    pending_tokens = (
        _keywords(pending.origin_request)
        | _keywords(pending.latest_request)
        | _keywords(pending.source_excerpt)
    )
    return bool(current & pending_tokens)


def should_resume_pending_work(pending: PendingWork, text: str | None) -> bool:
    """Decide whether coordinator-owned pending work should be re-injected."""
    if not isinstance(text, str) or not text.strip():
        return False
    if "approval" in pending.last_failure.lower() and looks_like_confirmation(text):
        return True
    if has_structured_payload(text):
        return False
    if not relates_to_pending_work(pending, text):
        return False
    return len(normalize_text(text)) <= 220


def extract_skill_name(text: str | None) -> str | None:
    """Best-effort extraction of a skill name from provided skill text."""
    if not isinstance(text, str):
        return None
    match = re.search(r"(?im)^[>\-*\s`_]*name\s*:\s*['\"]?([a-z0-9][a-z0-9._-]*)['\"]?\s*$", text)
    if not match:
        return None
    return match.group(1).strip()


def looks_like_skill_definition(text: str | None) -> bool:
    """Detect common skill-definition payloads."""
    if not isinstance(text, str):
        return False
    if extract_skill_name(text) is None:
        return False
    return bool(re.search(r"(?im)^[>\-*\s`_]*description\s*:", text))


def build_skill_creation_hint(source_text: str | None) -> str | None:
    """Provide a deterministic coordinator hint for skill-creation payloads."""
    skill_name = extract_skill_name(source_text)
    if not skill_name or not looks_like_skill_definition(source_text):
        return None
    return (
        "The provided content is a skill definition. Treat this as a file-creation or file-update "
        f"task for `skills/{skill_name}/SKILL.md`. Read the existing file first if it already exists; "
        "otherwise create the directory and write the skill content. Do not stop after acknowledging "
        "the request."
    )
