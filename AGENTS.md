# Repository Guidelines
Your job is to be the user's wingman for contributing to the HermitCrab repository. You are responsible for architectural consistency, coding standards, testing discipline, and keeping the project clean as it evolves. HermitCrab is still early and changing quickly, so this file should be treated as the source of truth when a session starts: review it, follow it, and update it when project direction materially changes.

## Project Structure
Core code lives in `hermitcrab/`.

- `agent/`: main loop, memory, journaling, reflection, subagents, tool orchestration
- `channels/`: CLI, Telegram, email, Nostr, and channel-specific reliability logic
- `providers/`: model backends and provider-specific transport/parsing logic
- `config/`: typed configuration, schemas, named models, aliases, provider options
- `cli/`: Typer commands and interactive CLI behavior
- `templates/`: reusable prompt and memory templates
- `skills/`: built-in operational skills
- `tests/`: focused regression and unit coverage

Top-level operational docs include `README.md`, `SECURITY.md`, `Dockerfile`, and `docker-compose.yml`.

## Build And Test
Use Python 3.11+.

- `uv sync --dev`: install runtime and development dependencies
- `uv run hermitcrab --help`: inspect CLI entrypoints
- `uv run hermitcrab onboard`: create local workspace and config
- `uv run hermitcrab agent`: run the local interactive agent
- `uv run pytest`: run the full test suite
- `uv run ruff check . --fix`: lint and normalize imports
- `./core_agent_lines.sh`: optional project metric

## Coding Standards
Follow Ruff defaults plus repo overrides: 100-character lines, Python 3.11 syntax, lint families `E`, `F`, `I`, `N`, and `W`.

- Use 4-space indentation.
- Use `snake_case` for modules, functions, and variables.
- Use `PascalCase` for classes.
- Prefer explicit, capability-based filenames such as `ollama_provider.py` or `manager.py`.
- Keep prompt templates, Markdown docs, and skill docs concise and operational.
- Favor deterministic Python-side guards over prompt-only fixes when model behavior is unreliable.

## Testing Standards
Pytest with `pytest-asyncio` is configured in `pyproject.toml` with `asyncio_mode = "auto"` and `testpaths = ["tests"]`.

- Add tests under `tests/` using `test_*.py`.
- Prefer focused regression tests for bug fixes before changing behavior.
- Cover provider parsing, session lifecycle, subagent orchestration, memory quality, and CLI behavior when touched.
- If a fix is meant to harden weak-model behavior, add a deterministic regression for that exact failure mode.

## Commit And PR Standards
Use Conventional Commits with short imperative subjects such as:

- `feat: add query-aware memory injection`
- `fix: suppress low-value background task replies`
- `docs: update AGENTS source of truth`

PRs should include:

- the concrete problem
- the behavior change
- test coverage notes
- user-facing CLI output or screenshots only when relevant

## Security And Runtime Boundaries
- Never commit secrets or local workspace state.
- Runtime config lives outside the repo in `~/.hermitcrab/`.
- Review `SECURITY.md` when changing execution, web access, or channel integrations.
- Keep enforcement in Python rather than trusting model output.

## Architecture Priorities
HermitCrab should prefer work that improves one or more of these:

- reliability under imperfect model output
- useful memory that stays clean over time
- better adaptation to a specific user
- lower hardware and operational footprint
- simpler architecture with fewer hidden behaviors

Reject work that mostly adds surface area, prompt churn, or cleverness without improving those properties.

## Current State
These points reflect the current state of the codebase and should override older assumptions:

- Native Ollama support is implemented and merged to `main`.
- `nvidia_nim` provider registration work is also merged to `main`.
- HermitCrab now supports named models as the canonical abstraction, and subagent model selection preserves named-model configuration such as `providerOptions`.
- CLI async output is hardened so background responses do not leak raw ANSI styling during prompt-toolkit input handling.
- Interactive CLI input supports multiline entry with `Ctrl+J`.

## Current Branch Focus
Active branch: `feat/memory-retrieval-quality`

This branch is focused on memory quality and coordinator reliability, not new product surface area.

Implemented on this branch so far:

- query-aware memory injection in prompt building
- deterministic retrieval ranking improvements
- history-window boundary repair for truncated session slices
- prompt token estimation and retrieval budgeting
- journaling scope cleanup to reduce raw tool and subagent noise
- reflection validation hardening and duplicate/contradiction guards
- suppression of blank progress updates and low-value background-task replies
- deterministic reflection override for high-priority delegation/ownership corrections

Current branch test status:

- full suite passing with `uv run pytest`

## Session Continuity
Use this file to preserve high-value context across new chat sessions.

Keep it updated with:

