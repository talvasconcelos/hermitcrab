# Sessions and memory

How HermitCrab remembers what matters across conversations.

## Sessions

Every conversation is a session. Sessions track messages, tool usage, and metadata.

### Session lifecycle

1. You start chatting — a new session opens (or a prior one resumes)
2. Messages accumulate with tool calls and responses
3. Session ends when you exit or after 30 minutes of inactivity
4. Session is archived to `workspace/sessions/archive/`
5. Background cognition runs: journal synthesis, optional distillation, reflection

### Session keys

Sessions are identified by keys:

| Channel | Key format |
|---------|-----------|
| CLI | `cli:direct` or `cli:<session-name>` |
| Nostr | `nostr:<sender_pubkey>` or `nostr:<workspace>:<sender_pubkey>` in multi-workspace |
| Telegram | `telegram:<chat_id>` |
| Email | `email:<sender_address>` |

### Resume sessions

```bash
hermitcrab agent -s "cli:my-project"
```

Opens or resumes the named session.

### Session storage

Sessions are stored as JSONL files in `workspace/sessions/`. They are primarily for debugging and recall — not the knowledge base.

## Memory system

HermitCrab's memory is deterministic, file-based, and human-readable. Every memory item is an atomic Markdown file with YAML frontmatter.

### Memory categories

| Category | Purpose | Example |
|----------|---------|---------|
| `facts` | Preferences, attributes, persistent truths | "User lives in Lisbon" |
| `decisions` | Choices and reasoning (immutable) | "Chose Flask over FastAPI because..." |
| `goals` | Long-term objectives | "Run 3x per week" |
| `tasks` | Actionable items with status and deadlines | "File taxes by April 30" |
| `reflections` | Self-analysis and pattern recognition | "I tend to over-explain technical concepts" |

### How memory is written

**Direct writes** — when you say "remember X" or the agent extracts a fact during conversation, a tool writes a Markdown file:

```markdown
---
id: a1b2c3d4e5f6
category: facts
title: User's doctor
created: 2025-03-15T10:30:00Z
---

Dr. Silva, office in Cascais.
```

**Background distillation** (optional, off by default) — after sessions end, a cheap local model reviews the conversation and proposes facts, tasks, decisions, and goals. The main agent's direct writes are always authoritative.

### Memory storage layout

```
workspace/memory/
├── facts/
│   └── users-doctor-silva.md
├── decisions/
│   └── chose-flask-over-fastapi.md
├── goals/
│   └── run-3x-per-week.md
├── tasks/
│   └── file-taxes-2026.md
└── reflections/
    └── over-explain-technical-concepts.md
```

### Duplicate prevention

When writing facts, the system checks for existing items covering the same content. Near-duplicates are rejected to prevent memory bloat.

### Task lifecycle

Tasks progress through states:

```
open → in_progress → done
               ↘ deferred
```

Tasks with past-due deadlines are flagged during session starts and heartbeat cycles.

## Knowledge library

Separate from memory, the knowledge library stores reference material:

```
workspace/knowledge/
├── articles/
├── books/
├── docs/
└── notes/
```

Ingest content with:

```
Save this article to my knowledge library: https://example.com/article
```

Search it with:

```
What do I know about async Python?
```

## Scratchpads

Every session has a scratchpad file at `workspace/scratchpads/<session>.md`. It holds transient working notes — intermediate reasoning, tool outputs, and draft thoughts.

Scratchpads are archived on session end and excluded from distillation. They are debugging and working-memory artifacts, not long-term knowledge.

## People

HermitCrab tracks named people you interact with:

```
workspace/people/
├── profiles/
│   └── sarah.md
└── interactions/
    └── sarah.json
```

Profiles store persistent attributes (relationships, preferences, context). Interaction history tracks follow-ups, reminders, and engagement patterns.

Tell the agent about someone:

```
Sarah is my colleague. She works on the payments team and prefers email over Slack.
```

The agent creates a profile and links future interactions to it.

## Managing memory manually

### Read memory

```
What do you know about my schedule?
```

Or use the memory tools directly in agent mode — the agent searches automatically.

### Edit memory

Memory files are plain Markdown. Open them in any editor and modify. The agent picks up changes on next read.

### Delete memory

Delete the Markdown file from the appropriate category directory. Or ask the agent:

```
Forget that I live in Porto.
```

The agent removes the matching fact.

## Memory and privacy

Memory lives in your workspace folder. It is:

- Human-readable and editable
- Not encrypted at rest (use filesystem encryption if needed)
- Portable — copy the workspace to move or backup

Sensitive data stored memory is subject to the same file-level access controls as the rest of the workspace. Config files (`config.json`) are created with restricted permissions (chmod 600/700).
