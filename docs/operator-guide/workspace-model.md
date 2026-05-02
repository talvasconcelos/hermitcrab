# Workspace Model

HermitCrab keeps one readable root at `~/.hermitcrab/`.

Runtime state is split into:

- `config.json`: one global configuration file
- `system/`: owner-managed operational guidance, logs, indexes, and history
- `identities/<name>/`: identity-scoped memory, sessions, skills, routines, and user profile

## Standard Layout

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
        ├── journal/
        ├── knowledge/
        ├── lists/
        ├── memory/
        ├── people/
        ├── projects/
        ├── reminders/
        ├── reports/
        ├── scratchpads/
        ├── sessions/
        └── skills/
```

`hermitcrab onboard` creates this layout. Single-user installs use the same identity structure as
multi-identity installs.

## Identities

An identity is an isolated context for a person, role, customer, project, or operating mode.

Each identity owns:

- memory and knowledge
- sessions and scratchpads
- people, lists, reminders, reports, and projects
- `IDENTITY.md`, `SOUL.md`, `USER.md`, and `HEARTBEAT.md`
- cron jobs and identity-local skills

The CLI uses `user` as the operator-facing command language:

```bash
hermitcrab user list
hermitcrab user add alice
hermitcrab user route nostr alice <pubkey>
hermitcrab user models alice --interactive main
```

## System State

`system/AGENTS.md` and `system/TOOLS.md` are owner/operator guidance shared by all identities.
Durable audit logs live under `system/logs/`.

Per-identity journals remain human-readable identity history; system audit is operational evidence.

## Routing

Channels identify senders. The gateway resolves senders to identity names through `config.json`, then
creates or reuses an identity-scoped agent.

Nostr routing uses `channels.nostr.identityBindings`:

```json
{
  "channels": {
    "nostr": {
      "enabled": true,
      "identityBindings": {
        "alice": ["<sender-pubkey-hex>"]
      }
    }
  }
}
```

If a sender is allowed but not explicitly bound, the gateway falls back to the owner identity. If the
sender is not allowed or routes to an inactive identity, the message is denied.

## Isolation

Identity roots are separate by default. There is no shared memory root and no automatic promotion
from one identity to another.

Shared state is reserved for future design work. Until then, keep cross-identity sharing explicit and
manual.
