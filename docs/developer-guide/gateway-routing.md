# Gateway routing

How inbound messages reach the correct workspace.

## Routing decision flow

```
Inbound message (any channel)
  -> _resolve_gateway_workspace_route(msg)
     -> Is it Nostr?
        -> No -> Route to admin workspace
        -> Yes -> Check msg.metadata["workspace_target"]
           -> "denied" -> DENIED
           -> "workspace" -> Check multi-workspace active
              -> Active -> Check msg.metadata["workspace_name"]
                 -> Present -> Route to workspace
                 -> Missing -> DENIED
              -> Not active -> DENIED
           -> Other/null -> Route to admin workspace
```

## GatewayWorkspaceRouteDecision

Every routing decision produces a deterministic result:

```python
GatewayWorkspaceRouteDecision(
    target="admin" | "workspace" | "denied",
    reason="<string>",
    workspace_name="<optional string>"
)
```

Possible targets and reasons:

| Target | Reason | When |
|--------|--------|------|
| `admin` | `non_nostr_channel` | Telegram, email, CLI messages |
| `admin` | `admin_default` | Nostr messages without workspace target |
| `workspace` | `workspace_binding` | Nostr message with valid binding |
| `denied` | `channel_metadata_denied` | Message explicitly denied in metadata |
| `denied` | `workspace_mode_disabled` | Workspace target but multi-workspace not active |
| `denied` | `missing_workspace_name` | Workspace target but no workspace name provided |

## Multi-workspace activation

Multi-workspace routing is active only when both conditions are met:

1. `config.workspaces.registry` is non-empty
2. `config.channels.nostr.workspace_bindings` is non-empty

If either is empty, all messages route to admin.

## Nostr pubkey resolution

For Nostr messages, the channel resolves the sender pubkey before routing:

1. Normalize pubkey to lowercase 64-char hex
2. Look up in `workspace_bindings` for workspace assignment
3. If not found, check `allowed_pubkeys` for admin fallback
4. If not in allowlist, deny

## Workspace readiness check

Before routing to a workspace, the gateway verifies:

1. Workspace is in the registry
2. Workspace directory exists
3. `AGENTS.md` exists in the workspace root (bootstrap check)

Failed readiness checks result in a "denied" decision.

## Agent creation per workspace

The gateway lazily creates `AgentLoop` instances per workspace:

1. First message to a workspace triggers agent creation
2. Each workspace gets its own `SessionManager` and reminder service
3. Workspace path is resolved from config
4. Tools are initialized with workspace-scoped policies

## Outbound dispatch

Responses are published to the message bus with the originating channel and session key. The `ChannelManager` matches outbound messages to the correct channel for delivery.

## Deny behavior

Denied messages are:

- Logged as audit events
- Not silently routed to admin
- Not retried
- Not responded to (no leakage to sender)

This is a hard invariant: **no silent fallback on unresolved routes**.

## Containment boundaries

### Admin-owned (never delegated)

- Cron service and job definitions
- Heartbeat service and `HEARTBEAT.md`
- Config file (`config.json`)
- CLI commands
- Provider configuration

### Per-workspace (isolated)

- Memory store
- Session manager
- Knowledge library
- People profiles
- Lists
- Reminders
- Scratchpads
- Bootstrap files

## Config validation

The config schema validates multi-workspace bindings at load time:

1. **Unique pubkeys** — a pubkey cannot be assigned to multiple workspaces
2. **Allowlist membership** — bound pubkeys must also appear in `allowedPubkeys`
3. **Workspace references** — bindings must reference configured workspaces
4. **No open mode with bindings** — `"*"` in `allowedPubkeys` is incompatible with `workspace_bindings`

## Key files

| File | Responsibility |
|------|---------------|
| `cli/commands.py` | `_resolve_gateway_workspace_route()`, `_run_gateway_inbound_router()`, `gateway` command |
| `config/schema.py` | `resolve_nostr_sender_workspace()`, `normalized_nostr_workspace_bindings()`, validation |
| `channels/nostr.py` | Nostr channel, pubkey resolution, relay discovery |
| `agent/audit.py` | Audit trail for denied routes |
