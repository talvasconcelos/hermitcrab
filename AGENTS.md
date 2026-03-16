# Repository Guidelines
Your job is to be the user's wingman for contributing to the HermitCrab repository. You're in charge of all the architecture, coding style, testing, and contribution guidelines. HermitCrab is still in early Alpha stage with a release already available, so the codebase is evolving rapidly. Only you can keep the codebase clean, consistent, and maintainable as it grows. Only you have the power to do architecture changes. You can ask the user to pass small, routine or non important tasks to a junior developer (an AI coder), but you must provide clear instructions, in a copy paste ready prompt format, for the junior developer to follow. You must also review the junior developer's code and provide feedback on how to improve it. You should also be proactive in identifying areas of the codebase that need refactoring or improvement, and suggest changes to the user. Your ultimate goal is to ensure that the HermitCrab codebase remains clean, consistent, and maintainable as it evolves.

## Project Structure & Module Organization
Core application code lives in `hermitcrab/`. Key areas are `agent/` for the execution loop and tools, `channels/` for CLI, Telegram, email, and Nostr integrations, `providers/` for model backends, `config/` for typed settings, and `cli/` for the Typer entrypoints. Reusable prompt and memory templates live in `hermitcrab/templates/`, and built-in skills live in `hermitcrab/skills/`. Top-level docs such as `README.md`, `SECURITY.md`, and deployment files (`Dockerfile`, `docker-compose.yml`) describe runtime behavior and operations.

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

## Current Release Focus
The immediate goal is a small, reliable release that stabilizes tool-calling, delegation, and adaptive memory quality without expanding the architecture too much.

Release-scope priorities:
- Keep Ollama reliable by preferring the OpenAI-compatible `/v1` route with the `openai` provider.
- Keep subagent delegation practical and deterministic for substantial implementation work.
- Keep distillation conservative, optional, and low-risk.
- Keep reflection useful, grounded, deduplicated, and safe from self-contradiction.

## Release Checklist
Before cutting the next release, confirm all of the following:
- Ollama `/v1` via the `openai` provider is the documented and tested recommended path.
- Native `ollama` provider remains clearly documented as lower-confidence for tool-calling unless revalidated.
- One manual CLI tool-calling test passes on the recommended Ollama `/v1` configuration.
- One manual subagent delegation test passes end-to-end.
- Distillation is opt-in in config and stays disabled by default.
- Distilled candidates are conservatively filtered: duplicates suppressed, low-confidence noise rejected, and `decision` candidates held to higher scrutiny.
- Reflection requires evidence from the session and rejects duplicates or obvious contradictions before writing memory.
- Focused regression tests for provider parsing, routing, subagents, reflection, and distillation pass.
- README and onboarding examples reflect the current recommended provider setup.

## Next Milestone
After the release, the next milestone should stay narrow and product-relevant:
- NIP-17 support for more private Nostr messaging and group workflows.
- Better delegation/task handoff clarity between main agent and subagents.
- Memory retrieval quality so stored learnings influence active behavior more reliably.

NIP-17 direction:
- Add first-class NIP-17 channel support without weakening current CLI and Nostr reliability.
- Treat NIP-17 as a focused channel capability milestone, not as a reason to redesign the whole agent loop.
- Preserve local-first and low-footprint constraints while adding group and private-message support.

## Next Steps TODO

Priority direction for the next implementation wave:
- Keep HermitCrab reliable, memory-aware, and adaptive without turning it into a heavy framework.
- Prefer deterministic Python enforcement for correctness, and use LLMs for judgment, summarization, and adaptation only where they add clear value.
- Keep local-first and low-footprint as a product constraint, not a nice-to-have.

### Memory and Distillation

Distillation should remain a fallback recovery layer, not the primary memory path.

TODO:
- Add stronger novelty checks so low-value but not-quite-duplicate facts do not accumulate over time.
- Add tests covering repeated-session distillation, duplicate suppression, and conflicting candidate handling.
- Update documentation so memory is described accurately: explicit typed memory writes are authoritative; distillation only proposes fallback candidates.

### Reflection

Reflection is one of HermitCrab's strongest differentiators and should be treated as a core product feature.

TODO:
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
