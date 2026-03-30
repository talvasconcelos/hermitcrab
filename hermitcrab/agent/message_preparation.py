"""Deterministic message-shaping helpers for the main agent."""

from __future__ import annotations

import re
from typing import Any


def is_intent_only_response(text: str | None) -> bool:
    """Detect non-final assistant replies that only narrate the next step."""
    if not text:
        return False
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    return bool(
        re.match(
            r"^(let me|i(?:'ll| will)|first[, ]+let me|now[, ]+let me|next[, ]+i(?:'ll| will)|i am going to)\b",
            normalized,
        )
    )


def is_empty_response(text: str | None) -> bool:
    """Treat blank or whitespace-only replies as missing output."""
    return text is None or not text.strip()


def is_resume_query(text: str | None) -> bool:
    """Detect user requests that ask to resume or recap prior work."""
    if not text:
        return False
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False

    markers = (
        "where did we leave off",
        "where did we left off",
        "pick up where we left off",
        "pick up where we left it",
        "what were we doing last time",
        "what were we working on",
        "remind me where we left off",
        "catch me up on where we left off",
        "what happened last time",
        "recap where we left off",
    )
    return any(marker in normalized for marker in markers)


def should_hint_subagent_delegation(
    user_message: str,
    *,
    has_spawn_tool: bool,
    subagent_model: str | None,
) -> bool:
    """Return True when the request looks like substantial implementation grunt work."""
    if not has_spawn_tool or not subagent_model:
        return False

    normalized = " ".join(user_message.lower().split())
    if not normalized:
        return False

    action_markers = (
        "build",
        "create",
        "implement",
        "refactor",
        "update",
        "rewrite",
        "start with",
        "work on",
    )
    scope_markers = (
        "project",
        "folder",
        "html",
        "css",
        "javascript",
        "app.js",
        "index.html",
        "web-chat",
        "page",
        "ui",
        "frontend",
        "files",
    )
    return any(marker in normalized for marker in action_markers) and any(
        marker in normalized for marker in scope_markers
    )


def build_delegation_hint(subagent_model: str) -> str:
    """Build a deterministic reminder to delegate substantial implementation work."""
    return (
        "This request looks like substantial implementation grunt work. "
        "Prefer using spawn() to delegate the execution to a subagent and keep the main "
        f"agent responsive. Use the configured subagent model `{subagent_model}` or an "
        "appropriate alias when delegating, unless there is a clear reason to stay in the "
        "main agent."
    )


def clean_snippet(value: Any, *, max_chars: int = 160) -> str:
    """Normalize text snippets for prompts and logs."""
    if value is None:
        return ""
    text = str(value).strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def is_subagent_completion_prompt(content: str) -> bool:
    """Return True when a stored user message is a synthetic subagent prompt."""
    normalized = clean_snippet(content, max_chars=4000)
    return (
        normalized.startswith("[Subagent '")
        and "Write a user-facing completion update." in normalized
    )


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
    normalized = clean_snippet(content, max_chars=240).lower()
    transition_markers = (
        "let me just",
        "let me try",
        "let me check",
        "let me use",
        "let me spawn",
        "my apologies",
        "right you are",
        "i am a helpful assistant",
        "i am here to assist you",
        "i am a knowledgeable assistant",
        "make your tasks easier and more efficient",
        "there appears to be an issue with the subagent system",
        "the subagent keeps completing without",
        "the subagent completed but didn't return useful output",
        "i don't have a",
    )
    if any(marker in normalized for marker in transition_markers):
        return True
    return bool(
        tool_calls
        and normalized.startswith(("right", "ok", "okay", "sure", "let me", "my apologies"))
    )


def is_low_signal_journal_body(body: str) -> bool:
    """Reject journal synthesis that parrots scaffolding or synthetic prompt text."""
    normalized = clean_snippet(body, max_chars=600).lower()
    banned_markers = (
        "[subagent '",
        "write a user-facing completion update",
        "task completed but no final response was generated",
        "the subagent completed but didn't return useful output",
        "the subagent keeps completing without",
        "i am a helpful assistant",
        "i am a knowledgeable assistant",
        "i am here to assist you",
    )
    return any(marker in normalized for marker in banned_markers)
