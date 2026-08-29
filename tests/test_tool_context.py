from __future__ import annotations

import asyncio

import pytest

from hermitcrab.agent.tools.context import set_turn_context
from hermitcrab.agent.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_routes_using_turn_context_not_shared_instance_state() -> None:
    sent: list[tuple[str, str]] = []

    async def send(msg) -> None:
        sent.append((msg.channel, msg.chat_id))

    tool = MessageTool(send_callback=send, default_channel="cli", default_chat_id="direct")

    async def turn(channel: str, chat_id: str, delay: float) -> None:
        set_turn_context(channel=channel, chat_id=chat_id)
        if delay:
            await asyncio.sleep(delay)
        await tool.execute("hello")

    await asyncio.gather(
        asyncio.create_task(turn("telegram", "111", 0.02)),
        asyncio.create_task(turn("nostr", "abc", 0.0)),
    )

    assert ("telegram", "111") in sent
    assert ("nostr", "abc") in sent
    assert ("cli", "direct") not in sent
