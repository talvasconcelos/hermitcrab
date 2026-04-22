# Incident playbook

Step-by-step recovery when things go wrong.

## Before you start

Run diagnostics:

```bash
hermitcrab doctor
hermitcrab status
hermitcrab audit --limit 50
```

Gather information before taking action.

## Agent won't respond

### Symptom

`hermitcrab agent` starts but produces no output, or the gateway connects but messages get no reply.

### Steps

1. Check provider configuration:

```bash
hermitcrab status
```

If the provider shows "not configured," add the API key.

2. Test provider connectivity:

```bash
hermitcrab agent -m "Say hello"
```

If this hangs, the provider is unreachable or the API key is invalid.

3. Check logs:

```bash
hermitcrab gateway --verbose
```

Look for provider connection errors or rate limiting messages.

4. Check model string:

Verify the model string in `config.json` matches the provider format:
- Anthropic: `"anthropic/claude-opus-4-5"`
- OpenRouter: `"anthropic/claude-sonnet-4"`
- Ollama: `"ollama/gemma4:e4b"`

5. Retry with backoff:

LLM retries use exponential backoff. Wait 30 seconds and try again.

## Channel disconnected

### Symptom

Messages arrive on one channel but not others, or a channel shows connection errors in logs.

### Steps

1. Check channel config:

```bash
hermitcrab status
```

Verify the channel is enabled.

2. Verify credentials:

- Telegram: Check bot token with BotFather
- Nostr: Check private key is valid (nsec or 64-char hex)
- Email: Check IMAP/SMTP credentials work

3. Restart the gateway:

```bash
systemctl --user restart hermitcrab-gateway
```

4. Check network connectivity:

- Telegram: Test `curl https://api.telegram.org`
- Nostr: Test relay WebSocket connectivity
- Email: Test IMAP/SMTP with a mail client

5. Run with verbose logging:

```bash
hermitcrab gateway --verbose
```

Look for specific channel connection errors.

## Memory is wrong or contradictory

### Symptom

The agent states incorrect facts or has conflicting information.

### Steps

1. Inspect memory files:

```bash
grep -r "keyword" ~/.hermitcrab/workspace/memory/facts/
```

2. Remove incorrect facts:

```bash
rm ~/.hermitcrab/workspace/memory/facts/<incorrect-file>.md
```

3. Or tell the agent:

```
Forget that [incorrect fact].
```

4. Check for duplicates:

```bash
grep "^title:" ~/.hermitcrab/workspace/memory/facts/*.md | sort
```

Remove near-duplicates manually.

5. Add corrected information:

```
Remember that [correct fact].
```

## Agent is in a loop

### Symptom

The agent repeats the same action or response cycle.

### Steps

1. Interrupt:

Type a new message or press `Ctrl+C`.

2. Check tool iteration limits:

The agent has built-in loop detection (max 40 iterations, 2 identical tool cycles). If loops are happening within these limits, the agent's reasoning is driving it.

3. Check memory for stale goals:

```bash
ls ~/.hermitcrab/workspace/memory/goals/
```

Remove goals that may be causing repeated behavior.

4. Reset the session:

```
/new
```

Starts a fresh session without accumulated context.

## Workspace corrupted

### Symptom

Files are missing, memory is malformed, or the agent crashes on startup.

### Steps

1. Restore from backup:

```bash
tar xzf hermitcrab-backup-20260414.tar.gz -C /
```

2. If no backup exists, re-bootstrap:

```bash
hermitcrab onboard
```

This recreates missing template files and directories without overwriting existing data.

3. Manually recreate critical files:

- `AGENTS.md` — workspace instructions
- `IDENTITY.md` — self-description
- `SOUL.md` — behavioral guardrails

## Gateway won't start

### Symptom

`hermitcrab gateway` exits immediately or fails.

### Steps

1. Check config:

```bash
hermitcrab doctor
```

Fix any errors reported.

2. Check port availability:

```bash
ss -tlnp | grep 18790
```

If something else is using the port, use a different one:

```bash
hermitcrab gateway --port 18791
```

3. Check workspace invariants:

```bash
python -c "
from hermitcrab.config.loader import load_config
config = load_config(strict=True)
assert config.workspace_path == config.admin_workspace_path
print('Invariants OK')
"
```

4. Check channel configs for invalid values:

- Nostr: Valid private key
- Telegram: Valid bot token
- Email: Valid IMAP/SMTP settings

## Data loss suspected

### Symptom

Files are missing from the workspace or disk failure occurred.

### Steps

1. Stop the gateway immediately:

```bash
systemctl --user stop hermitcrab-gateway
```

Prevents further writes to a potentially corrupted state.

2. Assess damage:

```bash
hermitcrab doctor
ls ~/.hermitcrab/workspace/memory/
```

3. Restore from backup if available.

4. If no backup, check session archives:

```bash
ls ~/.hermitcrab/workspace/sessions/archive/
```

Session files contain conversation history that can be used to reconstruct memory.

5. Reconstruct critical memory:

Tell the agent what it should know:

```
Here's what you need to remember: [list of facts]
```

## Security incident

### Symptom

Unauthorized access, unexpected tool usage, or config compromise suspected.

### Steps

1. Stop the gateway:

```bash
systemctl --user stop hermitcrab-gateway
```

2. Rotate all secrets:

- Regenerate provider API keys
- Regenerate Nostr key pair
- Regenerate Telegram bot token
- Change email passwords

3. Review audit trail:

```bash
hermitcrab audit --limit 200
hermitcrab audit --event tool.policy_denied
```

4. Check config for unauthorized changes:

```bash
cat ~/.hermitcrab/config.json
```

5. Check workspace files for unauthorized modifications:

```bash
ls -lt ~/.hermitcrab/workspace/
```

6. Restore from a known-good backup.

7. Re-enable with new credentials.

## Escalation

If none of the above resolves the issue:

1. Run full diagnostics and capture output:

```bash
hermitcrab doctor --json > doctor.json
hermitcrab status --json > status.json
hermitcrab audit --json --limit 100 > audit.json
```

2. Run gateway with verbose logging and capture output:

```bash
hermitcrab gateway --verbose > gateway.log 2>&1
```

3. Include:

- HermitCrab version (`hermitcrab --version`)
- Python version (`python --version`)
- OS and architecture (Linux/macOS, Docker/systemd)
- The captured diagnostic and log files
