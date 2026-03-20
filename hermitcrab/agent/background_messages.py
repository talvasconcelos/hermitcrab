"""Deterministic helpers for user-facing background-task messaging."""

from __future__ import annotations


def fallback_system_task_summary(content: str) -> str:
    """Create a deterministic fallback summary for background task results."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return "Background task finished."

    label = "Background task"
    task = ""
    result = ""

    for idx, line in enumerate(lines):
        if line.startswith("[Subagent '") and "' " in line:
            label = line.strip("[]")
        elif line.startswith("Task:"):
            task = line[5:].strip()
        elif line == "Result:":
            result = "\n".join(lines[idx + 1 :]).strip()
            break

    if result:
        summary = result.splitlines()[0].strip()[:280]
        if task:
            return (
                f"{label} finished in the background. I reviewed the result for '{task}'. "
                f"{summary}"
            )
        return f"{label} finished in the background. {summary}"

    if task:
        return f"{label} finished in the background. The task '{task}' has completed."

    return f"{label} finished in the background."


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
    )
    return any(marker in normalized for marker in low_value_markers)
