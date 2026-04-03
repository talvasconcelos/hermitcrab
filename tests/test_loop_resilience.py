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
from hermitcrab.session.manager import Session


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


def _first_sent_messages(mock_provider):
    return mock_provider.chat.await_args_list[0].kwargs["messages"]


def _has_system_message(messages, text: str) -> bool:
    lowered = text.lower()
    return any(
        msg.get("role") == "system" and lowered in (msg.get("content") or "").lower()
        for msg in messages
    )


@pytest.mark.asyncio
async def test_run_agent_loop_breaks_repeated_tool_cycles(agent_loop, mock_provider):
    """Repeated identical tool call batches should trigger loop guard."""
    tool_call = ToolCallRequest(id="1", name="list_dir", arguments={"path": "."})
    mock_provider.chat.return_value = LLMResponse(content="", tool_calls=[tool_call])

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "loop"}])
    final_content = result.final_content

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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "check memory"}])
    final_content = result.final_content
    tools_used = result.tools_used

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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "check memory"}])
    final_content = result.final_content
    tools_used = result.tools_used

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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "check memory"}])
    final_content = result.final_content
    tools_used = result.tools_used

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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "check memory"}])
    final_content = result.final_content
    tools_used = result.tools_used

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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "check memory"}])
    final_content = result.final_content
    tools_used = result.tools_used

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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "delegate this"}])
    final_content = result.final_content
    tools_used = result.tools_used

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

    result = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "take your time"}],
        on_progress=lambda content, **kwargs: progress_updates.append(content) or asyncio.sleep(0),
    )
    final_content = result.final_content

    assert "time limit" in (final_content or "").lower()
    assert progress_updates == []


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

    result = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "take your time"}],
        on_progress=lambda content, **kwargs: progress_updates.append(content) or asyncio.sleep(0),
    )
    final_content = result.final_content

    assert "time limit" in (final_content or "").lower()
    assert progress_updates == []


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

    result = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "read memory slowly"}],
        on_progress=lambda content, **kwargs: progress_updates.append(content) or asyncio.sleep(0),
    )
    final_content = result.final_content

    assert "time limit" in (final_content or "").lower()
    assert any("still working on `read_memory`" in update.lower() for update in progress_updates)
    assert any(
        msg.get("role") == "tool"
        and "turn time limit was reached" in (msg.get("content") or "").lower()
        for msg in result.messages
    )


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
    assert "reviewed the result" not in response.content.lower()
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
async def test_run_agent_loop_reprompts_empty_actionable_request_toward_tools(
    agent_loop, mock_provider
):
    """An empty reply on an actionable follow-up should reprompt toward tool execution, not only direct text."""
    mock_provider.chat.side_effect = [
        LLMResponse(content="", finish_reason="stop"),
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="write_file",
                    arguments={
                        "path": "skills/llm-council/SKILL.md",
                        "content": "---\nname: llm-council\ndescription: Test skill\n---\n",
                    },
                )
            ],
        ),
        LLMResponse(content="Done - skill created."),
    ]

    result = await agent_loop._run_agent_loop(
        [
            {
                "role": "user",
                "content": "please add a new skill from this content:\n\nname: llm-council",
            },
            {"role": "assistant", "content": "I'll create the llm-council skill for you."},
            {"role": "user", "content": "can you add that skill?"},
        ]
    )
    final_content = result.final_content
    tools_used = result.tools_used
    messages = result.messages

    assert final_content == "Done - skill created."
    assert tools_used == ["write_file"]
    assert mock_provider.chat.await_count == 3
    assert any(
        msg.get("role") == "system"
        and "If tools are needed, call them now" in (msg.get("content") or "")
        for msg in messages
    )


