# Architecture

How HermitCrab is structured under the hood.

## Design principles

- **Python is the source of truth** — the LLM proposes; Python enforces
- **Memory is deterministic and auditable** — plain Markdown files, no hidden state
- **Local-first** — works fully offline with Ollama
- **Small enough to read in a weekend** — lean codebase, not a framework
- **Hackable and understandable** — no magic, no opaque abstractions

## Module map

```
hermitcrab/
├── agent/           # Core agent loop, tools, memory, cognition
│   ├── loop.py      # AgentLoop — main processing loop
│   ├── memory.py    # MemoryStore — deterministic category-based memory
│   ├── tools/       # All tool implementations
│   ├── subagent.py  # SubagentManager — background delegation
│   ├── reflection.py# ReflectionService — self-improvement
│   ├── skills.py    # SkillsLoader — structured skill discovery
│   ├── people.py    # PeopleStore — named people profiles
│   ├── audit.py     # AuditTrail — append-only JSONL log
│   ├── knowledge.py # KnowledgeStore — reference library
│   ├── lists.py     # ListStore — checklist management
│   ├── journal.py   # JournalStore — narrative summaries
│   ├── context.py   # ContextBuilder — prompt assembly
│   ├── turn_runner.py # TurnRunner — individual turn execution
│   └── session_lifecycle.py # Session timeout detection
├── channels/        # External chat platform integrations
│   ├── base.py      # BaseChannel ABC
│   ├── manager.py   # ChannelManager — channel coordination
│   ├── nostr.py     # NostrChannel — NIP-04/NIP-17
│   ├── telegram.py  # TelegramChannel
│   └── email.py     # EmailChannel — IMAP/SMTP
├── providers/       # LLM provider abstractions
│   ├── base.py      # LLMProvider ABC
│   ├── registry.py  # ProviderSpec registry (20+ providers)
│   ├── litellm_provider.py # LiteLLM-backed provider
│   └── ollama_provider.py  # Dedicated Ollama provider
├── config/          # Typed configuration (Pydantic)
│   ├── schema.py    # All config models
│   └── loader.py    # Config loading and migration
├── cli/             # Typer commands and diagnostics
│   ├── commands.py  # All CLI commands
│   └── diagnostics.py # Status and doctor reports
├── cron/            # Scheduled job service
├── heartbeat/       # Periodic agent wake-up service
├── reminders/       # Reminder delivery service
├── session/         # Session lifecycle management
├── bus/             # Message bus and events
└── skills/          # Built-in skills (SKILL.md files)
```

## Key responsibilities

### agent/

The core processing engine. `AgentLoop` orchestrates message processing, tool execution, session management, and background cognition (journal, distillation, reflection).

### channels/

External chat platform integrations. Each channel implements `BaseChannel` with `start()`, `stop()`, `send()`, and `is_allowed()`. The `ChannelManager` coordinates all enabled channels and handles outbound dispatch.

### providers/

LLM provider abstraction layer. Supports 20+ providers via LiteLLM plus dedicated implementations for Ollama, custom OpenAI-compatible endpoints, and OAuth-based providers (OpenAI Codex, Qwen, GitHub Copilot).

### config/

Pydantic-based typed configuration. Loads from `~/.hermitcrab/config.json` with environment variable overrides (`HERMITCRAB__` prefix). Includes multi-workspace validation and provider matching.

### cli/

Typer-based CLI. Commands: `onboard`, `agent`, `gateway`, `status`, `doctor`, `audit`.

### cron/, heartbeat/, reminders/

Background services that run inside the gateway. Cron manages scheduled jobs, heartbeat drives periodic agent wake-ups, and reminders deliver time-based notifications.

### bus/

In-process message bus for decoupled communication between channels, agent loops, and background services.

## Runtime flow

```
User message
  -> Channel receives message
  -> Publishes to MessageBus
  -> Gateway routes to workspace (single or multi-workspace)
  -> AgentLoop processes the turn
     -> ContextBuilder assembles prompt (memory, session history, bootstrap files)
     -> LLM is called via Provider
     -> LLM response parsed (text and/or tool calls)
     -> Tool calls validated by ToolPermissionPolicy
     -> Approved tools execute
     -> Results fed back to LLM for next iteration
     -> Final response published to MessageBus
  -> ChannelManager dispatches response to user's channel
  -> Session updated
  -> On session end: background cognition runs (journal, distillation, reflection)
```

## Complexity hotspots

These modules carry the most behavioral complexity:

- `agent/loop.py` — main orchestration, job routing, background cognition scheduling
- `agent/tools/registry.py` and `policy.py` — dynamic tool registration and permission enforcement
- `channels/nostr.py` — NIP-04/NIP-17 handling, relay discovery, multi-workspace routing
- `cli/commands.py` — all CLI commands, gateway logic, inbound routing
- `config/schema.py` — validation, provider matching, model resolution, workspace routing
- `agent/memory.py` — duplicate detection, category management, thread safety

## Extension points

- **New tools** — subclass `Tool` in `agent/tools/base.py`, register in the tool registry
- **New channels** — subclass `BaseChannel` in `channels/base.py`, wire into `ChannelManager`
- **New providers** — add to the provider registry in `providers/registry.py`
- **New skills** — add a directory under `hermitcrab/skills/` with a `SKILL.md` file
- **New CLI commands** — add to `cli/commands.py` using Typer decorators
