"""Focused regressions for interactive turn execution."""

from __future__ import annotations

from types import SimpleNamespace

from hermitcrab.agent.loop import AgentLoop
from hermitcrab.agent.turn_runner import TurnRunner, TurnRunnerConfig
from hermitcrab.providers.base import LLMResponse, ToolCallRequest


class _Context:
    def add_assistant_message(self, messages, content, tool_calls=None, **kwargs):
        message = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return [*messages, message]

    def add_tool_result(self, messages, tool_call_id, name, result):
        return [
            *messages,
            {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": result},
        ]


class _Tools:
    def __init__(self, results):
        self.results = results

    def get_definitions(self):
        return [{"type": "function"}]

    def has(self, name):
        return True

    async def execute(self, name, arguments):
        return self.results[name]


def _runner(responses, tool_results=None):
    async def chat(**kwargs):
        return responses.pop(0)

    return TurnRunner(
        context=_Context(),
        tools=_Tools(tool_results or {}),
        config=TurnRunnerConfig(4, 30, 3, 0.0, 100, None),
        chat_callable=chat,
        stream_chat_callable=None,
        get_model_for_job=lambda job: "test",
        strip_think=lambda content: content,
        tool_hint=lambda calls: "working",
        is_empty_response=lambda content: not content or not content.strip(),
    )


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


def test_exec_approval_reason_distinguishes_destructive_from_shell_syntax() -> None:
    assert (
        TurnRunner._exec_approval_reason(
            "Error: Command blocked by safety guard (destructive command requires explicit approval)"
        )
        == "destructive"
    )
    assert (
        TurnRunner._exec_approval_reason(
            "Error: Command blocked by safety guard (shell syntax requires explicit approval)"
        )
        == "shell_syntax"
    )
    assert TurnRunner._exec_approval_reason("ok") is None
    assert TurnRunner._exec_approval_reason(None) is None


def test_build_exec_approval_request_messages_shell_syntax_accurately() -> None:
    shell_msg = TurnRunner._build_exec_approval_request(
        "exec",
        {"command": "which nak; nak --version 2>/dev/null"},
        reason="shell_syntax",
    )

    assert "shell syntax" in shell_msg
    assert "destructively" not in shell_msg
    assert "which nak; nak --version 2>/dev/null" in shell_msg

    destructive_msg = TurnRunner._build_exec_approval_request(
        "exec",
        {"command": "rm draft.md"},
        reason="destructive",
    )

    assert "deleting" in destructive_msg


async def test_run_records_tool_results_before_final_answer() -> None:
    runner = _runner(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest("one", "first", {"value": 1}),
                    ToolCallRequest("two", "second", {"value": 2}),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="Done."),
        ],
        {"first": "first result", "second": "second result"},
    )

    result = await runner.run([{"role": "user", "content": "Do the work"}])

    assert result.final_content == "Done."
    assert result.tools_used == ["first", "second"]
    assert [message["role"] for message in result.messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert [message["content"] for message in result.messages[2:4]] == [
        "first result",
        "second result",
    ]


async def test_run_uses_tool_fallback_after_repeated_empty_post_tool_response() -> None:
    runner = _runner(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest("one", "read_file", {"path": "note.md"})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content=None),
            LLMResponse(content=None),
        ],
        {"read_file": "important result"},
    )

    result = await runner.run([{"role": "user", "content": "Read the note"}])

    assert result.outcome.value == "tool_fallback"
    assert "important result" in result.final_content
