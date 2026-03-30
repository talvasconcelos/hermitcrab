"""Deterministic helpers for resume-style session recall."""

from __future__ import annotations

from typing import Any

from hermitcrab.agent.background_messages import is_low_value_system_reply
from hermitcrab.agent.message_preparation import clean_snippet, is_transition_assistant_message


def build_resume_reply(messages: list[dict[str, Any]]) -> str | None:
    """Build a concise recap of recent work from saved session messages."""
    visible = [msg for msg in messages if msg.get("role") != "system"]
    if not visible:
        return None

    last_final_idx = _find_last_final_assistant_index(visible)
    last_request = _find_last_user_before(visible, len(visible))
    completed_request = (
        _find_last_user_before(visible, last_final_idx) if last_final_idx >= 0 else None
    )
    completed_reply = visible[last_final_idx] if last_final_idx >= 0 else None
    trailing = visible[last_final_idx + 1 :] if last_final_idx >= 0 else visible

    bullets: list[str] = []
    if completed_request and completed_reply:
        bullets.append(f"Last completed request: {clean_snippet(completed_request.get('content'))}")
        bullets.append(f"Last answer: {clean_snippet(completed_reply.get('content'))}")
    elif last_request:
        bullets.append(f"Most recent request: {clean_snippet(last_request.get('content'))}")

    open_loop = _describe_open_loop(trailing)
    if open_loop:
        bullets.append(open_loop)

    if not bullets:
        recent = _last_visible_message(visible)
        if recent and recent.get("content"):
            bullets.append(f"Most recent saved message: {clean_snippet(recent.get('content'))}")

    if not bullets:
        return None

    return "Here's where we left off:\n- " + "\n- ".join(bullets)


def _find_last_final_assistant_index(messages: list[dict[str, Any]]) -> int:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if msg.get("tool_calls"):
            continue
        if is_low_value_system_reply(content):
            continue
        if is_transition_assistant_message(content, []):
            continue
        return idx
    return -1


def _find_last_user_before(messages: list[dict[str, Any]], end_idx: int) -> dict[str, Any] | None:
    for idx in range(min(end_idx - 1, len(messages) - 1), -1, -1):
        msg = messages[idx]
        if (
            msg.get("role") == "user"
            and isinstance(msg.get("content"), str)
            and msg.get("content").strip()
        ):
            return msg
    return None


def _last_visible_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for msg in reversed(messages):
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return msg
    return None


def _describe_open_loop(messages: list[dict[str, Any]]) -> str | None:
    if not messages:
        return None

    last_user = _find_last_user_before(messages, len(messages))
    tool_names: list[str] = []
    tool_results = 0
    assistant_started = False

    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            assistant_started = True
            for tc in msg.get("tool_calls") or []:
                function = tc.get("function") if isinstance(tc, dict) else None
                name = function.get("name") if isinstance(function, dict) else None
                if isinstance(name, str) and name and name not in tool_names:
                    tool_names.append(name)
        elif role == "tool":
            tool_results += 1
            name = msg.get("name")
            if isinstance(name, str) and name and name not in tool_names:
                tool_names.append(name)

    if assistant_started or tool_results:
        request = clean_snippet(last_user.get("content")) if last_user else "the latest request"
        if tool_names:
            names = ", ".join(f"`{name}`" for name in tool_names[:3])
            return (
                f"Open loop: I started working on {request} and used {names}, but no final answer was "
                "saved for you yet."
            )
        return f"Open loop: I started working on {request}, but no final answer was saved yet."

    last = _last_visible_message(messages)
    if last and last.get("role") == "user":
        return f"Open loop: your latest saved message was {clean_snippet(last.get('content'))}."

    return None
