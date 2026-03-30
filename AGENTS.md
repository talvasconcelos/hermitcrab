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
Active branch: `main`

`v0.1.0a4` release prep is complete locally. The reliability work from `release/pre-v0.1.0a4-reliability` is ready on `main`, with publishing left as a manual step.

Recently landed:

- deterministic resume-query handling for "where did we leave off?"-style requests using saved session history instead of weak memory guesses
- final assistant replies are now persisted into session history so later diagnosis sees what the user actually saw
- post-tool completion is hardened with one explicit final-answer repair pass before failure
- repeated empty or intent-only post-tool replies can now fall back to grounded tool-result summaries instead of fake-complete placeholders
- low-value post-tool failure text is filtered more consistently from user-facing background updates
- live regressions now cover repeated post-tool silence, final-reply persistence, and deterministic session recap behavior
- prerelease version is now `0.1.0a4`, with release notes prepared and local build validation completed

Near-term focus:

- publish `v0.1.0a4`
- start the beta-oriented household usability and polish milestone without regressing the new reliability work
- keep dogfooding interrupted turns, recall queries, and weak-model post-tool behavior so any remaining failures become focused regressions

Current branch validation status:

- full suite passing with `uv run pytest`
- lint passing with `uv run ruff check . --fix`
- distribution artifacts building successfully with `uv build`

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
- After `feat/learning-quality` is merged, the next broader milestone should be context and coordination reliability before taking on the next product-facing push.
- Dogfooding should include materially different user profiles, not only the repository owner; validate day-to-day assistant behavior for family scheduling, kid activities, and household coordination use cases.
- Add a built-in `here.now` skill as a near-term product task.
- These goals are urgent and should be treated as next-milestone work, not as distant backlog items.
- Prompt/context changes should preserve strong recent-conversation awareness; avoid bloated or duplicated bootstrap prompt sections that drown out the live exchange.
- The user wants the main agent to behave as a visible coordinator for substantial work: plan first, delegate bounded research/execution where appropriate, stay responsive, and avoid filler or repeated apology loops.
- The user wants hardcoded English-specific marker heuristics reduced where structural evidence or model judgment can do the job more cleanly.
- Tests are still local-only for now; do not newly commit ignored test files even when they are useful for local validation.
- After `v0.1.0a4`, the next milestone should shift from reliability-only work toward a beta-oriented product pass for everyday households, while preserving the reliability gains.
- HermitCrab should aim to work for mainstream users managing kids, chores, school, schedules, and household coordination, not only technically fluent operators.
- Evaluate a two-lane product experience after `v0.1.0a4`: a simpler mainstream mode and a more transparent/power-user mode, with final naming still open.
- The simpler mainstream mode must remain a presentation/defaults layer over the same deterministic, agentic HermitCrab core, not a fork or weakened architecture.
- Mainstream users should not need to care about Markdown, YAML, workspace internals, or manual memory curation in normal use.
- Simpler install, first-run setup, and adaptation to the specific user/household should be treated as beta-critical product work.
- Normie-friendly support should include first-class lists and contextual recall flows such as groceries/errands and user-triggered check-ins like "I'm at the supermarket, what do I need?"
- Consider an optional polished UI shell or plugin with surfaces like Chat, Today, Tasks, and Notes, while keeping the core product architecture unified.

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
- Typed provider streaming events now exist for text deltas, tool calls, and terminal metadata; prefer consuming those over recovering tool intent from streamed text when a provider can supply them.
- Provider-side accumulation of partial streamed tool-call arguments is now the preferred fix for SSE fragmentation; keep inline JSON/XML recovery as a fallback path, not the primary mechanism.
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
- `ZeroClaw`'s recent releases added typed streaming tool events and provider-side SSE tool-call accumulation; adapt that style to reduce raw JSON/XML leakage and make the provider/tool boundary more deterministic in HermitCrab.
- `ZeroClaw` also added boundary-aware context compression with provider-error limit parsing and tool-pair repair; borrow those ideas when HermitCrab needs stronger long-session history compaction beyond prompt-budget trimming.
- `ZeroClaw`'s delegation path sharpens subagent boundaries with per-agent tool allowlists and enriched subagent prompts; reuse the bounded-tool-surface idea when tightening HermitCrab coordinator/subagent behavior, without importing the full delegate subsystem.
- `Hermes` treats "self-improvement" as disciplined file-backed accumulation rather than opaque adaptation: small curated declarative memory, reusable procedural skills, transcript recall, and explicit maintenance of stale procedures. HermitCrab should borrow the discipline, not the marketing.
- `Hermes`' strongest extractable pattern is procedural-memory capture: after a difficult or high-tool-count success, the agent is nudged to save the workflow as a portable skill and patch it quickly when reality diverges. HermitCrab should evaluate a safe, auditable `skill_manage`-style workflow for workspace skills.
- `Hermes` also reinforces that "grows with you" depends heavily on searchable session history. HermitCrab should evaluate adding direct cross-session transcript recall before considering heavier user-modeling layers.
- Do not regress HermitCrab toward a tiny flat `MEMORY.md`/`USER.md` design; the existing category-based memory, reflection validation, and conservative bootstrap promotion are the stronger foundation. The missing piece is clearer separation between durable facts/policy and reusable workflows.

