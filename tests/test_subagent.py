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

    assert "Subagent [website] started in the background" in result
    assert "`implementation` profile" in result

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    provider.chat_with_retry.assert_awaited()
    assert provider.chat_with_retry.await_args.kwargs["model"] == "ollama/qwen3.5:4b"
    bus.publish_inbound.assert_awaited()


def test_subagent_research_profile_hides_write_tools(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")

    bus = MessageBus()

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
    )

    tools, profile = manager._build_tools("research")

    assert profile.name == "research"
    assert "write_file" not in tools.tool_names()
    assert "edit_file" not in tools.tool_names()
    assert "exec" not in tools.tool_names()
    assert "read_file" in tools.tool_names()
    assert "web_search" in tools.tool_names()


@pytest.mark.asyncio
async def test_subagent_research_profile_denies_write_attempt_even_if_model_asks(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="1",
                        name="write_file",
                        arguments={"path": "notes.txt", "content": "hello"},
                    )
                ],
            ),
            LLMResponse(content="I could not write because the profile blocked that action."),
        ]
    )

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
    )

    await manager.spawn(
        task="Research the project and save findings",
        label="research",
        origin_channel="cli",
        origin_chat_id="direct",
        profile="research",
    )

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    first_call_tools = provider.chat_with_retry.await_args_list[0].kwargs["tools"]
    exposed_names = {tool["function"]["name"] for tool in first_call_tools}
    assert "write_file" not in exposed_names
    assert "read_file" in exposed_names

    bus.publish_inbound.assert_awaited()
    published = bus.publish_inbound.await_args.args[0]
    assert "Profile: research" in published.content
    assert "could not write" in published.content.lower()


@pytest.mark.asyncio
async def test_subagent_reports_partial_completion_honestly(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="1",
                        name="write_file",
                        arguments={"path": "notes.txt", "content": "hello"},
                    )
                ],
            ),
            LLMResponse(content=""),
            LLMResponse(content="   "),
        ]
    )

    bus = MessageBus()
    bus.publish_inbound = AsyncMock()

    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        model="anthropic/claude-opus-4-5",
        exec_config=ExecToolConfig(),
    )

    await manager.spawn(
        task="Write a note",
        label="notes",
        origin_channel="cli",
        origin_chat_id="direct",
    )

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    published = bus.publish_inbound.await_args.args[0]
    assert "completed partially" in published.content.lower()
    assert "Exit reason: empty_post_tool_reply" in published.content
    assert "Files: notes.txt" in published.content


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
                    "Let me inspect the file. "
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

    await manager.spawn(
        task="Inspect README", label="readme", origin_channel="cli", origin_chat_id="direct"
    )

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
                    "<minimax:tool_call>\n"
                    '<invoke name="read_file">\n'
                    '<parameter name="path">README.md</parameter>\n'
                    "</invoke>\n"
                    "</minimax:tool_call>"
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

    await manager.spawn(
        task="Inspect README", label="readme", origin_channel="cli", origin_chat_id="direct"
    )

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

    await manager.spawn(
        task="Inspect README", label="readme", origin_channel="cli", origin_chat_id="direct"
    )

    for _ in range(20):
        if manager.get_running_count() == 0:
            break
        await asyncio.sleep(0)

    bus.publish_inbound.assert_awaited()
