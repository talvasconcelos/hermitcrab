"""Interactive turn execution for the main agent loop."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from loguru import logger

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.agent.tool_call_recovery import coerce_inline_tool_calls, normalize_tool_calls
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


class TurnRunner:
    """Run one assistant turn from prompt assembly through tool execution."""

    PROGRESS_HEARTBEAT_SECONDS = 15.0
    MAX_IDENTICAL_HEARTBEATS_PER_WAIT = 1

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

    @staticmethod
    def _build_successful_write_fallback(tool_name: str, tool_result: str) -> str | None:
        """Turn successful write-style tool results into user-facing final replies."""
        result = tool_result.strip()
        if not result:
            return None

        if tool_name == "write_task" and result.startswith("Task saved:"):
            return f"Done — {result}"
        if tool_name == "write_goal" and result.startswith("Goal saved:"):
            return f"Done — {result}"
        if tool_name == "write_fact" and result.startswith("Fact saved:"):
            return f"Done — {result}"
        if tool_name == "write_decision" and result.startswith("Decision saved:"):
            return f"Done — {result}"
        if tool_name == "write_reflection" and result.startswith("Reflection saved:"):
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

        while True:
            remaining = self._remaining_seconds(started_at)
            if remaining <= 0:
                await stream.aclose()
                raise TimeoutError("turn exceeded max_loop_seconds while streaming provider output")

            try:
                event = await asyncio.wait_for(
                    stream.__anext__(),
                    timeout=min(self.PROGRESS_HEARTBEAT_SECONDS, max(0.01, remaining)),
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                if on_progress and heartbeats_sent < self.MAX_IDENTICAL_HEARTBEATS_PER_WAIT:
                    await on_progress("Still working on the next step.")
                    heartbeats_sent += 1
                continue

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
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Execute the interactive tool loop for a single agent turn."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        tool_results: list[tuple[str, str]] = []
        started_at = time.monotonic()
        last_tool_signature: str | None = None
        repeated_tool_cycles = 0
        intent_reprompt_count = 0
        post_tool_repair_attempted = False
        current_request = next(
            (
                msg.get("content")
                for msg in reversed(initial_messages)
                if msg.get("role") == "user" and isinstance(msg.get("content"), str)
            ),
            None,
        )

        model = self.get_model_for_job(job_class)
        if model is None:
            return None, [], []

        job_name = getattr(job_class, "value", str(job_class or "unknown"))

        while iteration < self.config.max_iterations:
            if time.monotonic() - started_at > self.config.max_loop_seconds:
                logger.warning("Max loop time reached ({}s)", self.config.max_loop_seconds)
                final_content = (
                    f"I hit the time limit for this response ({self.config.max_loop_seconds}s) "
                    "before completing all tool calls. Try a smaller step."
                )
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
                            waiting_message="Still working on the next step.",
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
                        waiting_message="Still working on the next step.",
                    )
            except TimeoutError:
                logger.warning("Max loop time reached ({}s)", self.config.max_loop_seconds)
                final_content = (
                    f"I hit the time limit for this response ({self.config.max_loop_seconds}s) "
                    "before completing all tool calls. Try a smaller step."
                )
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
                        break
                    except Exception as exc:
                        logger.error("Tool execution failed: {}: {}", tool_call.name, exc)
                        result = f"Tool error: {type(exc).__name__}: {exc}"
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    tool_results.append((tool_call.name, result))
                    if tool_call.name == "spawn" and spawned_result is None:
                        spawned_result = result

                if spawned_result is not None:
                    final_content = spawned_result
                    logger.info("Returning immediately after spawn to keep main agent responsive")
                    break
                if final_content is not None:
                    break
                post_tool_repair_attempted = False
                continue

            repeated_tool_cycles = 0
            last_tool_signature = None
            final_content = self.strip_think(response.content)
            needs_reprompt = tools_used and self.is_empty_response(final_content)
            if needs_reprompt:
                intent_reprompt_count += 1
                failure_type = "empty_post_tool_response"
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
                                "Do not stop with an empty reply. Do not call more tools unless the "
                                "tool results clearly show something is missing. Either reply to the "
                                "user with the actual result, or explain exactly what is missing in "
                                "one concise answer."
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
                    break

                logger.warning("Stopping after repeated non-final responses post-tool usage")
                final_content = (
                    "I completed the tool work, but the model stopped before producing a usable "
                    f"final answer ({failure_type}). Please retry this request or switch to a "
                    "stronger tool-calling model."
                )
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

        logger.info(
            "Agent loop finished (job={}, iterations={}, tools_used={}, final_chars={})",
            job_name,
            iteration,
            tools_used,
            len(final_content or ""),
        )
        messages = self._append_final_assistant_message(messages, final_content)
        return final_content, tools_used, messages