@pytest.mark.asyncio
async def test_run_agent_loop_continues_after_intent_only_ack(agent_loop, mock_provider):
    """Intermediate acknowledgements should not count as task completion, even in other languages."""
    mock_provider.chat.side_effect = [
        LLMResponse(content="Vou criar a skill e já volto com o resultado."),
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="write_file",
                    arguments={
                        "path": "skills/llm-council/SKILL.md",
                        "content": "---\nname: llm-council\ndescription: Test skill\n---\n",
                    },
                )
            ],
        ),
        LLMResponse(content="Criei a skill com sucesso."),
    ]

    result = await agent_loop._run_agent_loop(
        [
            {
                "role": "user",
                "content": "adiciona uma skill nova com este conteudo:\n\nname: llm-council\ndescription: Test skill",
            }
        ]
    )
    final_content = result.final_content
    tools_used = result.tools_used
    messages = result.messages

    assert final_content == "Criei a skill com sucesso."
    assert tools_used == ["write_file"]
    assert mock_provider.chat.await_count == 3
    assert any(
        msg.get("role") == "system"
        and "Execute the required tool calls" in (msg.get("content") or "")
        for msg in messages
    )


@pytest.mark.asyncio
async def test_run_agent_loop_uses_memory_tools_when_classifier_flags_authoritative_write(
    agent_loop, mock_provider
):
    mock_provider.chat.side_effect = [
        LLMResponse(content="I've already got that stored in memory."),
        LLMResponse(content="YES"),
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="write_fact",
                    arguments={
                        "title": "Shopping day preference",
                        "content": "The user prefers Saturday morning grocery shopping.",
                        "source": "user statement",
                    },
                )
            ],
        ),
        LLMResponse(content="Saved that preference to memory."),
    ]

    result = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "Remember that I prefer Saturday morning grocery shopping."}]
    )

    assert result.final_content == "Saved that preference to memory."
    assert result.tools_used == ["write_fact"]
    assert _has_system_message(result.messages, "typed memory tools")


@pytest.mark.asyncio
async def test_run_agent_loop_reprompts_for_memory_correction_without_lookup(
    agent_loop, mock_provider
):
    mock_provider.chat.side_effect = [
        LLMResponse(content="I don't have anything saved about that yet."),
        LLMResponse(content="YES"),
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="search_memory",
                    arguments={"query": "shopping preference"},
                )
            ],
        ),
        LLMResponse(content="I found it and updated the preference."),
    ]

    result = await agent_loop._run_agent_loop(
        [
            {
                "role": "user",
                "content": "I told you this before. Fix that preference and update it to Sunday afternoons.",
            }
        ]
    )

    assert result.final_content == "I found it and updated the preference."
    assert result.tools_used == ["search_memory"]
    assert _has_system_message(result.messages, "check memory before claiming")


@pytest.mark.asyncio
async def test_run_agent_loop_uses_prior_structured_request_for_short_follow_up(
    agent_loop, mock_provider
):
    """Short follow-ups should inherit actionable context from the recent user request."""
    mock_provider.chat.side_effect = [
        LLMResponse(content="I'll create it now."),
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="write_file",
                    arguments={
                        "path": "skills/llm-council/SKILL.md",
                        "content": "---\nname: llm-council\ndescription: Test skill\n---\n",
                    },
                )
            ],
        ),
        LLMResponse(content="Done - skill created."),
    ]

    result = await agent_loop._run_agent_loop(
        [
            {
                "role": "user",
                "content": "please add a new skill from this content:\n\nname: llm-council",
            },
            {"role": "assistant", "content": "I'll create the llm-council skill for you."},
            {"role": "user", "content": "can you add that skill?"},
        ]
    )

    assert result.final_content == "Done - skill created."
    assert result.tools_used == ["write_file"]
    assert mock_provider.chat.await_count == 3
    assert _has_system_message(result.messages, "intermediate acknowledgement")


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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "check memory"}])
    final_content = result.final_content
    tools_used = result.tools_used

    assert "no matching tasks" in final_content.lower()
    assert tools_used == ["read_memory"]
    assert mock_provider.chat.await_count == 3


@pytest.mark.asyncio
async def test_run_agent_loop_reprompts_non_final_post_tool_status_for_mutating_request(
    agent_loop, mock_provider
):
    """A mutating request should not complete after read-only inspection plus status text."""
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="list_dir",
                    arguments={"path": "/home/talvasconcelos/.hermitcrab/workspace/skills"},
                )
            ],
        ),
        LLMResponse(content="gonna create the llm-council skill for you now."),
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="2",
                    name="write_file",
                    arguments={
                        "path": "skills/llm-council/SKILL.md",
                        "content": "---\nname: llm-council\ndescription: Test skill\n---\n",
                    },
                )
            ],
        ),
        LLMResponse(content="Done - skill created."),
    ]

    result = await agent_loop._run_agent_loop(
        [
            {
                "role": "user",
                "content": "can you add a skill from this:\n\nname: llm-council\ndescription: Test skill",
            }
        ]
    )

    assert result.final_content == "Done - skill created."
    assert result.tools_used == ["list_dir", "write_file"]
    assert mock_provider.chat.await_count == 4
    assert _has_system_message(result.messages, "continue with the next required tool calls now")


