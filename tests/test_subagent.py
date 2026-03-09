import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.agent.subagent import SubagentManager
from hermitcrab.bus.queue import MessageBus
from hermitcrab.config.schema import ExecToolConfig
from hermitcrab.providers.base import LLMResponse


@pytest.mark.asyncio
async def test_subagent_spawn_resolves_model_alias_and_reports_result(tmp_path):
    provider = MagicMock()
    provider.get_default_model = MagicMock(return_value="anthropic/claude-opus-4-5")
    provider.chat = AsyncMock(return_value=LLMResponse(content="done"))

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

    provider.chat.assert_awaited()
    assert provider.chat.await_args.kwargs["model"] == "ollama/qwen3.5:4b"
    bus.publish_inbound.assert_awaited()
