# Built-in skills catalog

Skills HermitCrab ships with out of the box. Each skill lives as a directory under `hermitcrab/skills/` with a `SKILL.md` file describing what it does and how to use it.

## How skills work

Skills are discovered and loaded by the `SkillsLoader` at runtime. The agent selects skills based on query token overlap with the skill's description and metadata. Skills can declare:

- **Always-loaded** — included in every session prompt regardless of query
- **Binary requirements** — external CLIs that must be on PATH
- **Environment variable requirements** — API keys or configuration
- **OS constraints** — platform restrictions

## Skill inventory

### clawhub

Search and install agent skills from ClawHub, the public skill registry.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/clawhub/` |
| Required binaries | `npx` (Node.js) |
| Homepage | <https://clawhub.ai> |
| Always loaded | No |

Use this skill to browse, search, and install community skills from the ClawHub registry. Requires Node.js for `npx` execution.

---

### cron

Schedule reminders and recurring tasks through the built-in cron tool.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/cron/` |
| Required binaries | None (uses runtime `cron` tool) |
| Always loaded | No |

Provides guidance on using the `cron` tool for one-shot (`at`), interval (`every`), and cron-expression scheduling. Works with the gateway cron service.

---

### github

Interact with GitHub using the `gh` CLI for issues, PRs, CI runs, and advanced queries.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/github/` |
| Required binaries | `gh` |
| Always loaded | No |

Install `gh`:

```bash
# macOS
brew install gh

# Debian/Ubuntu
sudo apt install gh
```

Then authenticate:

```bash
gh auth login
```

The skill covers `gh issue`, `gh pr`, `gh run`, and `gh api` workflows.

---

### here-now

Publish static files and sites to here.now for instant public URLs.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/here-now/` |
| Required binaries | `curl` |
| Environment | `HERENOW_API_KEY` or `~/.herenow/credentials` for authenticated publishing |
| Homepage | <https://here.now/docs> |
| Always loaded | No |

Recommended workflow:

1. Build a manifest of files to publish
2. Create a site via the here.now API
3. Upload files
4. Finalize the publish
5. Return the public URL
6. Surface the `claimUrl` for ownership proof

---

### memory

Category-based atomic memory system with explicit typed operations.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/memory/` |
| Required binaries | None (uses runtime memory functions) |
| Always loaded | **Yes** |

This skill is loaded in every session. It provides guidance on using the memory tools:

- `write_fact` — persistent attributes and truths
- `write_decision` — choices with reasoning (immutable)
- `write_goal` — long-term objectives
- `write_task` — actionable items with status and deadlines
- `write_reflection` — self-analysis and patterns
- `search_memory` — keyword search across categories
- `read_memory` — read by category or ID
- `list_memories` — enumerate items in a category

Memory is stored as atomic Markdown files with YAML frontmatter in `workspace/memory/<category>/`.

---

### skill-creator

Create or update agent skills. Use when designing, structuring, or packaging skills with scripts, references, and assets.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/skill-creator/` |
| Required binaries | Runtime scripts (`init_skill.py`, `package_skill.py`) |
| Always loaded | No |

Guides through a 6-step skill creation process:

1. Understand the skill's purpose and target user
2. Plan the structure (SKILL.md, scripts, references, assets)
3. Initialize the skill directory
4. Edit SKILL.md with frontmatter and body content
5. Package and test the skill
6. Iterate based on agent behavior

---

### summarize

Summarize or extract text and transcripts from URLs, podcasts, and local files. Great fallback for "transcribe this YouTube/video."

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/summarize/` |
| Required binaries | `summarize` |
| Environment | Provider API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, or `GEMINI_API_KEY` (aliases: `GOOGLE_GENERATIVE_AI_API_KEY`, `GOOGLE_API_KEY`) |
| Optional env | `FIRECRAWL_API_KEY`, `APIFY_API_TOKEN` |
| Homepage | <https://summarize.sh> |
| Always loaded | No |

Install:

```bash
brew install steipete/tap/summarize
```

Configure at least one provider API key for LLM-powered summarization.

---

### tmux

Remote-control tmux sessions for interactive CLIs by sending keystrokes and scraping pane output.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/tmux/` |
| Required binaries | `tmux` |
| OS | macOS (`darwin`), Linux |
| Environment | `HERMITCRAB_TMUX_SOCKET_DIR` (optional, defaults to `$TMPDIR/hermitcrab-tmux-sockets`) |
| Always loaded | No |

Includes helper scripts:

- `scripts/find-sessions.sh` — find tmux sessions on a socket
- `scripts/wait-for-text.sh` — poll a pane for a regex or fixed string with a timeout

Useful for running interactive CLI programs (REPLs, menus, TUIs) where the agent needs to send input and read output.

---

### weather

Get current weather and forecasts. No API key required.

| Property | Value |
|----------|-------|
| Directory | `hermitcrab/skills/weather/` |
| Required binaries | `curl` |
| Homepage | <https://wttr.in/:help> |
| Always loaded | No |

Uses wttr.in and Open-Meteo for weather data. Supports location lookup by city name, coordinates, or IP-based geolocation. No registration or API keys needed.

---

## Skill activation notes

**Current behavior:** Skills are selected based on folder name matching and description text overlap with the user's query. Structured `activation` metadata (aliases, tags, keywords) is not yet widely used across built-in skills. This is a direction for more deterministic skill activation.

**Always-loaded skills:** Only the `memory` skill has `always: true`, meaning it is included in every session prompt. This ensures the agent always knows how to use the memory system.

**Missing requirements:** Skills with unmet binary or environment variable requirements are reported as unavailable in `hermitcrab status` output.

## Adding workspace skills

Place custom skills in `workspace/skills/`. The `SkillsLoader` checks workspace skills before bundled skills, allowing you to override or extend built-in behavior.

Each workspace skill needs a `SKILL.md` with frontmatter:

```markdown
---
name: my-skill
description: What this skill does
---

# My Skill

Instructions for the agent...
```

## Extending skills

To add a new skill to the codebase, see [Extending tools](developer-guide/extending-tools.md) for the general pattern. Skills are discovered automatically from directories under `hermitcrab/skills/`.
