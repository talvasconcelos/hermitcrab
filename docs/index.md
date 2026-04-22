# HermitCrab

**Your local, private AI companion that actually remembers — and gets better over time.**

HermitCrab is a personal AI agent you run on your own machine. It remembers what matters to you in plain Markdown files, distills conversations into actionable knowledge, and reflects on its own behavior to improve over time. No vector databases. No hidden state. No subscription traps.

Move your `workspace/` folder anywhere — your agent picks up exactly where it left off.

## Who this is for

**Normal users** — household members, freelancers, small-team operators who want a dependable AI assistant that remembers context across sessions and channels without complex setup.

**Power users** — developers and tinkerers who want transparent architecture, extensible tools, multi-model routing, and full auditability.

## Quick navigation

### Getting started

| Guide | Solves |
|-------|--------|
| [Installation](getting-started/installation.md) | Install HermitCrab on Linux, macOS, or in Docker |
| [Quickstart](getting-started/quickstart.md) | First conversation in under 2 minutes |
| [Learning path](getting-started/learning-path.md) | Find the right docs for your experience level |

### User guide

| Guide | Solves |
|-------|--------|
| [Daily use](user-guide/daily-use.md) | How to talk to HermitCrab day-to-day |
| [Channels](user-guide/channels.md) | Connect via Nostr, Telegram, email, or CLI |
| [Sessions and memory](user-guide/sessions-and-memory.md) | How memory works and how to manage it |
| [Reminders and cron](user-guide/reminders-and-cron.md) | Schedule tasks and set reminders |
| [Troubleshooting](user-guide/troubleshooting.md) | Fix common problems |
| [Security and permissions](user-guide/security-and-permissions.md) | Understand safety defaults and controls |

### Operator guide

| Guide | Solves |
|-------|--------|
| [Gateway operations](operator-guide/gateway-operations.md) | Run and manage the long-running gateway service |
| [Workspace model](operator-guide/workspace-model.md) | Single and multi-workspace architecture |
| [Backups and recovery](operator-guide/backups-and-recovery.md) | Protect and restore workspace data |
| [Observability and audit](operator-guide/observability-audit.md) | Monitor health and inspect decisions |
| [Incident playbook](operator-guide/incident-playbook.md) | Step-by-step recovery when things go wrong |

### Reference

| Reference | Solves |
|-----------|--------|
| [CLI commands](reference/cli-commands.md) | Every command with flags and examples |
| [Config reference](reference/config-reference.md) | All config fields with defaults and examples |
| [Tools](reference/tools-reference.md) | Every built-in tool and what it does |
| [Channels](reference/channel-reference.md) | Channel setup details and limits |
| [Skills catalog](reference/skills-catalog.md) | Every built-in skill with requirements |
| [FAQ](reference/faq.md) | Common questions answered |

### Developer guide

| Guide | Solves |
|-------|--------|
| [Architecture](developer-guide/architecture.md) | How the pieces fit together |
| [Agent loop](developer-guide/agent-loop.md) | The core message-processing loop |
| [Gateway routing](developer-guide/gateway-routing.md) | How inbound messages reach workspaces |
| [Memory and session model](developer-guide/memory-and-session.md) | Deterministic memory and session lifecycle |
| [Extending tools](developer-guide/extending-tools.md) | Add new tools and skills |
| [Contributing](developer-guide/contributing.md) | How to contribute code and docs |

## Key properties

- **Local-first** — runs on your machine, your VPS, or in Docker. Works fully offline with Ollama.
- **Plain-text memory** — atomic Markdown files with YAML frontmatter, readable in any editor.
- **Self-improvement** — optional distillation extracts facts and tasks; reflection spots patterns and mistakes.
- **Multi-channel** — Nostr (primary), Telegram, email, and CLI from one gateway.
- **Multi-model routing** — interactive replies use your strongest model; background jobs route to cheaper models.
- **Transparent** — Python enforces all tool access. The LLM proposes; Python decides.
- **Portable** — copy your workspace folder to a new machine; everything comes with it.

## Current status

HermitCrab is in **beta** (`0.1.0b2`). It is usable for real daily workflows but may have rough edges in evolving areas like Nostr NIP-17 groups, multi-workspace isolation, and advanced permission UX.

See the [changelog](changelog.md) for version history.
