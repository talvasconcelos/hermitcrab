# hermitcrab Skills

This directory contains built-in skills that extend hermitcrab's capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

Recommended frontmatter structure:
- top-level `name` and `description` for discovery
- optional `metadata.hermitcrab.activation` for deterministic aliases, tags, and keywords
- optional `metadata.hermitcrab.workflow` for procedural skills that need explicit phase/artifact tracking

HermitCrab treats the filesystem as the source of truth for installed skills. Discovery should stay cheap and structured; large skill bodies are loaded only for selected skills.

## Attribution

These skills are adapted from [OpenClaw](https://github.com/openclaw/openclaw)'s skill system.
The skill format and metadata structure follow OpenClaw's conventions to maintain compatibility.

## Available Skills

| Skill | Description |
|-------|-------------|
| `github` | Interact with GitHub using the `gh` CLI |
| `weather` | Get weather info using wttr.in and Open-Meteo |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Remote-control tmux sessions |
| `clawhub` | Search and install skills from ClawHub registry |
| `here-now` | Publish static sites and files to here.now |
| `skill-creator` | Create new skills |
