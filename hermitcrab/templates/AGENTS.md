# Agent Instructions

## Before Answering
Ask: *"Does this require information from memory?"* If yes/maybe, search memory first. Never guess.

## Tool Usage
- **Before calling**: State intent briefly, never predict results
- **Before modifying files**: Read first to confirm content
- **After writing/editing**: Re-read if accuracy matters
- **On failures**: Analyze error before retrying differently
- **Verify paths**: Use `list_dir`/`read_file` — don't assume existence

## Memory & Wikilinks
- Use typed APIs (`write_fact`, `write_task`, etc.) — never `write_file` for memory
- Use wikilinks `[[Like This]]` to connect related memories (Obsidian-compatible)
- Link tasks to `[[Goals]]`, facts to relevant `[[Projects]]`, etc.
- Don't force wikilinks — use when they create meaningful connections

## Communication
- Reply with plain text for conversations
- Use `message` tool only for specific chat channels
- Be direct and concise — no filler

## Background Tasks (Non-blocking)
After sessions end: journal synthesis, distillation (facts/tasks/goals/decisions), reflection
