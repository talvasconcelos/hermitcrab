# Manual Migration

HermitCrab beta releases may introduce breaking filesystem and config changes. The current standard
layout is `~/.hermitcrab/system/` plus `~/.hermitcrab/identities/<name>/`.

There is no automatic in-place migration. Back up first, run fresh onboarding, then copy the data you
want to keep.

## 1. Back Up

```bash
tar czf hermitcrab-before-upgrade-$(date +%Y%m%d).tar.gz ~/.hermitcrab/
```

## 2. Start From The Standard Layout

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

## 3. Copy Owner Identity Data

From the old single-workspace layout, copy user-authored identity data into the owner identity:

| Old path | New path |
|----------|----------|
| `~/.hermitcrab.old/workspace/IDENTITY.md` | `~/.hermitcrab/identities/owner/IDENTITY.md` |
| `~/.hermitcrab.old/workspace/SOUL.md` | `~/.hermitcrab/identities/owner/SOUL.md` |
| `~/.hermitcrab.old/workspace/USER.md` | `~/.hermitcrab/identities/owner/USER.md` |
| `~/.hermitcrab.old/workspace/HEARTBEAT.md` | `~/.hermitcrab/identities/owner/HEARTBEAT.md` |
| `~/.hermitcrab.old/workspace/memory/` | `~/.hermitcrab/identities/owner/memory/` |
| `~/.hermitcrab.old/workspace/knowledge/` | `~/.hermitcrab/identities/owner/knowledge/` |
| `~/.hermitcrab.old/workspace/lists/` | `~/.hermitcrab/identities/owner/lists/` |
| `~/.hermitcrab.old/workspace/people/` | `~/.hermitcrab/identities/owner/people/` |
| `~/.hermitcrab.old/workspace/projects/` | `~/.hermitcrab/identities/owner/projects/` |
| `~/.hermitcrab.old/workspace/reminders/` | `~/.hermitcrab/identities/owner/reminders/` |
| `~/.hermitcrab.old/workspace/reports/` | `~/.hermitcrab/identities/owner/reports/` |
| `~/.hermitcrab.old/workspace/sessions/` | `~/.hermitcrab/identities/owner/sessions/` |
| `~/.hermitcrab.old/workspace/skills/` | `~/.hermitcrab/identities/owner/skills/` |

Copy `AGENTS.md` and `TOOLS.md` into `system/` only if they contain operator guidance you want to
keep:

| Old path | New path |
|----------|----------|
| `~/.hermitcrab.old/workspace/AGENTS.md` | `~/.hermitcrab/system/AGENTS.md` |
| `~/.hermitcrab.old/workspace/TOOLS.md` | `~/.hermitcrab/system/TOOLS.md` |

## 4. Review Multi-User Data Manually

Old named workspace routing is not preserved automatically. For every old isolated context, create a
new identity:

```bash
hermitcrab user add alice
hermitcrab user route nostr alice <sender-pubkey>
```

Then copy that context's data into `~/.hermitcrab/identities/alice/`.

Review Nostr pubkeys carefully. Pubkeys are identity-critical; do not migrate old aliases or bindings
without checking who owns each key.

## 5. Reconfigure Secrets

Provider credentials and channel secrets live in `config.json`. Re-enter them manually instead of
copying stale config wholesale.

## 6. Verify

```bash
hermitcrab doctor
hermitcrab status
hermitcrab user list
```

Start the gateway only after the config and identity roots look correct.
