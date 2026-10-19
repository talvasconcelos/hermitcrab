"""Reasoning traces are stored, then replayed only where they help and fit.

Regression: `TurnPersistence` dropped `reasoning_content` before saving and the
provider stripped it again on replay, so reasoning models resumed a conversation
without their own earlier chain of thought.
"""

from __future__ import annotations

from hermitcrab.agent.prompt_history import PromptHistoryConfig, build_prompt_history
from hermitcrab.agent.turn_persistence import TurnPersistence
from hermitcrab.providers.litellm_provider import LiteLLMProvider
from hermitcrab.session.manager import Session


def _turn_messages() -> list[dict]:
    return [
        {"role": "user", "content": "check the provider path"},
        {
            "role": "assistant",
            "content": "done",
            "reasoning_content": "I should compare the providers first.",
        },
    ]


def test_persistence_keeps_reasoning() -> None:
    session = Session(key="test:1")

    TurnPersistence.save_turn(session, _turn_messages(), skip=0, update_session_timer=lambda _k: None)

    assert session.messages[1]["reasoning_content"] == "I should compare the providers first."


def test_history_round_trips_reasoning() -> None:
    session = Session(key="test:1")
    TurnPersistence.save_turn(session, _turn_messages(), skip=0, update_session_timer=lambda _k: None)

    history = session.get_history()

    assert history[1]["reasoning_content"] == "I should compare the providers first."


def test_prompt_keeps_recent_reasoning_and_drops_older_ones() -> None:
    messages: list[dict] = []
    for index in range(8):
        messages.append({"role": "user", "content": f"question {index}"})
        messages.append(
            {
                "role": "assistant",
                "content": f"answer {index}",
                "reasoning_content": f"reasoning {index} " + "x" * 400,
            }
        )

    cfg = PromptHistoryConfig(
        preserve_recent_turns=8,
        max_reasoning_turns=2,
        reasoning_char_budget=3000,
        max_reasoning_chars=1200,
    )
    shaped = build_prompt_history(messages, config=cfg)

    with_reasoning = [m for m in shaped if "reasoning_content" in m]
    assert len(with_reasoning) == 2
    assert "reasoning 7" in with_reasoning[-1]["reasoning_content"]
    assert "reasoning 6" in with_reasoning[-2]["reasoning_content"]


def test_reasoning_respects_the_character_budget() -> None:
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", "reasoning_content": "r" * 5000},
    ]
    cfg = PromptHistoryConfig(reasoning_char_budget=300, max_reasoning_chars=5000)

    shaped = build_prompt_history(messages, config=cfg)

    reasoning = shaped[-1]["reasoning_content"]
    assert len(reasoning) == 300


def test_provider_replays_reasoning_only_where_it_is_accepted() -> None:
    assert LiteLLMProvider._replays_reasoning("deepseek/deepseek-flash", "deepseek") is True
    assert LiteLLMProvider._replays_reasoning("moonshot/kimi-k2", None) is True
    assert LiteLLMProvider._replays_reasoning("openai/gpt-4o", "openai") is False


def test_sanitizer_keeps_reasoning_only_when_allowed() -> None:
    message = {
        "role": "assistant",
        "content": "done",
        "reasoning_content": "because",
    }

    kept = LiteLLMProvider._sanitize_messages([dict(message)], allow_reasoning=True)
    stripped = LiteLLMProvider._sanitize_messages([dict(message)], allow_reasoning=False)

    assert kept[0]["reasoning_content"] == "because"
    assert "reasoning_content" not in stripped[0]
