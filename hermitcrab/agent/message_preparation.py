"""Deterministic message-shaping helpers for the main agent."""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_ASSISTANT_REPLIES = {
    '""',
    "''",
    "thinking",
    "thinking.",
    "thinking..",
    "thinking...",
    "processing",
    "processing.",
    "processing..",
    "processing...",
    "working on it",
    "working on it.",
    "working on it..",
    "working on it...",
    "still working on it",
    "still working on it.",
    "still working on it..",
    "still working on it...",
    "one moment",
    "one moment.",
    "one moment..",
    "one moment...",
    "just a moment",
    "just a moment.",
    "just a moment..",
    "just a moment...",
    "please wait",
    "please wait.",
    "please wait..",
    "please wait...",
}


def is_empty_response(text: str | None) -> bool:
    """Treat blank or whitespace-only replies as missing output."""
    return text is None or not text.strip()


def clean_snippet(value: Any, *, max_chars: int = 160) -> str:
    """Normalize text snippets for prompts and logs."""
    if value is None:
        return ""
    text = str(value).strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def is_placeholder_assistant_reply(text: str | None) -> bool:
    """Reject short status-only assistant text that should not reach the user as a final answer."""
    if is_empty_response(text):
        return False
    normalized = clean_snippet(text, max_chars=120).lower()
    return normalized in _PLACEHOLDER_ASSISTANT_REPLIES


def is_subagent_completion_prompt(content: str) -> bool:
    """Return True when a stored user message is a synthetic subagent prompt."""
    normalized = clean_snippet(content, max_chars=4000)
    return (
        normalized.startswith("[Subagent '")
        and "Write a user-facing completion update." in normalized
    )


def parse_subagent_completion_prompt(content: str) -> dict[str, str] | None:
    """Extract structured fields from a synthetic subagent completion prompt."""
    if not is_subagent_completion_prompt(content):
        return None

    header_match = re.search(
        r"^\[Subagent '(.+)' (completed successfully|completed partially|failed)\]\s*$",
        content,
        flags=re.MULTILINE,
    )
    if not header_match:
        return None

    def _field(label: str) -> str:
        match = re.search(rf"(?m)^{re.escape(label)}:\s*(.+)$", content)
        return match.group(1).strip() if match else ""

    result_match = re.search(
        r"(?ms)^Result:\s*\n(.*?)\n\s*Write a user-facing completion update\.\s*$",
        content,
    )
    result = result_match.group(1).strip() if result_match else ""

    return {
        "label": header_match.group(1).strip(),
        "status": header_match.group(2).strip(),
        "task": _field("Task"),
        "profile": _field("Profile"),
        "exit_reason": _field("Exit reason"),
        "tools_used": _field("Tools used"),
        "files": _field("Files"),
        "escalation_action": _field("Escalation action"),
        "escalation_target": _field("Escalation target"),
        "escalation_reason": _field("Escalation reason"),
        "result": result,
    }


def extract_subagent_task(content: str) -> str:
    """Extract delegated task text from a synthetic subagent completion prompt."""
    match = re.search(r"\nTask:\s*(.*?)\n\nResult:\n", content, flags=re.DOTALL)
    if not match:
        return ""
    return clean_snippet(match.group(1), max_chars=180)


def is_transition_assistant_message(content: str, tool_calls: list[dict[str, Any]]) -> bool:
    """Detect low-signal assistant scaffolding around tool usage."""
    if not content:
        return False
    normalized = clean_snippet(content, max_chars=240)
    if tool_calls:
        return True
    stripped = normalized.lstrip()
    if stripped.startswith("[Subagent '") and "Task:" in normalized and "Result:" in normalized:
        return True
    return False


def is_low_signal_journal_body(body: str) -> bool:
    """Reject journal synthesis that parrots scaffolding or synthetic prompt text."""
    normalized = clean_snippet(body, max_chars=600)
    stripped = normalized.lstrip()
    return stripped.startswith("[Subagent '") and "Task:" in normalized and "Result:" in normalized
