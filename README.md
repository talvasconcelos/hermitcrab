# 🦀 HermitCrab

**A local-first AI agent with deterministic memory and real self-improvement**

[![PyPI](https://img.shields.io/pypi/v/hermitcrab-ai)](https://pypi.org/project/hermitcrab-ai/)
[![Python](https://img.shields.io/badge/python-≥3.11-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## What Is HermitCrab?

HermitCrab is a **personal AI agent** that runs on your hardware, remembers what matters, and improves over time.

It is not a chatbot wrapper.
It is not a cloud SaaS.
It is not a 300k-line framework.

It is a focused, readable, extensible agent core — **6,891 lines of code** — that you can audit yourself with a simple bash script.

Think of it as a **portable second brain**:

* 💬 Converses via Nostr, Telegram, Email, or CLI
* 🧠 Stores structured, atomic memory across sessions
* 📝 Generates narrative journal entries automatically
* 🔍 Distills knowledge and extracts tasks
* 🪞 Reflects on mistakes and patterns
* 🔧 Executes tools safely under Python control
* 🏠 Runs fully offline with local LLMs

**Same crab, new shell.**
Move your `workspace/` folder and config to a new machine, and your agent continues exactly where it left off.

---

# ⚡ Quick Start

### 1. Install

```bash
pip install hermitcrab-ai
```

### 2. Initialize

```bash
hermitcrab onboard
```

### 3. Configure a Model

Edit `~/.hermitcrab/config.json`:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5"
    }
  }
}
```

### 4. Run

```bash
hermitcrab agent
```

You now have a persistent personal AI agent.

---

# 🔁 Agent Lifecycle

HermitCrab is not a stateless chat loop.
It runs a structured lifecycle.

Each session follows:

1. **Interactive Phase**

   * LLM response
   * Tool execution
   * Context includes last interactions + relevant memory

2. **Session End Detection**

   * Manual exit, or
   * 30 minutes of inactivity

3. **Journal Synthesis**

   * Narrative summary of what happened
   * Includes key takeaways and tool usage
   * Appends to daily journal file

4. **Distillation**

   * Extract atomic facts, tasks, decisions, goals
   * Store as structured markdown notes

5. **Reflection**

   * Identify mistakes
   * Detect patterns
   * Suggest internal improvements

Interactive and background phases can use different models.
Cheap local models handle synthesis. Premium models handle reasoning.

This separation keeps costs low and architecture clean.

---

# 🧠 Deterministic Memory

HermitCrab uses **atomic markdown files**, not databases and not opaque embeddings.

Memory lives inside your `workspace/` folder:

```
workspace/
├── memory/
│   ├── facts/
│   ├── decisions/
│   ├── goals/
│   ├── tasks/
│   └── reflections/
├── journal/
└── sessions/
```

All files are:

* Human-readable
* Git-friendly
* Obsidian-compatible
* YAML-frontmatter structured
* Wikilink enabled

Example:

```markdown
---
title: "User prefers dark mode"
type: fact
category: facts
tags: [preference, ui]
confidence: 0.95
---

User explicitly stated preference for dark mode UI.
```

Memory mutation is deterministic.
LLMs propose. Python validates and writes.

No hallucinated state. No hidden vectors. No silent corruption.

---

# 📝 Narrative Journal

HermitCrab automatically generates session summaries.

Journal entries:

* Are narrative, not atomic
* Are appended per session end
* Use the session end timestamp
* Include key interactions and tools used
* Have no authoritative power over memory

Example:

```markdown
---
date: 2026-02-25
session_keys:
  - cli:default
tags: [session]
---

User redesigned memory lifecycle. Identified improvements in provider fallback logic.
Used tools: read_file, web_search.
```

The journal helps:

* Humans review progress
* The agent reflect over time
* Detect patterns in behavior

It is an aid, not a source of truth.

---

# 🪞 Self-Improvement Engine

HermitCrab improves over time through:

### Distillation

Extracts structured knowledge from sessions.

### Reflection

Analyzes:

* Repeated mistakes
* Failed tool usage
* Decision inconsistencies
* Model misrouting

### Model Routing

Different tasks use different models:

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5",
      "job_models": {
        "interactive_response": "anthropic/claude-opus-4-5",
        "journal_synthesis": "ollama/llama3.2:3b",
        "distillation": "ollama/phi-3-mini"
      }
    }
  }
}
```

If a job-class model is unavailable, HermitCrab falls back to the default provider.

Result:

* High quality where needed
* Cheap local processing for background work
* Predictable behavior

---

# 💬 Channels

## Nostr (Primary)

Encrypted DMs via NIP-04.

Decentralized. Censorship-resistant. No central server.

```json
{
  "channels": {
    "nostr": {
      "enabled": true,
      "private_key": "nsec1...",
      "relays": ["wss://relay.damus.io"],
      "allowedPubkeys": ["USER_PUBKEY"]
    }
  }
}
```

## Telegram

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "BOT_TOKEN",
      "allowFrom": ["USER_ID"]
    }
  }
}
```

## Email

IMAP + SMTP integration supported.

---

# 🛠 Tools

HermitCrab includes:

| Tool         | Description             |
| ------------ | ----------------------- |
| `read_file`  | Read workspace files    |
| `write_file` | Create or modify files  |
| `edit_file`  | Structured edits        |
| `list_dir`   | Browse directories      |
| `exec`       | Run shell commands      |
| `web_search` | Brave search            |
| `web_fetch`  | Retrieve page content   |
| `message`    | Send outbound messages  |
| `spawn`      | Launch subagents        |
| `cron`       | Schedule recurring jobs |

Tool execution is controlled by Python.
LLMs cannot mutate state directly.

Workspace restriction can be enforced via:

```json
"tools": {
  "restrict_to_workspace": true
}
```

---

# 🏠 Fully Offline Mode

Install Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
```

