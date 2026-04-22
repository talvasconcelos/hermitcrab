# Channel reference

Channel setup details, behavior, and limits.

## CLI

Always available. No configuration needed.

### Behavior

- Runs in the foreground
- Uses `prompt_toolkit` for input editing, history, and paste support
- History stored in `~/.hermitcrab/history/cli_history`
- `Ctrl+J` inserts newline
- `Ctrl+C` interrupts and exits

### Session keys

- `cli:direct` — default session
- `cli:<name>` — named session via `-s` flag

## Nostr

Primary channel. Encrypted DMs via NIP-04 (legacy) or NIP-17 (modern).

### Protocols

**NIP-04** — legacy encrypted DMs. Stable and widely supported. Uses kind 4 events with shared-key encryption.

**NIP-17** — modern gift-wrap DMs. Uses kind 14 events with relay discovery via kind 10050. Support is evolving in beta3.

### Relay discovery (NIP-17)

1. Check kind 10050 for recipient's preferred relays
2. Timeout after 4 seconds (configurable: `nip17RelayDiscoveryTimeoutS`)
3. Fall back to configured relays if discovery fails (configurable: `nip17FallbackToConfiguredRelays`)
4. Cache relay info for 10 minutes (configurable: `nip17RelayCacheTtlS`)

### Default relays

- `wss://relay.damus.io`
- `wss://relay.primal.net`
- `wss://nostr-pub.wellorder.net`

### Access control

`allowedPubkeys` controls who can message your agent:

- `[]` — deny all (no one can message)
- `["pubkey1", "pubkey2"]` — allowlist (only listed senders)
- `["*"]` — open mode (anyone can message)

Pubkeys are configured as lowercase 64-char hex strings. `npub` format is converted automatically.

### Multi-workspace routing

In multi-workspace mode, sender pubkeys are mapped to specific workspaces via `workspaceBindings`. See [Workspace model](../operator-guide/workspace-model.md).

### Session keys

- `nostr:<sender_pubkey>` — single workspace
- `nostr:<workspace>:<sender_pubkey>` — multi-workspace

### Current behavior and known limits

- NIP-04 is stable
- NIP-17 is actively developed in beta3
- Group conversation support is a beta3 priority
- Relay discovery can fail on less-popular relays; fallback to configured relays handles this

## Telegram

Classic Telegram bot.

### Setup

1. Create a bot via `@BotFather` — send `/newbot` and follow prompts
2. Copy the bot token
3. Configure in `config.json`

### Access control

`allowFrom` lists allowed user IDs or usernames:

- `[]` — allow all
- `["123456789", "@username"]` — allowlist

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `replyToMessage` | `false` | Quote the original message in replies |
| `proxy` | `null` | HTTP/SOCKS5 proxy URL |

### Session keys

- `telegram:<chat_id>`

## Email

IMAP inbound + SMTP outbound integration.

### Setup

1. Enable IMAP and SMTP on your email provider
2. Generate an app-specific password (not your main password)
3. Configure both inbound (IMAP) and outbound (SMTP) settings

### Behavior

- Polls IMAP every 30 seconds (configurable)
- Auto-replies to inbound emails (configurable: `autoReplyEnabled`)
- Marks processed emails as read (configurable: `markSeen`)
- Truncates body at 12000 characters (configurable: `maxBodyChars`)
- Prefixes outbound replies with "Re: " (configurable: `subjectPrefix`)

### Access control

`allowFrom` lists allowed sender addresses:

- `[]` — allow all
- `["trusted@example.com"]` — allowlist

### Consent

`consentGranted` must be `true`. This is an explicit owner permission to access mailbox data.

### Session keys

- `email:<sender_address>`

## Outbound message dispatch

The `ChannelManager` routes outbound messages back to the originating channel. When the agent calls `message` or produces a final response:

1. The response is published to the message bus
2. The `ChannelManager` dispatches it to the correct channel
3. Progress updates stream if `sendProgress` is enabled
4. Tool hints stream if `sendToolHints` is enabled

## Channel startup order

When the gateway starts, channels are initialized and connected in sequence. Each channel connects independently — a failure in one does not block others.