- the active branch and current milestone
- what has already landed on the branch
- near-term goals and next likely targets
- recurring user corrections or workflow disagreements that should change coordinator behavior
- project-specific decisions that are too important to rely on short-lived session history for

Do not use this file as a raw journal or a dumping ground. Prefer concise, durable guidance that a fresh session should inherit immediately.

Current continuity points to preserve:

- The user expects new sessions to recover branch direction and recent progress from `AGENTS.md` rather than requiring the same re-explanation in chat.
- Coordinator failures should be treated as product issues to fix at the root cause, not by stacking narrow prompt band-aids.
- Broad tasks should stay owned by the main agent; subagents are for bounded execution work, not for handing off the whole deliverable.
- Prompt/context changes should preserve strong recent-conversation awareness; avoid bloated or duplicated bootstrap prompt sections that drown out the live exchange.
- The user wants the main agent to behave as a visible coordinator for substantial work: plan first, delegate bounded research/execution where appropriate, stay responsive, and avoid filler or repeated apology loops.

## Coordinator And Subagent Policy
Broad or strategic tasks must remain owned by the main agent.

Expected behavior:

- plan first for multi-step or ambiguous work
- delegate only bounded, low-risk subtasks to subagents
- keep synthesis, judgment, and integration in the main agent
- monitor subagent failures internally
- retry with tighter scope or take over directly when subagents fail
- never surface raw inner-loop failure text as the final user-facing answer
- give useful progress updates when background work is underway
- keep status reporting consistent with actual execution state; do not claim "no blockers" if a subagent failed and fallback/retry is still in progress
- prefer deterministic fallback over user-visible thrash when delegation fails
- avoid repeating acknowledgements after a correction; record it once, then act
- for longer tasks, updates should clearly state what is in progress, what is done, whether there is any blocker, and what fallback path exists

Do not offload an entire strategic deliverable to a weaker subagent just because delegation is available.

## Memory, Journal, And Reflection Direction
The current milestone is memory retrieval quality and better learning extraction.

What has already improved:

- relevant memory is surfaced more selectively
- broad memory dumps are reduced when prompt budget is tight
- low-signal reflection artifacts are filtered from active context
- orphaned tool-result history is repaired before prompt assembly

What still needs improvement:

- journal synthesis should rely less on brittle phrase markers
- reflection priority should become more structural and less English-specific
- coordinator progress and recovery behavior should become more deterministic
- memory retrieval should keep improving without turning into a heavy subsystem

Near-term direction:

1. Replace narrow hardcoded marker logic with broader structural scoring where feasible.
2. Make correction severity and recovery impact matter more than exact wording.
3. Keep user-specific preferences in memory, but keep general coordinator policy in product logic.

## Provider And Tooling Takeaways
Recent implementation and debugging takeaways:

- HermitCrab's biggest reliability risk remains the provider/tool-call boundary.
- Empty replies after tool use, raw JSON tool calls, and XML-like inline tool calls are real failure modes and must be handled in Python.
- Native Ollama transport should remain narrow, explicit, and protocol-aware.
- Provider-safe message construction should stay centralized so loops, subagents, and providers do not drift.
- Deterministic delegation hints are useful for substantial implementation grunt work, but policy should not rely only on prompt wording.

## Cross-Project Research Policy
From time to time, inspect nearby assistant projects such as `OpenClaw`, `NanoBot`, `ZeroClaw`, and `NemoClaw` for implementation ideas worth extracting.

Rules for doing this:

- treat them as research sources, not architecture to import wholesale
- prefer reimplementation over cherry-picking
- extract concrete ideas such as transport correctness, parsing repairs, routing patterns, or reliability guards
- adapt ideas to HermitCrab's architecture, constraints, and product philosophy
- do not let another project's abstractions distort HermitCrab's simpler design

This is especially useful for:

- provider hardening
- Ollama transport and discovery ideas
- tool-call parsing edge cases
- channel reliability patterns
- lightweight memory and orchestration safeguards

## Next Targets
After stabilizing the current memory-quality branch, the next likely targets are:

1. Better coordinator/task handoff clarity between main agent and subagents
2. Smarter journal and reflection prioritization based on session structure
3. NIP-17 and thread-aware messaging improvements for Nostr workflows
4. Test-suite rationalization: keep high-value regressions, merge overlapping cases, and remove low-signal implementation-specific tests
5. Further memory retrieval gains only if they stay lightweight and testable
6. Add deterministic coordinator execution-state handling for plan/delegate/wait/fallback/complete so progress updates and recovery stay consistent

## Working Style
When deciding what to do next:

- inspect the codebase first
- favor narrow, high-signal improvements
- add regressions for real failures seen in live use
- keep docs and `AGENTS.md` aligned with the actual state of the code
- be willing to revisit temporary heuristics and replace them with better structure later
