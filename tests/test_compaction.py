"""DSH-style compaction: the checkpoint replaces a covered span, the log stays intact."""

from __future__ import annotations

import pytest

from hermitcrab.agent.compaction import (
    CHECKPOINT_INSTRUCTION,
    COMPACTED_SUMMARY_TAG,
    CompactionCheckpoint,
    compact_session,
    load_checkpoint,
    needs_compaction,
    summarizer_messages,
    visible_after_checkpoint,
)
from hermitcrab.agent.prompt_history import build_prompt_history


class _Session:
    def __init__(self, messages):
        self.messages = list(messages)
        self.metadata: dict = {}


class _Response:
    def __init__(self, content: str):
        self.content = content


def _messages(count: int, *, chars: int = 200) -> list[dict]:
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"m{index} " + "x" * chars,
            "timestamp": "2026-10-19T00:00:00+00:00",
        }
        for index in range(count)
    ]


def test_checkpoint_hides_the_covered_prefix() -> None:
    checkpoint = CompactionCheckpoint(summary="S", covered=6, created_at="")
    history = [{"role": "user", "content": f"m{i}"} for i in range(10)]

    visible = visible_after_checkpoint(history, checkpoint)

    assert [message["content"] for message in visible] == ["m6", "m7", "m8", "m9"]


def test_checkpoint_older_than_the_window_keeps_the_whole_window() -> None:
    checkpoint = CompactionCheckpoint(summary="S", covered=3, created_at="")
    history = [{"role": "user", "content": f"m{i}"} for i in range(3, 8)]

    visible = visible_after_checkpoint(history, checkpoint, total_messages=8)

    assert len(visible) == 5


def test_prompt_history_swaps_the_covered_span_for_a_tagged_checkpoint() -> None:
    messages = _messages(12)
    checkpoint = CompactionCheckpoint(summary="Earlier: built the thing.", covered=8, created_at="")

    shaped = build_prompt_history(messages, checkpoint=checkpoint, total_messages=len(messages))

    assert shaped[0]["content"].startswith(f"<{COMPACTED_SUMMARY_TAG}>")
    assert "Earlier: built the thing." in shaped[0]["content"]
    assert not any("m0 " in item["content"] for item in shaped)
    assert any("m11 " in item["content"] for item in shaped)


def test_needs_compaction_tracks_the_window_threshold() -> None:
    assert needs_compaction(_messages(2, chars=50), 10_000) is False
    assert needs_compaction(_messages(200, chars=400), 8_000) is True
    assert needs_compaction(_messages(200, chars=400), 0) is False


def test_summarizer_replays_the_span_and_merges_a_prior_checkpoint() -> None:
    span = [{"role": "user", "content": "hi"}]
    prior = CompactionCheckpoint(summary="PRIOR", covered=2, created_at="")

    plain = summarizer_messages(span, None)
    assert len(plain) == 2
    assert plain[-1]["content"] == CHECKPOINT_INSTRUCTION

    merged = summarizer_messages(span, prior)
    assert merged[0]["role"] == "user"
    assert "PRIOR" in merged[0]["content"]
    assert merged[-1]["content"] == CHECKPOINT_INSTRUCTION


def test_checkpoint_instruction_prioritizes_user_context_over_code() -> None:
    assert "## Primary Request and Intent" in CHECKPOINT_INSTRUCTION
    assert "## Commitments, Tasks, and Deadlines" in CHECKPOINT_INSTRUCTION
    assert "## Preferences and Corrections" in CHECKPOINT_INSTRUCTION
    assert "## Open Questions" in CHECKPOINT_INSTRUCTION
    assert "Files and Code" not in CHECKPOINT_INSTRUCTION
    assert "technical detail only when it is essential" in CHECKPOINT_INSTRUCTION


