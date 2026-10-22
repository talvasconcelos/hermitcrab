"""Async message queue for decoupled channel-agent communication."""

import asyncio

from loguru import logger

from hermitcrab.bus.events import InboundMessage, OutboundMessage
from hermitcrab.bus.inbox import InboundInbox


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    When an ``InboundInbox`` is attached, channel events carrying a canonical id
    are persisted before they are queued. The consumer must acknowledge a message
    after processing it; unfinished work is surfaced on the next startup instead
    of disappearing with the in-memory queue.
    """

    def __init__(self, inbox: InboundInbox | None = None):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self.inbox = inbox

    @staticmethod
    def _canonical_id(msg: InboundMessage) -> str:
        value = (msg.metadata or {}).get("canonical_id")
        return value if isinstance(value, str) else ""

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent.

        The durable row is written before the in-memory enqueue so a crash at any
        later point leaves recoverable work rather than a silently lost request.
        """
        canonical_id = self._canonical_id(msg)
        if self.inbox is not None and canonical_id:
            if not self.inbox.record(msg, canonical_id):
                logger.info(
                    "Inbound event {} is already queued or handled; skipping duplicate",
                    canonical_id,
                )
                return
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message and mark durable work as processing."""
        msg = await self.inbound.get()
        canonical_id = self._canonical_id(msg)
        if self.inbox is not None and canonical_id:
            self.inbox.claim(canonical_id)
        return msg

    async def ack_inbound(self, msg: InboundMessage) -> None:
        """Mark a durable inbound message as fully handled."""
        canonical_id = self._canonical_id(msg)
        if self.inbox is not None and canonical_id:
            self.inbox.complete(canonical_id)

    async def fail_inbound(self, msg: InboundMessage, error: str) -> None:
        """Mark a durable inbound message as failed; it is not replayed blindly."""
        canonical_id = self._canonical_id(msg)
        if self.inbox is not None and canonical_id:
            self.inbox.fail(canonical_id, error)

    async def recover_inbound(self) -> int:
        """Requeue pending work after a restart and report interrupted turns.

        Returns the number of messages requeued for processing.
        """
        if self.inbox is None:
            return 0
        replayable, interrupted = self.inbox.recover()
        for msg in replayable:
            await self.inbound.put(msg)
        if replayable:
            logger.info("Recovered {} pending inbound message(s)", len(replayable))
        for canonical_id in interrupted:
            logger.warning(
                "Inbound event {} was interrupted mid-turn; marked uncertain and not replayed",
                canonical_id,
            )
        return len(replayable)

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
