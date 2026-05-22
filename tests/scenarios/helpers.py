"""Scenario-test helpers for end-to-end-ish agent behavior.

These helpers keep behavioral scenarios readable without hitting real LLM providers
or external channels.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from hermitcrab.agent.loop import AgentLoop
from hermitcrab.bus.queue import MessageBus


def fake_provider(default_model: str = "test-model") -> MagicMock:
    """Return a provider double with the minimal AgentLoop surface."""
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    return provider


def scenario_loop(
    tmp_path: Path,
    *,
    identity: str = "owner",
) -> AgentLoop:
    """Build an isolated AgentLoop rooted under one temporary identity."""
    identity_root = tmp_path / "identities" / identity
    return AgentLoop(
        bus=MessageBus(),
        provider=fake_provider(),
        workspace=identity_root,
        identity_name=identity,
        identity_root=identity_root,
        system_root=tmp_path / "system",
    )


def visible_dialogue(loop: AgentLoop, session_key: str) -> list[dict[str, str]]:
    """Return the saved user/assistant dialogue for assertions."""
    return loop.sessions.get_or_create(session_key).get_recent_visible_dialogue(max_messages=20)
