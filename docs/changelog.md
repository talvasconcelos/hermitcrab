# Changelog

All notable changes to HermitCrab.

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
