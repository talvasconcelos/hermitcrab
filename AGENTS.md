# Repository Guidelines
Your job is to be the user's wingman for contributing to the HermitCrab repository. You're in charge of all the architecture, coding style, testing, and contribution guidelines. HermitCrab is still in early Alpha stage with a release already available, so the codebase is evolving rapidly. Only you can keep the codebase clean, consistent, and maintainable as it grows. Only you have the power to do architecture changes. You can ask the user to pass small, routine or non important tasks to a junior developer (an AI coder), but you must provide clear instructions, in a copy paste ready prompt format, for the junior developer to follow. You must also review the junior developer's code and provide feedback on how to improve it. You should also be proactive in identifying areas of the codebase that need refactoring or improvement, and suggest changes to the user. Your ultimate goal is to ensure that the HermitCrab codebase remains clean, consistent, and maintainable as it evolves.

## Project Structure & Module Organization
Core application code lives in `hermitcrab/`. Key areas are `agent/` for the execution loop and tools, `channels/` for CLI, Telegram, email, and Nostr integrations, `providers/` for model backends, `config/` for typed settings, and `cli/` for the Typer entrypoints. Reusable prompt and memory templates live in `hermitcrab/templates/`, and built-in skills live in `hermitcrab/skills/`. Top-level docs such as `README.md`, `SECURITY.md`, `COMMUNICATION.md`, and deployment files (`Dockerfile`, `docker-compose.yml`) describe runtime behavior and operations.

## Build, Test, and Development Commands
Use Python 3.11+.

- `uv sync --dev`: install runtime and development dependencies from `pyproject.toml`/`uv.lock`.
- `uv run hermitcrab --help`: inspect the CLI entrypoints.
- `uv run hermitcrab onboard`: create the local workspace and default config.
- `uv run hermitcrab agent`: run the local agent after configuring a provider.
- `uv run pytest`: run the test suite defined under `tests/`.
- `uv run ruff check . --fix`: run lint and import-order checks.
- `./core_agent_lines.sh`: optional project metric used in the README.

## Coding Style & Naming Conventions
Follow Ruff defaults with the repository overrides: 100-character lines, Python 3.11 syntax, and lint families `E`, `F`, `I`, `N`, and `W`. Use 4-space indentation, `snake_case` for modules, functions, and variables, `PascalCase` for classes, and clear, capability-based filenames such as `openai_codex_provider.py` or `manager.py`. Keep Markdown templates and skill docs concise and operational.

## Testing Guidelines
Pytest with `pytest-asyncio` is configured in `pyproject.toml` with `asyncio_mode = "auto"` and `testpaths = ["tests"]`. Add tests under `tests/` using `test_*.py` filenames. Prefer focused unit tests for config loading, provider/tool behavior, and async service flows; add regression tests for bug fixes before changing behavior.

## Commit & Pull Request Guidelines
Recent history uses Conventional Commit prefixes, especially `fix:`. Continue with short, imperative subjects such as `feat: add email channel retries` or `docs: clarify onboarding config`. Pull requests should include a concise problem statement, the behavior change, test coverage notes, and linked issues when applicable. Include CLI output or screenshots only when user-facing behavior changes.

## Security & Configuration Tips
Do not commit secrets or local workspace state. Runtime config is created outside the repo in `~/.hermitcrab/`. When changing tool execution, web access, or channel integrations, review `SECURITY.md` and keep Python as the enforcement layer rather than trusting model output.

## Recent Debugging Takeaways
- When mining `nanobot` for fixes, prefer reimplementation over cherry-picking. The most useful source of ideas has been `providers/` compatibility work, not wholesale `agent/` loop changes.
- `nanobot/main` is worth checking for provider hardening ideas such as OpenAI-compatible provider support, request sanitization, tool-call ID normalization, and multi-choice parsing.
- HermitCrab's biggest reliability risk has been the provider/tool-call boundary, not the higher-level CLI or session lifecycle.
- Ollama works more reliably through its OpenAI-compatible `/v1` endpoint using the `openai` provider than through LiteLLM's native `ollama` route.
- The native `ollama` provider currently has poorer tool-calling coverage and more malformed output variants; keep it only as a compatibility path unless it is revalidated.
- Empty replies after tool use, raw JSON tool calls, and XML-like inline tool calls are all real failure modes that must be handled in Python, not assumed away.
- Subagent delegation should be encouraged deterministically for substantial implementation grunt work; do not rely only on vague prompt wording.

## Current Rebuild Decision
The `broken_changes` branch must not be cherry-picked or merged incrementally. Rebuild the desired work fresh on top of `main`, on clean branch(es), and validate tool-calling after each feature lands.

Features confirmed worth rebuilding:
- Proper Ollama `:cloud` handling while still using the local Ollama provider/library. Example: `ollama/kimi-k2.5:cloud` must remain routed through local Ollama and keep the `:cloud` suffix when required.
- Subagents with model aliases and delegated task execution. The main agent should stay responsive and delegate substantial work to subagents when appropriate. Users may specify aliases like `coder`, but the agent should also be able to choose delegation/model routing on its own.
- `reasoning_effort` configuration for supported thinking models.
- General memory sanitation and deduplication improvements.

Feature inventory from `broken_changes` that is worth using as a reference only, not as a cherry-pick source:
- Subagent model aliases and per-task model selection.
- `reasoning_effort` config propagation.
- Ollama `:cloud` suffix preservation.
- Optional memory item `id` support.
- Blocking `write_file` from writing directly into `memory/`.

