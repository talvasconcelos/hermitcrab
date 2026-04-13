# 🦀 HermitCrab  
**Your local, private AI companion that actually remembers — and gets better over time**

[![PyPI version](https://img.shields.io/pypi/v/hermitcrab-ai)](https://pypi.org/project/hermitcrab-ai/)
[![Python ≥3.11](https://img.shields.io/badge/python-≥3.11-blue)](https://python.org)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Current release line: `0.1.0b1` beta

This is the first beta line: usable, local-first, and already good for real daily workflows, but still actively hardening around edge cases, polish, and long-session reliability.

### What is HermitCrab, really?

HermitCrab is a **personal AI agent** you run on your own machine.  
It’s not another cloud wrapper, not a bloated framework, not yet another SaaS subscription trap.  

It’s lean, readable, auditable, and built around one simple idea:  
**Your AI should remember what matters to you — forever — without turning into a black box.**

Think of it as a **second brain** you can carry in your pocket (or copy to a new laptop/VPS in seconds).  
Just move the `workspace/` folder and you’re back in business — same memories, same personality, same progress.

### Why people may be drawn to it

- Supports **fully offline** operation with local models (native Ollama or OpenAI-compatible local routes)  
- Remembers things in **plain, human-readable Markdown files** (Obsidian compatible, git-friendly)  
- Can **distill** conversations into facts, tasks, decisions, goals, and reflections when that optional background pass is enabled  
- **Reflects** on itself — spots patterns, mistakes, contradictions, and suggests improvements  
- Talks via **Nostr** (primary), Telegram, email, or plain CLI — your choice  
- Stays tiny, fast, and cheap — no 100k+ line monolith
- Aims to stay powerful for operators while still being approachable for normal household use

**Same crab, new shell.**  
Move your workspace anywhere. The agent picks up exactly where it left off.

### Quick Start

**Easy install**

This avoids global `pip` and installs HermitCrab into its own virtual environment under `~/.local/share/hermitcrab`:

```bash
curl -fsSL https://raw.githubusercontent.com/talvasconcelos/hermitcrab/main/scripts/install.sh | bash
```

Optional: also install and enable a user-level gateway service:

```bash
curl -fsSL https://raw.githubusercontent.com/talvasconcelos/hermitcrab/main/scripts/install.sh | bash -s -- --systemd-user --enable-service --start-service
```

The service runs `hermitcrab gateway` via `systemd --user`, which is the right long-running mode for channels, reminders, and heartbeat-driven work.
The installer itself is meant to be generic for Unix-like systems; the `systemd --user` service step is Linux-specific.

**Manual install (3 commands)**

1. **Install**  
   ```bash
   pip install hermitcrab-ai
   ```

2. **Set up your workspace & config**  
   ```bash
   hermitcrab onboard
   ```
   (creates `~/.hermitcrab/` with config and empty workspace)

3. **Pick a model & run**

   **Option A: Local Ollama (recommended for privacy & free)**
   
   a. Install Ollama:
   ```bash
   # macOS
   brew install ollama
   
   # Linux
   curl -fsSL https://ollama.com/install.sh | sh
   
   # Start Ollama (runs in background)
   ollama serve
   ```
   
   b. Pull a model:
   ```bash
   ollama pull gemma4:e4b  # Fast thinking model
   # Or: ollama pull llama3.1:8b      # General purpose
   # Or: ollama pull qwen3.5:7b # Coding specialist
   ```
   
   c. Edit `~/.hermitcrab/config.json`:
   ```json
   {
     "providers": {
       "ollama": {
         "apiKey": "",
         "apiBase": "http://localhost:11434"
       }
     },
     "models": {
       "main": {
         "model": "ollama/gemma4:e4b"
       },
       "localCoder": {
         "model": "ollama/qwen3.5:7b"
       }
     },
     "agents": {
       "modelAliases": {
         "coder": "localCoder"
       },
       "defaults": {
         "model": "main",
         "jobModels": {
           "subagent": "localCoder"
         }
       }
     }
   }
   ```

   Advanced local Ollama example with named models, cloud-routed models, and optional shorthand aliases:
   ```json
   {
     "providers": {
       "ollama": {
         "apiKey": "",
         "apiBase": "http://localhost:11434"
       }
     },
     "models": {
       "main": {
         "model": "ollama/glm-5:cloud"
       },
       "coder": {
         "model": "ollama/qwen3.5:4b"
       },
       "fast": {
         "model": "ollama/gemma4:e2b",
         "reasoningEffort": "medium"
       }
     },
     "agents": {
       "modelAliases": {
         "code": "coder"
       },
       "defaults": {
         "model": "main",
         "jobModels": {
           "subagent": "coder",
           "reflection": "fast",
           "reasoningEffort": "medium"
         }
       }
     }
   }
   ```
   Notes:
   - For Ollama, use the dedicated `ollama` provider.
   - Set `providers.ollama.apiBase` to your Ollama server root, typically `http://localhost:11434` with no `/v1` suffix.
   - Use `ollama/...` model IDs for local Ollama models and Ollama-routed cloud models.
   - Prefer the top-level `models` section as the canonical place for model definitions.
   - Per-model `providerOptions` can be used to tune Ollama behavior such as `num_ctx`, `temperature`, `max_tokens`, and related runtime settings.
   - `agents.modelAliases` is optional shorthand for runtime ergonomics; it is not required if your named model keys are already concise.
   - Subagents can use named models directly, or aliases when you want shorter operator-facing names.
   
   **Option B: Cloud model (OpenRouter)**
   ```bash
   # Get API key at https://openrouter.ai/keys
   ```
   Edit `~/.hermitcrab/config.json`:
   ```json
   {
     "providers": {
       "openrouter": {
         "apiKey": "sk-or-..."
       }
     },
     "agents": {
       "defaults": {
         "model": "anthropic/claude-sonnet-4"
       }
     }
   }
   ```

   Then run:
   ```bash
   hermitcrab agent
   ```

   Notes:
   - OpenRouter should be configured under `providers.openrouter`, not `providers.custom`.
   - Recommended model forms are `anthropic/...`, `openai/...`, `google/...`, and similar upstream model IDs.
   - `openrouter/anthropic/...` also works if you want to be explicit.
   - If OpenRouter is your only configured provider, HermitCrab will still route the default `anthropic/claude-opus-4-5` model through OpenRouter.

You're now talking to your own persistent, memory-aware agent.

### Useful first commands

```bash
hermitcrab agent      # interactive local chat
hermitcrab status     # quick runtime and config status
hermitcrab doctor     # diagnose config/provider issues
hermitcrab gateway    # run configured channels
```

### How the agent actually thinks & remembers

HermitCrab is **not** a stateless chat loop.  
Every session follows a clean lifecycle:

1. You talk → agent responds → tools run if needed  
2. Session ends (you exit, or 30 min of silence)  
3. **Journal synthesis** — narrative summary of what happened (cheap model)  
4. **Optional distillation** — proposes fallback facts, tasks, goals, and decisions when enabled  
5. **Reflection** — looks for mistakes, contradictions, patterns (smarter model)
6. **Scratchpad archival** — per-session transient notes are archived on session end

All extracted knowledge lands as tiny, atomic Markdown notes in `workspace/memory/`:

```
workspace/
├── memory/
│   ├── facts/          # preferences, hard truths
│   ├── decisions/      # choices & reasoning (immutable)
│   ├── goals/          # long-term objectives
│   ├── tasks/          # things to do (with deadlines & status)
│   └── reflections/    # self-analysis, cleanups
├── knowledge/          # reference library (articles, docs, notes)
├── journal/            # narrative session summaries
├── scratchpads/        # per-session transient working notes
└── sessions/           # raw chat logs (for debugging)
```

Everything is:
- Human-readable & editable (open in Obsidian, Vim, Notepad)
- Structured with YAML frontmatter
- Wikilink-friendly
- Deterministic — Python, not the LLM, writes the files

No vector databases. No silent embeddings. No hidden state corruption.

Distillation is conservative and optional by design. Explicit memory writes remain authoritative.

### Scratchpad and channel prompts

- Every session has a dedicated scratchpad file at `workspace/scratchpads/<session>.md`.
- Scratchpad is transient by design: it is archived to `workspace/scratchpads/archive/` on session end.
- Scratchpad traces are excluded from distillation so transient reasoning doesn't pollute long-term memory.
- Optional per-channel prompt overlays:
  - `workspace/prompts/<channel>.md`
  - `workspace/prompts/<channel>/<chat_id>.md`

### Channels — where you talk to your crab

- **Nostr** (default / primary) — encrypted DMs (NIP-04 + NIP-17 groups coming)  
- **Telegram** — classic bot  
- **Email** — IMAP/SMTP  
- **CLI** — quick local chats

All channels feed into the same memory & reflection engine.

### Tools — what the agent can actually do

| Tool              | What it does                              |
|-------------------|-------------------------------------------|
| read_file         | Peek at files in workspace                |
| write_file        | Create / overwrite files                  |
| edit_file         | Precise replacements                      |
| list_dir          | Browse directories                        |
| exec              | Run safe shell commands                   |
| web_search        | DuckDuckGo search (no API key needed)     |
| web_fetch         | Fetch & extract URL content (sanitized)   |
| knowledge_search  | Search your knowledge library             |
| knowledge_ingest  | Save articles/docs to library             |
| message           | Reply to you on the active channel        |
| spawn             | Launch sub-agents                         |
| cron              | Schedule recurring jobs                   |

**Security:** Web content is automatically sanitized to remove prompt injection attacks, hidden instructions, and encoded payloads.

Execution is **always** gated by Python — the LLM can only propose.

### Self-Improvement — the part that actually matters

HermitCrab gets smarter over time by:

- **Distilling** conversations → new facts/tasks/goals/reflections
- **Reflecting** on patterns → mistakes, contradictions, model misbehavior
- **Routing** jobs to the right model:
  - Interactive replies → strong model (Claude, GPT-4o, etc.)
  - Journal + distillation → cheap local (Llama 3.2 3B, Phi-3-mini)
  - Reflection → medium model

This keeps costs low while letting the agent learn without constant supervision.

### Subagents and models

HermitCrab can delegate longer-running or specialized work to subagents while the main agent stays responsive.

- Define reusable models in top-level `models`
- Set a dedicated subagent model in `agents.defaults.jobModels.subagent`
- Optionally add short aliases in `agents.modelAliases` for runtime convenience
- The agent can use either named models or aliases when spawning delegated work

Example use cases:
- "Build a simple website for X, use the coder subagent"
- "Investigate this bug in the background and report back"

### Architecture at a glance

HermitCrab is intentionally kept lean enough to read, debug, and adapt without inheriting a giant framework.

```
hermitcrab/
├── agent/         # loop, tools, memory handling
├── channels/      # Nostr, Telegram, email, CLI
├── providers/     # LLM abstraction (litellm + fallbacks)
├── config/        # typed config loading
├── cli/           # typer-based interface
└── utils/         # helpers
```

Design rules we live by:
- Python is the source of truth — LLM is untrusted
- Memory is deterministic & auditable
- Local-first by default
- Small enough to read in a weekend
- Hackable, understandable

### Runtime safety defaults

Production-minded defaults are in `hermitcrab/config/schema.py` and are written into `~/.hermitcrab/config.json` on `hermitcrab onboard`.

- LLM retries with exponential backoff
- Max response loop time cap
- Repeated tool-cycle detection (loop break)
- Bounded memory context injection
- Reflection auto-promotion disabled by default (safer file integrity)

### Comparison — why this feels different

| Aspect              | HermitCrab                          | Typical AI Framework / Chatbot      |
|---------------------|-------------------------------------|-------------------------------------|
| Core code size      | Lean Python codebase                | 50k–300k+ lines                     |
| Memory              | Atomic Markdown                     | Vector DB or forgotten             |
| Portability         | Copy workspace → works              | Cloud account locked                |
| Transparency        | Fully auditable                     | Opaque internals                    |
| Cost                | Local models cheap                  | API calls add up fast               |
| Self-improvement    | Built-in distillation & reflection  | Rare or manual                      |

### Beta focus

For `0.1.0b1`, the priorities are:

- strong local-first UX
- clean, low-duplication memory
- reliable tool use and session continuity
- smooth onboarding and diagnostics
- a product that feels good for both power users and everyday household use

### Why I built this

Most AI tools today are:
- Tied to someone else’s cloud
- Forget everything after 4k tokens
- Impossible to truly understand or audit
- Expensive to run 24/7

HermitCrab exists to prove a quieter truth:

A personal AI can be **small**, **local**, **private**, **deterministic**, and still **grow with you** — without turning into a 200k-line monster or a subscription bill.

Keep it yours. Keep it local. Keep it simple. 🦀

### Get started

```bash
curl -fsSL https://raw.githubusercontent.com/talvasconcelos/hermitcrab/main/scripts/install.sh | bash
hermitcrab doctor
hermitcrab agent
```

Welcome to your own second brain.
Let's make it remember everything that matters.

### Docker

`Dockerfile` and `docker-compose.yml` build/run HermitCrab directly.

- Build: `docker compose build`
- Run gateway: `docker compose up -d hermitcrab-gateway`

## 🤝 Acknowledgments

**HermitCrab is a fork of [nanobot](https://github.com/HKUDS/nanobot)** by [HKUDS](https://github.com/HKUDS).

We stand on the shoulders of giants:
- Original nanobot architecture © HKUDS (MIT License)
- Inspired by [OpenClaw](https://github.com/openclaw/openclaw)

**Thank you** to the nanobot team for creating the foundation that made HermitCrab possible.

Persisted data lives at `~/.hermitcrab` and can be mounted into containers when you use Docker.
