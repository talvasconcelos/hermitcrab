"""Message bus module for decoupled channel-agent communication."""

from hermitcrab.bus.events import InboundMessage, OutboundMessage
from hermitcrab.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
