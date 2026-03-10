# 🦀 HermitCrab  
**Your local, private AI companion that actually remembers — and gets better over time**

[![PyPI version](https://img.shields.io/pypi/v/hermitcrab-ai)](https://pypi.org/project/hermitcrab-ai/)
[![Python ≥3.11](https://img.shields.io/badge/python-≥3.11-blue)](https://python.org)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 🤝 Acknowledgments

**HermitCrab is a fork of [nanobot](https://github.com/HKUDS/nanobot)** by [HKUDS](https://github.com/HKUDS).

We stand on the shoulders of giants:
- Original nanobot architecture © HKUDS (MIT License)
- Inspired by [OpenClaw](https://github.com/openclaw/openclaw)

**Thank you** to the nanobot team for creating the foundation that made HermitCrab possible.

### What is HermitCrab, really?

HermitCrab is a **personal AI agent** you run on your own machine.  
It’s not another cloud wrapper, not a bloated framework, not yet another SaaS subscription trap.  

It’s small (under 6,000 lines of core code), readable, auditable, and built around one simple idea:  
**Your AI should remember what matters to you — forever — without turning into a black box.**

Think of it as a **second brain** you can carry in your pocket (or copy to a new laptop/VPS in seconds).  
Just move the `workspace/` folder and you’re back in business — same memories, same personality, same progress.

### Why people may be drawn to it

- Supports **fully offline** operation with local models (Ollama via LiteLLM)  
- Remembers things in **plain, human-readable Markdown files** (Obsidian compatible, git-friendly)  
- Can **distill** conversations into facts, tasks, decisions, goals, and reflections when that optional background pass is enabled  
- **Reflects** on itself — spots patterns, mistakes, contradictions, and suggests improvements  
- Talks via **Nostr** (primary), Telegram, email, or plain CLI — your choice  
- Stays tiny, fast, and cheap — no 100k+ line monolith

**Same crab, new shell.**  
Move your workspace anywhere. The agent picks up exactly where it left off.

### Quick Start (3 commands)

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
   ollama pull lfm2.5-thinking:latest  # Fast thinking model
   # Or: ollama pull llama3.1:8b      # General purpose
   # Or: ollama pull qwen2.5-coder:7b # Coding specialist
   ```
   
   c. Edit `~/.hermitcrab/config.json`:
   ```json
   {
     "providers": {
       "openai": {
         "apiKey": "ollama",
         "apiBase": "http://localhost:11434/v1"
       }
     },
     "agents": {
       "defaults": {
         "model": "openai/lfm2.5-thinking:latest"
       }
     }
   }
   ```

   Advanced local Ollama example with cloud-routed models, subagent aliases, and reasoning control:
   ```json
   {
     "providers": {
       "openai": {
         "apiKey": "ollama",
         "apiBase": "http://localhost:11434/v1"
       }
     },
     "agents": {
       "modelAliases": {
         "coder": "openai/qwen3.5:4b",
         "fast": "openai/lfm2.5-thinking:latest"
       },
       "defaults": {
         "model": "openai/kimi-k2.5:cloud",
         "jobModels": {
           "subagent": "openai/qwen3.5:4b",
           "reasoningEffort": "medium"
         }
       }
     }
   }
   ```
   Notes:
   - For Ollama, prefer the `openai` provider pointed at `http://localhost:11434/v1`. In practice this has much better tool-calling reliability than LiteLLM's native `ollama` route.
   - The `ollama` provider is still available, but it currently has weaker tool coverage and more provider-specific tool-call quirks.
   - Keep the model name exactly as Ollama exposes it when using the OpenAI-compatible route.
   - Subagents can use aliases such as `coder` for heavier delegated work.
   
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
     }
   }
   ```

   Then run:
   ```bash
   hermitcrab agent
   ```

You're now talking to your own persistent, memory-aware agent.

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
| message           | Reply to you                              |
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

### Subagents and model aliases

HermitCrab can delegate longer-running or specialized work to subagents while the main agent stays responsive.

- Configure aliases in `agents.modelAliases`
- Set a dedicated subagent model in `agents.defaults.jobModels.subagent`
- The agent can use these aliases when spawning delegated work

Example use cases:
- "Build a simple website for X, use the coder subagent"
- "Investigate this bug in the background and report back"

### Architecture at a glance

Total core agent code: 6,927 lines (run `./core_agent_lines.sh` to verify).

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
| Core code size      | ~6k lines                           | 50k–300k+ lines                     |
| Memory              | Atomic Markdown                     | Vector DB or forgotten             |
| Portability         | Copy workspace → works              | Cloud account locked                |
| Transparency        | Fully auditable                     | Opaque internals                    |
| Cost                | Local models cheap                  | API calls add up fast               |
| Self-improvement    | Built-in distillation & reflection  | Rare or manual                      |

### Roadmap (where we're going)

**Done**
- Atomic memory system
- Journal + distillation
- Reflection basics
- Nostr integration
- Local-first deployment

**In progress**
- Observability / metrics
- Full integration tests

**Planned**
- Journal search
- Backup & migration helpers
- Optional health-check endpoint
- Web chat companion (static HTML + Nostr)

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
pip install hermitcrab-ai
hermitcrab onboard
hermitcrab gateway
```

Welcome to your own second brain.
Let's make it remember everything that matters.

### Docker

`Dockerfile` and `docker-compose.yml` build/run HermitCrab directly.

- Build: `docker compose build`
- Run gateway: `docker compose up -d hermitcrab-gateway`
- Persisted data lives at `~/.hermitcrab` (mounted into container).
