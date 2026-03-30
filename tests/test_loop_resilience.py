"""Resilience tests for AgentLoop retries, loop guards, and delegation hints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.distillation import AtomicCandidate, CandidateType
from hermitcrab.agent.loop import AgentLoop
from hermitcrab.agent.turn_runner import TurnRunner
from hermitcrab.bus.events import InboundMessage
from hermitcrab.providers.base import (
    LLMProvider,
    LLMResponse,
    ResponseDoneEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolCallRequest,
)


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.consume_inbound = AsyncMock()
    bus.publish_inbound = AsyncMock()
    bus.publish_outbound = AsyncMock()
    return bus


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.chat = AsyncMock()
    provider.chat_with_retry = provider.chat
    provider.get_default_model = MagicMock(return_value="test-model")
    return provider


@pytest.fixture
def agent_loop(mock_bus, mock_provider, tmp_path):
    return AgentLoop(
        bus=mock_bus,
        provider=mock_provider,
        workspace=tmp_path,
        llm_max_retries=1,
        llm_retry_base_delay_s=0.0,
        max_identical_tool_cycles=2,
    )


@pytest.mark.asyncio
async def test_run_agent_loop_breaks_repeated_tool_cycles(agent_loop, mock_provider):
    """Repeated identical tool call batches should trigger loop guard."""
    tool_call = ToolCallRequest(id="1", name="list_dir", arguments={"path": "."})
    mock_provider.chat.return_value = LLMResponse(content="", tool_calls=[tool_call])

    final_content, _, _ = await agent_loop._run_agent_loop([{"role": "user", "content": "loop"}])

    assert final_content is not None
    assert "repeated tool calls" in final_content.lower()


@pytest.mark.asyncio
async def test_run_agent_loop_recovers_inline_json_tool_call(agent_loop, mock_provider):
    """Raw JSON tool-call text should be recovered and executed."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=(
                "Let me first check memory. "
                '{"name":"read_memory","arguments":{"category":"facts"}}'
            )
        ),
        LLMResponse(content="done"),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert final_content == "done"
    assert tools_used == ["read_memory"]


@pytest.mark.asyncio
async def test_run_agent_loop_recovers_inline_xml_tool_call(agent_loop, mock_provider):
    """XML-like inline tool calls should be recovered and executed."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=(
                "<minimax:tool_call>\n"
                '<invoke name="read_memory">\n'
                '<parameter name="category">facts</parameter>\n'
                "</invoke>\n"
                "</minimax:tool_call>"
            )
        ),
        LLMResponse(content="done"),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert final_content == "done"
    assert tools_used == ["read_memory"]


@pytest.mark.asyncio
async def test_run_agent_loop_normalizes_string_tool_arguments(agent_loop, mock_provider):
    """Provider tool calls with stringified JSON arguments should still execute."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="1", name="read_memory", arguments='{"category":"facts"}')
            ],
        ),
        LLMResponse(content="done"),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert final_content == "done"
    assert tools_used == ["read_memory"]


@pytest.mark.asyncio
async def test_run_agent_loop_executes_streamed_tool_calls_without_chat_fallback(
    mock_bus, tmp_path
):
    """Typed provider tool events should drive the loop without raw-text recovery."""
    batches = [
        [
            ToolCallEvent(
                tool_call=ToolCallRequest(
                    id="1", name="read_memory", arguments={"category": "facts"}
                )
            ),
            ResponseDoneEvent(finish_reason="tool_calls"),
        ],
        [TextDeltaEvent(delta="done"), ResponseDoneEvent(finish_reason="stop")],
    ]

    class StreamingProvider(LLMProvider):
        async def chat(self, *args, **kwargs):
            raise AssertionError("chat fallback should not run")

        async def stream_chat(self, *args, **kwargs):
            for event in batches.pop(0):
                yield event

        def get_default_model(self) -> str:
            return "test-model"

    agent_loop = AgentLoop(
        bus=mock_bus,
        provider=StreamingProvider(),
        workspace=tmp_path,
        llm_max_retries=1,
        llm_retry_base_delay_s=0.0,
        max_identical_tool_cycles=2,
    )

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert final_content == "done"
    assert tools_used == ["read_memory"]


