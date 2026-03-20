"""Active session timeout service for idle-triggered cognition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger


class SessionTimeoutService:
    """Periodically checks for idle sessions and finalizes expired ones."""

    def __init__(
        self,
        on_check: Callable[[], Awaitable[int]],
        *,
        interval_s: int = 60,
        enabled: bool = True,
    ) -> None:
        self.on_check = on_check
        self.interval_s = max(1, interval_s)
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the timeout monitor."""
        if not self.enabled:
            logger.info("Session timeout monitor disabled")
            return
        if self._running:
            logger.warning("Session timeout monitor already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="session-timeout-monitor")
        logger.info("Session timeout monitor started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """Stop the timeout monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if not self._running:
                    break
                expired = await self.on_check()
                if expired:
                    logger.info("Session timeout monitor finalized {} expired session(s)", expired)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Session timeout monitor error: {}", exc)