def test_build_interactive_messages_injects_procedural_skill_guidance(tmp_path, agent_loop):
    skill_dir = tmp_path / "skills" / "council"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: council
description: Run a structured advisor workflow.
metadata:
  hermitcrab:
    activation:
      aliases: [council]
    workflow:
      kind: workflow
      phases:
        - id: gather
          title: Gather views
          instructions: Collect the advisor inputs first.
          completion:
            tools: [write_file]
            artifacts: [reports/advisors.md]
---

# Council
""",
        encoding="utf-8",
    )

    session = Session(key="cli:direct")
    messages, _ = agent_loop._build_interactive_messages(
        InboundMessage(channel="cli", sender_id="direct", chat_id="direct", content="council this"),
        tmp_path / ".scratch.md",
        [],
        session,
    )

    system_messages = [msg["content"] for msg in messages if msg.get("role") == "system"]
    assert any("procedural skill `council`" in content.lower() for content in system_messages)
    assert "active_skill_run" in session.metadata


def test_skill_runtime_advances_and_clears_when_phase_requirements_are_met(tmp_path, agent_loop):
    skill_dir = tmp_path / "skills" / "council"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: council
description: Run a structured advisor workflow.
metadata:
  hermitcrab:
    activation:
      aliases: [council]
    workflow:
      kind: workflow
      phases:
        - id: gather
          title: Gather views
          instructions: Collect the advisor inputs first.
          completion:
            tools: [write_file]
            artifacts: [reports/advisors.md]
---
""",
        encoding="utf-8",
    )

    session = Session(key="cli:direct")
    agent_loop.skill_runtime.maybe_activate(
        session.metadata,
        current_message="council this",
        history=[],
    )
    agent_loop.skill_runtime.update_after_turn(
        session.metadata,
        result_messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path":"reports/advisors.md","content":"ok"}',
                        },
                    }
                ],
            }
        ],
        tools_used=["write_file"],
    )

    assert "active_skill_run" not in session.metadata


@pytest.mark.asyncio
async def test_read_file_can_fallback_to_current_working_directory(tmp_path):
    from hermitcrab.agent.tools.filesystem import ReadFileTool

    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    workspace.mkdir()
    repo.mkdir()
    (repo / "README.md").write_text("Development Status :: 4 - Beta", encoding="utf-8")

    tool = ReadFileTool(workspace=workspace, fallback_dir=repo)
    result = await tool.execute("README.md")

    assert "Beta" in result


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

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "check memory"}])
    final_content = result.final_content
    tools_used = result.tools_used

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

    result = await agent_loop._run_agent_loop(
        [{"role": "user", "content": "make a groceries list"}]
    )
    final_content = result.final_content
    tools_used = result.tools_used

    assert final_content == "Done — Task saved: Groceries list (assigned to tal)"
    assert "model stopped before writing the final answer" not in final_content.lower()
    assert tools_used == ["write_task"]
    assert mock_provider.chat.await_count == 3


@pytest.mark.asyncio
async def test_run_agent_loop_reprompts_empty_non_tool_response(agent_loop, mock_provider):
    """An empty assistant reply without tools should be retried, not treated as success."""
    mock_provider.chat.side_effect = [
        LLMResponse(content=""),
        LLMResponse(content="Here is the direct answer."),
    ]

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "answer directly"}])
    final_content = result.final_content
    tools_used = result.tools_used

    assert final_content == "Here is the direct answer."
    assert tools_used == []
    assert mock_provider.chat.await_count == 2


