"""Interactive turn execution for the main agent loop."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable

from loguru import logger

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.agent.message_preparation import is_placeholder_assistant_reply
from hermitcrab.agent.pending_work import has_structured_payload, is_short_follow_up
from hermitcrab.agent.tool_call_recovery import coerce_inline_tool_calls, normalize_tool_calls
from hermitcrab.agent.tools.policy import ToolPermissionLevel
from hermitcrab.agent.tools.registry import ToolRegistry
from hermitcrab.providers.base import LLMResponse, ResponseDoneEvent, TextDeltaEvent, ToolCallEvent


@dataclass(slots=True)
class TurnRunnerConfig:
    """Operational limits and sampling settings for one turn."""

    max_iterations: int
    max_loop_seconds: int
    max_identical_tool_cycles: int
    temperature: float
    max_tokens: int
    reasoning_effort: str | None


class TurnOutcome(str, Enum):
    COMPLETED = "completed"
    EMPTY_REPLY = "empty_reply"
    INCOMPLETE_ACTION = "incomplete_action"
    TOOL_FALLBACK = "tool_fallback"
    MAX_ITERATIONS = "max_iterations"
    TIMEOUT = "timeout"
    REPEATED_TOOL_CYCLE = "repeated_tool_cycle"
    DELEGATED = "delegated"


@dataclass(slots=True)
class TurnResult:
    final_content: str | None
    tools_used: list[str]
    messages: list[dict[str, Any]]
    outcome: TurnOutcome


class TurnRunner:
    """Run one assistant turn from prompt assembly through tool execution."""

    PROGRESS_HEARTBEAT_SECONDS = 15.0
    MAX_IDENTICAL_HEARTBEATS_PER_WAIT = 1
    INTERMEDIATE_ACK_MAX_RETRIES = 2

    def __init__(
        self,
        *,
        context: ContextBuilder,
        tools: ToolRegistry,
        config: TurnRunnerConfig,
        chat_callable: Callable[..., Awaitable[Any]],
        stream_chat_callable: Callable[..., AsyncIterator[Any]] | None,
        get_model_for_job: Callable[[Any], str | None],
        strip_think: Callable[[str | None], str | None],
        tool_hint: Callable[[list[Any]], str],
        is_empty_response: Callable[[str | None], bool],
    ):
        self.context = context
        self.tools = tools
        self.config = config
        self.chat_callable = chat_callable
        self.stream_chat_callable = stream_chat_callable
        self.get_model_for_job = get_model_for_job
        self.strip_think = strip_think
        self.tool_hint = tool_hint
        self.is_empty_response = is_empty_response

    def _remaining_seconds(self, started_at: float) -> float:
        return self.config.max_loop_seconds - (time.monotonic() - started_at)

    async def _await_with_progress(
        self,
        awaitable: Awaitable[Any],
        *,
        started_at: float,
        on_progress: Callable[..., Awaitable[None]] | None,
        waiting_message: str,
    ) -> Any:
        """Await a long-running step with periodic progress heartbeats and a hard deadline."""
        task = asyncio.create_task(awaitable)
        heartbeats_sent = 0

        while True:
            remaining = self._remaining_seconds(started_at)
            if remaining <= 0:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise TimeoutError(
                    "turn exceeded max_loop_seconds while waiting for work to finish"
                )

            try:
                return await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=min(self.PROGRESS_HEARTBEAT_SECONDS, max(0.01, remaining)),
                )
            except asyncio.TimeoutError:
                if task.done():
                    return await task
                if on_progress and heartbeats_sent < self.MAX_IDENTICAL_HEARTBEATS_PER_WAIT:
                    if waiting_message:
                        await on_progress(waiting_message)
                    heartbeats_sent += 1

    @staticmethod
    def _tool_cycle_signature(tool_calls: list[Any]) -> str:
        payload = [{"name": tc.name, "arguments": tc.arguments} for tc in tool_calls]
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)

    @staticmethod
    def _is_tool_error_result(result: str | None) -> bool:
        normalized = (result or "").strip().lower()
        return normalized.startswith("error") or normalized.startswith("tool error")

    @staticmethod
    def _trim_tool_result(result: str, max_chars: int = 900) -> str:
        result = result.strip()
        if len(result) <= max_chars:
            return result
        return result[: max_chars - 3].rstrip() + "..."

    def _is_missing_final_response(self, content: str | None) -> bool:
        """Treat empty or placeholder assistant text as missing final output."""
        return self.is_empty_response(content) or is_placeholder_assistant_reply(content)

    @staticmethod
    def _build_successful_write_fallback(tool_name: str, tool_result: str) -> str | None:
        """Turn successful write-style tool results into user-facing final replies."""
        result = tool_result.strip()
        if not result:
            return None

        simple_prefixes = {
            "write_task": ("Task saved:",),
            "write_goal": ("Goal saved:",),
            "write_fact": ("Fact saved:", "Fact updated:", "Fact already covered:"),
            "write_decision": ("Decision saved:",),
            "write_reflection": ("Reflection saved:",),
        }
        valid_prefixes = simple_prefixes.get(tool_name, ())
        if valid_prefixes and any(result.startswith(prefix) for prefix in valid_prefixes):
            return f"Done — {result}"
        if tool_name == "knowledge_ingest" and result.startswith("Knowledge item ingested:"):
            lines = [line.strip() for line in result.splitlines() if line.strip()]
            path_line = lines[0].replace("Knowledge item ingested:", "Saved to").strip()
            title_line = next((line for line in lines[1:] if line.startswith("Title:")), None)
            category_line = next((line for line in lines[1:] if line.startswith("Category:")), None)
            parts = ["Done — saved the knowledge note.", path_line]
            if title_line:
                parts.append(title_line)
            if category_line:
                parts.append(category_line)
            return "\n".join(parts)
        if tool_name == "list_add_items" and result.startswith("Checklist updated:"):
            return f"Done — updated the checklist.\n{result}"
        if tool_name == "list_set_item_status" and result.startswith("Updated checklist:"):
            return f"Done — updated the checklist item status.\n{result}"
        if tool_name == "list_remove_items" and result.startswith("Updated checklist:"):
            return f"Done — removed item(s) from the checklist.\n{result}"
        if tool_name == "list_delete" and result.startswith("Deleted checklist:"):
            return f"Done — deleted the checklist.\n{result}"
        return None

    def _build_tool_result_fallback(
        self,
        *,
        request_text: str | None,
        tool_results: list[tuple[str, str]],
    ) -> str | None:
        usable = [
            (name, result)
            for name, result in tool_results
            if not self._is_tool_error_result(result)
        ]
        if not usable:
            return None

        latest_name, latest_result = usable[-1]
        request_snippet = None
        if isinstance(request_text, str) and request_text.strip():
            request_snippet = " ".join(request_text.strip().split())

        if write_fallback := self._build_successful_write_fallback(latest_name, latest_result):
            return write_fallback

        if latest_name in {"read_memory", "search_memory"}:
            prefix = "I checked memory"
        elif latest_name == "list_dir":
            prefix = "I checked the directory contents"
        elif latest_name == "read_file":
            prefix = "I checked the file"
        elif latest_name == "exec":
            prefix = "I ran the requested command"
        else:
            prefix = f"I completed the `{latest_name}` step"

        if request_snippet:
            prefix += f" for: {request_snippet}"

        if len(usable) == 1:
            return f"{prefix}, but the model stopped before writing the final answer.\n\n{self._trim_tool_result(latest_result)}"

        lines = [
            f"{prefix}, but the model stopped before writing the final answer.",
            "",
            "Here are the latest grounded tool results:",
        ]
        for name, result in usable[-3:]:
            lines.append(f"- `{name}`: {self._trim_tool_result(result, max_chars=280)}")
        return "\n".join(lines)

    @staticmethod
    def _derive_action_request_text(
        messages: list[dict[str, Any]], current_request: str | None
    ) -> str | None:
        """Resolve the strongest recent actionable request for this turn."""
        if isinstance(current_request, str) and has_structured_payload(current_request):
            return current_request

        if not (isinstance(current_request, str) and is_short_follow_up(current_request)):
            return current_request

        seen_current = False
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            if not seen_current and content == current_request:
                seen_current = True
                continue
            if has_structured_payload(content):
                return content
        return current_request

    @staticmethod
    def _request_has_structural_action_signal(request_text: str | None) -> bool:
        """Detect requests that strongly imply tool-backed action without language markers."""
        if not isinstance(request_text, str):
            return False
        text = request_text.strip()
        if not text:
            return False

        normalized = " ".join(text.split())
        if normalized.endswith("?"):
            return False
        return has_structured_payload(text)

    @staticmethod
    def _response_looks_like_blocker_or_final(content: str | None) -> bool:
        """Allow obvious blockers or substantive final answers through unchanged."""
        text = (content or "").strip()
        if not text:
            return False
        if text.endswith("?"):
            return True
        if len(text) >= 220:
            return True
        if text.count("\n") >= 3:
            return True
        if any(token in text for token in ("```", "- ", "1. ", "2. ", ":\n")):
            return True
        return False

    def _should_reprompt_incomplete_non_tool_response(
        self,
        *,
        current_request: str | None,
        assistant_content: str | None,
        tools_used: list[str],
    ) -> bool:
        """Detect structurally incomplete non-tool replies without an extra model call."""
        if tools_used:
            return False
        if not self.tools.get_definitions():
            return False
        if not self._request_has_structural_action_signal(current_request):
            return False
        if self._response_looks_like_blocker_or_final(assistant_content):
            return False

        text = " ".join((assistant_content or "").strip().split())
        return bool(text) and len(text) <= 160

    def _used_workspace_write_tool(self, tools_used: list[str]) -> bool:
        for tool_name in tools_used:
            metadata = self.tools.get_metadata(tool_name)
            if metadata and metadata.permission_level == ToolPermissionLevel.WORKSPACE_WRITE:
                return True
        return False

    def _should_reprompt_incomplete_post_tool_response(
        self,
        *,
        current_request: str | None,
        assistant_content: str | None,
        tools_used: list[str],
    ) -> bool:
        """Reject post-tool status text when an actionable turn still lacks mutation."""
        if not tools_used:
            return False
        if not self.tools.get_definitions():
            return False
        if not self._request_has_structural_action_signal(current_request):
            return False
        if self._used_workspace_write_tool(tools_used):
            return False
        if self._response_looks_like_blocker_or_final(assistant_content):
            return False

        text = " ".join((assistant_content or "").strip().split())
        return bool(text) and len(text) <= 160

    def _build_empty_non_tool_reprompt(self, current_request: str | None) -> str:
        """Ask the model to continue without forcing an extra classifier call."""
        if self.tools.get_definitions():
            return (
                "You must complete the user's request in this turn. If tools are needed, call them now. "
                "Do not return an empty reply. If the task is already complete, give the final result. "
                "If something is missing or blocked, briefly say exactly what is missing."
            )
        return (
            "You must answer the user's request directly in this turn. Do not return an empty reply. "
            "If you cannot comply, briefly say what is missing or blocking you."
        )

    @staticmethod
    def _build_empty_response_fallback(
        request_text: str | None,
        *,
        finish_reason: str | None,
    ) -> str:
        """Create a user-facing fallback when the model returns no usable text and no tools."""
        request_snippet = None
        if isinstance(request_text, str) and request_text.strip():
            request_snippet = " ".join(request_text.strip().split())
        finish_label = finish_reason or "unknown"
        if request_snippet:
            return (
                "The model returned an empty reply before answering your request"
                f" about: {request_snippet}\n\n"
                f"Finish reason: {finish_label}. Please retry the request or switch to a stronger model."
            )
        return (
            "The model returned an empty reply before answering. "
            f"Finish reason: {finish_label}. Please retry the request or switch to a stronger model."
        )

    async def _classify_memory_authority_requirement(
        self,
        *,
        current_request: str | None,
        assistant_content: str | None,
        model: str,
        job_class: Any,
    ) -> bool:
        """Use model judgment for ambiguous memory-write intents instead of language markers."""
        if not isinstance(current_request, str):
            return False
        request = " ".join(current_request.strip().split())
        reply = " ".join((assistant_content or "").strip().split())
        if not request or not reply:
            return False
        if request.endswith("?"):
            return False
        if has_structured_payload(request):
            return False
        if len(request) < 20 or len(request) > 400:
            return False

        prompt = (
            "Decide whether the assistant must use authoritative memory tools before answering.\n"
            "Return YES or NO only.\n\n"
            "Answer YES when the user is asking to store, update, or confirm durable personal/project "
            "memory, a lasting preference, or a fact that should persist across later turns or sessions. "
            "If the user is telling the assistant to remember something for the future, answer YES even "
            "if the information may already exist.\n"
            "Answer YES when the user is correcting, replacing, or fixing something previously said or "
            "previously stored, because the assistant should check memory before claiming the fact is missing.\n"
            "Answer NO for normal conversation, emotional support, transient chat, or requests that "
            "do not need durable memory authority.\n\n"
            "Examples:\n"
            "- YES: a request equivalent to 'remember this preference for later'\n"
            "- YES: a request equivalent to 'store this fact in memory'\n"
            "- YES: a request equivalent to 'fix what I told you before' or 'update that preference'\n"
            "- NO: ordinary conversation about preferences without asking for memory action\n"
            "- NO: casual chat or emotional support\n\n"
            f"User request: {request}\n"
            f"Assistant reply: {reply}"
        )

        try:
            response = await self.chat_callable(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                model=model,
                temperature=0.0,
                max_tokens=8,
                job_class=job_class,
                reasoning_effort="none",
            )
        except Exception as exc:
            logger.debug("Memory-authority classifier failed: {}", exc)
            return False

        answer = (response.content or "").strip().lower()
        return answer.startswith("yes")

    def _append_missing_tool_results(
        self,
        messages: list[dict[str, Any]],
        pending_tool_calls: list[Any],
        *,
        tool_results: list[tuple[str, str]],
        result_text: str,
    ) -> list[dict[str, Any]]:
        """Backfill missing tool results so saved history stays protocol-valid."""
        repaired = messages
        for tool_call in pending_tool_calls:
            repaired = self.context.add_tool_result(
                repaired,
                tool_call.id,
                tool_call.name,
                result_text,
            )
            tool_results.append((tool_call.name, result_text))
        return repaired

    def _append_final_assistant_message(
        self,
        messages: list[dict[str, Any]],
        final_content: str | None,
    ) -> list[dict[str, Any]]:
        if final_content is None:
            return messages
        if messages:
            last = messages[-1]
            if (
                last.get("role") == "assistant"
                and last.get("content") == final_content
                and not last.get("tool_calls")
            ):
                return messages
        return self.context.add_assistant_message(messages, final_content)

    async def _consume_streaming_response(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        started_at: float,
        on_progress: Callable[..., Awaitable[None]] | None,
        job_class: Any,
    ) -> LLMResponse:
        """Consume typed provider events into a normalized LLMResponse."""
        assert self.stream_chat_callable is not None

        stream = self.stream_chat_callable(
            messages=messages,
            tools=self.tools.get_definitions(),
            model=model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            job_class=job_class,
            reasoning_effort=self.config.reasoning_effort,
        )
        response_text_parts: list[str] = []
        tool_calls: list[Any] = []
        usage: dict[str, int] = {}
        reasoning_content: str | None = None
        finish_reason = "stop"
        heartbeats_sent = 0
        pending_event: asyncio.Task[Any] | None = None

        while True:
            remaining = self._remaining_seconds(started_at)
            if remaining <= 0:
                if pending_event is not None and not pending_event.done():
                    pending_event.cancel()
                    try:
                        await pending_event
                    except (asyncio.CancelledError, StopAsyncIteration):
                        pass
                await stream.aclose()
                raise TimeoutError("turn exceeded max_loop_seconds while streaming provider output")

            try:
                if pending_event is None:
                    pending_event = asyncio.create_task(stream.__anext__())
                done, _ = await asyncio.wait(
                    {pending_event},
                    timeout=min(self.PROGRESS_HEARTBEAT_SECONDS, max(0.01, remaining)),
                )
                if not done:
                    raise asyncio.TimeoutError
                event = pending_event.result()
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                if on_progress and heartbeats_sent < self.MAX_IDENTICAL_HEARTBEATS_PER_WAIT:
                    heartbeats_sent += 1
                continue
            finally:
                if pending_event is not None and pending_event.done():
                    pending_event = None

            heartbeats_sent = 0
            if isinstance(event, TextDeltaEvent):
                if event.delta:
                    response_text_parts.append(event.delta)
                continue
            if isinstance(event, ToolCallEvent):
                tool_calls.append(event.tool_call)
                continue
            if isinstance(event, ResponseDoneEvent):
                finish_reason = event.finish_reason or finish_reason
                usage = event.usage or usage
                reasoning_content = event.reasoning_content or reasoning_content

        return LLMResponse(
            content="".join(response_text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content=reasoning_content,
        )

    async def run(
        self,
        initial_messages: list[dict[str, Any]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        job_class: Any = None,
    ) -> TurnResult:
        """Execute the interactive tool loop for a single agent turn."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        tool_results: list[tuple[str, str]] = []
        outcome = TurnOutcome.COMPLETED
        started_at = time.monotonic()
        last_tool_signature: str | None = None
        repeated_tool_cycles = 0
        intent_reprompt_count = 0
        empty_response_reprompt_count = 0
        intermediate_ack_reprompt_count = 0
        memory_authority_reprompt_count = 0
        post_tool_repair_attempted = False
        current_request = next(
            (
                msg.get("content")
                for msg in reversed(initial_messages)
                if msg.get("role") == "user" and isinstance(msg.get("content"), str)
            ),
            None,
        )
        action_request_text = self._derive_action_request_text(initial_messages, current_request)

        model = self.get_model_for_job(job_class)
        if model is None:
            return TurnResult(None, [], [], TurnOutcome.EMPTY_REPLY)

        job_name = getattr(job_class, "value", str(job_class or "unknown"))

        while iteration < self.config.max_iterations:
            if time.monotonic() - started_at > self.config.max_loop_seconds:
                logger.warning("Max loop time reached ({}s)", self.config.max_loop_seconds)
                final_content = (
                    f"I hit the time limit for this response ({self.config.max_loop_seconds}s) "
                    "before completing all tool calls. Try a smaller step."
                )
                outcome = TurnOutcome.TIMEOUT
                break

            iteration += 1
            logger.info(
                "Agent loop iteration {}/{} started (job={}, model={})",
                iteration,
                self.config.max_iterations,
                job_name,
                model,
            )

            try:
                if self.stream_chat_callable is not None:
                    try:
                        response = await self._consume_streaming_response(
                            messages=messages,
                            model=model,
                            started_at=started_at,
                            on_progress=on_progress,
                            job_class=job_class,
                        )
                    except TimeoutError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Provider streaming failed; falling back to non-streaming chat: {}",
                            exc,
                        )
                        response = await self._await_with_progress(
                            self.chat_callable(
                                messages=messages,
                                tools=self.tools.get_definitions(),
                                model=model,
                                temperature=self.config.temperature,
                                max_tokens=self.config.max_tokens,
                                job_class=job_class,
                                reasoning_effort=self.config.reasoning_effort,
                            ),
                            started_at=started_at,
                            on_progress=on_progress,
                            waiting_message="",
                        )
                else:
                    response = await self._await_with_progress(
                        self.chat_callable(
                            messages=messages,
                            tools=self.tools.get_definitions(),
                            model=model,
                            temperature=self.config.temperature,
                            max_tokens=self.config.max_tokens,
                            job_class=job_class,
                            reasoning_effort=self.config.reasoning_effort,
                        ),
                        started_at=started_at,
                        on_progress=on_progress,
                        waiting_message="",
                    )
            except TimeoutError:
                logger.warning("Max loop time reached ({}s)", self.config.max_loop_seconds)
                final_content = (
                    f"I hit the time limit for this response ({self.config.max_loop_seconds}s) "
                    "before completing all tool calls. Try a smaller step."
                )
                outcome = TurnOutcome.TIMEOUT
                break
            response.tool_calls = normalize_tool_calls(response.tool_calls)
            logger.info(
                "LLM response received (job={}, finish_reason={}, content_chars={}, tool_calls={})",
                job_name,
                response.finish_reason,
                len(response.content or ""),
                len(response.tool_calls),
            )
            if not response.has_tool_calls:
                content, inline_tool_calls = coerce_inline_tool_calls(
                    response.content, self.tools.has
                )
                if inline_tool_calls:
                    logger.warning(
                        "Recovered {} inline tool call(s) from assistant text in iteration {}",
                        len(inline_tool_calls),
                        iteration,
                    )
                    response.content = content
                    response.tool_calls = inline_tool_calls

            if response.has_tool_calls:
                tool_signature = self._tool_cycle_signature(response.tool_calls)
                if tool_signature == last_tool_signature:
                    repeated_tool_cycles += 1
                else:
                    repeated_tool_cycles = 1
                    last_tool_signature = tool_signature

                if repeated_tool_cycles >= self.config.max_identical_tool_cycles:
                    logger.warning(
                        "Breaking repeated tool cycle after {} identical iterations",
                        repeated_tool_cycles,
                    )
                    final_content = (
                        "I detected repeated tool calls without progress and stopped to avoid a loop. "
                        "Please refine the request or provide more constraints."
                    )
                    outcome = TurnOutcome.REPEATED_TOOL_CYCLE
                    break

                if on_progress:
                    clean = self.strip_think(response.content)
                    if clean:
                        await on_progress(clean)
                    await on_progress(self.tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                logger.info(
                    "Assistant tool-call turn appended (iteration={}, tool_names={})",
                    iteration,
                    [tc.name for tc in response.tool_calls],
                )

                spawned_result: str | None = None
                missing_tool_result_text: str | None = None
                executed_count = 0
                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    try:
                        result = await self._await_with_progress(
                            self.tools.execute(tool_call.name, tool_call.arguments),
                            started_at=started_at,
                            on_progress=on_progress,
                            waiting_message=(
                                f"Still working on `{tool_call.name}`."
                                if tool_call.name != "spawn"
                                else "Still coordinating the delegated task."
                            ),
                        )
                        logger.info(
                            "Tool completed: {} -> {} chars",
                            tool_call.name,
                            len(result) if isinstance(result, str) else 0,
                        )
                    except TimeoutError:
                        logger.warning(
                            "Max loop time reached ({}s) while executing {}",
                            self.config.max_loop_seconds,
                            tool_call.name,
                        )
                        final_content = (
                            f"I hit the time limit for this response ({self.config.max_loop_seconds}s) "
                            "before completing all tool calls. Try a smaller step."
                        )
                        missing_tool_result_text = (
                            "Tool error: execution stopped because the turn time limit was reached "
                            "before this tool completed."
                        )
                        break
                    except Exception as exc:
                        logger.error("Tool execution failed: {}: {}", tool_call.name, exc)
                        result = f"Tool error: {type(exc).__name__}: {exc}"
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    tool_results.append((tool_call.name, result))
                    executed_count += 1
                    if tool_call.name == "spawn" and spawned_result is None:
                        spawned_result = result

                if spawned_result is not None:
                    final_content = spawned_result
                    outcome = TurnOutcome.DELEGATED
                    logger.info("Returning immediately after spawn to keep main agent responsive")
                    break
                if final_content is not None:
                    if executed_count < len(response.tool_calls):
                        pending_tool_calls = response.tool_calls[executed_count:]
                        messages = self._append_missing_tool_results(
                            messages,
                            pending_tool_calls,
                            tool_results=tool_results,
                            result_text=missing_tool_result_text
                            or "Tool error: execution stopped before this tool could run.",
                        )
                    if outcome == TurnOutcome.COMPLETED:
                        outcome = TurnOutcome.TIMEOUT
                    break
                post_tool_repair_attempted = False
                continue

            repeated_tool_cycles = 0
            last_tool_signature = None
            final_content = self.strip_think(response.content)
            if not tools_used and self._is_missing_final_response(final_content):
                empty_response_reprompt_count += 1
                logger.warning(
                    "Empty response without tool usage; reprompting model (attempt {}, finish_reason={})",
                    empty_response_reprompt_count,
                    response.finish_reason,
                )
                if empty_response_reprompt_count < 2:
                    messages.append(
                        {
                            "role": "system",
                            "content": self._build_empty_non_tool_reprompt(current_request),
                        }
                    )
                    final_content = None
                    continue

                final_content = self._build_empty_response_fallback(
                    current_request,
                    finish_reason=response.finish_reason,
                )
                outcome = TurnOutcome.EMPTY_REPLY
                break

            if self._should_reprompt_incomplete_non_tool_response(
                current_request=action_request_text,
                assistant_content=final_content,
                tools_used=tools_used,
            ):
                intermediate_ack_reprompt_count += 1
                logger.warning(
                    "Detected incomplete non-tool response; reprompting model "
                    "(attempt {}, finish_reason={})",
                    intermediate_ack_reprompt_count,
                    response.finish_reason,
                )
                messages = self.context.add_assistant_message(messages, final_content)
                if intermediate_ack_reprompt_count < self.INTERMEDIATE_ACK_MAX_RETRIES:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Continue now. Execute the required tool calls and only send your "
                                "final answer after completing the task. Do not stop with an "
                                "intermediate acknowledgement or status update."
                            ),
                        }
                    )
                    final_content = None
                    continue

                final_content = (
                    "The model kept acknowledging the request without actually doing the work. "
                    "Please retry this request or switch to a stronger tool-calling model."
                )
                outcome = TurnOutcome.INCOMPLETE_ACTION
                break

            if not tools_used:
                requires_memory_authority = await self._classify_memory_authority_requirement(
                    current_request=current_request,
                    assistant_content=final_content,
                    model=model,
                    job_class=job_class,
                )
                if requires_memory_authority:
                    memory_authority_reprompt_count += 1
                    logger.warning(
                        "Detected ambiguous memory-write request without authoritative tools; reprompting model (attempt {})",
                        memory_authority_reprompt_count,
                    )
                    messages = self.context.add_assistant_message(messages, final_content)
                    if memory_authority_reprompt_count < 2:
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "This reply is not authoritative enough yet. If the user asked "
                                    "to save, update, correct, or confirm durable memory, use the typed "
                                    "memory tools before answering definitively. If this is about "
                                    "previously stored information, check memory before claiming "
                                    "the fact is missing. If no memory tool is actually needed, "
                                    "answer directly and explain why."
                                ),
                            }
                        )
                        final_content = None
                        continue

                    final_content = (
                        "I could not verify or update that memory authoritatively in this turn. "
                        "Please retry this request or use a stronger model."
                    )
                    outcome = TurnOutcome.INCOMPLETE_ACTION
                    break

            needs_reprompt = tools_used and self._is_missing_final_response(final_content)
            failure_type = "empty_post_tool_response"
            if not needs_reprompt and self._should_reprompt_incomplete_post_tool_response(
                current_request=action_request_text,
                assistant_content=final_content,
                tools_used=tools_used,
            ):
                needs_reprompt = True
                failure_type = "incomplete_post_tool_response"
            if needs_reprompt:
                intent_reprompt_count += 1
                logger.warning(
                    "Non-final response after tool usage; reprompting model (attempt {}, type={}, finish_reason={})",
                    intent_reprompt_count,
                    failure_type,
                    response.finish_reason,
                )
                if not post_tool_repair_attempted:
                    post_tool_repair_attempted = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Final answer required now. You already have the tool results. "
                                "Do not stop with an empty reply or an intermediate status update. "
                                "If the task is not complete yet, continue with the next required tool "
                                "calls now. Either reply with the actual completed result, or explain "
                                "exactly what is missing in one concise answer."
                            ),
                        }
                    )
                    final_content = None
                    continue

                tool_result_fallback = self._build_tool_result_fallback(
                    request_text=current_request,
                    tool_results=tool_results,
                )
                if tool_result_fallback:
                    logger.warning(
                        "Recovered post-tool turn with deterministic fallback (type={}, tools={})",
                        failure_type,
                        [name for name, _ in tool_results[-3:]],
                    )
                    final_content = tool_result_fallback
                    outcome = TurnOutcome.TOOL_FALLBACK
                    break

                logger.warning("Stopping after repeated non-final responses post-tool usage")
                final_content = (
                    "I completed the tool work, but the model stopped before producing a usable "
                    f"final answer ({failure_type}). Please retry this request or switch to a "
                    "stronger tool-calling model."
                )
                outcome = TurnOutcome.INCOMPLETE_ACTION
                break

            logger.info(
                "Agent loop completed without tool calls at iteration {} (final_chars={})",
                iteration,
                len(final_content or ""),
            )
            break

        if final_content is None and iteration >= self.config.max_iterations:
            logger.warning("Max iterations ({}) reached", self.config.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.config.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )
            outcome = TurnOutcome.MAX_ITERATIONS

        logger.info(
            "Agent loop finished (job={}, iterations={}, tools_used={}, final_chars={})",
            job_name,
            iteration,
            tools_used,
            len(final_content or ""),
        )
        messages = self._append_final_assistant_message(messages, final_content)
        return TurnResult(final_content, tools_used, messages, outcome)
