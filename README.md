# 🦀 HermitCrab

**Ultra-lightweight personal AI agent with persistent memory**

[![PyPI](https://img.shields.io/pypi/v/hermitcrab-ai)](https://pypi.org/project/hermitcrab-ai/)
[![Python](https://img.shields.io/badge/python-≥3.11-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## What is HermitCrab?

HermitCrab is a **personal AI assistant** that remembers everything, runs on your hardware, and connects to your favorite chat apps.

Think of it as a **second brain** that:
- 💬 Converses naturally via Nostr, Telegram, or Email
- 🧠 Remembers facts, decisions, goals, and tasks across sessions
- 📝 Keeps a daily journal of what you accomplished
- 🔧 Executes tools (web search, file operations, shell commands)
- 🏠 Runs locally on your machine (privacy-first)

**Same crab, new shell** — Your AI assistant stays the same when you change hardware. Just copy your workspace folder and config to a new machine, and your hermitcrab picks up right where it left off.

---

## ⚡ Quick Start (2 Minutes)

### 1. Install

```bash
pip install hermitcrab-ai
```

### 2. Initialize

```bash
hermitcrab onboard
```

### 3. Set API Key

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

Get API keys: [OpenRouter](https://openrouter.ai/keys) · [Anthropic](https://console.anthropic.com/)

### 4. Chat

```bash
hermitcrab agent
```

**Done!** You now have a personal AI assistant.

---

## 🎯 Key Features

### Persistent Memory

HermitCrab remembers across sessions using **atomic markdown notes** (Obsidian-compatible):

- **Facts** — User preferences, project context
- **Decisions** — Architectural choices (immutable)
- **Goals** — Long-term objectives
- **Tasks** — Actionable items with lifecycle
- **Reflections** — Meta-observations about agent behavior

Example memory file:
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

### Daily Journal

Automatic narrative summaries of what happened each session:

```markdown
---
date: 2026-02-25
session_keys:
  - cli:default
tags:
  - session
---

User explored memory lifecycle design. Identified issues with provider fallback logic.

*Used tools: read_file, web_search*
```

Journal is **non-authoritative** — helps you review, doesn't affect decisions.

### Self-Improvement

HermitCrab learns from experience:

- **Distillation** — Extracts atomic knowledge from sessions
- **Reflection** — Identifies mistakes, patterns, improvements
- **Job-class routing** — Uses cheap local models for background tasks

### Multi-Model Support

Route different tasks to different models:

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5",
      "job_models": {
        "interactive_response": "anthropic/claude-opus-4-5",
        "journal_synthesis": "ollama/llama-3.2-3b",
        "distillation": "ollama/phi-3-mini"
      }
    }
  }
}
```

**Result:** Quality when it matters, cheap/free for background tasks.

---

## 💬 Chat Channels

### Nostr (Primary) 🆕

Decentralized, encrypted DMs via NIP-04:

```json
{
  "channels": {
    "nostr": {
      "enabled": true,
      "private_key": "nsec1...",
      "relays": ["wss://relay.damus.io"],
      "allowed_pubkeys": ["npub1..."]
    }
  }
}
```

**Benefits:** Censorship-resistant, encrypted, no central server

### Telegram

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "BOT_TOKEN_FROM_BOTFATHER",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

### Email

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "imapHost": "imap.gmail.com",
      "smtpHost": "smtp.gmail.com",
      "imapUsername": "your@gmail.com",
      "imapPassword": "app-password"
    }
  }
}
```

---

## 🛠️ Tools

HermitCrab comes with built-in tools:

| Tool | Description |
|------|-------------|
| `read_file` | Read files from workspace |
| `write_file` | Create/modify files |
| `edit_file` | Surgical edits (search/replace) |
| `list_dir` | Browse directories |
| `exec` | Run shell commands |
| `web_search` | Brave web search |
| `web_fetch` | Fetch webpage content |
| `message` | Send messages to chat channels |
| `spawn` | Create subagents for background tasks |
| `cron` | Schedule recurring tasks |

### MCP (Model Context Protocol)

Connect to external MCP servers:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/share"]
      }
    }
  }
}
```

---

## 🏠 Local LLM Deployment

Run HermitCrab entirely offline with local models:

### 1. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
```

### 2. Configure

```json
{
  "providers": {
    "ollama": {
      "api_base": "http://localhost:11434"
    }
  },
  "agents": {
    "defaults": {
      "model": "ollama/llama3.2:3b"
    }
  }
}
```

### 3. Run

```bash
hermitcrab agent
```

**Tip:** Start with small models (3B parameters) for background tasks like journal synthesis and distillation. Use larger models only for interactive responses when quality matters.

