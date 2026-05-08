"""Focused regressions for interactive turn execution."""

from __future__ import annotations

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
