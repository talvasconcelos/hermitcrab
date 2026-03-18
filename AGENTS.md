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
- LiteLLM's Ollama routing remains a reliability risk. HermitCrab should stop depending on it as the long-term tool-calling path and move toward a native Ollama `/api/chat` transport in Python.
- OpenClaw's `v2026.3.12` Ollama work is the current best reference: first-class `api: "ollama"` routing, direct `/api/chat` transport, NDJSON stream handling, intermediate tool-call accumulation, `/api/tags` discovery, `/api/show` context-window enrichment, and centralized `/v1` stripping.
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

### Immediate Post-Alpha-3 Milestone

The next implementation focus after `0.1.0a3` should be memory retrieval quality.

High-prio:
- Rebuild Ollama integration around a native Python transport instead of relying on LiteLLM's Ollama routing. Treat this as a blocking reliability item within the next milestone, not a side experiment.

High-prio Ollama-native plan:
- Phase 1: Add a centralized Ollama base-URL normalizer that strips trailing slashes and `/v1` when the native API is required, while keeping explicit compat-mode behavior available when intentionally requested.
- Phase 2: Introduce a first-class native Ollama provider or transport path in Python that posts directly to `/api/chat` for chat plus tool-calling flows, instead of routing Ollama through LiteLLM's OpenAI-compatible abstractions.
- Phase 3: Implement native NDJSON stream parsing for Ollama responses and accumulate assistant text and tool calls across intermediate chunks, because Ollama may emit tool calls before the final `done: true` chunk.
- Phase 4: Preserve large integer tool arguments exactly when parsing native Ollama JSON so IDs and external platform identifiers are not corrupted by float or integer coercion.
- Phase 5: Convert HermitCrab's internal transcript format to Ollama-native request messages cleanly, including assistant `tool_calls`, tool result messages, user multimodal payloads, and explicit provider-safe message-key filtering.
- Phase 6: Inject `options.num_ctx` and `num_predict` on the native path so large system prompts and tool schemas do not silently collapse into Ollama's smaller default context window.
- Phase 7: Add native Ollama model discovery helpers using `/api/tags` plus best-effort `/api/show` context inspection so configured local models, context windows, and reasoning heuristics are grounded in the running Ollama instance.
- Phase 8: Keep LiteLLM-backed Ollama only as an explicit compatibility fallback until the native path is validated, then simplify or remove Ollama-specific repair logic that only exists to compensate for LiteLLM routing quirks.

High-prio extraction targets from OpenClaw `v2026.3.12`:
- Runtime routing split: first-class `api: "ollama"` handling instead of treating Ollama as just another OpenAI-compatible endpoint.
- Native transport details: `/api/chat`, NDJSON streaming, tool-call accumulation from non-final chunks, and per-request `num_ctx`.
- Discovery utilities: `/api/tags`, `/api/show`, model context-window enrichment, and centralized `/v1` stripping.
- Separation of concerns: native Ollama transport should own Ollama protocol details so the main agent loop is not forced to recover raw JSON or XML-like pseudo-tool-calls for the recommended Ollama path.

High-prio acceptance criteria:
- Native Ollama `/api/chat` becomes the documented recommended path for HermitCrab tool calling.
- One manual CLI tool-calling session passes end-to-end on the native Ollama provider with no fallback JSON/XML tool-call recovery required.
- One manual subagent delegation session passes end-to-end on the native Ollama provider.
- Focused regression tests cover native Ollama streaming, intermediate tool-call chunks, large integer arguments, `num_ctx` injection, `/v1` normalization, and model discovery.
- README and onboarding guidance are updated to stop presenting LiteLLM/OpenAI-compatible Ollama routing as the preferred long-term setup once the native path is validated.

Priority:
- Improve how existing memory is surfaced back into active context so stored learnings reliably affect live behavior.
- Strengthen retrieval ranking, deduplication, category selection, and context injection before adding more product surface area.
- Add focused regression tests for relevant memory not being surfaced, irrelevant memory polluting context, and repeated retrieval of duplicate or contradictory items.
- Add history-window boundary repair so truncated session slices never start with orphaned tool results after assistant tool calls fall out of the window.
- Add prompt-token estimation helpers for retrieval budgeting, prompt-window diagnostics, and context-window debugging.
- Centralize provider-safe assistant message construction so tool calls, reasoning fields, and provider-specific message keys do not drift between loops, subagents, and providers.
- Keep session-key override support in mind for future thread-aware and NIP-17 channel work, but do not let it drive this milestone.

Direction:
- Start by improving retrieval on top of the current structure rather than redesigning memory around a new subsystem.
- Keep the Ollama-native migration narrow: improve the provider boundary and transport correctness without turning it into a large provider-architecture rewrite.
- Semantic search is worth exploring only if it clearly improves retrieval quality and stays lightweight.
- If semantic retrieval is explored, treat it as an optional enhancement layer over the existing memory model, not a reason to replace the current typed/file-backed memory architecture.
- QMD or another lightweight local semantic index can be evaluated, but only if it stays deterministic enough, low-footprint enough, and testable enough for HermitCrab's product constraints.

### Product Standard

When deciding what to build next, prefer work that improves one or more of these:
- reliability under imperfect model output,
- useful memory that stays clean over time,
- better adaptation to a specific user,
- lower hardware and operational footprint,
- simpler architecture with fewer hidden behaviors.

Reject work that mainly adds surface area, prompt churn, or cleverness without improving those five qualities.
