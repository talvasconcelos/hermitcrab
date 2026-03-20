"""Tracked background task utilities for the agent loop."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable

from loguru import logger


class BackgroundTaskTracker:
    """Track fire-and-forget tasks so shutdown and waiting stay deterministic."""

    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[Any]] = set()

    def schedule(self, coro: Awaitable[Any], task_name: str) -> None:
        async def _wrapped() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                logger.debug("Background task cancelled: {}", task_name)
            except Exception as exc:
                logger.warning("Background task failed (non-fatal): {}: {}", task_name, exc)
            finally:
                self.tasks.discard(asyncio.current_task())

        task = asyncio.create_task(_wrapped(), name=task_name)
        self.tasks.add(task)

    async def shutdown(self) -> None:
        if not self.tasks:
            return
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.tasks.clear()

    async def wait(self, timeout_s: float = 5.0) -> tuple[int, int]:
        if not self.tasks:
            return 0, 0

        active = [task for task in self.tasks if not task.done()]
        if not active:
            return 0, 0

        done, pending = await asyncio.wait(active, timeout=max(0.0, timeout_s))
        if pending:
            logger.warning(
                "Background tasks still running after {:.1f}s: {} pending",
                timeout_s,
                len(pending),
            )
        return len(done), len(pending)
