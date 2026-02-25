# Memory System Refactoring Summary

**Date:** 2026-02-24  
**Type:** Breaking Change - Complete Memory System Replacement

## Overview

The old LLM-based memory consolidation system has been completely replaced with a strict, deterministic, category-based memory system.

---

## What Was Removed

### Files Deleted
- `hermitcrab/templates/memory/MEMORY.md` — Old memory template

### Classes/Methods Removed

#### `hermitcrab/agent/memory.py`
- `MemoryStore.consolidate()` — LLM-based consolidation method
- `_SAVE_MEMORY_TOOL` — Tool definition for save_memory
- All references to `MEMORY.md` and `HISTORY.md` files

#### `hermitcrab/agent/loop.py`
- `AgentLoop._consolidate_memory()` — Method that called MemoryStore.consolidate
- `AgentLoop._consolidating` — Set tracking consolidation in progress
- `AgentLoop._consolidation_tasks` — Set of consolidation tasks
- `AgentLoop._consolidation_locks` — Dict of consolidation locks
- `AgentLoop._get_consolidation_lock()` — Lock getter method
- `AgentLoop._prune_consolidation_lock()` — Lock cleanup method
- `/new` command consolidation logic (archive_all mode)
- Automatic consolidation trigger when `unconsolidated >= memory_window`

#### `hermitcrab/session/manager.py`
- `Session.last_consolidated` — Field tracking consolidated message count
- `Session.get_history()` — No longer slices based on last_consolidated
- `Session.clear()` — No longer resets last_consolidated
- `SessionManager._load()` — No longer loads last_consolidated from file
- `SessionManager.save()` — No longer saves last_consolidated to file

### Configuration Removed
- `memory_window` parameter in `AgentLoop.__init__()` — Still exists but only limits context window, not consolidation
- `archive_all` mode in consolidation — Removed with consolidation

---

## What Was Added

### New Memory Structure
```
workspace/
├── memory/
│   ├── facts/          # Long-term truths
│   ├── decisions/      # Locked choices (immutable)
│   ├── goals/          # Outcome-oriented objectives
│   ├── tasks/          # Actionable items with lifecycle
│   └── reflections/    # Subjective observations (append-only)
└── sessions/           # Ephemeral conversation history
```

### New API Methods

#### Write Operations
- `MemoryStore.write_fact(title, content, tags, confidence, source)`
- `MemoryStore.write_decision(title, content, tags, supersedes, rationale)`
- `MemoryStore.write_goal(title, content, tags, priority, status)`
- `MemoryStore.write_task(title, content, tags, status, assignee, deadline)`
- `MemoryStore.write_reflection(title, content, tags, context)`

#### Read Operations
- `MemoryStore.read_memory(category, id, query)`
- `MemoryStore.search_memory(query, categories, limit)`
- `MemoryStore.list_memories(category, include_archived)`
- `MemoryStore.get_memory_context()` — Builds context for system prompt

#### Update Operations
- `MemoryStore.update_memory(category, id, content, title, tags, **metadata)`
- `MemoryStore.update_task_status(task_id, new_status)`

#### Delete Operations
- `MemoryStore.delete_memory(category, id)` — With category-specific rules

### New Types
- `MemoryCategory` enum: FACTS, DECISIONS, GOALS, TASKS, REFLECTIONS
- `TaskStatus` enum: TODO, IN_PROGRESS, OPEN, DONE, DEFERRED, CANCELLED
- `MemoryItem` dataclass: Represents atomic memory items

---

## Tests Affected

### Tests to DELETE (obsolete - tested old consolidation system)
1. `tests/test_consolidate_offset.py` — All 830 lines test `last_consolidated` and consolidation logic
2. `tests/test_memory_consolidation_types.py` — Tests `MemoryStore.consolidate()` method

### Tests to UPDATE
1. `tests/test_commands.py::test_onboard_fresh_install`
   - **Old:** Asserted `memory/MEMORY.md` exists
   - **New:** Asserts category directories exist (facts/, decisions/, etc.)
   - **Status:** ✅ Updated

2. `tests/test_cli_input.py` — Fixed namespace (`nanobot` → `hermitcrab`)
   - **Status:** ✅ Updated

3. `tests/test_commands.py` — Fixed namespace (`nanobot` → `hermitcrab`)
   - **Status:** ✅ Updated

### Tests to ADD
New test file created: `tests/test_memory.py` with 42 tests covering:
- Memory category enum
- Task status enum
- MemoryStore initialization
- Write operations (fact, decision, goal, task, reflection)
- Read operations (by category, ID, query)
- Search operations
- Update operations
- Delete operations with category rules
- Memory context building
- List operations with archive handling

---

## Behavioral Changes

### Session Handling
| Before | After |
|--------|-------|
| Messages tracked with `last_consolidated` offset | All messages treated equally |
| Old messages consolidated to MEMORY.md | Sessions are ephemeral only |
| `/new` command triggered consolidation | `/new` just clears session |
| Automatic consolidation at memory_window | No automatic consolidation |

### Memory Persistence
| Before | After |
|--------|-------|
| Single MEMORY.md file | Category-based atomic files |
| HISTORY.md event log | No event log |
| LLM summarized conversations | Explicit typed writes only |
| Implicit memory extraction | User must call write_* methods |

### Category Rules Enforced
| Category | Write | Update | Delete |
|----------|-------|--------|--------|
| facts | ✅ | ✅ (if contradicted) | ⚠️ (rare) |
| decisions | ✅ | ❌ (immutable) | ❌ (never) |
| goals | ✅ | ✅ (refine/status) | ⚠️ (archive if achieved) |
| tasks | ✅ | ✅ (status only) | ⚠️ (archive if done) |
| reflections | ✅ | ❌ (append-only) | ❌ (never) |

---

## Migration Path

### For Users
1. Old `MEMORY.md` and `HISTORY.md` files are no longer read
2. New memory writes go to category directories
3. No automatic migration of old memory files

### For Developers
1. Replace any calls to `memory.consolidate()` with explicit `write_*` calls
2. Remove any code depending on `Session.last_consolidated`
3. Update tests that check for `MEMORY.md` existence

---

## Files Modified

### Core Implementation
- `hermitcrab/agent/memory.py` — Complete rewrite
- `hermitcrab/agent/loop.py` — Removed consolidation logic
- `hermitcrab/agent/context.py` — Updated system prompt
- `hermitcrab/session/manager.py` — Removed last_consolidated
- `hermitcrab/cli/commands.py` — Updated workspace creation
- `hermitcrab/skills/memory/SKILL.md` — Updated documentation

### Tests
- `tests/test_memory.py` — NEW (42 tests)
- `tests/test_commands.py` — Updated
- `tests/test_cli_input.py` — Fixed namespace

### To Be Deleted
- `tests/test_consolidate_offset.py`
- `tests/test_memory_consolidation_types.py`

---

## Success Criteria

✅ Memory survives process restarts  
✅ Memory is readable and auditable by humans  
✅ Agent never "forgets" stored facts or decisions  
✅ Agent never invents continuity not present in memory  
✅ Users never need to restate durable information once written  
✅ All 42 new memory tests pass  
✅ No LLM-based consolidation  
✅ No global memory files  
✅ Category rules enforced by construction  
