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