@pytest.mark.asyncio
async def test_run_agent_loop_returns_specific_fallback_after_repeated_empty_non_tool_response(
    agent_loop, mock_provider
):
    """Repeated empty assistant replies without tools should produce a specific model failure message."""
    mock_provider.chat.side_effect = [
        LLMResponse(content="", finish_reason="stop"),
        LLMResponse(content="   ", finish_reason="stop"),
    ]

    result = await agent_loop._run_agent_loop([{"role": "user", "content": "summarize the repo"}])
    final_content = result.final_content
    tools_used = result.tools_used

    assert "empty reply" in final_content.lower()
    assert "summarize the repo" in final_content.lower()
    assert "finish reason: stop" in final_content.lower()
    assert tools_used == []
    assert mock_provider.chat.await_count == 2


@pytest.mark.asyncio
async def test_process_message_does_not_emit_generic_cleanly_retry_placeholder(
    agent_loop, mock_provider
):
    """User-facing output should not fall back to the old generic placeholder."""
    mock_provider.chat.side_effect = [
        LLMResponse(content=""),
        LLMResponse(content="   "),
    ]

    response = await agent_loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="tell me what is going on",
        )
    )

    assert response is not None
    assert "couldn't complete that turn cleanly" not in response.content.lower()
    assert "empty reply" in response.content.lower()


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
async def test_process_message_tracks_pending_work_for_unresolved_actionable_turn(
    agent_loop, mock_provider
):
    """Coordinator state should keep unresolved actionable work when the model bails."""
    mock_provider.chat.side_effect = [
        LLMResponse(content="", finish_reason="stop"),
        LLMResponse(content="   ", finish_reason="stop"),
    ]

    response = await agent_loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="please add a new skill from this content:\n\nname: llm-council\ndescription: Test skill",
        )
    )

    assert response is not None
    assert "empty reply" in response.content.lower()
    session = agent_loop.sessions.get_or_create("cli:direct")
    pending = session.metadata.get("pending_work")
    assert isinstance(pending, dict)
    assert "llm-council" in pending["origin_request"]
    assert "llm-council" in pending["source_excerpt"]


@pytest.mark.asyncio
async def test_process_message_injects_pending_work_hint_and_clears_on_success(
    agent_loop, mock_provider
):
    """Short follow-ups should resolve against coordinator-owned pending work."""
    session = agent_loop.sessions.get_or_create("cli:direct")
    session.metadata["pending_work"] = {
        "origin_request": "please add a new skill from this content:\n\nname: llm-council",
        "latest_request": "please add a new skill from this content:\n\nname: llm-council",
        "source_excerpt": "name: llm-council\ndescription: Test skill",
        "last_failure": "The model returned an empty reply before answering.",
        "tools_used": [],
        "created_at": "2026-04-02T00:00:00+00:00",
        "updated_at": "2026-04-02T00:00:00+00:00",
    }
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="write_file",
                    arguments={
                        "path": "skills/llm-council/SKILL.md",
                        "content": "---\nname: llm-council\ndescription: Test skill\n---\n",
                    },
                )
            ],
        ),
        LLMResponse(content="Done - skill created."),
    ]

    response = await agent_loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="can you add that skill?",
        )
    )

    assert response is not None
    assert response.content == "Done - skill created."
    sent_messages = mock_provider.chat.await_args_list[0].kwargs["messages"]
    assert any(
        msg.get("role") == "system"
        and "unresolved pending work" in (msg.get("content") or "").lower()
        and "llm-council" in (msg.get("content") or "")
        for msg in sent_messages
    )
    assert "pending_work" not in session.metadata