async def test_compact_session_stores_a_checkpoint_without_touching_the_log() -> None:
    messages = _messages(40, chars=400)
    session = _Session(messages)

    async def chat(**kwargs):
        assert kwargs["max_tokens"] == 4096
        return _Response("## Primary Request and Intent\n- keep testing")

    result = await compact_session(
        session=session,
        chat=chat,
        model="test-model",
        context_window=10_000,
        force=True,
    )

    assert result.compacted is True
    assert result.checkpoint is not None
    assert 0 < result.checkpoint.covered < len(messages)
    assert session.metadata["compaction"]["summary"].startswith("## Primary Request")
    assert len(session.messages) == 40  # raw log stays replayable


async def test_compact_session_is_a_noop_under_the_threshold() -> None:
    session = _Session(_messages(4, chars=40))

    async def chat(**kwargs):
        raise AssertionError("nothing should be summarized")

    result = await compact_session(session=session, chat=chat, model="m", context_window=100_000)

    assert result.compacted is False


async def test_compact_session_keeps_the_prior_checkpoint_when_the_provider_fails() -> None:
    session = _Session(_messages(40, chars=400))
    session.metadata["compaction"] = {"summary": "old", "covered": 5, "created_at": ""}

    async def chat(**kwargs):
        raise RuntimeError("provider down")

    result = await compact_session(
        session=session,
        chat=chat,
        model="m",
        context_window=10_000,
        force=True,
    )

    assert result.compacted is False
    assert load_checkpoint(session).summary == "old"


async def test_compact_session_skips_an_already_fully_covered_session() -> None:
    session = _Session(_messages(6, chars=40))
    session.metadata["compaction"] = {"summary": "all", "covered": 6, "created_at": ""}

    async def chat(**kwargs):
        raise AssertionError("nothing left to summarize")

    result = await compact_session(
        session=session,
        chat=chat,
        model="m",
        context_window=1_000,
        force=True,
    )

    assert result.compacted is False
    assert load_checkpoint(session).covered == 6


@pytest.mark.parametrize("covered", [0, 4, 12])
def test_checkpoint_message_is_always_tagged(covered: int) -> None:
    checkpoint = CompactionCheckpoint(summary="S", covered=covered, created_at="")
    from hermitcrab.agent.compaction import checkpoint_message

    message = checkpoint_message(checkpoint)

    assert message["role"] == "user"
    assert message["content"].startswith(f"<{COMPACTED_SUMMARY_TAG}>")
    assert message["content"].rstrip().endswith(f"</{COMPACTED_SUMMARY_TAG}>")


def test_needs_compaction_ignores_history_the_checkpoint_covers() -> None:
    messages = _messages(100, chars=400)
    checkpoint = CompactionCheckpoint(summary="Short summary.", covered=90, created_at="")

    assert needs_compaction(messages, 10_000) is True
    assert needs_compaction(messages, 10_000, checkpoint=checkpoint) is False


async def test_follow_up_turns_do_not_resummarize_covered_history() -> None:
    messages = _messages(100, chars=400)
    session = _Session(messages)

    async def chat(**kwargs):
        return _Response("## Primary Request and Intent\n- compacted once")

    first = await compact_session(
        session=session,
        chat=chat,
        model="test-model",
        context_window=10_000,
    )
    assert first.compacted is True
    covered = first.checkpoint.covered
    assert covered > 0

    session.messages.extend(_messages(4, chars=400))

    async def chat_should_not_run(**kwargs):
        raise AssertionError("covered history must not be summarized again")

    second = await compact_session(
        session=session,
        chat=chat_should_not_run,
        model="test-model",
        context_window=10_000,
    )

    assert second.compacted is False
    assert load_checkpoint(session).covered == covered


async def test_forced_compaction_keeps_the_latest_user_correction_verbatim() -> None:
    messages = _messages(100, chars=400)
    correction = "Correction: Bob, not Alice, borrowed the book."
    messages[-1] = {"role": "user", "content": correction}
    session = _Session(messages)

    async def chat(**kwargs):
        return _Response("## Primary Request and Intent\n- earlier work")

    result = await compact_session(
        session=session,
        chat=chat,
        model="test-model",
        context_window=10_000,
        force=True,
    )

    assert result.compacted is True
    assert result.checkpoint is not None
    visible = visible_after_checkpoint(messages, result.checkpoint)
    assert any(message["content"] == correction for message in visible)
