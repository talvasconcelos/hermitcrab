"""Per-turn routing context for stateful tools.

Some tools (message, spawn, reminder, cron, person_profile) route their side
effects back to the conversation that triggered them. Routing must be scoped to
the current turn, not stored on the shared tool instance, otherwise two
concurrent turns could deliver a result to the wrong channel/chat_id.

``contextvars`` gives us task-local storage: values set here are visible to the
turn that set them (and tasks it spawns) but not to other concurrent turns.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnContext:
    channel: str = ""
    chat_id: str = ""
    message_id: str | None = None
    brief: str | None = None
    delivery_channel: str | None = None
    delivery_chat_id: str | None = None


_current_turn: ContextVar[TurnContext] = ContextVar("hermitcrab_turn_context", default=TurnContext())


def get_turn_context() -> TurnContext:
    """Return the routing context for the current turn."""
    return _current_turn.get()


def set_turn_context(
    *,
    channel: str,
    chat_id: str,
    message_id: str | None = None,
    brief: str | None = None,
    delivery_channel: str | None = None,
    delivery_chat_id: str | None = None,
) -> None:
    """Set the routing context for the current turn/task."""
    _current_turn.set(
        TurnContext(
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            brief=brief,
            delivery_channel=delivery_channel,
            delivery_chat_id=delivery_chat_id,
        )
    )


# Mutable per-turn state that must not leak between concurrent gateway turns.
_sent_in_turn: ContextVar[bool] = ContextVar("hermitcrab_sent_in_turn", default=False)
_approved_destructive_command: ContextVar[str | None] = ContextVar(
    "hermitcrab_approved_destructive_command", default=None
)


def get_sent_in_turn() -> bool:
    """Return whether a message was sent in the current turn."""
    return _sent_in_turn.get()


def set_sent_in_turn(value: bool) -> None:
    """Set the per-turn sent flag."""
    _sent_in_turn.set(value)


def get_approved_destructive_command() -> str | None:
    """Return the current turn's one-shot destructive command approval (or None)."""
    return _approved_destructive_command.get()


def set_approved_destructive_command(value: str | None) -> None:
    """Set or clear the current turn's one-shot destructive command approval."""
    _approved_destructive_command.set(value)