@pytest.mark.asyncio
async def test_run_agent_loop_falls_back_to_chat_when_streaming_fails(mock_bus, tmp_path):
    """Streaming failures should degrade to the existing non-streaming path."""

    class FallbackProvider(LLMProvider):
        def __init__(self):
            super().__init__()
            self.chat_mock = AsyncMock(return_value=LLMResponse(content="done"))

        async def chat(self, *args, **kwargs):
            return await self.chat_mock(*args, **kwargs)

        async def stream_chat(self, *args, **kwargs):
            raise RuntimeError("stream broke")
            yield  # pragma: no cover

        def get_default_model(self) -> str:
            return "test-model"

    provider = FallbackProvider()
    agent_loop = AgentLoop(
        bus=mock_bus,
        provider=provider,
        workspace=tmp_path,
        llm_max_retries=1,
        llm_retry_base_delay_s=0.0,
        max_identical_tool_cycles=2,
    )

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert final_content == "done"
    assert tools_used == []
    provider.chat_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_agent_loop_returns_immediately_after_spawn(agent_loop, mock_provider):
    """Spawn should end the interactive turn so subagent results can arrive later."""
    mock_provider.chat.return_value = LLMResponse(
        content="I'll delegate this now.",
        tool_calls=[
            ToolCallRequest(
                id="1",
                name="spawn",
                arguments={"task": "Handle web chat", "label": "web-chat", "model": "coder"},
            )
        ],
    )

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "delegate this"}]
    )

    assert "started" in final_content.lower()
    assert tools_used == ["spawn"]
    assert mock_provider.chat.await_count >= 1


@pytest.mark.asyncio
async def test_run_agent_loop_emits_heartbeat_during_slow_model_call(
    agent_loop, mock_provider, monkeypatch
):
    """Long model waits should emit visible progress before timing out."""

    async def slow_chat(**kwargs):
        await asyncio.sleep(0.08)
        return LLMResponse(content="done")

    agent_loop.max_loop_seconds = 0.05
    mock_provider.chat.side_effect = slow_chat
    progress_updates: list[str] = []
    monkeypatch.setattr(TurnRunner, "PROGRESS_HEARTBEAT_SECONDS", 0.01)

    final_content, _, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "take your time"}],
        on_progress=lambda content, **kwargs: progress_updates.append(content) or asyncio.sleep(0),
    )

    assert "time limit" in (final_content or "").lower()
    assert any("still working on the next step" in update.lower() for update in progress_updates)


@pytest.mark.asyncio
async def test_run_agent_loop_emits_only_one_identical_heartbeat_per_wait(
    agent_loop, mock_provider, monkeypatch
):
    """A single blocked await should not spam identical progress heartbeats."""

    async def slow_chat(**kwargs):
        await asyncio.sleep(0.05)
        return LLMResponse(content="done")

    agent_loop.max_loop_seconds = 0.04
    mock_provider.chat.side_effect = slow_chat
    progress_updates: list[str] = []
    monkeypatch.setattr(TurnRunner, "PROGRESS_HEARTBEAT_SECONDS", 0.01)

    final_content, _, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "take your time"}],
        on_progress=lambda content, **kwargs: progress_updates.append(content) or asyncio.sleep(0),
    )

    assert "time limit" in (final_content or "").lower()
    assert progress_updates.count("Still working on the next step.") == 1


@pytest.mark.asyncio
async def test_run_agent_loop_times_out_during_slow_tool_execution(
    agent_loop, mock_provider, monkeypatch
):
    """Long tool execution should respect the overall turn deadline."""
    mock_provider.chat.return_value = LLMResponse(
        content="I'll inspect that now.",
        tool_calls=[ToolCallRequest(id="1", name="read_memory", arguments={"category": "facts"})],
    )

    async def slow_execute(name, arguments):
        await asyncio.sleep(0.08)
        return "done"

    agent_loop.max_loop_seconds = 0.05
    agent_loop.tools.execute = slow_execute
    progress_updates: list[str] = []
    monkeypatch.setattr(TurnRunner, "PROGRESS_HEARTBEAT_SECONDS", 0.01)

    final_content, _, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "read memory slowly"}],
        on_progress=lambda content, **kwargs: progress_updates.append(content) or asyncio.sleep(0),
    )

    assert "time limit" in (final_content or "").lower()
    assert any("still working on `read_memory`" in update.lower() for update in progress_updates)