Areas explicitly considered unsafe from `broken_changes` and should be reimplemented carefully or skipped:
- Tool-call parsing and fallback behavior in the provider/agent loop boundary.
- Silent-failure/raw-JSON/tool-hint UX fixes that were layered on top of regressions.
- Any web-chat deletions or unrelated prompt churn.

## Clean Rebuild Status
Current clean rebuild branch: `fix/clean-rebuild-from-main`

Commits currently on that branch:
- `09ef714` `fix: preserve ollama cloud routing`
- `e1a1c8a` `feat: add subagent model aliases`
- `05ce54e` `feat: add reasoning effort control`
- `f2d3417` `fix: support legacy memory files`
- `f96429c` `fix: harden distillation candidate parsing`
- `890b9f1` `test: cover reflection parsing and subagent spawn`

Current validation status:
- Tool-calling on CLI and Nostr was manually confirmed working again on this branch.
- Focused automated tests for routing, commands, subagents, reflection, distillation resilience, and memory compatibility are passing.

Current manual merge gate before merging this branch to `main`:
- Run one successful manual subagent test end-to-end.
- If that passes, this branch is considered merge-ready.

## Next Steps TODO

Priority direction for the next implementation wave:
- Keep HermitCrab reliable, memory-aware, and adaptive without turning it into a heavy framework.
- Prefer deterministic Python enforcement for correctness, and use LLMs for judgment, summarization, and adaptation only where they add clear value.
- Keep local-first and low-footprint as a product constraint, not a nice-to-have.

### Memory and Distillation

Distillation should remain a fallback recovery layer, not the primary memory path.

TODO:
- Add duplicate and near-duplicate checks before committing distilled candidates into memory.
- Restrict distilled candidate types by default to `fact`, `goal`, and `task`.
- Treat distilled `decision` candidates as high-scrutiny items and only allow them with stronger validation.
- Stop using distillation as a general reflection path; reserve reflection learning for the dedicated reflection service.
- Add conservative novelty checks so low-value or repeated facts do not accumulate over time.
- Add tests covering repeated-session distillation, duplicate suppression, and conflicting candidate handling.
- Update documentation so memory is described accurately: explicit typed memory writes are authoritative; distillation only proposes fallback candidates.

### Reflection

Reflection is one of HermitCrab's strongest differentiators and should be treated as a core product feature.

TODO:
- Strengthen reflection validation so each reflection is grounded in concrete evidence from the session.
- Require reflection outputs to point to the user behavior, correction, or repeated pattern that triggered the learning.
- Add duplicate and contradiction checks before writing new reflections.
- Make bootstrap-file auto-promotion stricter than plain reflection writing.
- Prefer promoting corrections, preferences, and workflow learnings over vague "insights".
- Add tests for reflection deduplication, contradiction handling, and promotion gating.
- Keep reflection focused on user-specific adaptation, not generic summaries or bug logging.

### Reliability and Cleanliness

HermitCrab should feel dependable before it feels clever.

TODO:
- Continue hardening provider/tool-call boundaries so malformed model output cannot stall the agent loop.
- Expand deterministic guards around background services such as heartbeat, cron, journaling, distillation, and reflection.
- Add more focused regression tests for failure modes that can block the gateway or corrupt memory state.
- Prefer explicit fallback behavior over silent skipping when the system can recover safely.
- Review hot paths for over-coupling between prompts, provider parsing, and execution logic.
- Keep the Python layer authoritative for security, file access, and memory integrity.

### Low-Footprint Product Direction

HermitCrab should sit between bloated generalist agents and ultra-raw minimalist systems:
- more approachable and complete than low-level Rust experiments,
- cleaner and much lighter than large Node-based agent stacks,
- simple to run on modest local hardware,
- still capable of persistence, delegation, and adaptation.

Implementation priorities for that direction:
- Keep default models small and local where possible, especially for background cognition.
- Make every optional background pass skippable and separately configurable.
- Track and reduce memory, startup, and idle overhead across CLI and gateway modes.
- Avoid adding heavy orchestration layers, always-on daemons, or unnecessary in-memory state.
- Favor straightforward architecture over framework-like abstraction growth.
- Preserve fast cold-start and predictable runtime behavior on consumer hardware.

### Product Roadmap

Recommended roadmap order:

1. Memory quality and reflection quality
- Distillation deduplication, reflection evidence checks, stricter promotion rules.

2. Session reliability
- More resilient background task scheduling, failure isolation, and end-of-session processing.

3. Subagent maturity
- Validate real delegation flows, improve task handoff clarity, and keep the main agent responsive.

4. Memory retrieval quality
- Improve how existing memory is surfaced back into active context so stored learnings actually affect behavior.

5. Configuration ergonomics
- Make it easy to run HermitCrab well with a small local setup, while still allowing stronger models when available.

6. Channel robustness
- Keep Nostr and CLI first-class, since they are the primary ways users interact with HermitCrab.
- Treat Telegram, email, and gateway as secondary integrations that should remain solid without driving core architecture decisions.

### Product Standard

When deciding what to build next, prefer work that improves one or more of these:
- reliability under imperfect model output,
- useful memory that stays clean over time,
- better adaptation to a specific user,
- lower hardware and operational footprint,
- simpler architecture with fewer hidden behaviors.

Reject work that mainly adds surface area, prompt churn, or cleverness without improving those five qualities.
