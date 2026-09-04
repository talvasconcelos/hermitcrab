# Manual Migration

HermitCrab's standard layout is `~/.hermitcrab/system/` plus
`~/.hermitcrab/identities/<name>/`. Older installs used `workspace/` and sometimes
`workspaces/<name>/`.

There is no automatic in-place migration. The clean path is:

1. Back up the old root.
2. Move the old root aside.
3. Run fresh onboarding.
4. Copy the files you actually want to keep into the new layout.
5. Recreate config, credentials, routes, and models deliberately.

## 1. Stop HermitCrab

Stop the gateway before moving files:

```bash
systemctl --user stop hermitcrab-gateway
```

If you run Docker or a foreground gateway instead, stop that process first.

## 2. Back Up

Create a full archive before changing anything:

```bash
tar czf hermitcrab-before-upgrade-$(date +%Y%m%d).tar.gz ~/.hermitcrab/
```

Keep this archive until the new install has run successfully for a while.

## 3. Start Fresh

Move the old root aside and let HermitCrab create the standard layout:

```bash
mv ~/.hermitcrab ~/.hermitcrab.old
hermitcrab onboard
```

This creates:

```text
~/.hermitcrab/
├── config.json
├── system/
└── identities/
    └── owner/
```

Do not copy the old `config.json` over the new one. Re-enter provider credentials, channel settings,
model aliases, and routing after the filesystem move.

## 4. Copy Owner Identity Data

The owner identity replaces the old single `workspace/` root.

Use the table as a checklist. Copy only paths that exist in your old install.

| Old path | New path |
|----------|----------|
| `~/.hermitcrab.old/workspace/IDENTITY.md` | `~/.hermitcrab/identities/owner/IDENTITY.md` |
| `~/.hermitcrab.old/workspace/SOUL.md` | `~/.hermitcrab/identities/owner/SOUL.md` |
| `~/.hermitcrab.old/workspace/USER.md` | `~/.hermitcrab/identities/owner/USER.md` |
| `~/.hermitcrab.old/workspace/HEARTBEAT.md` | `~/.hermitcrab/identities/owner/HEARTBEAT.md` |
| `~/.hermitcrab.old/workspace/cron/` | `~/.hermitcrab/identities/owner/cron/` |
| `~/.hermitcrab.old/workspace/journal/` | `~/.hermitcrab/identities/owner/journal/` |
| `~/.hermitcrab.old/workspace/knowledge/` | `~/.hermitcrab/identities/owner/knowledge/` |
| `~/.hermitcrab.old/workspace/lists/` | `~/.hermitcrab/identities/owner/lists/` |
| `~/.hermitcrab.old/workspace/memory/` | `~/.hermitcrab/identities/owner/memory/` |
| `~/.hermitcrab.old/workspace/people/` | `~/.hermitcrab/identities/owner/people/` |
| `~/.hermitcrab.old/workspace/projects/` | `~/.hermitcrab/identities/owner/projects/` |
| `~/.hermitcrab.old/workspace/reminders/` | `~/.hermitcrab/identities/owner/reminders/` |
| `~/.hermitcrab.old/workspace/reports/` | `~/.hermitcrab/identities/owner/reports/` |
| `~/.hermitcrab.old/workspace/scratchpads/` | `~/.hermitcrab/identities/owner/scratchpads/` |
| `~/.hermitcrab.old/workspace/sessions/` | `~/.hermitcrab/identities/owner/sessions/` |
| `~/.hermitcrab.old/workspace/skills/` | `~/.hermitcrab/identities/owner/skills/` |

Example copy command for a directory:

```bash
rsync -av ~/.hermitcrab.old/workspace/memory/ ~/.hermitcrab/identities/owner/memory/
```

## 5. Copy System Data

`AGENTS.md` and `TOOLS.md` are now system-owned. Copy them only if they contain operator guidance you
want to keep:

| Old path | New path |
|----------|----------|
| `~/.hermitcrab.old/workspace/AGENTS.md` | `~/.hermitcrab/system/AGENTS.md` |
| `~/.hermitcrab.old/workspace/TOOLS.md` | `~/.hermitcrab/system/TOOLS.md` |
| `~/.hermitcrab.old/workspace/logs/` | `~/.hermitcrab/system/logs/` |
| `~/.hermitcrab.old/history/` | `~/.hermitcrab/system/history/` |

Old top-level cron state (`~/.hermitcrab.old/cron/`) should not be copied blindly. Recreate scheduled
jobs under the relevant identity unless you know the old JSON format matches the current cron store.

## 6. Migrate Old Named Workspaces Manually

If you used `~/.hermitcrab.old/workspaces/<name>/`, convert each old context into an identity:

```bash
hermitcrab user add alice
```

Then copy that old workspace's identity-owned data into `~/.hermitcrab/identities/alice/` using the
same mapping as the owner identity:

```bash
rsync -av ~/.hermitcrab.old/workspaces/alice/memory/ ~/.hermitcrab/identities/alice/memory/
rsync -av ~/.hermitcrab.old/workspaces/alice/people/ ~/.hermitcrab/identities/alice/people/
```

Repeat for lists, reminders, sessions, skills, and any other directories that exist.

## 7. Rebuild Config

Open the old and new config side by side:

```bash
less ~/.hermitcrab.old/config.json
$EDITOR ~/.hermitcrab/config.json
```

Re-enter:

- provider API keys
- selected default model and named models
- channel credentials
- Nostr relays and protocol
- identity model overrides
- tool restrictions and MCP servers

Do not copy removed `workspaces` or `workspaceBindings` config into the new file. Nostr routes now
bind sender pubkeys to identities:

```bash
hermitcrab user route nostr alice <sender-pubkey>
```

Review every pubkey before binding it. Pubkeys are identity-critical; old workspace names or aliases
are not proof of who owns a key.

## 8. Verify

Run:

```bash
hermitcrab doctor
hermitcrab status
hermitcrab user list
```

Check the filesystem:

```bash
ls ~/.hermitcrab/system/
ls ~/.hermitcrab/identities/owner/
ls ~/.hermitcrab/identities/owner/memory/
```

Start a local CLI session before restarting the gateway:

```bash
hermitcrab agent
```

Ask a simple memory question and inspect whether expected files are visible under the owner identity.

## 9. Restart The Gateway

After config and identity roots look correct:

```bash
systemctl --user start hermitcrab-gateway
```

Then watch status and audit output:

```bash
hermitcrab status
hermitcrab audit --limit 20
```