@pytest.mark.asyncio
async def test_process_message_does_not_inject_english_delegation_hint(agent_loop, mock_provider):
    """Large implementation requests should not rely on injected English-only hints."""
    mock_provider.chat.return_value = LLMResponse(content="done")

    await agent_loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="Start with the web-chat folder and build a simple HTML page from it.",
        )
    )

    sent_messages = mock_provider.chat.await_args.kwargs["messages"]
    assert not any("Prefer using spawn()" in (msg.get("content") or "") for msg in sent_messages)


@pytest.mark.asyncio
async def test_system_message_uses_background_task_fallback_when_model_returns_nothing(
    agent_loop, mock_provider
):
    """System task completions should not degrade to a generic placeholder."""
    response = await agent_loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:direct",
            content=(
                "[Subagent 'website' completed successfully]\n\n"
                "Task: Build the website\n\n"
                "Result:\n"
                "Created projects/site/index.html and projects/site/app.js."
            ),
        )
    )

    assert response is not None
    assert "finished in the background" in response.content
    assert "projects/site/index.html" in response.content
    mock_provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_message_uses_background_task_fallback_for_low_value_model_reply(
    agent_loop, mock_provider
):
    """Subagent completion prompts should not rely on another model pass."""

    response = await agent_loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:direct",
            content=(
                "[Subagent 'nostr' completed successfully]\n\n"
                "Task: Research Nostr integration\n\n"
                "Result:\n"
                "Drafted a relay strategy and zap-flow summary in "
                "projects/bitcoin-radio-station/subtask_outputs/nostr_integration_summary.md."
            ),
        )
    )

    assert response is not None
    assert "repeated tool calls" not in response.content.lower()
    assert "please refine the request" not in response.content.lower()
    assert "finished in the background" in response.content
    assert "nostr integration" in response.content.lower()
    mock_provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_message_reports_subagent_failure_without_retrying_work(
    agent_loop, mock_provider
):
    response = await agent_loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:direct",
            content=(
                "[Subagent 'law flow' failed]\n\n"
                "Task: Create the law-firm implementation flow\n\n"
                "Result:\n"
                "Error: I hit a temporary provider error while generating the response. "
                "Please retry this request."
            ),
        )
    )

    assert response is not None
    assert "failed" in response.content.lower()
    assert "temporary provider error" in response.content.lower()


@pytest.mark.asyncio
async def test_cancel_active_work_stops_running_turn_and_notifies_user(agent_loop, mock_bus):
    msg = InboundMessage(
        channel="cli",
        sender_id="user",
        chat_id="direct",
        content="Do a long task",
    )
    delivered = False

    async def consume_inbound():
        nonlocal delivered
        if not delivered:
            delivered = True
            return msg
        await asyncio.sleep(1)
        return msg

    async def long_process(_msg):
        await asyncio.sleep(10)
        return None

    mock_bus.consume_inbound.side_effect = consume_inbound
    agent_loop._process_message = long_process
    agent_loop.subagents.cancel_for_origin = AsyncMock(return_value=0)

    run_task = asyncio.create_task(agent_loop.run())
    await asyncio.sleep(0.05)

    cancelled = await agent_loop.cancel_active_work("cli:direct")
    await asyncio.sleep(0.05)
    agent_loop.stop()
    await asyncio.wait_for(run_task, timeout=1.0)

    assert cancelled is True
    assert any(
        call.args[0].content == "Stopped the active work. Ready for the next request."
        for call in mock_bus.publish_outbound.await_args_list
    )


@pytest.mark.asyncio
async def test_run_agent_loop_reprompts_empty_post_tool_response(agent_loop, mock_provider):
    """Blank text after tool use should be treated as a bad model turn, not success."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="1", name="read_memory", arguments={"category": "facts"})
            ],
        ),
        LLMResponse(content=""),
        LLMResponse(content="There are no matching tasks in memory yet."),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert "no matching tasks" in final_content.lower()
    assert tools_used == ["read_memory"]
    assert mock_provider.chat.await_count == 3


@pytest.mark.asyncio
async def test_run_agent_loop_returns_honest_fallback_after_repeated_empty_post_tool_response(
    agent_loop, mock_provider
):
    """Repeated blank replies after tool use should fall back to grounded tool output."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="1", name="read_memory", arguments={"category": "facts"})
            ],
        ),
        LLMResponse(content=""),
        LLMResponse(content="   "),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert "i checked memory" in final_content.lower()
    assert "no memory items found" in final_content.lower()
    assert tools_used == ["read_memory"]
    assert mock_provider.chat.await_count == 3


