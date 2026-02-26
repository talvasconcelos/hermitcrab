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

It’s small (under 7,000 lines of core code), readable, auditable, and built around one simple idea:  
**Your AI should remember what matters to you — forever — without turning into a black box.**

Think of it as a **second brain** you can carry in your pocket (or copy to a new laptop/VPS in seconds).  
Just move the `workspace/` folder and you’re back in business — same memories, same personality, same progress.

### Why people may be drawn to it

- Runs **fully offline** with local models (Ollama default)  
- Remembers things in **plain, human-readable Markdown files** (Obsidian compatible, git-friendly)  
- Automatically **distills** conversations into facts, tasks, decisions, goals, reflections  
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
   Edit `~/.hermitcrab/config.json` to point to your favorite model (local or cloud).  
   Then just:
   ```bash
   hermitcrab agent
   ```

You’re now talking to your own persistent, memory-aware agent.

### How the agent actually thinks & remembers

HermitCrab is **not** a stateless chat loop.  
Every session follows a clean lifecycle:

1. You talk → agent responds → tools run if needed  
2. Session ends (you exit, or 30 min of silence)  
3. **Journal synthesis** — narrative summary of what happened (cheap model)  
4. **Distillation** — extracts new facts, tasks, goals, decisions (cheap model)  
5. **Reflection** — looks for mistakes, contradictions, patterns (smarter model)

All extracted knowledge lands as tiny, atomic Markdown notes in `workspace/memory/`:

```
workspace/
├── memory/
│   ├── facts/          # preferences, hard truths
│   ├── decisions/      # choices & reasoning
│   ├── goals/          # long-term objectives
│   ├── tasks/          # things to do (with deadlines & status)
│   └── reflections/    # self-analysis, cleanups
├── journal/            # narrative session summaries
└── sessions/           # raw chat logs (for debugging)
```

Everything is:
- Human-readable & editable (open in Obsidian, Vim, Notepad)
- Structured with YAML frontmatter
- Wikilink-friendly
- Deterministic — Python, not the LLM, writes the files

No vector databases. No silent embeddings. No hidden state corruption.

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
| message           | Reply to you                              |
| spawn             | Launch sub-agents           |
| cron              | Schedule recurring jobs                   |

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
- Forkable, hackable, understandable

### Comparison — why this feels different

| Aspect              | HermitCrab                          | Typical AI Framework / Chatbot      |
|---------------------|-------------------------------------|-------------------------------------|
| Core code size      | ~7k lines                           | 50k–300k+ lines                     |
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
Let’s make it remember everything that matters.