---

## 📚 Documentation

| Guide | Description |
|-------|-------------|
| [`SECURITY.md`](SECURITY.md) | Security policy |

**Developer Notes:** Architecture details, API references, and debugging guides are available in the source code and developer documentation files.

---

## 🏗️ Architecture

HermitCrab is **~7,000 lines** of core agent code — 99% smaller than alternatives.

```
hermitcrab/
├── agent/           # Core logic (loop, memory, tools)
├── channels/        # Chat integrations (Nostr, Telegram, Email)
├── providers/       # LLM providers (OpenAI, Anthropic, Ollama, etc.)
├── config/          # Configuration system
├── cli/             # Command-line interface
└── utils/           # Helpers
```

**Design principles:**
- Python is authoritative (LLMs propose, Python decides)
- Memory mutation is deterministic (Tier 0 only)
- External LLMs are optional and untrusted
- Works on weak local hardware

---

## 📦 Installation

### From PyPI (Stable)

```bash
pip install hermitcrab-ai
```

### From Source (Latest)

```bash
git clone https://github.com/HKUDS/hermitcrab.git
cd hermitcrab
pip install -e .
```

### With uv (Fast)

```bash
uv tool install hermitcrab-ai
```

---

## 🔧 Configuration

Config file: `~/.hermitcrab/config.json`

### Essential Sections

```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5",
      "max_tokens": 8192,
      "temperature": 0.1
    }
  },
  "providers": {
    "anthropic": { "apiKey": "..." },
    "openrouter": { "apiKey": "..." },
    "ollama": { "api_base": "http://localhost:11434" }
  },
  "channels": {
    "nostr": { "enabled": true, "private_key": "nsec1..." },
    "telegram": { "enabled": true, "token": "..." }
  },
  "tools": {
    "web": { "braveApiKey": "..." },
    "exec": { "timeout": 60 },
    "restrict_to_workspace": true
  }
}
```

**Note:** Full configuration schema with all options is available in the source code (`hermitcrab/config/schema.py`).

---

## 📊 Comparison

| Feature | HermitCrab | Alternatives |
|---------|------------|--------------|
| **Code Size** | ~7,000 lines | 100k-400k+ lines |
| **Memory** | Atomic markdown files | Database / LLM summaries |
| **Local LLM** | First-class support | Afterthought |
| **Privacy** | Runs entirely offline | Cloud-dependent |
| **Extensibility** | Readable, modifiable | Black box |
| **Deployment** | `pip install` | Docker, Kubernetes |

---

## 🤝 Acknowledgments

**HermitCrab is a fork of [nanobot](https://github.com/HKUDS/nanobot)** by [HKUDS](https://github.com/HKUDS).

We stand on the shoulders of giants:
- Original nanobot architecture © HKUDS (MIT License)
- Inspired by [OpenClaw](https://github.com/openclaw/openclaw)
- Built with [LiteLLM](https://github.com/BerriAI/litellm) for multi-provider support

**Thank you** to the nanobot team for creating the foundation that made HermitCrab possible.

---

## 🗺️ Roadmap

### Completed (2026-02-25)
- ✅ Journal system (daily narrative logs)
- ✅ AgentLoop refactor (phase-separated lifecycle)
- ✅ Model configuration (job-class routing)
- ✅ Distillation (atomic knowledge extraction)
- ✅ Reflection (pattern detection, meta-analysis)
- ✅ Nostr channel (NIP-04 encrypted DMs)
- ✅ Session timeout (30-min inactivity)
- ✅ Local LLM documentation
- ✅ Observability plan

### In Progress
- ⏳ Observability implementation (structured logging + metrics)

### Planned
- 🔜 Integration tests (end-to-end flows)
- 🔜 Journal search functionality
- 🔜 Journal export/backup utilities
- 🔜 Health check endpoint (optional)

---

## 🐛 Troubleshooting

### "No module named 'hermitcrab'"

```bash
pip install --upgrade hermitcrab-ai
```

### "API key not configured"

Edit `~/.hermitcrab/config.json` and add your API key:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  }
}
```

### "Connection refused" (Ollama)

```bash
ollama serve  # Start Ollama server
```

### More Help

- [GitHub Issues](https://github.com/talvasconcelos/hermitcrab/issues)
- Source code documentation (inline comments and type hints)

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

**HermitCrab** is a fork of **nanobot** (MIT License).
Original work © [HKUDS](https://github.com/HKUDS).

---

## 🎉 Get Started

```bash
# Install
pip install hermitcrab-ai

# Initialize
hermitcrab onboard

# Chat
hermitcrab agent
```

**Welcome to the hermitcrab community! 🦀**
