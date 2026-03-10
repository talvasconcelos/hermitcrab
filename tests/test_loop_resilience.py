"""Resilience tests for AgentLoop retries, loop guards, and delegation hints."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.distillation import AtomicCandidate, CandidateType
from hermitcrab.agent.loop import AgentLoop
from hermitcrab.bus.events import InboundMessage
from hermitcrab.providers.base import LLMResponse, ToolCallRequest


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.consume_inbound = AsyncMock()
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

    final_content, _, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "loop"}]
    )

    assert final_content is not None
    assert "repeated tool calls" in final_content.lower()


@pytest.mark.asyncio
async def test_run_agent_loop_recovers_inline_json_tool_call(agent_loop, mock_provider):
    """Raw JSON tool-call text should be recovered and executed."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=(
                'Let me first check memory. '
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
                '<minimax:tool_call>\n'
                '<invoke name="read_memory">\n'
                '<parameter name="category">facts</parameter>\n'
                '</invoke>\n'
                '</minimax:tool_call>'
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
    assert mock_provider.chat.await_count == 1


@pytest.mark.asyncio
async def test_process_message_injects_subagent_hint_for_substantial_implementation_work(
    agent_loop, mock_provider
):
    """Large implementation requests should get an explicit delegation reminder."""
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
    assert any(
        msg.get("role") == "system" and "Prefer using spawn()" in (msg.get("content") or "")
        for msg in sent_messages
    )


@pytest.mark.asyncio
async def test_system_message_uses_background_task_fallback_when_model_returns_nothing(
    agent_loop, mock_provider
):
    """System task completions should not degrade to a generic placeholder."""
    mock_provider.chat.return_value = LLMResponse(content=None)

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


@pytest.mark.asyncio
async def test_run_agent_loop_reprompts_intent_only_post_tool_response(agent_loop, mock_provider):
    """Intent-only text after tool use should not be accepted as final."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="1", name="read_memory", arguments={"category": "facts"})
            ],
        ),
        LLMResponse(content="Let me gather the complete picture first."),
        LLMResponse(content="There is no saved web-chat task yet. I can start by inspecting the codebase."),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert "no saved web-chat task" in final_content.lower()
    assert tools_used == ["read_memory"]
    assert mock_provider.chat.await_count == 3


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
async def test_run_agent_loop_returns_honest_fallback_after_repeated_intent_only(agent_loop, mock_provider):
    """Repeated planning-only replies should not be forwarded as final output."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="1", name="read_memory", arguments={"category": "facts"})
            ],
        ),
        LLMResponse(content="Let me gather the complete picture first."),
        LLMResponse(content="I'll inspect the codebase and figure out the current state."),
    ]

    final_content, tools_used, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "check memory"}]
    )

    assert "without a usable answer after tool calls" in final_content.lower()
    assert tools_used == ["read_memory"]
    assert mock_provider.chat.await_count == 3


@pytest.mark.asyncio
async def test_run_agent_loop_returns_honest_fallback_after_repeated_empty_post_tool_response(
    agent_loop, mock_provider
):
    """Repeated blank replies after tool use should yield an explicit failure message."""
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

    assert "without a usable answer after tool calls" in final_content.lower()
    assert tools_used == ["read_memory"]
    assert mock_provider.chat.await_count == 3


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
