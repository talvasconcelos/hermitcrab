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
Active branch: `feat/learning-quality`

This branch is focused on journal durability, reflection quality, and promotion of durable learnings into persistent context files without adding a heavy subsystem.

Implemented on this branch so far:

- query-aware memory injection in prompt building
- deterministic retrieval ranking improvements
- history-window boundary repair for truncated session slices
- prompt token estimation and retrieval budgeting
- journaling scope cleanup to reduce raw tool and subagent noise
- reflection validation hardening and duplicate/contradiction guards
- suppression of blank progress updates and low-value background-task replies
- deterministic reflection override for high-priority delegation/ownership corrections
- session digests now preserve user goal, artifacts changed, decisions made, assistant responses, open loops, and structural signals for journal/reflection work
- journal prompts and fallback entries are more concrete and should stay understandable days later instead of collapsing into vague narration
- reflection output now uses observation, impact, lesson, recommended behavior, scope, confidence, evidence, and promotion target
- reflection promotion is stricter: user-specific preferences stay in memory, target/scope compatibility is enforced, duplicate/conflicting bootstrap bullets are rejected, and promotions are logged in `bootstrap_promotion_log.md`
- session digests now keep the primary user goal instead of letting late status pings overwrite journal/reflection framing, and successful file/tool writes count as outcomes
- journal synthesis now gets a stronger grounded prompt plus one repair pass before falling back, reducing empty or scaffold-parroting entries
- distillation no longer commits operational correction directives as facts; those are reserved for reflection/bootstrap routing instead
- corrective operational learnings are now rerouted deterministically toward `AGENTS.md` rather than drifting into `SOUL.md` or memory facts

Planned on this branch:

- manual dogfooding of real sessions to judge journal usefulness and promotion quality from actual outputs, not only tests
- refine promotion semantics between `SOUL.md` and `IDENTITY.md` if real sessions show muddled routing
- keep shrinking brittle English-specific heuristics where structural grounding or model judgment can replace them cleanly
- trim low-signal tests when they are clearly implementation bloat rather than behavior protection

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
- Reflections are not just summaries; they should become real learning material, and high-confidence durable lessons should be promoted into the correct persistent context file when viable.
- Promotion should stay conservative and auditable: bootstrap files should not accumulate duplicate or conflicting bullets, and every promotion should be inspectable afterward.
- Coordinator failures should be treated as product issues to fix at the root cause, not by stacking narrow prompt band-aids.
- Broad tasks should stay owned by the main agent; subagents are for bounded execution work, not for handing off the whole deliverable.
- The next Nostr milestone should include direct-message support for NIP-17; group handling can wait until later.
- Dogfooding should include materially different user profiles, not only the repository owner; validate day-to-day assistant behavior for family scheduling, kid activities, and household coordination use cases.
- Add a built-in `here.now` skill as a near-term product task.
- These goals are urgent and should be treated as next-milestone work, not as distant backlog items.
- Prompt/context changes should preserve strong recent-conversation awareness; avoid bloated or duplicated bootstrap prompt sections that drown out the live exchange.
- The user wants the main agent to behave as a visible coordinator for substantial work: plan first, delegate bounded research/execution where appropriate, stay responsive, and avoid filler or repeated apology loops.
- The user wants hardcoded English-specific marker heuristics reduced where structural evidence or model judgment can do the job more cleanly.
- Tests are still local-only for now; do not newly commit ignored test files even when they are useful for local validation.

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
The current milestone is learning quality: better journals, better reflections, and cleaner promotion of durable learnings.

What has already improved:

- relevant memory is surfaced more selectively
- broad memory dumps are reduced when prompt budget is tight
- low-signal reflection artifacts are filtered from active context
- orphaned tool-result history is repaired before prompt assembly

What still needs improvement:

- real-session journal output still needs dogfooding to verify that the new structure stays useful outside synthetic tests
- reflection promotion semantics between `SOUL.md` and `IDENTITY.md` may still need refinement after manual review
- English-specific marker logic should keep shrinking when structural grounding can replace it

Near-term direction:

1. Dogfood the new journal/reflection flow on real sessions and inspect `journal/`, `memory/reflections/`, and `bootstrap_promotion_log.md` directly.
2. Refine promotion routing between `SOUL.md` and `IDENTITY.md` if manual testing shows overlap or confusion.
3. Keep user-specific preferences in memory unless they are truly durable assistant-wide context.
4. Continue replacing brittle wording heuristics with structural grounding where possible.
5. Trim clearly low-signal local tests when they stop protecting real behavior.

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

Immediate external research to capture when relevant:

- inspect how OpenCode creates and internally uses subagents
- extract ideas that improve HermitCrab's coordinator/subagent boundaries without importing OpenCode's architecture wholesale

Recent nearby-project takeaways worth preserving:

- `NanoBot` recently replaced a global processing lock with per-session locks plus an optional global concurrency cap; this is a strong fit for HermitCrab when coordinator/session concurrency needs tightening without losing per-session ordering.
- `NanoBot` also rebinds tool routing context immediately before each execution round so concurrent sessions do not clobber channel/session metadata; reuse that idea if HermitCrab shows cross-session tool-context bleed.
- `NanoBot` reserves completion headroom before memory consolidation; keep that in mind for HermitCrab prompt budgeting, journal synthesis, and reflection passes.
- `OpenClaw` centralizes provider/model quirks in a capability registry instead of scattering ad-hoc conditionals; HermitCrab should move in that direction as provider edge-case handling grows.
- `OpenClaw`'s compaction safeguards use explicit structural sections, recent-turn preservation, exact-identifier preservation, and tool-failure harvesting; adapt those ideas to improve HermitCrab journal/digest quality without importing the whole subsystem.
- `OpenClaw` also uses strong dependency-injection seams around stateful runtime code; prefer that style when making HermitCrab coordinator, provider, or session-state code more testable.

## Next Targets
The next milestone should prioritize the following urgent targets:

1. Implement Nostr NIP-17 direct messages; group handling can follow in a later milestone.
2. Dogfood HermitCrab with distinct user profiles, including household/schedule-heavy usage patterns, and capture adaptation gaps.
3. Investigate OpenCode's subagent creation/internal-usage patterns and adapt only the parts that improve HermitCrab reliability and clarity.
4. Add a built-in `here.now` skill with clear operational scope.
5. Better coordinator/task handoff clarity between main agent and subagents, especially around delegated progress and recovery.
6. Smarter journal and reflection prioritization based on session structure instead of brittle markers.
7. Deterministic coordinator execution-state handling for plan/delegate/wait/fallback/complete across more surfaces.
8. Test-suite rationalization: keep high-value regressions, merge overlaps, and remove low-signal implementation-specific tests.
9. Further memory retrieval gains only if they stay lightweight and easy to validate.

## Working Style
When deciding what to do next:

- inspect the codebase first
- favor narrow, high-signal improvements
- add regressions for real failures seen in live use
- keep docs and `AGENTS.md` aligned with the actual state of the code
- be willing to revisit temporary heuristics and replace them with better structure later
