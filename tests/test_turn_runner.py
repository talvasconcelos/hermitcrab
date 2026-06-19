"""Focused regressions for interactive turn execution."""

from __future__ import annotations

from types import SimpleNamespace

from hermitcrab.agent.loop import AgentLoop
from hermitcrab.agent.turn_runner import TurnRunner


def test_collapse_exact_tandem_duplicate_response() -> None:
    content = "One useful reply.\n\nOne useful reply."

    assert TurnRunner._collapse_exact_tandem_duplicate(content) == "One useful reply."


def test_collapse_exact_tandem_duplicate_keeps_normal_repetition() -> None:
    content = "First point.\n\nSecond point repeats a phrase. Second point repeats a phrase."

    assert TurnRunner._collapse_exact_tandem_duplicate(content) == content


def test_repeated_tool_transition_response_is_not_final_answer() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "Let me read the current website files and the training materials.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }
    ]

    assert TurnRunner._is_repeated_tool_transition_response(
        "Let me read the current website files.", messages
    )


def test_repeated_tool_transition_response_allows_distinct_final_answer() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "Checking the file first.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }
    ]

    assert not TurnRunner._is_repeated_tool_transition_response(
        "I checked the file and found the missing section.", messages
    )


def test_tool_hint_redacts_sensitive_first_args_but_keeps_safe_paths() -> None:
    hints = AgentLoop._tool_hint(
        [
            SimpleNamespace(name="read_file", arguments={"path": "/tmp/safe.txt"}),
            SimpleNamespace(name="exec", arguments={"command": "cat .env && echo secret"}),
            SimpleNamespace(name="web_fetch", arguments={"url": "https://example.com/private?token=abc"}),
            SimpleNamespace(name="write_file", arguments={"path": "/tmp/out.txt", "content": "super secret body"}),
        ]
    )

    assert 'read_file("/tmp/safe.txt")' in hints
    assert 'write_file("/tmp/out.txt")' in hints
    assert "cat .env" not in hints
    assert "private?token=abc" not in hints
    assert "exec(…redacted…" in hints
    assert "web_fetch(…redacted…" in hints


def test_progress_heartbeat_message_includes_elapsed_seconds() -> None:
    message = TurnRunner._progress_heartbeat_message(
        "Still working on `read_file`.",
        started_at=0.0,
    )
    assert message.startswith("Still working on `read_file`.")
    assert message.endswith("elapsed)")
