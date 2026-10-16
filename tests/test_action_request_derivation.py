"""The incomplete-response guards must see the user's real request.

Regression: ``_derive_action_request_text`` resolved *any* short request against earlier
turns (``is_short_follow_up`` is true for every short message). The injected
``## Runtime Context`` message is always a ``user`` message with a structured payload
(paths, colons), so it became the request text on every turn. ``_request_has_structural_action_signal``
then returned true, and every short tool-free answer — e.g. a correct "SMOKE-OK" — was judged an
incomplete acknowledgement, re-prompted twice, and replaced with "The model kept acknowledging
the request without actually doing the work."
"""

from hermitcrab.agent.pending_work import looks_like_confirmation
from hermitcrab.agent.turn_runner import TurnRunner

RUNTIME_CONTEXT = (
    "## Runtime Context\n"
    "Current time: 2026-10-16 17:34 (Friday) (WEST)\n"
    "Channel: cli\n"
    "Chat ID: direct\n"
    "Session scratchpad: /home/u/.hermitcrab/identities/owner/scratchpads/cli_direct.md"
)
EARLIER_REQUEST = "delete the 12 stale reports under /tmp/reports"
SELF_CONTAINED = "Reply with exactly: SMOKE-OK"


def _derive(messages: list[dict], current: str | None) -> str | None:
    return TurnRunner._derive_action_request_text(messages, current)


def test_short_self_contained_request_is_not_shadowed_by_runtime_context() -> None:
    messages = [
        {"role": "user", "content": EARLIER_REQUEST},
        {"role": "user", "content": RUNTIME_CONTEXT},
        {"role": "user", "content": SELF_CONTAINED},
    ]
    assert _derive(messages, SELF_CONTAINED) == SELF_CONTAINED


def test_correct_short_reply_passes_the_incomplete_response_guard() -> None:
    """The guard's inputs for the failing turn: neither trigger may fire."""
    messages = [
        {"role": "user", "content": EARLIER_REQUEST},
        {"role": "user", "content": RUNTIME_CONTEXT},
        {"role": "user", "content": SELF_CONTAINED},
    ]
    action_request = _derive(messages, SELF_CONTAINED)
    assert TurnRunner._request_has_structural_action_signal(action_request) is False
    assert TurnRunner._looks_like_short_action_claim("SMOKE-OK") is False


def test_approval_follow_up_still_resolves_to_the_earlier_request() -> None:
    messages = [
        {"role": "user", "content": EARLIER_REQUEST},
        {"role": "user", "content": RUNTIME_CONTEXT},
        {"role": "user", "content": "yes, do it"},
    ]
    assert _derive(messages, "yes, do it") == EARLIER_REQUEST


def test_structured_request_wins_over_history() -> None:
    assert _derive([{"role": "user", "content": EARLIER_REQUEST}], EARLIER_REQUEST) == EARLIER_REQUEST


def test_confirmation_matching_ignores_punctuation() -> None:
    assert looks_like_confirmation("yes, do it")
    assert looks_like_confirmation("Yes please!")
    assert looks_like_confirmation("go ahead")
    assert not looks_like_confirmation(SELF_CONTAINED)
