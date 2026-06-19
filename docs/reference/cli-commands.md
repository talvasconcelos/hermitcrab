# CLI commands

Every HermitCrab command with flags and examples.

## hermitcrab onboard

Initialize config, system state, and the owner identity.

```bash
hermitcrab onboard
```

Creates `~/.hermitcrab/config.json`, `~/.hermitcrab/system/`, and
`~/.hermitcrab/identities/owner/`.

If config already exists, prompts:
- `y` — overwrite with defaults (loses existing values)
- `N` — refresh config (preserves existing values, adds new fields)

Creates system templates (`AGENTS.md`, `TOOLS.md`), identity templates (`IDENTITY.md`, `SOUL.md`,
`USER.md`, `HEARTBEAT.md`), and identity directories (`memory/`, `knowledge/`, `scratchpads/`,
`people/`, `lists/`, `reminders/`, `skills/`, `sessions/`, `cron/`).

## hermitcrab agent

Start an interactive session, send a one-shot message, or listen on Nostr.

### Interactive mode

```bash
hermitcrab agent
```

Opens a chat prompt. Type messages and press Enter. Use `Ctrl+J` for newlines.

### One-shot mode

```bash
hermitcrab agent -m "Your message here"
```

Sends a single message and prints the response. Exits after completion.

### Named session

```bash
hermitcrab agent -s "cli:my-project"
```

Opens or resumes the named session.

### No markdown rendering

```bash
hermitcrab agent --no-markdown
```

Prints raw text without Markdown rendering.

### Show runtime logs

```bash
hermitcrab agent --logs
```

Shows HermitCrab runtime logs during chat.

### Listen on Nostr

```bash
hermitcrab agent --nostr-pubkey "npub1..."
```

Listens for DMs from a specific Nostr pubkey. Useful for testing Nostr connectivity without running the full gateway.

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-m, --message TEXT` | — | One-shot message |
| `-s, --session TEXT` | `cli:direct` | Session ID |
| `--markdown / --no-markdown` | `True` | Render output as Markdown |
| `--logs / --no-logs` | `False` | Show runtime logs |
| `--nostr-pubkey TEXT` | — | Nostr pubkey to listen for |

## hermitcrab gateway

Run the long-running gateway service with channels, cron, heartbeat, and reminders.

```bash
hermitcrab gateway
```

### Custom port

```bash
hermitcrab gateway --port 18791
```

### Verbose logging

```bash
hermitcrab gateway --verbose
```

Sets log level to DEBUG.

### Custom log level

```bash
hermitcrab gateway --log-level TRACE
```

Options: TRACE, DEBUG, INFO, WARNING, ERROR.

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-p, --port INT` | `18790` | Gateway port |
| `-v, --verbose` | `False` | Verbose output (DEBUG level) |
| `--log-level TEXT` | `"INFO"` | Log level |

## hermitcrab status

Show runtime and configuration status.

```bash
hermitcrab status
```

Reports config state, identity readiness, provider status, skill availability, MCP server health, and audit trail summary.

### JSON output

```bash
hermitcrab status --json
```

## hermitcrab doctor

Run first-run diagnostics with remediation steps.

```bash
hermitcrab doctor
```

Checks config, identity readiness, provider readiness, Ollama binary, MCP servers, and skill requirements. Each check has a severity level: `error`, `warning`, `info`, or `ok`.

## hermitcrab user

Manage identity-scoped users.

```bash
hermitcrab user list
hermitcrab user add alice
hermitcrab user remove alice
hermitcrab user archive alice
hermitcrab user route nostr alice <pubkey>
hermitcrab user models alice --interactive main
hermitcrab user status alice
```

The CLI is owner/operator controlled. Non-owner identities primarily interact through routed
channels.

### JSON output

```bash
hermitcrab doctor --json
```

## hermitcrab audit

Show recent audit trail events.

```bash
hermitcrab audit
```

Shows the 20 most recent entries with event name, timestamp, and key-value pairs.

### Limit entries

```bash
hermitcrab audit --limit 50
```

### Filter by event type

```bash
hermitcrab audit --event tool.policy_denied
```

### JSON output

```bash
hermitcrab audit --json
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-n, --limit INT` | `20` | Maximum entries to show |
| `-e, --event TEXT` | — | Filter by event type |
| `--json` | `False` | Output as JSON |

## Exit commands

Inside `hermitcrab agent` interactive mode, these commands end the session:

```
exit
quit
/exit
/quit
:q
```

All are case-insensitive.
