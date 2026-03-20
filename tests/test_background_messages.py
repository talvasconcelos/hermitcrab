"""Tests for deterministic background-task message fallbacks."""

from hermitcrab.agent.background_messages import (
    fallback_system_task_summary,
    is_low_value_system_reply,
)


def test_fallback_system_task_summary_uses_task_and_result() -> None:
    summary = fallback_system_task_summary(
        "[Subagent 'nostr' completed successfully]\n\n"
        "Task: Research Nostr integration\n\n"
        "Result:\nDrafted relay notes in docs/nostr.md."
    )

    assert "finished in the background" in summary
    assert "Research Nostr integration" in summary
    assert "docs/nostr.md" in summary


def test_is_low_value_system_reply_flags_loop_failure_text() -> None:
    assert is_low_value_system_reply(
        "I detected repeated tool calls without progress and stopped to avoid a loop."
    )
    assert not is_low_value_system_reply("Background work finished and the file was updated.")
