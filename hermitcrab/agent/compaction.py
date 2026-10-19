"""DSH-style context condensation: fold an older span into one checkpoint message.

Mirrors ``@deepseek-ai/dsh-compaction-basic``: the selected span is summarized by the
model, replaced in the prompt by a single ``<compacted-summary>`` message, and the raw
session log stays untouched so replay stays deterministic. The checkpoint lives in
``session.metadata`` (already persisted), so condensing needs no new storage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from hermitcrab.utils.helpers import estimate_message_tokens

logger = logging.getLogger(__name__)

COMPACTED_SUMMARY_TAG = "compacted-summary"
THRESHOLD_RATIO = 0.8  # condense once the prompt reaches 80% of the window
RETAIN_RATIO = 0.16  # keep roughly this share of the window verbatim
MIN_SPAN_TOKENS = 256  # below this a checkpoint costs more than it saves
FORCE_RETAIN_TOKENS = 4000  # /compact keeps a small tail, not 16% of a 1M window
SUMMARY_MAX_TOKENS = 4096  # reasoning models spend output budget on reasoning first

CHECKPOINT_INSTRUCTION = """You are now acting as a compaction engine for this AI assistant. Condense the conversation ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential context.

Output EXACTLY the Markdown structure below: keep every section, in order. Use terse bullets, not prose paragraphs. Write "(none)" for an empty section — never drop a section.

## Primary Request and Intent
## Key Technical Concepts
## Files and Code
## Errors and Fixes
## Pending Jobs
## Current Work
## Next Step
## Critical Context

Rules:
- Preserve exact file paths, commands, error strings, identifiers, numeric values, and function signatures.
- Capture user feedback and explicit corrections faithfully.
- Do NOT mention this summarization request or that the context was compacted.
- Output only the checkpoint text: do not call any tool.
- If the conversation already contains a <compacted-summary> block, it is a PRIOR checkpoint: preserve still-true facts, drop stale ones, and merge newer information into one consolidated summary.
"""


@dataclass(slots=True)
class CompactionCheckpoint:
    """A persisted summary covering this many session messages from the start."""

    summary: str
    covered: int
    created_at: str


@dataclass(slots=True)
class CompactionResult:
    checkpoint: CompactionCheckpoint | None
    compacted: bool = False


def load_checkpoint(session: Any) -> CompactionCheckpoint | None:
    raw = session.metadata.get("compaction")
    if not isinstance(raw, dict):
        return None
    summary = raw.get("summary")
    covered = raw.get("covered")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(covered, int):
        return None
    return CompactionCheckpoint(
        summary=summary,
        covered=max(0, covered),
        created_at=str(raw.get("created_at") or ""),
    )


def checkpoint_message(checkpoint: CompactionCheckpoint) -> dict[str, str]:
    return {
        "role": "user",
        "content": f"<{COMPACTED_SUMMARY_TAG}>\n{checkpoint.summary}\n</{COMPACTED_SUMMARY_TAG}>",
    }


def visible_after_checkpoint(
    history: list[dict[str, Any]],
    checkpoint: CompactionCheckpoint | None,
    total_messages: int | None = None,
) -> list[dict[str, Any]]:
    """Drop history the checkpoint already covers, accounting for a truncated window."""
    if checkpoint is None:
        return history
    total = total_messages if total_messages is not None else len(history)
    window_start = max(0, total - len(history))
    cut = max(0, checkpoint.covered - window_start)
    return history[cut:]


def needs_compaction(raw_history: list[dict[str, Any]], context_window: int) -> bool:
    if context_window <= 0:
        return False
    return estimate_message_tokens(raw_history) >= int(context_window * THRESHOLD_RATIO)


def _retain_cut(messages: list[dict[str, Any]], *, covered: int, retain_tokens: int) -> int:
    """Index where the verbatim tail starts, keeping roughly ``retain_tokens`` verbatim."""
    used = 0
    for index in range(len(messages) - 1, covered - 1, -1):
        used += estimate_message_tokens([messages[index]])
        if used >= retain_tokens:
            return index
    return covered


def summarizer_messages(
    span: list[dict[str, Any]],
    prior: CompactionCheckpoint | None,
) -> list[dict[str, Any]]:
    """Replay the span as-is, then append the instruction.

    The span is sent unshaped so a provider that caches the raw conversation prefix can
    still hit it; shaping would rewrite the earlier messages and miss that cache.
    """
    messages = list(span)
    if prior is not None:
        messages.insert(0, checkpoint_message(prior))
    messages.append({"role": "user", "content": CHECKPOINT_INSTRUCTION})
    return messages


async def compact_session(
    *,
    session: Any,
    chat: Callable[..., Awaitable[Any]],
    model: str,
    context_window: int,
    force: bool = False,
) -> CompactionResult:
    """Condense the uncovered span when the prompt is tight, or on demand."""
    prior = load_checkpoint(session)
    covered = prior.covered if prior else 0
    messages: list[dict[str, Any]] = session.messages
    if covered >= len(messages):
        return CompactionResult(prior)
    if not force and not needs_compaction(_chat_messages(messages), context_window):
        return CompactionResult(prior)

    cut = _retain_cut(
        messages,
        covered=covered,
        retain_tokens=(
            FORCE_RETAIN_TOKENS
            if force
            else max(512, int(context_window * RETAIN_RATIO))
        ),
    )
    if cut <= covered:
        return CompactionResult(prior)
    span = _chat_messages(messages[covered:cut])
    if estimate_message_tokens(span) < MIN_SPAN_TOKENS:
        return CompactionResult(prior)

    try:
        response = await chat(
            messages=summarizer_messages(span, prior),
            model=model,
            temperature=0.2,
            max_tokens=SUMMARY_MAX_TOKENS,
        )
    except Exception as exc:  # best effort: condensing must never break the turn
        logger.warning("Context compaction failed: %s", exc)
        return CompactionResult(prior)

    summary = (getattr(response, "content", "") or "").strip()
    if not summary:
        logger.warning("Context compaction produced an empty summary; keeping prior checkpoint")
        return CompactionResult(prior)

    checkpoint = CompactionCheckpoint(
        summary=summary,
        covered=cut,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    session.metadata["compaction"] = {
        "summary": checkpoint.summary,
        "covered": checkpoint.covered,
        "created_at": checkpoint.created_at,
    }
    return CompactionResult(checkpoint, compacted=True)


def _chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip session bookkeeping (timestamps) down to plain chat messages."""
    out: list[dict[str, Any]] = []
    for message in messages:
        entry: dict[str, Any] = {"role": message.get("role"), "content": message.get("content", "")}
        for key in ("tool_calls", "tool_call_id", "name"):
            if key in message:
                entry[key] = message[key]
        out.append(entry)
    return out
