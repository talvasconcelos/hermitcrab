# Changelog

All notable changes to HermitCrab.

## [0.1.0b5] — 2026-09-04

### Added

- **SQLite-backed session storage** — sessions now live in `sessions.sqlite3` with automatic one-time migration from legacy JSONL files (imported, backed up under `jsonl-migrated-backup/`, then marked complete). Faster listing, search, and export.
- **Onboarding state gate** — new identities run a guided onboarding mode; `hermitcrab onboarding` (status/pause/resume/complete) manages it. Existing identities are unaffected.
- **Configurable context window** — `agents.defaults.contextWindow` for an explicit override, or auto-derived from the model's known context length with a 20% buffer (fallback 32k). Per-model override via `providerOptions.num_ctx` or `model add --context-window`.
- **Deferred tool schemas** — rarely-needed tools (reminders, people, subagents, message, cron, URL ingest, MCP) are withheld behind a `tool_search` discovery tool, cutting per-turn tool-schema tokens ~30% while keeping every capability reachable.
- **Grounded reflection/distillation** — reflection and distillation decisions now require supporting evidence; low-value reflections are rejected.
- **CLI** — `model add --context-window` and a "Context" column in `model list`.

### Changed

- **Email channel decommissioned** — operational email code removed; an inert config tombstone keeps old configs parseable.
- **Ollama reasoning effort honored** — `reasoningEffort` now maps to Ollama's `think` parameter (previously ignored by the native provider).
- **DST-safe cron** — schedules resolve and persist the local IANA timezone (`tzlocal`).
- **CLI refactor** — commands split into focused modules; no user-facing behavior change.
- **Hardened fresh installation** and bootstrap edge cases.

### Fixed

- **Shell**: argv-based execution; shell metacharacters require a separately-approved shell mode (closes an operator bypass).
- **Web**: SSRF blocked (private/reserved IPs), redirects re-validated, and DNS rebinding closed by pinning to one validated address.
- **Knowledge**: URL-derived items are marked untrusted and sanitized with a warning at retrieval.
- **Nostr**: NIP-04 signatures verified before decrypt; relayed id must match the canonical id; nsec is never logged (npub only).
- **Telegram**: fail-closed allowlists; inbound logs carry metadata only (never transcription content); UUID media names.
- **Codex**: never downgrades TLS verification.
- **Sessions**: WAL + busy-timeout, a single UTC clock, and hardened filenames/heartbeat bounds.
- **Agent**: per-turn contextvars for send-tracking, destructive approval, and tool routing; subagent escalation validated against its profile.

## [0.1.0b4] — 2026-06-14

### Added

- Concise `/capabilities` diagnostics for operator-facing runtime visibility
- OpenRouter attribution headers for provider requests

### Changed

- Preserved recent session context more reliably across turns
- Reduced surprising progress/tool hint behavior during agent runs
- Improved partial reflection-output salvage so useful reflections are not discarded unnecessarily
- Refreshed release metadata and public setup examples for the beta4 line

## [0.1.0b3] — 2026-04-24

### Added

- Nostr NIP-17 direct-message support alongside legacy NIP-04 handling
- Owner-managed identity roots with Nostr pubkey routing
- `hermitcrab user` commands for listing, bootstrapping, and resolving routes
- Durable audit trail views via `hermitcrab audit`
- Expanded operator documentation for gateway operations, identity routing, observability, and recovery

### Changed

- Strengthened tool permission policy behavior with structured denial hints and audit events
- Improved runtime diagnostics surfaced through `status` and `doctor`
- Standardized runtime state under `system/` and `identities/<name>/`

## [0.1.0b2] — 2025

### Added

- One-command installer for a clean local setup under `~/.local/share/hermitcrab`
- Stronger onboarding and diagnostics for getting providers and runtime config working
- Filesystem-backed reminder artifacts and more reliable reminder delivery
- First `people` primitive with profiles, linked follow-ups, interaction history, primary-person handling, and duplicate guards
- Tighter prompt history, session cognition, and resume ordering for long-lived conversations
- Harder edges around destructive shell actions and other trust-sensitive flows

### Changed

- Improved session archival and scratchpad handling
- Refined tool permission policy enforcement with structured denial hints

## [0.1.0b1] — Initial beta

### Added

- Personal AI agent with local-first, memory-first architecture
- Deterministic Markdown-based memory system (facts, decisions, goals, tasks, reflections)
- Multi-channel support: Nostr (NIP-04), Telegram, email, CLI
- Multi-model routing: interactive replies, journal synthesis, distillation, reflection
- Tool system with permission levels and policy enforcement
- Subagent delegation with profile-based tool filtering
- Structured skill system with SKILL.md frontmatter
- Background cognition: journal synthesis, optional distillation, reflection
- Session lifecycle management with timeout detection
- Audit trail with auto-rotation
- Knowledge library for reference material
- Checklist/list management
- Web search (DuckDuckGo) and URL fetch with sanitization
- Shell execution with safety guards
- MCP server integration
- Docker support via Dockerfile and docker-compose.yml
- Typer-based CLI with interactive prompt_toolkit editing
- 20+ LLM provider support via LiteLLM
- Ollama dedicated provider
- OAuth-based providers: OpenAI Codex, Qwen, GitHub Copilot
- Custom OpenAI-compatible endpoint support
- Workspace bootstrap with template files
- Named model definitions with provider-specific options
- Model aliases with reasoning effort control
- Gateway service with cron, heartbeat, and reminder services
- Multi-workspace support with Nostr pubkey routing
- Session search across current and archived sessions
- People profiles with interaction history
