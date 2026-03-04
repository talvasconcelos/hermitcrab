# Tool Usage Notes

Tool signatures provided via function calling. Non-obvious constraints below.

## Memory vs Knowledge

**Memory** = Identity (authoritative, auto-distilled from conversations)
**Knowledge** = Reference library (external info, explicit retrieval only)

## Memory Operations — Use Typed APIs

**IMPORTANT:** Use typed memory APIs instead of `write_file` for long-term memory.

| Operation | Use Case |
|-----------|----------|
| `write_fact` | User preferences, established truths |
| `write_decision` | Architectural choices (immutable) |
| `write_goal` | Objectives to achieve |
| `write_task` | Action items (requires `assignee`) |
| `write_reflection` | Meta-observations (append-only) |

### Category Rules

| Category | Updateable? | Deletable? |
|----------|-------------|------------|
| **facts** | ✅ If contradicted | ⚠️ Rare |
| **decisions** | ❌ Immutable | ❌ Never |
| **goals** | ✅ Refine/status | ⚠️ Archive if achieved |
| **tasks** | ✅ Status only | ⚠️ Archive if done |
| **reflections** | ❌ Append-only | ❌ Never |

## Knowledge Operations

| Tool | Purpose |
|------|---------|
| `knowledge_search` | Search by query, category, or tags |
| `knowledge_ingest` | Save content to library (articles, docs, notes) |
| `knowledge_ingest_url` | Fetch URL and save to library |
| `knowledge_list` | Browse with filters (returns metadata only) |

**Categories:** `articles`, `books`, `docs`, `notes`

## Other Tools

- **exec**: Timeout 60s, dangerous commands blocked, output truncated at 10k chars
- **cron**: See cron skill for scheduled tasks
- **spawn**: Background tasks with isolated sessions, shared memory
