# Memory and session model

Deterministic memory and session lifecycle.

## Memory architecture

### Categories

Memory is organized into five categories:

| Category | Purpose | Mutability |
|----------|---------|------------|
| `facts` | Persistent attributes and truths | Append-only (individual facts) |
| `decisions` | Choices with reasoning | Immutable |
| `goals` | Long-term objectives | Mutable (status updates) |
| `tasks` | Actionable items | Mutable (lifecycle: open → in_progress → done/deferred) |
| `reflections` | Self-analysis | Append-only |

### Storage format

Each item is an atomic Markdown file with YAML frontmatter:

```markdown
---
id: <sha256 of title:content>
category: <category>
title: <title>
created: <ISO timestamp>
status: <task status, if applicable>
---

<content>
```

### ID generation

IDs are SHA-256 hashes of `title:content`. This provides deterministic deduplication — the same fact written twice produces the same ID.

### Duplicate detection

When writing facts, the system:

1. Tokenizes the content
2. Checks existing items for token coverage
3. Rejects near-duplicates

This prevents memory bloat from repeated or slightly-rephrased facts.

### Thread safety

Memory writes use `RLock` for thread-safe concurrent writes. Reads are lock-free.

## MemoryStore

`MemoryStore` (in `agent/memory.py`) provides:

- CRUD operations per category
- Search by keyword
- Duplicate detection for facts
- Task lifecycle management
- Bounded context injection for prompt assembly

### Prompt context injection

When building prompt context, memory is bounded:

- Max chars total: `memoryContextMaxChars` (default 10,000)
- Max items per category: `memoryContextMaxItemsPerCategory` (default 20)
- Max chars per item: `memoryContextMaxItemChars` (default 500)

This prevents memory from flooding the prompt window.

## Session model

### Session structure

Sessions are stored as JSONL files in `workspace/sessions/`:

```
sessions/
├── cli:direct.jsonl
├── nostr:abc123.jsonl
└── archive/
    └── cli:direct-20260414.jsonl
```

Each line is a JSON object representing a message with role, content, timestamp, and optional tool call metadata.

### Session lifecycle

1. **Open** — user sends first message
2. **Active** — messages accumulate, tool calls execute
3. **End** — user exits or inactivity timeout
4. **Archive** — moved to `sessions/archive/`
5. **Cognition** — journal, distillation, reflection run

### Truncated history repair

The session manager detects broken leading segments in session files. If a session file has a partial or corrupted leading segment, it is repaired by finding the next valid JSONL entry point.

### Session search

`SessionSearchTool` searches across current and archived sessions by keyword. This provides conversation history recall independent of the memory system.

## Scratchpads

Every session has a scratchpad at `workspace/scratchpads/<session>.md`. It holds:

- Intermediate reasoning
- Tool outputs
- Draft thoughts
- Working notes

On session end, scratchpads are archived to `workspace/scratchpads/archive/` and excluded from distillation. They are transient working artifacts, not long-term knowledge.

## People

`PeopleStore` (in `agent/people.py`) manages named people:

- Profiles stored as Markdown in `workspace/people/profiles/`
- Interaction history as JSON in `workspace/people/interactions/`
- Duplicate guard prevents creating profiles for the same person
- Linked follow-ups track engagement patterns

## Knowledge library

Separate from memory, the knowledge library stores reference material:

```
knowledge/
├── articles/
├── books/
├── docs/
└── notes/
```

Managed by `KnowledgeStore`. Content is ingested via `knowledge_ingest` and `knowledge_ingest_url` tools. Searched via `knowledge_search`.

## Lists

`ListStore` (in `agent/lists.py`) manages checklists:

- Lists stored in `workspace/lists/`
- Items have status: open, done, deferred
- Operations: show, add, set status, remove, delete

## Journal

`JournalStore` (in `agent/journal.py`) stores narrative session summaries:

- Generated after each session ends
- Uses a cheap local model
- Provides human-readable session overview
- Stored in `workspace/journal/`

## Key invariants

- Memory files are always valid Markdown with YAML frontmatter
- IDs are deterministic (SHA-256 of title:content)
- Direct memory writes are always authoritative over distillation proposals
- Scratchpad content is never distilled into long-term memory
- Session files are append-only during active sessions
- Bounded context injection prevents prompt flooding
- Thread-safe writes, lock-free reads
