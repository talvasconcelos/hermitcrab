# HermitCrab v0.1.0a4

This prerelease focuses on context and completion reliability, especially after tool use and during session recovery.

## Highlights

- Added deterministic "where did we leave off?" session recall based on saved session history instead of weak memory guesses.
- Persisted final assistant replies into session history so later recall and debugging see the exact answer the user received.
- Hardened post-tool completion with one explicit final-answer repair pass before giving up.
- Added grounded fallback replies when a weak model keeps stopping after tools without producing a usable final answer.
- Filtered low-value post-tool failure chatter more consistently from user-facing background updates.

## Reliability and UX improvements

- Improved CLI reliability around terminal handling and atomic template writes.
- Made CLI progress rendering follow channel settings more consistently for tool hints vs. normal progress.
- Extended the interactive CLI background-task wait window to reduce premature shutdown during local testing.

## Tests and validation

- Added regressions for repeated empty and intent-only post-tool replies.
- Added regressions for deterministic resume-query handling and persistence of final assistant replies.
- Local validation completed with `uv run pytest`, `uv run ruff check . --fix`, and `uv build`.

## Upgrade notes

- Package version is now `0.1.0a4`.
- No config migration is required for this prerelease.
