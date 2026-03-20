import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.subagent import SubagentManager
from hermitcrab.bus.queue import MessageBus
from hermitcrab.config.schema import ExecToolConfig, ModelAliasConfig, NamedModelConfig
from hermitcrab.providers.base import LLMResponse, ToolCallRequest


@pytest.mark.asyncio
async def test_subagent_spawn_resolves_model_alias_and_reports_result(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
        model_aliases={"coder": "ollama/qwen3.5:4b"},
    )

    result = await manager.spawn(
        task="Build a simple page",
        label="website",
        origin_channel="cli",
        origin_chat_id="direct",
        model="coder",
    )

    assert "Subagent [website] started" in result

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    provider.chat_with_retry.assert_awaited()
    assert provider.chat_with_retry.await_args.kwargs["model"] == "ollama/qwen3.5:4b"
    bus.publish_inbound.assert_awaited()


@pytest.mark.asyncio
async def test_subagent_spawn_alias_can_disable_thinking(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
        model_aliases={
            "fast": ModelAliasConfig(model="openai/lfm2.5-thinking:latest", thinking=False)
        },
    )

    await manager.spawn(
        task="Summarize the file",
        label="summary",
        origin_channel="cli",
        origin_chat_id="direct",
        model="fast",
    )

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    provider.chat_with_retry.assert_awaited()
    assert provider.chat_with_retry.await_args.kwargs["model"] == "openai/lfm2.5-thinking:latest"
    assert provider.chat_with_retry.await_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_subagent_spawn_named_model_alias_resolves_underlying_model(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
        model_aliases={
            "coder": ModelAliasConfig(
                model="ollama/qwen2.5-coder:7b",
                reasoning_effort="low",
            )
        },
    )

    await manager.spawn(
        task="Write tests",
        label="tests",
        origin_channel="cli",
        origin_chat_id="direct",
        model="coder",
    )

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    provider.chat_with_retry.assert_awaited()
    assert provider.chat_with_retry.await_args.kwargs["model"] == "ollama/qwen2.5-coder:7b"
    assert provider.chat_with_retry.await_args.kwargs["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_subagent_spawn_named_model_resolves_without_alias(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done"))

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
        named_models={
            "fast_model": NamedModelConfig(
                model="ollama/granite3.2:2b",
                reasoning_effort="none",
            )
        },
    )

    await manager.spawn(
        task="List open tasks",
        label="tasks",
        origin_channel="cli",
        origin_chat_id="direct",
        model="fast_model",
    )

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    provider.chat_with_retry.assert_awaited()
    assert provider.chat_with_retry.await_args.kwargs["model"] == "fast_model"
    assert provider.chat_with_retry.await_args.kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_subagent_recovers_inline_json_tool_call(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content=(
                    'Let me inspect the file. '
                    '{"name":"read_file","arguments":{"path":"README.md"}}'
                )
            ),
            LLMResponse(content="done"),
        ]
    )

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
    )

    await manager.spawn(task="Inspect README", label="readme", origin_channel="cli", origin_chat_id="direct")

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    bus.publish_inbound.assert_awaited()


@pytest.mark.asyncio
async def test_subagent_recovers_inline_xml_tool_call(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content=(
                    '<minimax:tool_call>\n'
                    '<invoke name="read_file">\n'
                    '<parameter name="path">README.md</parameter>\n'
                    '</invoke>\n'
                    '</minimax:tool_call>'
                )
            ),
            LLMResponse(content="done"),
        ]
    )

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
    )

    await manager.spawn(task="Inspect README", label="readme", origin_channel="cli", origin_chat_id="direct")

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    bus.publish_inbound.assert_awaited()


@pytest.mark.asyncio
async def test_subagent_normalizes_string_tool_arguments(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="1", name="read_file", arguments='{"path":"README.md"}')
                ],
            ),
            LLMResponse(content="done"),
        ]
    )

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
    )

    await manager.spawn(task="Inspect README", label="readme", origin_channel="cli", origin_chat_id="direct")

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    bus.publish_inbound.assert_awaited()


@pytest.mark.asyncio
async def test_subagent_reprompts_intent_only_post_tool_response(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="1", name="read_file", arguments={"path": "README.md"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="Let me inspect the rest of the file first."),
            LLMResponse(content="README inspected successfully."),
        ]
    )

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
    )

    await manager.spawn(task="Inspect README", label="readme", origin_channel="cli", origin_chat_id="direct")

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    provider.chat_with_retry.assert_awaited()
    bus.publish_inbound.assert_awaited()
