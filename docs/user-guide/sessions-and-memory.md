# Sessions And Memory

How HermitCrab remembers what matters across conversations.

## Sessions

Every conversation is a session. Sessions track messages, tool usage, and metadata.

### Session Lifecycle

1. You start chatting; a new session opens or a prior one resumes.
2. Messages accumulate with tool calls and responses.
3. Session ends when you exit or after inactivity.
4. Session is archived to `identities/<name>/sessions/archive/`.
5. Background cognition runs: journal synthesis, optional distillation, and reflection.

### Session Keys

Sessions are identified by keys:

| Channel | Key format |
|---------|------------|
| CLI | `cli:direct` or `cli:<session-name>` |
| Nostr | `nostr:<identity>:<sender_pubkey>` |
| Telegram | `telegram:<chat_id>` |
| Email | `email:<sender_address>` |

### Resume Sessions

```bash
hermitcrab agent -s "cli:my-project"
```

### Session Storage

Sessions are stored as JSONL files in `~/.hermitcrab/identities/<name>/sessions/`. They are
primarily for debugging and recall, not the knowledge base.

## Memory

HermitCrab's memory is deterministic, file-based, and human-readable. Every memory item is an atomic
Markdown file with YAML frontmatter.

### Memory Categories

| Category | Purpose | Example |
|----------|---------|---------|
| `facts` | Preferences, attributes, persistent truths | "User lives in Lisbon" |
| `decisions` | Choices and reasoning | "Chose Flask over FastAPI because..." |
| `goals` | Long-term objectives | "Run 3x per week" |
| `tasks` | Actionable items with status and deadlines | "File taxes by April 30" |
| `reflections` | Self-analysis and pattern recognition | "I tend to over-explain technical concepts" |

### Storage Layout

```text
~/.hermitcrab/identities/owner/memory/
├── facts/
├── decisions/
├── goals/
├── tasks/
└── reflections/
```

## Knowledge

The knowledge library stores reference material:

```text
~/.hermitcrab/identities/owner/knowledge/
├── articles/
├── books/
├── docs/
└── notes/
```

## Scratchpads

Every session has a scratchpad at `identities/<name>/scratchpads/<session>.md`. Scratchpads are
archived on session end and excluded from distillation.

## People

People profiles live under the identity root:

```text
~/.hermitcrab/identities/owner/people/
├── profiles/
└── interactions/
```

## Manual Edits

Memory files are plain Markdown. Open them in any editor and modify or delete the relevant file.
HermitCrab reads the filesystem state on future turns.

## Privacy

Memory lives in your HermitCrab root. It is human-readable, editable, and not encrypted at rest. Use
filesystem encryption if you need at-rest protection.
