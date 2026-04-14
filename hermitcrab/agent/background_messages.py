"""Deterministic helpers for user-facing background-task messaging."""

from __future__ import annotations

import re


def summarize_subagent_completion(content: str) -> str:
    """Create a deterministic user-facing update from a subagent completion prompt."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return "Background task finished."

    header = lines[0]
    label = "Background task"
    header_match = re.match(
        r"^\[Subagent '(.+)' (completed successfully|completed partially|failed)\]$",
        header,
    )
    if header_match:
        label = header_match.group(1)
        status = header_match.group(2)
    else:
        status = "completed successfully"

    task = ""
    result = ""
    files = ""
    escalation_action = ""
    escalation_target = ""
    escalation_reason = ""
    for idx, line in enumerate(lines):
        if line.startswith("Task:"):
            task = line[5:].strip()
        elif line.startswith("Files:"):
            files = line[6:].strip()
        elif line.startswith("Escalation action:"):
            escalation_action = line[18:].strip()
        elif line.startswith("Escalation target:"):
            escalation_target = line[18:].strip()
        elif line.startswith("Escalation reason:"):
            escalation_reason = line[18:].strip()
        elif line == "Result:":
            result = "\n".join(lines[idx + 1 :]).strip()
            break

    if status == "failed":
        blocker = _clean_result_line(result)
        task_snippet = f" for '{task}'" if task else ""
        if blocker:
            summary = f"Background work for {label}{task_snippet} failed. Main blocker: {blocker}"
        else:
            summary = f"Background work for {label}{task_snippet} failed."
        if escalation_action and escalation_target:
            summary += (
                " Suggested next step: coordinator may "
                f"{_render_escalation(escalation_action, escalation_target, escalation_reason)}"
            )
        return summary

    if status == "completed partially":
        detail = _clean_result_line(result) or files
        task_snippet = f" for '{task}'" if task else ""
        if detail:
            summary = f"{label} finished partially in the background{task_snippet}. {detail}"
        else:
            summary = f"{label} finished partially in the background{task_snippet}."
        if escalation_action and escalation_target:
            summary += (
                " Suggested next step: coordinator may "
                f"{_render_escalation(escalation_action, escalation_target, escalation_reason)}"
            )
        return summary

    if result:
        summary = _clean_result_line(result)
        if task:
            return f"{label} finished in the background for '{task}'. {summary}"
        return f"{label} finished in the background. {summary}"

    if files:
        if task:
            return f"{label} finished in the background for '{task}'. Main files: {files}"
        return f"{label} finished in the background. Main files: {files}"

    if task:
        return f"{label} finished in the background. The task '{task}' has completed."

    return f"{label} finished in the background."


def _clean_result_line(result: str) -> str:
    """Normalize a result snippet for concise user-facing updates."""
    first_line = result.splitlines()[0].strip() if result else ""
    first_line = re.sub(r"^Error:\s*", "", first_line, flags=re.IGNORECASE)
    if not first_line:
        return ""
    if len(first_line) > 280:
        return first_line[:277].rstrip() + "..."
    return first_line


def _render_escalation(action: str, target: str, reason: str) -> str:
    if action == "retry_with_profile":
        text = f"retry with `{target}` profile."
    elif action == "continue_read_only":
        text = f"continue with read-only result from `{target}`."
    else:
        text = "take over in main agent."
    if reason:
        return f"{text} Reason: {reason}"
    return text


def fallback_system_task_summary(content: str) -> str:
    """Create a deterministic fallback summary for background task results."""
    return summarize_subagent_completion(content)


def is_grounded_system_reply(source_content: str, reply_content: str | None) -> bool:
    """Return True when a generated system reply is grounded in the source update."""
    reply = (reply_content or "").strip()
    if not reply:
        return False

    source_tokens = _grounding_tokens(source_content)
    reply_tokens = _grounding_tokens(reply)
    if not source_tokens or not reply_tokens:
        return False

    overlap = source_tokens & reply_tokens
    return len(overlap) >= 2


def _grounding_tokens(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return {token for token in normalized.split() if len(token) >= 4}
