# Channels

Connect HermitCrab to messaging platforms so you can interact from anywhere.

## Overview

HermitCrab supports four channels:

| Channel | Use case | Setup complexity |
|---------|----------|------------------|
| CLI | Local terminal sessions | None — works out of the box |
| Nostr | Encrypted DMs | Medium — requires key pair |
| Telegram | Bot on Telegram | Low — requires bot token |
| Email | IMAP/SMTP integration | Medium — requires IMAP/SMTP config |

Channels run inside the gateway service. Start it with:

```bash
hermitcrab gateway
```

## CLI

Always available. Run `hermitcrab agent` for an interactive session or `hermitcrab agent -m "message"` for one-shot mode.

No configuration needed.

## Nostr

Nostr is the primary channel. It supports encrypted direct messages via NIP-04 (legacy) or NIP-17 (modern).

### Generate a key pair

```bash
python -c 'from pynostr.key import PrivateKey; k = PrivateKey(); print(f"nsec: {k.bech32()}"); print(f"npub: {k.public_key.bech32()}")'
```

Save both values. The `nsec` is your agent's identity. The `npub` is what people use to message you.

### Configure the channel

Edit `~/.hermitcrab/config.json`:

```json
{
  "channels": {
    "nostr": {
      "enabled": true,
      "privateKey": "nsec1... or hex",
      "protocol": "nip17",
      "allowedPubkeys": [
        "a1b2c3d4e5f6..."
      ]
    }
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | Yes | Set to `true` to activate the channel |
| `privateKey` | Yes | Your agent's nsec or hex private key |
| `protocol` | No | `"nip04"` (legacy) or `"nip17"` (modern). Default: `"nip04"` |
| `allowedPubkeys` | Yes | List of sender pubkeys allowed to message you. Use `[]` for strict deny-all |
| `relays` | No | Bootstrap relays. Default: Damus, Primal, WellOrder |

### Allow all senders (open mode)

```json
{
  "channels": {
    "nostr": {
      "enabled": true,
      "privateKey": "nsec1...",
      "allowedPubkeys": ["*"]
    }
  }
}
```

Warning: this lets anyone message your agent. Use only if you want open access.

### Current behavior and known limits

- NIP-04 DMs are stable. NIP-17 DM support continues to improve.
- NIP-17 uses kind 10050 relay discovery with fallback to configured relays.

## Telegram

### Create a bot

1. Message `@BotFather` on Telegram
2. Send `/newbot` and follow prompts
3. Copy the bot token

### Configure the channel

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
      "allowFrom": ["123456789", "@username"]
    }
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | Yes | Set to `true` |
| `token` | Yes | Bot token from BotFather |
| `allowFrom` | No | Allowed user IDs or usernames. Empty = allow all |
| `replyToMessage` | No | If `true`, bot quotes the original message. Default: `false` |
| `proxy` | No | HTTP/SOCKS5 proxy URL |

## Email

### Configure IMAP and SMTP

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUsername": "you@gmail.com",
      "imapPassword": "your-app-password",
      "imapMailbox": "INBOX",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUsername": "you@gmail.com",
      "smtpPassword": "your-app-password",
      "fromAddress": "you@gmail.com",
      "allowFrom": ["trusted-sender@example.com"]
    }
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `consentGranted` | Yes | Explicit permission to access mailbox data |
| `autoReplyEnabled` | No | Send automatic replies. Default: `true` |
| `pollIntervalSeconds` | No | How often to check for mail. Default: `30` |
| `markSeen` | No | Mark processed emails as read. Default: `true` |
| `maxBodyChars` | No | Truncate inbound bodies. Default: `12000` |
| `subjectPrefix` | No | Prefix for outbound replies. Default: `"Re: "` |
| `allowFrom` | No | Allowed sender addresses. Empty = allow all |

### Use app passwords

For Gmail and most providers, use an app-specific password, not your main account password.

## Starting the gateway

```bash
hermitcrab gateway
```

The gateway starts all enabled channels simultaneously. Each channel connects independently.

## Outbound messages

When the agent replies, messages are dispatched through the same channel the user messaged on. Progress updates and tool hints can be streamed if enabled:

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": false
  }
}
```

## Multi-channel identity

The same agent handles all channels. Memory, tasks, and knowledge are shared across channels. Sessions remain channel-scoped unless you explicitly target the same session key.
