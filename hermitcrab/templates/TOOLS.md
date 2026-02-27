# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## Memory Operations — Use Typed APIs

**IMPORTANT:** When saving knowledge to long-term memory, use the typed memory APIs instead of `write_file`.

| Operation | Use Case |
|-----------|----------|
| `write_fact` | User preferences, established truths |
| `write_decision` | Architectural choices, trade-offs (immutable) |
| `write_goal` | Objectives the user wants to achieve |
| `write_task` | Action items, todos (requires `assignee`) |
| `write_reflection` | Meta-observations, patterns (append-only) |

### Category Rules

| Category | Updateable? | Deletable? |
|----------|-------------|------------|
| **facts** | ✅ If contradicted | ⚠️ Rare |
| **decisions** | ❌ Immutable | ❌ Never |
| **goals** | ✅ Refine/status | ⚠️ Archive if achieved |
| **tasks** | ✅ Status only | ⚠️ Archive if done |
| **reflections** | ❌ Append-only | ❌ Never |

## cron — Scheduled Tasks

Refer to the cron skill for usage. Jobs run at configured intervals.

## spawn — Sub-agents

Use for background tasks that don't need immediate response. Sub-agents have isolated sessions but share the same memory.
