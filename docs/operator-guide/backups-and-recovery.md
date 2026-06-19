# Backups And Recovery

Protect and restore HermitCrab data.

## What To Back Up

Back up the whole HermitCrab root:

```text
~/.hermitcrab/
├── config.json
├── system/
│   ├── AGENTS.md
│   ├── TOOLS.md
│   ├── history/
│   ├── indexes/
│   └── logs/
└── identities/
    └── owner/
        ├── IDENTITY.md
        ├── SOUL.md
        ├── USER.md
        ├── HEARTBEAT.md
        ├── cron/
        ├── memory/
        ├── knowledge/
        ├── people/
        ├── lists/
        ├── reminders/
        ├── journal/
        ├── sessions/
        ├── scratchpads/
        └── skills/
```

## Full Backup

```bash
tar czf hermitcrab-backup-$(date +%Y%m%d).tar.gz ~/.hermitcrab/
```

## Incremental Backup

```bash
rsync -av --delete ~/.hermitcrab/ /path/to/backup/hermitcrab/
```

## Encrypted Backup

```bash
tar czf - ~/.hermitcrab/ | openssl enc -aes-256-cbc -salt -out hermitcrab-backup-$(date +%Y%m%d).tar.gz.enc
```

## Safe Exclusions

These directories can be excluded if space is a concern:

| Directory | Why exclude | Safe to exclude? |
|-----------|-------------|------------------|
| `identities/*/sessions/` | Raw logs, partially reconstructable from memory | Yes |
| `identities/*/scratchpads/` | Transient working notes | Yes |
| `system/logs/audit.jsonl` | Audit trail can grow large | Optional |

## Restore

Full restore:

```bash
tar xzf hermitcrab-backup-20260414.tar.gz -C /
```

Selective memory restore for the owner identity:

```bash
tar xzf hermitcrab-backup-20260414.tar.gz -C / --strip-components=2 home/user/.hermitcrab/identities/owner/memory
```

After restore:

```bash
hermitcrab doctor
systemctl --user restart hermitcrab-gateway
```

## Disaster Recovery

If `config.json` is lost, run:

```bash
hermitcrab onboard
```

Then re-add provider and channel credentials.

If identity memory is lost but sessions remain, optional distillation may recover some facts from
conversation history. Treat that as best-effort recovery, not a substitute for backups.

## Verification

```bash
tar tzf hermitcrab-backup-20260414.tar.gz | head -20
tar tzf hermitcrab-backup-20260414.tar.gz | grep -E "config.json|system/AGENTS.md|identities/.+/memory/"
```
