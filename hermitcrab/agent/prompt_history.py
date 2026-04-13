"""Deterministic prompt-history shaping for token-efficient interactive turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermitcrab.agent.message_preparation import clean_snippet
from hermitcrab.utils.helpers import estimate_message_tokens


@dataclass(slots=True)
class PromptHistoryConfig:
    """Bounds for shaping raw session history into prompt-safe context."""

    preserve_recent_turns: int = 3
    min_recent_turns: int = 1
    max_summary_turns: int = 12
    max_user_chars: int = 220
    max_assistant_chars: int = 260
    max_tool_result_chars: int = 160
    max_summary_chars: int = 1400
    target_history_tokens: int = 1200


def build_prompt_history(
    history: list[dict[str, Any]],
    *,
    config: PromptHistoryConfig | None = None,
) -> list[dict[str, str]]:
    """Shape raw history into a compact user-facing transcript for the next prompt."""
    cfg = config or PromptHistoryConfig()
    turns = _split_turns(history)
    if not turns:
        return _fallback_visible_messages(history, cfg)

    preserve_count = min(len(turns), max(cfg.min_recent_turns, cfg.preserve_recent_turns))
    shaped = _shape_history_from_turns(turns, cfg, preserve_count=preserve_count)
    while estimate_message_tokens(shaped) > cfg.target_history_tokens and preserve_count > cfg.min_recent_turns:
        preserve_count -= 1
        shaped = _shape_history_from_turns(turns, cfg, preserve_count=preserve_count)

    if estimate_message_tokens(shaped) > cfg.target_history_tokens:
        shaped = _shape_history_from_turns(
            turns,
            _compacted_config(cfg),
            preserve_count=max(cfg.min_recent_turns, min(preserve_count, len(turns))),
        )

    if estimate_message_tokens(shaped) > cfg.target_history_tokens:
        shaped = _enforce_history_budget(shaped, cfg.target_history_tokens)

    return shaped or _fallback_visible_messages(history, cfg)


def _shape_history_from_turns(
    turns: list[list[dict[str, Any]]],
    cfg: PromptHistoryConfig,
    *,
    preserve_count: int,
) -> list[dict[str, str]]:
    preserved_turns = turns[-preserve_count:]
    older_turns = turns[: max(0, len(turns) - preserve_count)]

    shaped: list[dict[str, str]] = []
    if older_turns:
        summary = _summarize_older_turns(
            older_turns[-cfg.max_summary_turns :],
            cfg,
            compacted=len(older_turns) > cfg.max_summary_turns or preserve_count < cfg.preserve_recent_turns,
        )
        if summary:
            shaped.append({"role": "system", "content": summary})

    for turn in preserved_turns:
        shaped.extend(_shape_recent_turn(turn, cfg))

    return shaped


def _split_turns(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for message in history:
        role = message.get("role")
        if role == "user" and current:
            turns.append(current)
            current = [message]
        else:
            current.append(message)

    if current:
        turns.append(current)
    return turns


def _shape_recent_turn(turn: list[dict[str, Any]], cfg: PromptHistoryConfig) -> list[dict[str, str]]:
    shaped: list[dict[str, str]] = []
    tool_summary = _tool_activity_summary(turn, cfg)

    for message in turn:
        role = str(message.get("role") or "")
        content = message.get("content")
        if role == "user" and isinstance(content, str) and content.strip():
            shaped.append({"role": "user", "content": clean_snippet(content, max_chars=cfg.max_user_chars)})
            continue
        if role == "assistant":
            if message.get("tool_calls"):
                continue
            if isinstance(content, str) and content.strip():
                shaped.append(
                    {
                        "role": "assistant",
                        "content": clean_snippet(content, max_chars=cfg.max_assistant_chars),
                    }
                )

    if tool_summary and not any(item["role"] == "assistant" for item in shaped[-1:]):
        shaped.append({"role": "system", "content": tool_summary})
    elif tool_summary:
        shaped.append({"role": "system", "content": tool_summary})

    return shaped


def _summarize_older_turns(
    turns: list[list[dict[str, Any]]],
    cfg: PromptHistoryConfig,
    *,
    compacted: bool = False,
) -> str:
    heading = "Earlier conversation summary (compacted live session):" if compacted else "Earlier conversation summary:"
    lines = [heading]

    for turn in turns:
        user_request = ""
        assistant_outcome = ""
        for message in turn:
            role = str(message.get("role") or "")
            content = message.get("content")
            if role == "user" and isinstance(content, str) and content.strip() and not user_request:
                user_request = clean_snippet(content, max_chars=cfg.max_user_chars)
            elif (
                role == "assistant"
                and not message.get("tool_calls")
                and isinstance(content, str)
                and content.strip()
            ):
                assistant_outcome = clean_snippet(content, max_chars=cfg.max_assistant_chars)

        tool_summary = _tool_activity_summary(turn, cfg)
        turn_line = f"- User asked: {user_request or 'unknown request'}"
        if assistant_outcome:
            turn_line += f" | Outcome: {assistant_outcome}"
        elif tool_summary:
            turn_line += f" | {tool_summary.removeprefix('Tool activity: ')}"
        lines.append(turn_line)

    summary = "\n".join(lines)
    return clean_snippet(summary, max_chars=cfg.max_summary_chars)


def _tool_activity_summary(turn: list[dict[str, Any]], cfg: PromptHistoryConfig) -> str:
    tool_names: list[str] = []
    result_snippets: list[str] = []

    for message in turn:
        if message.get("role") == "assistant" and isinstance(message.get("tool_calls"), list):
            for tool_call in message["tool_calls"]:
                if isinstance(tool_call, dict):
                    function = tool_call.get("function")
                    if isinstance(function, dict) and function.get("name"):
                        tool_names.append(str(function["name"]))
        elif message.get("role") == "tool":
            name = message.get("name")
            if name:
                tool_names.append(str(name))
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                snippet = clean_snippet(_strip_tool_noise(content), max_chars=cfg.max_tool_result_chars)
                if snippet:
                    result_snippets.append(snippet)

    if not tool_names and not result_snippets:
        return ""

    ordered_tools = list(dict.fromkeys(tool_names))
    parts: list[str] = []
    if ordered_tools:
        parts.append(f"tools used: {', '.join(ordered_tools[:4])}")
    if result_snippets:
        parts.append(f"grounded result: {result_snippets[0]}")
    return "Tool activity: " + " | ".join(parts)


def _strip_tool_noise(content: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    filtered = [
        line
        for line in lines
        if not line.startswith("[SECURITY:")
        and line not in {"Results for:", "Reminders:"}
        and not line.startswith("Path:")
    ]
    return filtered[0] if filtered else ""


def _fallback_visible_messages(
    history: list[dict[str, Any]],
    cfg: PromptHistoryConfig,
) -> list[dict[str, str]]:
    visible: list[dict[str, str]] = []
    for message in history:
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        if role == "assistant" and message.get("tool_calls"):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        max_chars = cfg.max_user_chars if role == "user" else cfg.max_assistant_chars
        visible.append({"role": role, "content": clean_snippet(content, max_chars=max_chars)})
    return visible[-(cfg.preserve_recent_turns * 2) :]


def _compacted_config(cfg: PromptHistoryConfig) -> PromptHistoryConfig:
    """Return a stricter config for long live sessions that exceed the normal history budget."""
    return PromptHistoryConfig(
        preserve_recent_turns=max(cfg.min_recent_turns, min(cfg.preserve_recent_turns, 2)),
        min_recent_turns=cfg.min_recent_turns,
        max_summary_turns=max(6, min(cfg.max_summary_turns, 10)),
        max_user_chars=max(120, min(cfg.max_user_chars, 160)),
        max_assistant_chars=max(140, min(cfg.max_assistant_chars, 180)),
        max_tool_result_chars=max(80, min(cfg.max_tool_result_chars, 100)),
        max_summary_chars=max(500, min(cfg.max_summary_chars, 900)),
        target_history_tokens=cfg.target_history_tokens,
    )


def _enforce_history_budget(
    shaped: list[dict[str, str]],
    target_history_tokens: int,
) -> list[dict[str, str]]:
    """Clamp already-shaped history to a strict token budget."""
    if estimate_message_tokens(shaped) <= target_history_tokens:
        return shaped

    tightened = shaped
    for system_chars, user_chars, assistant_chars in (
        (280, 120, 140),
        (180, 90, 110),
        (120, 70, 90),
    ):
        compacted: list[dict[str, str]] = []
        for message in tightened:
            role = message.get("role") or "system"
            content = message.get("content") or ""
            if role == "system":
                max_chars = system_chars
            elif role == "user":
                max_chars = user_chars
            else:
                max_chars = assistant_chars
            compacted.append({"role": role, "content": clean_snippet(content, max_chars=max_chars)})
        tightened = compacted
        if estimate_message_tokens(tightened) <= target_history_tokens:
            return tightened

    if tightened and tightened[0].get("role") == "system":
        without_summary = tightened[1:]
        if estimate_message_tokens(without_summary) <= target_history_tokens:
            return without_summary
        tightened = without_summary

    return tightened
