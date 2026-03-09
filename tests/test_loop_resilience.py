"""Resilience tests for AgentLoop retries and loop guards."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.loop import AgentLoop
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
async def test_run_agent_loop_retries_then_succeeds(agent_loop, mock_provider):
    """Transient provider failure should be retried once."""
    mock_provider.chat.side_effect = [
        RuntimeError("temporary outage"),
        LLMResponse(content="done"),
    ]

    final_content, _, _ = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "hello"}]
    )

    assert final_content == "done"
    assert mock_provider.chat.await_count == 2


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
async def test_distillation_skips_null_candidates(agent_loop, mock_provider):
    session = agent_loop.sessions.get_or_create("cli:test-distill")
    session.messages = [
        {"role": "user", "content": "remember this", "timestamp": "2026-03-09T00:00:00+00:00"},
        {"role": "assistant", "content": "ok", "timestamp": "2026-03-09T00:00:01+00:00"},
    ]
    mock_provider.chat.return_value = LLMResponse(content='{"candidates": [null]}')

    await agent_loop._distill_session(session)
