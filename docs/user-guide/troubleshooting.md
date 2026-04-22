# Troubleshooting

Fix common problems with HermitCrab.

## Start with diagnostics

Run these commands before digging deeper:

```bash
hermitcrab doctor
hermitcrab status
```

`doctor` checks for configuration, workspace, and provider issues with remediation steps.
`status` reports runtime state, provider readiness, skill availability, and audit trail summary.

## Provider issues

### "No API key configured" error

Check your config:

```bash
hermitcrab status
```

Look for the selected provider. If it shows "not configured," add the API key:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-..."
    }
  }
}
```

### Ollama connection refused

1. Verify Ollama is running: `ollama list`
2. Check the apiBase in config: `"apiBase": "http://localhost:11434"` (no `/v1` suffix)
3. Verify the model is pulled: `ollama pull <model-name>`

### Model not found error

Check the model string format:

- Cloud models: `"anthropic/claude-opus-4-5"`, `"openai/gpt-4o"`
- OpenRouter: `"anthropic/claude-sonnet-4"` or `"openrouter/anthropic/claude-sonnet-4"`
- Ollama: `"ollama/gemma4:e4b"`

Run `hermitcrab doctor` to validate.

## Gateway issues

### Gateway won't start

Check for port conflicts:

```bash
hermitcrab gateway --port 18791
```

Review startup logs for channel connection errors.

### Channel not connecting

1. Verify the channel is enabled in config
2. Check credentials (bot token, private key, IMAP password)
3. Run with verbose logging: `hermitcrab gateway --verbose`
4. Check firewall/proxy settings

### Nostr relay connection failing

- Verify relays are accessible in config
- Check your private key is valid (nsec or 64-char hex)
- Try default relays: Damus, Primal, WellOrder

## Memory issues

### Agent doesn remember something

1. Check if the file exists: `ls workspace/memory/facts/`
2. Verify the frontmatter is valid YAML
3. Check for duplicate rejection — the agent may have rejected a near-duplicate

### Corrupted memory file

Memory files are plain Markdown with YAML frontmatter. Open in any editor and fix. The agent reads the file on next access.

## Session issues

### Session not resuming

Session keys are channel-specific. Verify you're using the right key:

```bash
hermitcrab agent -s "cli:direct"
```

### Session timeout too short

Default is 30 minutes. Adjust in config:

```json
{
  "agents": {
    "defaults": {
      "inactivityTimeoutS": 3600
    }
  }
}
```

## Tool issues

### Shell command blocked

The exec tool blocks dangerous operations:

- Disk writes (`dd`, `mkfs`)
- Process forks (fork bombs)
- Shutdown commands
- Network exfiltration patterns

If a legitimate command is blocked, rephrase it or use a safer alternative.

### File access denied

File tools are restricted to configured paths. By default, workspace directory access is enforced.

## Workspace issues

### Workspace not bootstrapped

Run:

```bash
hermitcrab onboard
```

This creates the directory structure and template files.

### Missing template files

Templates are copied during onboarding. If missing, re-run onboard or create them manually:

- `AGENTS.md` — workspace instructions
- `IDENTITY.md` — self-description
- `SOUL.md` — behavioral boundaries
- `USER.md` — user preferences
- `TOOLS.md` — tool discipline
- `HEARTBEAT.md` — heartbeat tasks

## Docker issues

### Container won't start

Check volume mounts:

```yaml
volumes:
  - ~/.hermitcrab:/root/.hermitcrab
```

Verify the host path exists and has correct permissions.

### Gateway in Docker can't reach Ollama

Ollama on `localhost` is not reachable from inside the container. Use the host's IP or `host.docker.internal`:

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://host.docker.internal:11434"
    }
  }
}
```

## Getting help

If the above doesn't resolve your issue:

1. Run `hermitcrab doctor --json` and `hermitcrab status --json` for full diagnostic output
2. Check the audit log: `hermitcrab audit`
3. Run the gateway with `--verbose` for detailed logs
4. Review the [FAQ](../reference/faq.md) for common questions