## Next Targets
The next milestone should prioritize the following urgent targets:

1. After merging `feat/learning-quality`, run a `context-and-coordination-reliability` milestone focused on context-boundary recovery, coordinator execution-state hardening, tighter subagent tool boundaries, and real dogfooding-derived regressions.
2. In that milestone, add deterministic provider context-limit parsing plus boundary-aware history compaction that preserves recent turns, tool/result pairs, identifiers, decisions, and open loops before retrying.
3. Tighten coordinator/task handoff clarity between main agent and subagents, especially around delegated progress, retries, fallback, and completion-state honesty.
4. Dogfood HermitCrab with distinct user profiles, including household/schedule-heavy usage patterns, and convert real failures into focused regressions.
5. Investigate OpenCode's subagent creation/internal-usage patterns and adapt only the parts that improve HermitCrab reliability and clarity.
6. Test-suite rationalization: keep high-value regressions, merge overlaps, and remove low-signal implementation-specific tests.
7. Evaluate a lightweight, file-backed procedural-memory flow inspired by `Hermes`: safe skill creation/patching for proven workflows, but enforced with deterministic Python-side policy and auditability rather than prompt-only nudges.
8. Evaluate direct session-history recall tooling for cross-session memory of prior work, fixes, and discussions before adding any heavier user-modeling subsystem.
9. Once context and coordination reliability is steadier, implement Nostr NIP-17 direct messages; group handling can follow in a later milestone.
10. Add a built-in `here.now` skill with clear operational scope.
11. Smarter journal and reflection prioritization based on session structure instead of brittle markers, and further memory retrieval gains only if they stay lightweight and easy to validate.

## Next Milestone Roadmap
After `v0.1.0a4`, target a beta-focused milestone centered on mainstream usability without regressing reliability.

Suggested milestone name: `beta-household-usability-and-polish`

Goals for that milestone:

1. Make HermitCrab approachable for non-technical everyday users.
2. Preserve strong adaptation and continuity while hiding unnecessary file-oriented complexity.
3. Smooth installation, onboarding, and first-week usage.
4. Clean remaining project rough edges before broader beta exposure.

Priority roadmap:

1. Product mode design
   - define a simpler default mode for mainstream users and a separate power-user mode
   - choose names that describe confidence and audience better than "normal" and "advanced" if possible
   - decide which capabilities stay visible in each mode: memory files, scratchpads, slash commands, raw tooling, model/provider detail
2. Installation and first-run simplicity
   - reduce setup friction for users who just want the assistant running locally
   - evaluate a friendlier onboarding flow than editing raw config by hand
   - improve first-run guidance for model selection, missing dependencies, and common local-setup failures
3. Household adaptation features
    - improve support for schedules, chores, activities, school logistics, reminders, and recurring family coordination
    - keep mainstream-friendly behavior as an abstraction layer over the same power-user-capable deterministic core rather than a separate product flavor
    - add first-class lists, contextual reminders, and user-triggered context recall flows for errands such as supermarket/pharmacy/school
    - design memory/retrieval behavior around recurring household rhythms, not only project work
    - dogfood with real family-style scenarios and convert failures into focused regressions
4. User-facing UX polish
   - make responses feel calmer, clearer, and more helpful for non-technical users
   - reduce visible internal jargon, tool framing, and implementation language in mainstream flows
   - keep the power-user path available without forcing it into the default experience
5. Product cleanup and consistency
   - remove lingering `nanobot` references across code, docs, scripts, and packaging before beta push
   - tighten naming consistency, version messaging, and project identity
   - clean stale docs or scripts that no longer match HermitCrab behavior

Execution checklist for the next milestone:

- define the beta audience and choose the mode split/naming
- map current onboarding pain points in CLI, docs, and config setup
- design a simpler first-run flow for mainstream users
- identify which advanced surfaces should be hidden, deferred, or relabeled in the simpler mode
- create a household-use-case dogfooding matrix: schedules, chores, school, errands, reminders, family planning
- define a local-first calendar/reminder/list model plus a future sync path, keeping exact scheduling separate from coarse heartbeat behavior
- evaluate an optional UI/plugin direction for Chat/Today/Tasks/Notes without splitting the core product
- add regressions for the most important household-memory and continuity flows
- audit and remove remaining `nanobot` references
- review docs, screenshots, and help text from a non-technical user perspective
- prepare a beta readiness checklist covering install, onboarding, continuity, adaptation, and terminology

## Working Style
When deciding what to do next:

- inspect the codebase first
- favor narrow, high-signal improvements
- add regressions for real failures seen in live use
- keep docs and `AGENTS.md` aligned with the actual state of the code
- be willing to revisit temporary heuristics and replace them with better structure later
