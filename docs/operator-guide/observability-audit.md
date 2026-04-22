# Observability and audit

Monitor HermitCrab health and inspect what the agent is doing.

## Diagnostic commands

### Quick status

```bash
hermitcrab status
```

Reports:

- Config path, existence, validity
- Workspace path and bootstrap readiness
- Named workspace count and readiness
- Nostr workspace binding count
- Multi-workspace routing status
- Selected model and provider
- Provider status across all configured providers
- Skill availability
- MCP server status
- Audit trail summary
- Next steps

Use `--json` for machine-readable output.

### Deep diagnostics

```bash
hermitcrab doctor
```

Checks with severity levels:

| Severity | Meaning |
|----------|---------|
| `error` | Blocks operation — must fix |
| `warning` | May cause issues — should fix |
| `info` | Informational — good to know |
| `ok` | All clear |

Checks include:

- Config existence and parsing
- Workspace bootstrapping
- Provider readiness
- Ollama binary presence
- MCP server configuration
- Skill requirements

Use `--json` for machine-readable output.

## Audit trail

### What is logged

The audit trail (`workspace/logs/audit.jsonl`) records:

- Tool policy denials (`tool.policy_denied`)
- Tool policy redirections (`tool.policy_redirected`)
- Significant gateway routing decisions

### Viewing audit entries

```bash
hermitcrab audit
```

Shows the 20 most recent entries with event name, timestamp, and key-value pairs.

### Filter by event type

```bash
hermitcrab audit --event tool.policy_denied
```

### Show more entries

```bash
hermitcrab audit --limit 100
```

### JSON output

```bash
hermitcrab audit --json
```

For piping to `jq` or other tools.

### Audit log rotation

- Auto-rotates at 256KB
- Archives to `workspace/logs/archive/`
- Keeps maximum 5 archived files

### Inspecting audit entries manually

Each line is a JSON object:

```bash
tail -5 ~/.hermitcrab/workspace/logs/audit.jsonl | python -m json.tool
```

## Monitoring gateway health

### Service status

```bash
systemctl --user status hermitcrab-gateway
```

### Log tail

```bash
journalctl --user -u hermitcrab-gateway --no-pager -n 50
```

### Follow logs

```bash
journalctl --user -u hermitcrab-gateway -f
```

### Docker logs

```bash
docker compose logs -f hermitcrab-gateway
```

## Inspecting route decisions

### Multi-workspace routing status

```bash
hermitcrab status
```

Look for:

```
Multi-workspace routing: active
  Workspaces configured: 2
  Nostr bindings: 2
```

### Binding verification

Check your config:

```bash
python -c "
import json
with open('/home/user/.hermitcrab/config.json') as f:
    config = json.load(f)
print('Allowlist:', config['channels']['nostr']['allowedPubkeys'])
print('Bindings:', config['channels']['nostr']['workspaceBindings'])
"
```

Verify:

1. Every pubkey in bindings appears in allowlist
2. No pubkey appears in multiple bindings
3. Every binding references a configured workspace

### Audit routing denials

```bash
hermitcrab audit --event tool.policy_denied
```

Look for denied inbound attempts from unknown pubkeys.

## Memory quality checks

### Count memory items

```bash
echo "Facts: $(ls ~/.hermitcrab/workspace/memory/facts/ 2>/dev/null | wc -l)"
echo "Tasks: $(ls ~/.hermitcrab/workspace/memory/tasks/ 2>/dev/null | wc -l)"
echo "Goals: $(ls ~/.hermitcrab/workspace/memory/goals/ 2>/dev/null | wc -l)"
echo "Decisions: $(ls ~/.hermitcrab/workspace/memory/decisions/ 2>/dev/null | wc -l)"
echo "Reflections: $(ls ~/.hermitcrab/workspace/memory/reflections/ 2>/dev/null | wc -l)"
```

### Check for malformed files

```bash
for f in ~/.hermitcrab/workspace/memory/facts/*.md; do
  head -1 "$f" | grep -q '---' || echo "Missing frontmatter: $f"
done
```

### Check for duplicate-like files

```bash
# Sort by title (manual inspection)
grep "^title:" ~/.hermitcrab/workspace/memory/facts/*.md | sort
```

## Session health

### Active sessions

```bash
ls -lt ~/.hermitcrab/workspace/sessions/
```

Most recent session listed first.

### Archived sessions

```bash
ls ~/.hermitcrab/workspace/sessions/archive/ | wc -l
```

### Check for truncated history

The session manager detects broken leading segments in session files. If you see repair log entries, the session was automatically repaired.

## Tool usage patterns

### Most used tools

The audit trail can reveal tool usage patterns:

```bash
grep -o '"tool_name":"[^"]*"' ~/.hermitcrab/workspace/logs/audit.jsonl | sort | uniq -c | sort -rn
```

### Policy denial patterns

```bash
grep "policy_denied" ~/.hermitcrab/workspace/logs/audit.jsonl | python -m json.tool
```

Frequent denials may indicate the agent is attempting unsafe operations or a user needs education on capabilities.
