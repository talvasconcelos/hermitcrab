# Tool Usage Notes

Tool signatures provided via function calling. Non-obvious constraints below.

## Session-Aware Retrieval

- Check the recent conversation first when the user seems to be replying to your own prior message.
- Use memory search for durable facts, decisions, workflows, and preferences.
- Do not say you lack context until you have checked both recent conversation and memory when relevant.

## Memory vs Knowledge

**Memory** = Identity (authoritative, auto-distilled from conversations)
**Knowledge** = Reference library (external info, explicit retrieval only)

## Memory Operations — Use Typed APIs

**IMPORTANT:** Use typed memory APIs instead of `write_file` for long-term memory.

| Operation | Use Case |
|-----------|----------|
| `write_fact` | User preferences, established truths |
| `write_decision` | User-confirmed locked choices only (immutable) |
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

Use `write_decision` only when the user made or clearly accepted the choice. Assistant-authored recommendations, reports, and option lists belong in `projects/` or `knowledge/`, not `memory/decisions/`.

Use `write_task` only for actionable commitments that somebody should do or track. Shopping lists, reusable checklists, reference notes, and "remember this list for later" content belong in `knowledge/notes/` via `knowledge_ingest`.

## File Placement Rules

- Keep the workspace root clean. Do not save normal reports, drafts, or ad-hoc notes directly in the root.
- Use `projects/<slug>/` for user-requested deliverables such as reports, plans, generated apps, and project artifacts.
- Use `knowledge_ingest` or `knowledge_ingest_url` for reference material that should live under `knowledge/`.
- Use `scratchpads/` only for transient working notes.
- Use root-level files only for explicit bootstrap/control files such as `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, and `IDENTITY.md`.

## Other Tools

- **exec**: Timeout 60s, dangerous commands blocked, output truncated at 10k chars
- **cron**: See cron skill for scheduled tasks
- **spawn**: Background tasks with isolated sessions, shared memory

## Cost And Loop Discipline

- Prefer `search`, line ranges, or metadata before reading whole large files.
- Verify state before acting; do not edit based on assumptions.
- If the same fix fails three times in a row, stop retrying blindly and change approach or report the blocker.
- Use subagents for bounded grunt work, not to hand off the entire strategic deliverable.