Configure:

```json
{
  "providers": {
    "vllm": {
        "apiKey": "ollama",
        "apiBase": "http://localhost:11434/v1",
        "extraHeaders": null
    },
  },
  "agents": {
    "defaults": {
      "model": "ollama/llama3.2:3b"
    }
  }
}
```

HermitCrab now runs entirely on your machine.

Start small models for:

* Journal
* Distillation
* Reflection

Use larger models only when necessary.

---

# 🏗 Architecture

HermitCrab is **6,891 lines of core agent code**.

You can verify this yourself:

```bash
./core_agent_lines.sh
```

Structure:

```
hermitcrab/
├── agent/
├── channels/
├── providers/
├── config/
├── cli/
└── utils/
```

Design principles:

* Python is authoritative
* Memory mutation is deterministic
* External LLMs are optional and untrusted
* Works on weak hardware
* No hidden databases
* No forced cloud dependency

Readable. Hackable. Forkable.

---

# 📊 Philosophy Comparison

| Feature      | HermitCrab      | Typical Agent Framework |
| ------------ | --------------- | ----------------------- |
| Core Code    | 6,891 lines     | 100k+ lines             |
| Memory       | Atomic markdown | Vector DB               |
| Portability  | Copy workspace  | Cloud-tied              |
| Local LLM    | First-class     | Optional                |
| Transparency | Fully auditable | Opaque                  |
| Control      | Python governs  | LLM-driven              |

HermitCrab favors clarity over complexity.

---

# 🗺 Roadmap

### Completed

* Journal system
* Phase-separated AgentLoop
* Model routing
* Distillation
* Reflection
* Nostr integration
* Session timeout
* Local-first deployment

### In Progress

* Observability and structured metrics

### Planned

* Integration tests
* Journal search
* Backup utilities
* Optional health endpoint

---

# 🦀 Why HermitCrab Exists

Modern AI tools are:

* Cloud-bound
* Opaque
* Ephemeral
* Hard to audit
* Expensive to run continuously

HermitCrab exists to prove something simpler:

A personal AI agent can be:

* Local
* Deterministic
* Understandable
* Evolvable
* Small enough to audit

And still powerful.

---

# License

MIT License.

HermitCrab is a fork of nanobot by HKUDS.
Built with gratitude to the original architecture.

---

# Get Started

```bash
pip install hermitcrab-ai
hermitcrab onboard
hermitcrab gateway
```

Build your second brain.
Keep it local.
Make it yours. 🦀
