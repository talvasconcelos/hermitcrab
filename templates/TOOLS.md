# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## cron — Scheduled Reminders

- Please refer to cron skill for usage.

## Memory Operations — Use Typed APIs

**IMPORTANT:** When saving knowledge to long-term memory, use the typed memory APIs instead of `write_file`. This ensures proper filename formatting, metadata, and validation.

### Available Memory APIs

| Operation | Use Case | Example |
|-----------|----------|---------|
| `write_fact` | User preferences, established truths | Save "user prefers dark mode" |
| `write_decision` | Architectural choices, trade-offs | Save "chose PostgreSQL over MySQL" |
| `write_goal` | Objectives the user wants to achieve | Save "learn Python by June" |
| `write_task` | Action items, todos | Save "book flights by Friday" |
| `write_reflection` | Meta-observations, patterns | Save "agent struggled with X" |

### Why Use Typed APIs?

✅ **Automatic filename generation**: `{timestamp}-{uuid}-{category}-{slug}.md`
✅ **Proper frontmatter**: All required metadata fields added automatically
✅ **Validation**: Ensures required fields per category (e.g., `assignee` for tasks)
✅ **Category enforcement**: Prevents writing to wrong category
✅ **Consistency**: All memory files follow the same structure

### Example Usage

**Instead of:**
```json
{
  "tool": "write_file",
  "path": "/workspace/memory/facts/user-preference.md",
  "content": "User likes dark mode"
}
```

**Use:**
```json
{
  "tool": "write_fact",
  "title": "User prefers dark mode",
  "content": "User prefers dark mode for the UI theme",
  "tags": ["preference", "ui"],
  "confidence": 0.9
}
```

### Category Rules

| Category | Writable? | Updateable? | Deletable? |
|----------|-----------|-------------|------------|
| **facts** | ✅ Explicit only | ✅ If contradicted | ⚠️ Rare |
| **decisions** | ✅ | ❌ Immutable | ❌ Never |
| **goals** | ✅ | ✅ Refine/status | ⚠️ Archive if achieved |
| **tasks** | ✅ | ✅ Status only | ⚠️ Archive if done |
| **reflections** | ✅ | ❌ Append-only | ❌ Never |

### When to Use Each

**Facts:**
- User preferences ("prefers cool weather")
- Project context ("uses Python 3.12")
- Established truths ("Lisbon is in Portugal")

**Decisions:**
- Architectural choices ("using SQLite for local storage")
- Trade-offs ("chose speed over accuracy")
- Locked choices ("API version locked to v2")

**Goals:**
- Desired outcomes ("learn Portuguese phrases")
- Objectives ("visit Portugal in March")
- Aspirations ("run a marathon")

**Tasks:**
- Action items ("book flights by next week")
- Todos ("call dentist")
- Deadlines ("submit report by Friday")

**Reflections:**
- Pattern observations ("user corrects weather queries often")
- Meta-analysis ("agent struggles with ambiguous paths")
- Self-improvement notes ("should confirm before expensive API calls")
