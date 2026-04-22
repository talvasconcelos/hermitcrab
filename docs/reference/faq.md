# FAQ

Common questions answered.

## General

### What is HermitCrab?

A personal AI agent you run on your own machine. It remembers what matters to you in plain Markdown files, can distill conversations into knowledge, and reflects on its own behavior to improve over time.

### Is this production-ready?

HermitCrab is usable for real daily workflows and actively evolving. For stability-sensitive setups, review the changelog and run `hermitcrab doctor` and `hermitcrab status` after upgrades.

### What models does it support?

HermitCrab works with any model supported by LiteLLM: Anthropic (Claude), OpenAI (GPT), Google (Gemini), OpenRouter, Ollama (local), DeepSeek, Groq, and many more. See the providers in the config schema for the full list.

### Can I run it offline?

Yes. Use Ollama with a local model. Configure `providers.ollama.apiBase` to `http://localhost:11434` and set your model to `"ollama/<model-name>"`.

### Does it use vector databases?

No. Memory is deterministic and file-based — atomic Markdown files with YAML frontmatter. No vector embeddings, no hidden state.

## Memory

### Where is memory stored?

`workspace/memory/` with subdirectories for facts, decisions, goals, tasks, and reflections. Each item is a Markdown file with YAML frontmatter.

### Can I edit memory?

Yes. Memory files are plain Markdown. Open them in any editor and modify. The agent picks up changes on next read.

### How does duplicate detection work?

When writing facts, the system tokenizes the content and checks existing items for token coverage. Near-duplicates are rejected to prevent memory bloat.

### Can I move memory to another machine?

Copy the entire `workspace/` folder. Everything comes with it — memory, sessions, knowledge, personality files.

## Sessions

### How long do sessions last?

Until you exit or 30 minutes of inactivity (configurable: `inactivityTimeoutS`).

### Can I have multiple sessions?

Yes. Use `-s "cli:<name>"` to create or resume named sessions.

### Are sessions encrypted?

No. Sessions are stored as JSONL files in plain text. Use filesystem encryption if needed.

### What happens to old sessions?

They are archived to `workspace/sessions/archive/`. They are not deleted.

## Channels

### Which channel should I start with?

CLI is the easiest for local testing. Nostr is the primary channel for remote access. Telegram is the easiest for phone access.

### Can I use multiple channels at once?

Yes. The gateway runs all enabled channels simultaneously. Memory and knowledge are shared across channels.

### Does Nostr require a relay?

Yes. HermitCrab connects to Nostr relays (WebSocket servers). Default relays are Damus, Primal, and WellOrder. You can add custom relays.

### Can I restrict who messages my agent?

Yes. Use `allowedPubkeys` for Nostr, `allowFrom` for Telegram and email. Unknown senders are denied by default.

## Tools and security

### Can the agent run any command?

No. Shell execution has safety guards: deny patterns for destructive operations, timeouts, and risk classification. The LLM proposes; Python enforces.

### Can the agent access files outside the workspace?

By default, file tools access paths within the workspace. When `tools.restrictToWorkspace` is `true`, all file access is restricted to the workspace directory.

### What happens when a tool call is denied?

The call is rejected, an audit event is logged, and the agent receives a structured hint with alternatives.

### Are web results sanitized?

Yes. Content fetched via `web_fetch` is automatically sanitized to remove hidden instructions, encoded payloads, and prompt injection patterns.

## Gateway

### Do I need the gateway running?

For channels (Nostr, Telegram, email), yes. For CLI sessions (`hermitcrab agent`), no.

### Can I run the gateway on a VPS?

Yes. The gateway is designed for long-running operation on a server. Docker and systemd service are both supported.

### What port does the gateway use?

Default is 18790. Change with `--port` or in `gateway.port` config.

### What happens if the gateway crashes?

Configure `Restart=on-failure` in the systemd service or use Docker restart policy. On restart, all services (cron, heartbeat, reminders) resume cleanly.

## Multi-workspace

### Should I use multi-workspace?

Only if you need isolated contexts for different people or purposes. Single workspace is the default and simpler model.

### Can sub-workspace users use the CLI?

No. Sub-workspaces are channel-only. CLI and config remain admin-owned.

### Can workspaces share memory?

No. Each workspace has its own isolated memory, knowledge, people, and sessions.

### What happens to unbound senders in multi-workspace mode?

If a sender is in the `allowedPubkeys` list but not bound to a workspace, they land in the admin workspace. If not in the allowlist, they are denied.

## Upgrading

### How do I upgrade?

```bash
pip install --upgrade hermitcrab-ai
hermitcrab onboard  # picks up new config fields
```

Then restart the gateway.

### Will upgrading break my workspace?

Workspace data is backward-compatible. New config fields are added by `hermitcrab onboard`. Existing data is preserved.

### How do I know what changed?

Check the release notes in the repository changelog.

## Docker

### Can I run HermitCrab in Docker?

Yes. `Dockerfile` and `docker-compose.yml` are included.

### Does Docker preserve my data?

Yes. The compose file mounts `~/.hermitcrab:/root/.hermitcrab` so workspace and config persist across container restarts.

### Can Docker HermitCrab reach my local Ollama?

Use `host.docker.internal` instead of `localhost`:

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://host.docker.internal:11434"
    }
  }
}
```

## Troubleshooting

### The agent doesn't remember something

Memory is file-based. Check if the file exists in `workspace/memory/<category>/`. If not, the agent may not have written it. Tell it again explicitly.

### The agent is in a loop

Type a new message to interrupt it, or press `Ctrl+C`. The agent has built-in loop detection (max 40 tool iterations, 2 identical tool cycles).

### Provider says "API key not configured"

Check `hermitcrab status` to see which provider is selected. Add the API key to the correct provider section in `config.json`.

### Gateway won't start

Run `hermitcrab doctor` to check for config or workspace issues. Run with `--verbose` for detailed logs.
