# Contributing

How to contribute to HermitCrab.

## Development setup

```bash
git clone https://github.com/talvasconcelos/hermitcrab.git
cd hermitcrab
uv sync --dev
```

## Running the project

```bash
uv run hermitcrab --help
uv run hermitcrab onboard
uv run hermitcrab agent
```

## Testing

```bash
uv run pytest
```

Focused test:

```bash
uv run pytest tests/test_memory.py -v
```

## Linting

```bash
uv run ruff check . --fix
```

Follows Ruff defaults plus repo overrides: 100-character lines, Python 3.11 syntax, lint families `E`, `F`, `I`, `N`, `W`.

## Coding standards

- 4-space indentation
- `snake_case` for modules, functions, and variables
- `PascalCase` for classes
- Explicit, capability-based filenames
- Deterministic Python-side enforcement over prompt-only fixes
- No English-specific trigger heuristics when structural metadata or runtime checks can do the job

## PR guidelines

### Commit messages

Use Conventional Commits with short imperative subjects:

```
feat: add procedural skill runtime state
fix: harden post-tool completion repair
docs: trim AGENTS guidance
```

### PR description

PRs should state:

1. The concrete problem being solved
2. The behavior change
3. Test coverage notes

### Change scope

- Touch only what you must
- Don't "improve" adjacent code, comments, or formatting
- Match existing style
- Remove imports/variables/functions that YOUR changes made unused
- Don't remove pre-existing dead code unless asked

## Documentation

### Updating docs

Documentation lives in `docs/`. Follow the same structure and style:

- Clear headings, short sections, concrete examples
- Progressive disclosure: quick action first, deep explanation second
- No marketing fl
- Code is source of truth — verify against implementation

### When to update docs

- Adding a new CLI flag or command
- Changing config schema fields
- Modifying tool behavior or permissions
- Adding or removing channels
- Changing gateway behavior
- Updating workspace model

## Architecture priorities

Prefer work that improves:

- Reliability under imperfect model output
- Useful memory that stays clean over time
- Simpler architecture with fewer hidden behaviors
- Lower hardware and operational footprint
- Adaptation to a specific household or user without opaque behavior

Reject work that mostly add prompt churn, cleverness, or surface area without improving those properties.

## Complexity management

- Reduce complexity in large coordination modules (`agent/loop.py`, `agent/memory.py`)
- Continue removing helper sprawl, duplicated fallback logic, and stale compatibility baggage
- Keep LOC pressure visible, especially in `agent/` and `agent/tools/`
- Prefer extracting clean, composable helpers over growing giant files

## Testing philosophy

- Focused regressions for real failures
- Deterministic regression for exact failure modes
- Cover provider parsing, session lifecycle, subagent orchestration, CLI behavior, memory quality
- Smaller, higher-signal suite over broad noisy coverage
- Treat test growth as suspect until proven otherwise

## Beta3 focus areas

- Nostr NIP-17 and group conversation support
- Explicit conversation-level model switching
- Permission/policy UX and auditability
- Owner-managed multi-workspace foundations
- Workspace hygiene, onboarding, help, and diagnostics

See `project/BETA3_ROADMAP.md` for detailed planning.

## Getting help

- Read `AGENTS.md` for repository guidance
- Read `project/BETA3_ROADMAP.md` for beta3 planning
- Check existing issues and PRs on GitHub
- Ask in project channels
