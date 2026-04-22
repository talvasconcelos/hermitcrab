# Backups and recovery

Protect and restore HermitCrab workspace data.

## What to backup

The workspace directory contains everything that matters:

```
~/.hermitcrab/
├── config.json          # Configuration (API keys, channel config)
└── workspace/           # All runtime data
    ├── memory/          # Deterministic memory (facts, decisions, goals, tasks, reflections)
    ├── knowledge/       # Reference library
    ├── people/          # Named people profiles
    ├── lists/           # Checklists
    ├── reminders/       # Reminder artifacts
    ├── journal/         # Narrative session summaries
    ├── sessions/        # Raw conversation logs
    ├── scratchpads/     # Transient working notes (optional to backup)
    ├── logs/            # Audit trail
    ├── AGENTS.md        # Workspace instructions
    ├── IDENTITY.md      # Self-description
    ├── SOUL.md          # Behavioral guardrails
    ├── USER.md          # User preferences
    └── TOOLS.md         # Tool discipline
```

## Backup strategy

### Full workspace backup

```bash
tar czf hermitcrab-backup-$(date +%Y%m%d).tar.gz ~/.hermitcrab/
```

### Incremental backup

Use `rsync` for efficient incremental backups:

```bash
rsync -av --delete ~/.hermitcrab/ /path/to/backup/hermitcrab/
```

### Encrypted backup

```bash
tar czf - ~/.hermitcrab/ | openssl enc -aes-256-cbc -salt -out hermitcrab-backup-$(date +%Y%m%d).tar.gz.enc
```

### Docker backup

```bash
docker compose run --rm hermitcrab-cli tar czf /tmp/backup.tar.gz /root/.hermitcrab
docker cp $(docker compose ps -q hermitcrab-cli):/tmp/backup.tar.gz .
```

## What is safe to exclude

These directories can be excluded from backup if space is a concern:

| Directory | Why exclude | Safe to exclude? |
|-----------|-------------|------------------|
| `sessions/` | Raw logs, reconstructable from memory | Yes |
| `scratchpads/` | Transient working notes | Yes |
| `scratchpads/archive/` | Archived transient notes | Yes |
| `logs/audit.jsonl` | Audit trail (can grow large) | Optional |

## Restore from backup

### Full restore

```bash
tar xzf hermitcrab-backup-20260414.tar.gz -C /
```

### Selective restore

Restore only memory:

```bash
tar xzf hermitcrab-backup-20260414.tar.gz -C / --strip-components=2 home/user/.hermitcrab/workspace/memory
```

### After restore

1. Verify workspace structure:

```bash
hermitcrab doctor
```

2. Verify memory integrity:

```bash
ls ~/.hermitcrab/workspace/memory/facts/
ls ~/.hermitcrab/workspace/memory/tasks/
```

3. Restart the gateway if running:

```bash
systemctl --user restart hermitcrab-gateway
```

## Multi-workspace backup

Include all workspace roots:

```bash
tar czf hermitcrab-full-backup-$(date +%Y%m%d).tar.gz \
  ~/.hermitcrab/config.json \
  ~/.hermitcrab/workspace/ \
  ~/.hermitcrab/workspaces/
```

Restore the same way — all paths are restored to their original locations.

## Automated backups

### Cron job via HermitCrab

Tell the agent:

```
Create a cron job that backs up my HermitCrab workspace to /backup/ every Sunday at 2am.
```

### System cron

```cron
0 2 * * 0 tar czf /backup/hermitcrab-$(date +\%Y\%m\%d).tar.gz ~/.hermitcrab/
```

## Disaster recovery

### Lost config

Re-run onboarding and reconfigure providers:

```bash
hermitcrab onboard
```

Then add provider API keys to `~/.hermitcrab/config.json`.

### Lost memory

Memory files are plain Markdown. If lost but you have session logs, the agent can reconstruct some facts from conversation history using distillation (if enabled).

### Corrupted workspace

Restore from the most recent backup. If no backup exists, check if session archives contain recoverable information:

```bash
ls ~/.hermitcrab/workspace/sessions/archive/
```

### Lost API keys

API keys are not stored in memory or sessions. You must regenerate them from your provider's dashboard.

## Backup verification

After backup, verify the archive:

```bash
tar tzf hermitcrab-backup-20260414.tar.gz | head -20
```

Check for critical files:

```bash
tar tzf hermitcrab-backup-20260414.tar.gz | grep -E "config.json|AGENTS.md|memory/"
```
