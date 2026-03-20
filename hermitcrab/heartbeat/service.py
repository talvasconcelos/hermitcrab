"""Heartbeat service - periodic agent wake-up to check for tasks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

if TYPE_CHECKING:
    from hermitcrab.providers.base import LLMProvider

_HEARTBEAT_DISABLED_MARKER = "HEARTBEAT_DISABLED"
_HEARTBEAT_DIRECT_MARKER = "HEARTBEAT_DIRECT"

_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of active tasks (required for run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


class HeartbeatService:
    """
    Periodic heartbeat service that wakes the agent to check for tasks.

    Phase 1 (decision): reads HEARTBEAT.md and asks the LLM — via a virtual
    tool call — whether there are active tasks.  This avoids free-text parsing
    and the unreliable HEARTBEAT_OK token.

    Phase 2 (execution): only triggered when Phase 1 returns ``run``.  The
    ``on_execute`` callback runs the task through the full agent loop and
    returns the result to deliver.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        on_execute: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        interval_s: int = 30 * 60,
        enabled: bool = True,
    ):
        self.workspace = workspace
        self.provider = provider
        self.model = model
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def _is_heartbeat_disabled(self, content: str) -> bool:
        """Check if heartbeat is disabled via marker comment.

        Looks for <!-- HEARTBEAT_DISABLED --> at the top of the file.
        This allows users to disable heartbeat without deleting the file.
        """
        if not content:
            return True  # Empty file = disabled

        # Check for disable marker in first 500 chars (should be at top)
        preview = content[:500].upper()
        return _HEARTBEAT_DISABLED_MARKER in preview

    def _should_bypass_llm(self, content: str) -> bool:
        """Check if heartbeat should execute directly without an LLM call."""
        if not content:
            return False
        return _HEARTBEAT_DIRECT_MARKER in content[:500].upper()

    def _extract_active_tasks(self, content: str) -> str:
        """Extract non-comment task lines from the Active Tasks section."""
        lines = content.splitlines()
        in_active_tasks = False
        task_lines: list[str] = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("## "):
                if in_active_tasks:
                    break
                in_active_tasks = stripped.lower() == "## active tasks"
                continue

            if not in_active_tasks:
                continue

            if not stripped or stripped.startswith("<!--"):
                continue

            task_lines.append(line.rstrip())

        return "\n".join(task_lines).strip()

    def _parse_heartbeat_tool_args(self, arguments: Any) -> dict[str, Any]:
        """Normalize tool call arguments from providers that return JSON strings."""
        if isinstance(arguments, dict):
            return arguments

        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                logger.warning("Heartbeat: invalid tool arguments, defaulting to skip")
                return {}
            if isinstance(parsed, dict):
                return parsed

        logger.warning("Heartbeat: unexpected tool arguments type {}, defaulting to skip", type(arguments).__name__)
        return {}

    def _normalize_decision(self, arguments: Any) -> tuple[str, str]:
        """Return a safe heartbeat decision tuple from provider tool arguments."""
        args = self._parse_heartbeat_tool_args(arguments)

        action = args.get("action", "skip")
        if action not in {"skip", "run"}:
            logger.warning("Heartbeat: invalid action {!r}, defaulting to skip", action)
            action = "skip"

        tasks = args.get("tasks", "")
        if not isinstance(tasks, str):
            logger.warning("Heartbeat: invalid tasks type {}, defaulting to empty string", type(tasks).__name__)
            tasks = ""

        return action, tasks

    async def _decide(self, content: str) -> tuple[str, str]:
        """Phase 1: ask LLM to decide skip/run via virtual tool call.

        Returns (action, tasks) where action is 'skip' or 'run'.
        """
        response = await self.provider.chat(
            messages=[
                {"role": "system", "content": "You are a heartbeat agent. Call the heartbeat tool to report your decision."},
                {"role": "user", "content": (
                    "Review the following HEARTBEAT.md and decide whether there are active tasks.\n\n"
                    f"{content}"
                )},
            ],
            tools=_HEARTBEAT_TOOL,
            model=self.model,
        )

        if not response.has_tool_calls:
            return "skip", ""

        return self._normalize_decision(response.tool_calls[0].arguments)

    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)

    async def _tick(self) -> None:
        """Execute a single heartbeat tick."""
        content = self._read_heartbeat_file()

        # Check for disable marker first (avoids LLM call)
        if self._is_heartbeat_disabled(content):
            logger.debug("Heartbeat: disabled via marker or empty file")
            return

        if self._should_bypass_llm(content):
            tasks = self._extract_active_tasks(content)
            if not tasks:
                logger.info("Heartbeat: direct mode enabled, but no active tasks found")
                return
            logger.info("Heartbeat: direct mode enabled, executing without LLM")
            if self.on_execute:
                response = await self.on_execute(tasks)
                if response and self.on_notify:
                    logger.info("Heartbeat: completed, delivering response")
                    await self.on_notify(response)
            return

        logger.info("Heartbeat: checking for tasks...")

        try:
            action, tasks = await self._decide(content)

            if action != "run":
                logger.info("Heartbeat: OK (nothing to report)")
                return

            logger.info("Heartbeat: tasks found, executing...")
            if self.on_execute:
                response = await self.on_execute(tasks)
                if response and self.on_notify:
                    logger.info("Heartbeat: completed, delivering response")
                    await self.on_notify(response)
        except Exception:
            logger.exception("Heartbeat execution failed")

    async def trigger_now(self) -> str | None:
        """Manually trigger a heartbeat."""
        content = self._read_heartbeat_file()

        # Check for disable marker first (avoids LLM call)
        if self._is_heartbeat_disabled(content):
            logger.debug("Heartbeat: disabled via marker or empty file")
            return None

        if self._should_bypass_llm(content):
            tasks = self._extract_active_tasks(content)
            if not tasks or not self.on_execute:
                return None
            return await self.on_execute(tasks)

        action, tasks = await self._decide(content)
        if action != "run" or not self.on_execute:
            return None
        return await self.on_execute(tasks)
