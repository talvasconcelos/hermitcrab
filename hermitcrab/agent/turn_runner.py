"""Interactive turn execution for the main agent loop."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger

from hermitcrab.agent.context import ContextBuilder
from hermitcrab.agent.tool_call_recovery import coerce_inline_tool_calls, normalize_tool_calls
from hermitcrab.agent.tools.registry import ToolRegistry


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
        get_model_for_job: Callable[[Any], str | None],
        strip_think: Callable[[str | None], str | None],
        tool_hint: Callable[[list[Any]], str],
        is_empty_response: Callable[[str | None], bool],
        is_intent_only_response: Callable[[str | None], bool],
    ):
        self.context = context
        self.tools = tools
        self.config = config
        self.chat_callable = chat_callable
        self.get_model_for_job = get_model_for_job
        self.strip_think = strip_think
        self.tool_hint = tool_hint
        self.is_empty_response = is_empty_response
        self.is_intent_only_response = is_intent_only_response

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
        started_at = time.monotonic()
        last_tool_signature: str | None = None
        repeated_tool_cycles = 0
        intent_reprompt_count = 0

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
                    if tool_call.name == "spawn" and spawned_result is None:
                        spawned_result = result

                if spawned_result is not None:
                    final_content = spawned_result
                    logger.info("Returning immediately after spawn to keep main agent responsive")
                    break
                if final_content is not None:
                    break
                continue

            repeated_tool_cycles = 0
            last_tool_signature = None
            final_content = self.strip_think(response.content)
            needs_reprompt = tools_used and (
                self.is_empty_response(final_content) or self.is_intent_only_response(final_content)
            )
            if needs_reprompt:
                intent_reprompt_count += 1
                logger.warning(
                    "Non-final response after tool usage; reprompting model (attempt {}, empty={}, intent_only={})",
                    intent_reprompt_count,
                    self.is_empty_response(final_content),
                    self.is_intent_only_response(final_content),
                )
                if intent_reprompt_count >= 2:
                    logger.warning("Stopping after repeated non-final responses post-tool usage")
                    final_content = (
                        "I checked the available context, but the model kept stopping without a "
                        "usable answer after tool calls. Please retry this request or switch to "
                        "a stronger tool-calling model."
                    )
                    break

                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Do not stop with an empty reply or an intention statement. "
                            "You already used tools. Either call the next tool now, or reply "
                            "with the actual result for the user."
                        ),
                    }
                )
                final_content = None
                continue

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
        return final_content, tools_used, messages
