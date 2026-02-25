---
name: memory
description: Category-based atomic memory system with explicit typed operations.
always: true
---

# Memory

## Structure

Memory is stored as atomic markdown files with YAML frontmatter in category directories:

- `memory/facts/` — Long-term truths (preferences, project context, relationships)
- `memory/decisions/` — Locked architectural or behavioral choices (immutable)
- `memory/goals/` — Outcome-oriented objectives
- `memory/tasks/` — Concrete actionable items with lifecycle
- `memory/reflections/` — Subjective observations (append-only)

Each file contains one memory item with metadata in YAML frontmatter.

## Memory Operations

Use the typed memory functions to write durable knowledge:

### write_fact(title, content, tags=None, confidence=None, source=None)
Write a long-term truth. Only write if explicitly stated or unambiguous.

### write_decision(title, content, tags=None, supersedes=None, rationale=None)
Record a locked choice. Decisions are immutable — never edited, only superseded.

### write_goal(title, content, tags=None, priority=None, status="active")
Define an outcome-oriented objective. May be refined or marked achieved.

### write_task(title, content, tags=None, status="todo", assignee=None, deadline=None)
Create an actionable item. Tasks have lifecycle: todo → in_progress → done.

### write_reflection(title, content, tags=None, context=None)
Record subjective observations. Append-only — never edited or deleted.

## Search and Retrieval

### search_memory(query, categories=None, limit=None)
Search across memory categories. Deterministic: filenames → frontmatter → content.

### read_memory(category, id=None, query=None)
Read items from a specific category with optional filtering.

### list_memories(category=None, include_archived=False)
List all memory items, optionally filtered by category.

## Category Rules

**Facts**: Long-term truths. Written only if explicit. Rarely deleted.

**Decisions**: Immutable choices. Never edited. Only superseded by new decisions referencing the old. Never deleted.

**Goals**: Durable objectives. May be refined or marked achieved. Not silently removed.

**Tasks**: Actionable items with lifecycle. State transitions only. Completed tasks archived, not deleted.

**Reflections**: Subjective observations. Append-only. Never edited or deleted.

## No Automatic Consolidation

Memory is explicit and deterministic. No LLM summarization. No automatic extraction from conversations. Write memory only when the user explicitly states durable information.
