# Gateway Routing

How inbound messages reach the correct identity.

## Routing Decision Flow

```text
Inbound message
  -> channel extracts sender metadata
  -> Nostr channel resolves sender with config.resolve_nostr_sender_identity()
  -> gateway reads identity metadata
     -> denied -> DENIED
     -> identity -> get or create identity-scoped AgentLoop
     -> missing metadata -> owner identity fallback
```

Non-Nostr channels route to the owner identity.

## GatewayIdentityRouteDecision

Every routing decision produces a deterministic result:

```python
GatewayIdentityRouteDecision(
    target="identity" | "denied",
    reason="<string>",
    identity_name="<optional string>"
)
```

Common reasons:

| Target | Reason | When |
|--------|--------|------|
| `identity` | `non_nostr_owner_fallback` | Non-Nostr channel message |
| `identity` | `channel_metadata_identity` | Nostr metadata names a resolved identity |
| `identity` | `owner_fallback` | No explicit identity metadata is present |
| `denied` | `channel_metadata_denied` | Channel resolution denied the sender |
| `denied` | `missing_identity_name` | Metadata says identity but omits the name |
| `denied` | `identity_not_configured` | Config has no such identity |
| `denied` | `identity_inactive` | Identity is configured but inactive |

## Nostr Pubkey Resolution

For Nostr messages, sender resolution is config-driven:

1. Normalize pubkey to lowercase 64-character hex.
2. Look up `channels.nostr.identityBindings`.
3. If bound, route to that active identity.
4. If unbound but allowed, route to the owner identity.
5. If not allowed, deny.

## Agent Creation

The gateway lazily creates `AgentLoop` instances per identity:

1. First message to an identity triggers agent creation.
2. The identity root is resolved from `config.identities.registry`.
3. Sessions, memory, reminders, cron, heartbeat, skills, and scratchpads root under that identity.
4. System guidance and audit use `system/`.

## Deny Behavior

Denied messages are audited, not silently rerouted, and not retried. This protects identity
boundaries.

## Boundaries

System-owned:

- `config.json`
- `system/AGENTS.md`
- `system/TOOLS.md`
- `system/logs/audit.jsonl`
- provider credentials and model registry

Identity-owned:

- memory and knowledge
- sessions and scratchpads
- people, lists, reminders, reports, projects
- `IDENTITY.md`, `SOUL.md`, `USER.md`, `HEARTBEAT.md`
- cron jobs and identity-local skills

## Config Validation

The config schema validates identity bindings at load time:

1. Bound identities must exist in `identities.registry`.
2. Routing pubkeys must be unique.
3. Removed workspace routing fields are rejected.

## Key Files

| File | Responsibility |
|------|----------------|
| `cli/commands.py` | gateway route resolution, identity agent lifecycle, `gateway` command |
| `config/schema.py` | `resolve_nostr_sender_identity()`, identity binding validation |
| `channels/nostr.py` | Nostr channel, pubkey metadata, relay discovery |
| `agent/audit.py` | audit trail for denied routes and tool events |
