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
    header_match = re.match(r"^\[Subagent '(.+)' (completed successfully|failed)\]$", header)
    if header_match:
        label = header_match.group(1)
        status = header_match.group(2)
    else:
        status = "completed successfully"

    task = ""
    result = ""
    for idx, line in enumerate(lines):
        if line.startswith("Task:"):
            task = line[5:].strip()
        elif line == "Result:":
            result = "\n".join(lines[idx + 1 :]).strip()
            break

    if status == "failed":
        blocker = _clean_result_line(result)
        task_snippet = f" for '{task}'" if task else ""
        if blocker:
            return f"Background work for {label}{task_snippet} failed. " f"Main blocker: {blocker}"
        return f"Background work for {label}{task_snippet} failed."

    if result:
        summary = _clean_result_line(result)
        if task:
            return (
                f"{label} finished in the background. I reviewed the result for '{task}'. "
                f"{summary}"
            )
        return f"{label} finished in the background. {summary}"

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


def fallback_system_task_summary(content: str) -> str:
    """Create a deterministic fallback summary for background task results."""
    return summarize_subagent_completion(content)


def is_low_value_system_reply(content: str | None) -> bool:
    """Detect inner-loop failure text that should not be surfaced."""
    normalized = (content or "").strip().lower()
    if not normalized:
        return True

    low_value_markers = (
        "i detected repeated tool calls without progress",
        "please refine the request or provide more constraints",
        "i've completed processing but have no response to give",
        "i reached the maximum number of tool call iterations",
        "i completed the tool work, but the model stopped before producing a usable final answer",
    )
    return any(marker in normalized for marker in low_value_markers)
