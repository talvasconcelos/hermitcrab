from __future__ import annotations

import asyncio

import pytest

from hermitcrab.agent.tools.context import set_turn_context
from hermitcrab.agent.tools.message import MessageTool
from hermitcrab.agent.tools.shell import ExecTool


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


@pytest.mark.asyncio
async def test_sent_flag_is_isolated_across_concurrent_turns() -> None:
    sent: list[tuple[str, str]] = []

    async def send(msg) -> None:
        sent.append((msg.channel, msg.chat_id))

    tool = MessageTool(send_callback=send, default_channel="cli", default_chat_id="direct")

    async def turn(channel: str, chat_id: str, should_send: bool, delay: float) -> bool:
        set_turn_context(channel=channel, chat_id=chat_id)
        tool.start_turn()
        if delay:
            await asyncio.sleep(delay)
        if should_send:
            await tool.execute("hello")
        await asyncio.sleep(delay or 0.02)
        return tool.has_sent_in_turn

    a, b = await asyncio.gather(
        asyncio.create_task(turn("telegram", "111", True, 0.02)),
        asyncio.create_task(turn("nostr", "abc", False, 0.0)),
    )

    assert a is True
    assert b is False
    assert sent == [("telegram", "111")]


@pytest.mark.asyncio
async def test_destructive_approval_is_isolated_across_concurrent_turns() -> None:
    tool = ExecTool()

    async def approving_turn() -> bool:
        tool.allow_destructive_command("rm victim")
        await asyncio.sleep(0.02)
        return tool._is_approved_destructive_command("rm victim")

    async def clearing_turn() -> bool:
        tool.clear_destructive_approval()
        await asyncio.sleep(0.0)
        return tool._is_approved_destructive_command("rm victim")

    approving, clearing = await asyncio.gather(
        asyncio.create_task(approving_turn()),
        asyncio.create_task(clearing_turn()),
    )

    assert approving is True
    assert clearing is False