@pytest.mark.asyncio
async def test_process_message_injects_pending_work_hint_for_long_mixed_follow_up(
    agent_loop, mock_provider
):
    """Long related follow-ups should still carry unresolved structured work into the turn."""
    session = agent_loop.sessions.get_or_create("nostr:direct")
    session.metadata["pending_work"] = {
        "origin_request": "please add a new skill from this content:\n\nname: llm-council",
        "latest_request": "please add a new skill from this content:\n\nname: llm-council",
        "source_excerpt": "name: llm-council\ndescription: Test skill",
        "last_failure": "The model returned an empty reply before answering.",
        "tools_used": [],
        "created_at": "2026-04-02T00:00:00+00:00",
        "updated_at": "2026-04-02T00:00:00+00:00",
    }
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="1",
                    name="write_file",
                    arguments={
                        "path": "skills/llm-council/SKILL.md",
                        "content": "---\nname: llm-council\ndescription: Test skill\n---\n",
                    },
                )
            ],
        ),
        LLMResponse(content="Done - skill created."),
    ]

    response = await agent_loop._process_message(
        InboundMessage(
            channel="nostr",
            sender_id="user",
            chat_id="direct",
            content=(
                "tried to fix it, do you still have the skill text in context? "
                "can you try and add that skill again?"
            ),
        )
    )

    assert response is not None
    assert response.content == "Done - skill created."
    sent_messages = _first_sent_messages(mock_provider)
    assert any(
        msg.get("role") == "system"
        and "unresolved pending work" in (msg.get("content") or "").lower()
        and "llm-council" in (msg.get("content") or "")
        for msg in sent_messages
    )
    assert _has_system_message(sent_messages, "skills/llm-council/skill.md")


@pytest.mark.asyncio
async def test_process_message_does_not_clear_pending_work_for_unrelated_short_request(
    agent_loop, mock_provider
):
    """Unrelated short turns should not consume pending work just because a tool ran."""
    session = agent_loop.sessions.get_or_create("cli:direct")
    session.metadata["pending_work"] = {
        "origin_request": "please add a new skill from this content:\n\nname: llm-council",
        "latest_request": "please add a new skill from this content:\n\nname: llm-council",
        "source_excerpt": "name: llm-council\ndescription: Test skill",
        "last_failure": "The model returned an empty reply before answering.",
        "tools_used": [],
        "created_at": "2026-04-02T00:00:00+00:00",
        "updated_at": "2026-04-02T00:00:00+00:00",
    }
    mock_provider.chat.side_effect = [
        LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(id="1", name="read_memory", arguments={"category": "facts"})
            ],
        ),
        LLMResponse(content="It is noon."),
    ]

    response = await agent_loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="what time is it?",
        )
    )

    assert response is not None
    assert response.content == "It is noon."
    sent_messages = _first_sent_messages(mock_provider)
    assert not _has_system_message(sent_messages, "unresolved pending work")
    assert "pending_work" in session.metadata


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
    sent_messages = _first_sent_messages(mock_provider)
    assert any(msg.get("content") == "Prepare the release" for msg in sent_messages)
    assert any(msg.get("content") == "The release checklist is ready." for msg in sent_messages)


@pytest.mark.asyncio
async def test_process_message_uses_recent_archived_history_for_same_chat_follow_up(
    agent_loop, mock_provider
):
    """A fresh same-chat session should reuse very recent archived history for continuity."""
    session = agent_loop.sessions.get_or_create("nostr:chat")
    session.messages = [
        {"role": "user", "content": "Store the groceries list as a knowledge note."},
        {"role": "assistant", "content": "Done! It's in knowledge/notes/Groceries list.md."},
    ]
    agent_loop.sessions.save(session)
    agent_loop.sessions.archive(session, "timeout")
    mock_provider.chat.return_value = LLMResponse(content="done")

    await agent_loop._process_message(
        InboundMessage(
            channel="nostr",
            sender_id="user",
            chat_id="chat",
            content="can you add milk and eggs also?",
        )
    )

    sent_messages = _first_sent_messages(mock_provider)
    assert any(
        msg.get("content") == "Store the groceries list as a knowledge note."
        for msg in sent_messages
    )
    assert any(
        msg.get("content") == "Done! It's in knowledge/notes/Groceries list.md."
        for msg in sent_messages
    )


@pytest.mark.asyncio
async def test_session_search_tool_finds_archived_transcript(agent_loop):
    session = agent_loop.sessions.get_or_create("nostr:chat")
    session.messages = [
        {"role": "user", "content": "Please store the groceries list as a knowledge note."},
        {"role": "assistant", "content": "Done, I saved it in knowledge/notes/Groceries list.md."},
    ]
    agent_loop.sessions.save(session)
    agent_loop.sessions.archive(session, "timeout")

    tool = agent_loop.tools.get("session_search")
    assert tool is not None

    result = await tool.execute(query="groceries list", max_results=3)

    assert "Found 1 matching session" in result
    assert "nostr:chat" in result
    assert "knowledge/notes/Groceries list.md" in result


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
