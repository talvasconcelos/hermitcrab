"""Focused regressions for interactive turn execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermitcrab.agent.loop import AgentLoop
from hermitcrab.agent.tools.policy import get_default_tool_metadata
from hermitcrab.agent.turn_runner import TurnOutcome, TurnRunner, TurnRunnerConfig
from hermitcrab.providers.base import LLMResponse, ProviderError, ToolCallRequest


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

    def get_metadata(self, name):
        return get_default_tool_metadata(name)

    def has(self, name):
        return True

    async def execute(self, name, arguments):
        return self.results[name]


def _runner(responses, tool_results=None, *, memory_authority="NO"):
    async def chat(**kwargs):
        if kwargs.get("tools") == []:
            return LLMResponse(content=memory_authority)
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


async def test_task_update_fallback_keeps_unverified_work_pending() -> None:
    from hermitcrab.agent.pending_work import PendingWork
    from hermitcrab.bus.events import InboundMessage
    from hermitcrab.session.manager import Session

    runner = _runner(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest("one", "update_task_status", {
                    "task_id": "1234", "status": "done",
                })],
            ),
            LLMResponse(content=None),
            LLMResponse(content=None),
        ],
        {"update_task_status": "Task updated: School trip prep (id=1234, status=done)"},
    )
    request = "mark the school trip task done please"
    result = await runner.run([{"role": "user", "content": request}])

    assert result.outcome == TurnOutcome.TOOL_FALLBACK
    assert "status=done" in result.final_content
    assert "before confirming the full request was complete" in result.final_content
    session = Session(key="cli:direct")
    AgentLoop._update_pending_work(
        None, session, InboundMessage(channel="cli", sender_id="user", chat_id="direct",
                                     content=request), result,
    )
    assert PendingWork.from_metadata(session.metadata) is not None


async def test_partial_batch_fallback_does_not_complete_the_request(tmp_path) -> None:
    from hermitcrab.agent.memory import MemoryStore
    from hermitcrab.agent.tools.memory import ReadMemoryTool, UpdateTaskStatusTool
    from hermitcrab.agent.tools.registry import ToolRegistry

    store = MemoryStore(tmp_path)
    first = store.write_task("First", "First task.", "tal")
    second = store.write_task("Second", "Second task.", "tal")
    registry = ToolRegistry()
    registry.register(ReadMemoryTool(store))
    registry.register(UpdateTaskStatusTool(store))
    runner = _runner([
        LLMResponse(content=None, tool_calls=[
            ToolCallRequest("read", "read_memory", {"category": "tasks"}),
        ]),
        LLMResponse(content=None, tool_calls=[
            ToolCallRequest("update", "update_task_status", {"task_id": first.id, "status": "done"}),
        ]),
        LLMResponse(content=None),
        LLMResponse(content=None),
    ])
    runner.tools = registry

    result = await runner.run([{"role": "user", "content": "Mark all my tasks done please"}])

    assert store.read_memory("tasks", id=first.id)[0].metadata["status"] == "done"
    assert store.read_memory("tasks", id=second.id)[0].metadata["status"] == "open"
    assert result.outcome == TurnOutcome.TOOL_FALLBACK
    assert "before confirming the full request was complete" in result.final_content
    assert f"id={first.id}, status=done" in result.final_content


def test_fallback_reports_successes_and_failures_together() -> None:
    text = _runner([])._build_tool_result_fallback(tool_results=[
        ("update_task_status", "Task updated: First (id=first, status=done)"),
        ("update_task_status", "Error updating task: unknown ID"),
    ])

    assert "id=first, status=done" in text
    assert "Error updating task: unknown ID" in text
    assert not text.startswith("Done")


@pytest.mark.parametrize("user_request", ["mark them done please", "can you mark them done?", "yes"])
@pytest.mark.parametrize("memory_authority", ["YES", ""])
async def test_read_only_results_cannot_support_completion_claims(user_request, memory_authority) -> None:
    runner = _runner([
        LLMResponse(content=None, tool_calls=[
            ToolCallRequest("read", "read_memory", {"category": "tasks"}),
        ]),
        LLMResponse(content="All open tasks marked done."),
        LLMResponse(content="All open tasks marked done."),
    ], {"read_memory": "[tasks] First (id=first)\nStatus: open | Assignee: tal"},
        memory_authority=memory_authority)

    result = await runner.run([{"role": "user", "content": user_request}])

    assert result.outcome == TurnOutcome.INCOMPLETE_ACTION
    assert result.tools_used == ["read_memory"]
    assert result.final_content != "All open tasks marked done."


async def test_read_only_completion_claim_can_be_repaired_with_a_write() -> None:
    runner = _runner([
        LLMResponse(content=None, tool_calls=[
            ToolCallRequest("read", "read_memory", {"category": "tasks"}),
        ]),
        LLMResponse(content="All open tasks marked done."),
        LLMResponse(content=None, tool_calls=[
            ToolCallRequest("write", "update_task_status", {"task_id": "first", "status": "done"}),
        ]),
        LLMResponse(content="The task is now marked done."),
    ], {
        "read_memory": "[tasks] First (id=first)\nStatus: open | Assignee: tal",
        "update_task_status": "Task updated: First (id=first, status=done)",
    }, memory_authority="YES")

    result = await runner.run([{"role": "user", "content": "mark them done please"}])

    assert result.outcome == TurnOutcome.COMPLETED
    assert result.tools_used == ["read_memory", "update_task_status"]


async def test_failed_write_does_not_support_a_completion_claim() -> None:
    runner = _runner([
        LLMResponse(content=None, tool_calls=[
            ToolCallRequest("write", "update_task_status", {"task_id": "missing", "status": "done"}),
        ]),
        LLMResponse(content="All open tasks marked done."),
        LLMResponse(content="All open tasks marked done."),
    ], {"update_task_status": "Error updating task: unknown ID"}, memory_authority="YES")

    result = await runner.run([{"role": "user", "content": "mark them done please"}])

    assert result.outcome == TurnOutcome.INCOMPLETE_ACTION


async def test_read_only_state_report_remains_valid() -> None:
    runner = _runner([
        LLMResponse(content=None, tool_calls=[
            ToolCallRequest("read", "read_memory", {"category": "tasks"}),
        ]),
        LLMResponse(content="You have no open tasks. The task was already done."),
    ], {"read_memory": "[tasks] First (id=first)\nStatus: done | Assignee: tal"})

    result = await runner.run([{"role": "user", "content": "what are my open tasks?"}])

    assert result.outcome == TurnOutcome.COMPLETED
    assert result.tools_used == ["read_memory"]


async def test_authority_classifier_allows_for_hidden_reasoning_tokens() -> None:
    captured = {}
    runner = _runner([])

    async def chat(**kwargs):
        captured.update(kwargs)
        return LLMResponse(content="NO", usage={"completion_tokens": 76})

    runner.chat_callable = chat
    requires_write = await runner._classify_memory_authority_requirement(
        current_request="what are my open tasks?", assistant_content="You have two open tasks.",
        model="test", job_class=None, require_write=True,
    )

    assert not requires_write
    assert captured["max_tokens"] >= 128


async def test_checklist_follow_up_uses_conversation_target(tmp_path) -> None:
    from hermitcrab.agent.lists import ListStore
    from hermitcrab.agent.prompt_history import build_prompt_history
    from hermitcrab.agent.tools.lists import SetListItemStatusTool, ShowListTool
    from hermitcrab.agent.tools.registry import ToolRegistry

    lists = ListStore(tmp_path)
    lists.add_items("weekend-prep", ["vacuum living room", "water plants"])
    registry = ToolRegistry()
    registry.register(SetListItemStatusTool(lists))
    history = build_prompt_history([
        {"role": "user", "content": "get my weekend list"},
        {"role": "tool", "name": "list_show",
         "content": await ShowListTool(lists).execute(list_name="weekend-prep")},
        {"role": "assistant", "content": "Your weekend list: vacuum living room, water plants."},
    ])
    runner = _runner([
        LLMResponse(content=None, tool_calls=[ToolCallRequest("one", "list_set_item_status", {
            "list_name": "weekend-prep", "items": ["vacuum living room"], "done": True,
        })]),
        LLMResponse(content="Marked vacuum living room done."),
    ])
    runner.tools = registry

    result = await runner.run([
        *history, {"role": "user", "content": "already vacuumed, mark it done please"},
    ])

    assert result.outcome == TurnOutcome.COMPLETED
    assert result.tools_used == ["list_set_item_status"]
    stored = lists.find_list("weekend-prep")
    assert stored.completed_count == 1
    assert stored.remaining_count == 1
    assert (await registry.execute("list_set_item_status", {
        "items": ["water plants"], "done": True,
    })).startswith("Error")
    assert lists.find_list("weekend-prep").remaining_count == 1


async def test_provider_error_is_reported_as_a_provider_failure() -> None:
    """A provider failure must never be dressed up as the model's own words."""

    async def failing_chat(**kwargs):
        raise ProviderError("invalid api key")

    runner = TurnRunner(
        context=_Context(),
        tools=_Tools({}),
        config=TurnRunnerConfig(4, 30, 3, 0.0, 100, None),
        chat_callable=failing_chat,
        stream_chat_callable=None,
        get_model_for_job=lambda job: "test",
        strip_think=lambda content: content,
        tool_hint=lambda calls: "working",
        is_empty_response=lambda content: not content or not content.strip(),
    )

    result = await runner.run([{"role": "user", "content": "hi"}])

    assert result.outcome is TurnOutcome.PROVIDER_ERROR
    assert result.final_content.startswith("Provider error: invalid api key")
    assert "not a weak model" in result.final_content
