# Workspace model

HermitCrab's workspace architecture — single-workspace baseline and additive multi-workspace.

## Single workspace (default)

Every HermitCrab installation has one admin workspace:

```
~/.hermitcrab/workspace/
├── AGENTS.md          # Workspace instructions
├── IDENTITY.md        # Stable self-description
├── SOUL.md            # Behavioral guardrails
├── USER.md            # User preferences
├── TOOLS.md           # Tool discipline
├── HEARTBEAT.md       # Heartbeat tasks
├── memory/            # Deterministic memory
├── knowledge/         # Reference library
├── sessions/          # Raw conversation logs
├── scratchpads/       # Transient working notes
├── people/            # Named people profiles
├── lists/             # Checklists and todos
├── reminders/         # Scheduled reminder files
├── journal/           # Narrative session summaries
└── logs/              # Audit trail
```

This is the default model. All channels, sessions, memory, and tools operate within this single workspace.

## Multi-workspace (optional, additive)

Multi-workspace adds isolated sub-workspaces on top of the admin workspace. It is **not** a replacement for the admin path.

### Key principles

- The admin workspace (`~/.hermitcrab/workspace`) is always present and unchanged
- Sub-workspaces are additional isolated roots under `~/.hermitcrab/workspaces/`
- Each workspace keeps its own bootstrap files, memory, sessions, reminders, people, skills, and personality state
- There is no implicit fallback from admin workspace into sub-workspaces
- Sub-workspaces are **channel-only** — CLI and config remain admin-owned

### When to use multi-workspace

- You want isolated contexts for different people or purposes
- You want different Nostr senders to reach different workspaces
- You want isolated memory and knowledge per context

### Configuration

```json
{
  "workspaces": {
    "root": "~/.hermitcrab/workspaces",
    "registry": {
      "family": {
        "path": "family",
        "label": "Family workspace",
        "channelOnly": true
      },
      "work": {
        "path": "work",
        "label": "Work workspace",
        "channelOnly": true
      }
    }
  },
  "channels": {
    "nostr": {
      "enabled": true,
      "privateKey": "nsec1...",
      "allowedPubkeys": [
        "a1b2c3d4e5f6...",
        "d4e5f6a1b2c3..."
      ],
      "workspaceBindings": {
        "family": ["a1b2c3d4e5f6..."],
        "work": ["d4e5f6a1b2c3..."]
      }
    }
  }
}
```

### Validation rules

The config schema enforces:

1. **Unique pubkey bindings** — a pubkey cannot be assigned to multiple workspaces
2. **Allowlist membership** — bound pubkeys must also appear in `allowedPubkeys`
3. **Workspace references** — bindings must reference configured workspaces
4. **No open mode with bindings** — `"*"` in `allowedPubkeys` is incompatible with workspace bindings

### Workspace resolution

When a Nostr message arrives:

1. The sender pubkey is normalized to lowercase hex
2. The binding map is checked for a workspace assignment
3. If found, the message routes to that workspace's agent loop
4. If not found but in the admin allowlist, it routes to the admin workspace
5. If not in the allowlist, it is **denied** — no silent fallback

### Routing decision flow

```
Inbound Nostr message
  -> Normalize sender pubkey
  -> Check workspace_bindings
     -> Found -> Route to workspace agent
     -> Not found -> Check allowed_pubkeys
        -> In allowlist -> Route to admin workspace
        -> Not in allowlist -> DENIED (audit event logged)
```

Non-Nostr channels (Telegram, email, CLI) always route to the admin workspace.

### Sub-workspace behavior

Sub-workspaces are **channel-only**:

- Users interact via their bound Nostr pubkey
- They get isolated memory, sessions, knowledge, people, lists
- They do **not** have CLI access
- They do **not** have config access
- They do **not** see the admin workspace's data

### Admin workspace behavior in multi-workspace mode

The admin workspace remains fully functional:

- CLI access unchanged
- Config access unchanged
- Any pubkey in `allowedPubkeys` but not bound to a sub-workspace lands here
- Non-Nostr channels (Telegram, email) always land here

## Workspace bootstrap

Running `hermitcrab onboard` bootstraps the admin workspace:

1. Creates the workspace directory
2. Copies template files (if they don't exist): `AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `HEARTBEAT.md`
3. Creates subdirectories: `memory/`, `knowledge/`, `sessions/`, `scratchpads/`, `people/`, `lists/`, `reminders/`, `journal/`, `logs/`

Sub-workspaces must be bootstrapped manually or via config-driven onboarding (a beta3 direction).

### Bootstrap file requirements

A workspace is considered ready when:

- The directory exists
- `AGENTS.md` exists in the workspace root

The gateway checks this before routing messages to a workspace. Unbootstrapped workspaces receive a "denied" response.

## Workspace paths

| Workspace | Path |
|-----------|------|
| Admin (default) | `~/.hermitcrab/workspace` |
| Sub-workspace "family" | `~/.hermitcrab/workspaces/family` |
| Sub-workspace "work" | `~/.hermitcrab/workspaces/work` |

Relative paths in the registry are resolved against `workspaces.root`.

## Known limits

- Sub-workspace onboarding is manual today (copy templates, create dirs)
- Workspace isolation is enforced at the agent level, not at the filesystem permission level
- Cross-workspace memory sharing is not supported
- CLI commands always operate on the admin workspace