@pytest.mark.asyncio
async def test_run_agent_loop_returns_user_facing_fallback_for_successful_write_task(
    agent_loop, mock_provider
):
    """Successful write-style tool results should degrade to user-facing confirmations."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="write_task",
                    arguments={
                        "title": "Groceries list",
                        "content": "Buy snacks and water.",
                        "assignee": "tal",
                    },
                )
            ],
        ),
        LLMResponse(content=""),
        LLMResponse(content="   "),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "make a groceries list"}]
    )

    assert final_content == "Done — Task saved: Groceries list (assigned to tal)"
    assert "model stopped before writing the final answer" not in final_content.lower()
    assert tools_used == ["write_task"]
    assert mock_provider.chat.await_count == 3


@pytest.mark.asyncio
async def test_process_message_persists_final_assistant_reply(agent_loop, mock_provider):
    """The saved session should include the exact assistant reply shown to the user."""
    mock_provider.chat.return_value = LLMResponse(content="done")

    response = await agent_loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="say done",
        )
    )

    assert response is not None
    assert response.content == "done"

    session = agent_loop.sessions.get_or_create("cli:direct")
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "done"


@pytest.mark.asyncio
async def test_process_message_resume_query_flows_through_normal_model_turn(
    agent_loop, mock_provider
):
    """Resume-style requests should use the normal turn flow without an extra classifier pass."""
    session = agent_loop.sessions.get_or_create("cli:direct")
    session.messages = [
        {"role": "user", "content": "Prepare the release"},
        {"role": "assistant", "content": "The release checklist is ready."},
    ]
    mock_provider.chat.return_value = LLMResponse(
        content="Here's where we left off: the checklist is ready."
    )

    response = await agent_loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="where did we leave off last time?",
        )
    )

    assert response is not None
    assert "here's where we left off" in response.content.lower()
    assert mock_provider.chat.await_count == 1
    sent_messages = mock_provider.chat.await_args.kwargs["messages"]
    assert any(msg.get("content") == "Prepare the release" for msg in sent_messages)
    assert any(msg.get("content") == "The release checklist is ready." for msg in sent_messages)


@pytest.mark.asyncio
async def test_distillation_skips_null_candidates(agent_loop, mock_provider):
    session = agent_loop.sessions.get_or_create("cli:test-distill")
    session.messages = [
        {"role": "user", "content": "remember this", "timestamp": "2026-03-09T00:00:00+00:00"},
        {"role": "assistant", "content": "ok", "timestamp": "2026-03-09T00:00:01+00:00"},
    ]
    mock_provider.chat.return_value = LLMResponse(content='{"candidates": [null]}')

    await agent_loop._distill_session(session)


def test_commit_candidate_to_memory_skips_duplicate_fact(agent_loop):
    """Distillation should not write near-duplicate facts repeatedly."""
    agent_loop.memory.write_fact(
        title="User prefers concise answers",
        content="The user prefers concise answers for quick operational questions.",
        confidence=0.9,
        source="manual",
    )

    candidate = AtomicCandidate(
        type=CandidateType.FACT,
        title="User prefers concise answers",
        content="User prefers concise answers for operational questions.",
        confidence=0.95,
    )

    agent_loop._commit_candidate_to_memory(candidate)

    facts = agent_loop.memory.list_memories("facts")
    assert len(facts) == 1


def test_commit_candidate_to_memory_rejects_low_scrutiny_decision(agent_loop):
    """Distilled decisions should need stronger evidence before being stored."""
    candidate = AtomicCandidate(
        type=CandidateType.DECISION,
        title="Use framework X",
        content="We should use framework X.",
        confidence=0.8,
        decision_rationale=None,
    )

    agent_loop._commit_candidate_to_memory(candidate)

    decisions = agent_loop.memory.list_memories("decisions")
    assert decisions == []


def test_commit_candidate_to_memory_rejects_recommendation_report_decision(agent_loop):
    """Distilled decisions should not store assistant-authored reports or recommendations."""
    candidate = AtomicCandidate(
        type=CandidateType.DECISION,
        title="Local-First Note Assistant Architecture Recommendation",
        content="This report recommends a local-first architecture we should use.",
        confidence=0.97,
        decision_rationale="Recommendation based on the trade-off analysis in the report.",
    )

    agent_loop._commit_candidate_to_memory(candidate)

    decisions = agent_loop.memory.list_memories("decisions")
    assert decisions == []
