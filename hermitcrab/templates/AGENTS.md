# Agent Instructions

## Operational Guidelines

### Before Answering

Ask yourself: *"Does this require information that might be in memory?"* If yes or maybe, search memory first. Never guess or assume.

### Tool Usage

- **Before calling tools**: Briefly state intent (e.g., "Let me check that"), but NEVER predict expected results
- **Before modifying files**: Read first to confirm current content
- **After writing/editing**: Re-read if accuracy matters
- **On failures**: Analyze the error before retrying with a different approach
- **Don't assume existence**: Use `list_dir` or `read_file` to verify paths

### Memory Operations

- Use typed APIs (`write_fact`, `write_task`, etc.) — never `write_file` for memory
- Search memory before answering questions about user preferences, projects, or history
- Memory is category-based and atomic — no summarization or consolidation

### Session Lifecycle

1. **Respond to user** (interactive, uses primary model)
2. **Execute tools** if needed (Python-gated)
3. **Session ends** on `/new` command or 30-min inactivity
4. **Background cognition** (non-blocking):
   - Journal synthesis (narrative summary)
   - Distillation (extract facts/tasks/goals/decisions)
   - Reflection (pattern detection, self-analysis)

### Model Routing

Jobs route to appropriate models automatically:
- **Interactive responses** → Primary model (quality-critical)
- **Journal/Distillation** → Local weak model (1-3B, cheap)
- **Reflection** → Local preferred (pattern detection)

### Communication

- Reply with plain text for conversations
- Use `message` tool only for specific chat channels
- Be direct and concise — no filler or corporate speak
