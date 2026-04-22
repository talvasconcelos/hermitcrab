# Config reference

All configuration fields, defaults, and examples.

Config lives at `~/.hermitcrab/config.json`. It is created by `hermitcrab onboard` and can be edited manually.

## Root structure

```json
{
  "agents": {},
  "workspaces": {},
  "models": {},
  "channels": {},
  "providers": {},
  "gateway": {},
  "tools": {},
  "reflection": {}
}
```

## agents

Agent behavior defaults and model configuration.

### agents.defaults

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.hermitcrab/workspace",
      "model": "anthropic/claude-opus-4-5",
      "jobModels": {
        "interactiveResponse": "",
        "journalSynthesis": null,
        "distillation": null,
        "reflection": null,
        "summarisation": null,
        "subagent": null,
        "reasoningEffort": "medium"
      },
      "enableDistillation": false,
      "maxTokens": 8192,
      "temperature": 0.1,
      "maxToolIterations": 40,
      "memoryWindow": 100,
      "inactivityTimeoutS": 1800,
      "llmMaxRetries": 3,
      "llmRetryBaseDelayS": 0.6,
      "maxLoopSeconds": 300,
      "maxIdenticalToolCycles": 2,
      "memoryContextMaxChars": 10000,
      "memoryContextMaxItemsPerCategory": 20,
      "memoryContextMaxItemChars": 500
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `workspace` | `~/.hermitcrab/workspace` | Admin workspace path |
| `model` | `anthropic/claude-opus-4-5` | Primary model for interactive responses |
| `jobModels` | See below | Per-job-class model routing |
| `enableDistillation` | `false` | Enable background distillation |
| `maxTokens` | `8192` | Max tokens per response |
| `temperature` | `0.1` | Sampling temperature |
| `maxToolIterations` | `40` | Max tool-call loops per turn |
| `memoryWindow` | `100` | Messages in prompt context |
| `inactivityTimeoutS` | `1800` | Session timeout (30 min) |
| `llmMaxRetries` | `3` | Max LLM API retries |
| `llmRetryBaseDelayS` | `0.6` | Base delay for retry backoff |
| `maxLoopSeconds` | `300` | Max loop execution time (5 min) |
| `maxIdenticalToolCycles` | `2` | Detect stuck tool loops |
| `memoryContextMaxChars` | `10000` | Max chars injected from memory |
| `memoryContextMaxItemsPerCategory` | `20` | Max memory items per category in context |
| `memoryContextMaxItemChars` | `500` | Max chars per memory item in context |

### agents.defaults.jobModels

Per-job model routing. Fallback scheme:

1. Job-specific model if configured (non-empty string)
2. `interactive_response` — never falls back (must be configured)
3. `journal_synthesis` / `reflection` — fall back to primary model
4. `distillation` — `null` means "skip" (local-only policy)
5. `summarisation` / `subagent` — fall back to primary model

Reasoning effort (`"none"`, `"low"`, `"medium"`, `"high"`) is passed to LiteLLM and silently ignored by models that don't support it.

### agents.modelAliases

Shorthand aliases for model names:

```json
{
  "agents": {
    "modelAliases": {
      "coder": "localCoder",
      "fast": {
        "model": "ollama/gemma4:e2b",
        "reasoningEffort": "medium"
      }
    }
  }
}
```

## workspaces

Workspace registry for multi-workspace mode.

```json
{
  "workspaces": {
    "root": "~/.hermitcrab/workspaces",
    "registry": {
      "family": {
        "path": "family",
        "label": "Family workspace",
        "channelOnly": true
      }
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `root` | `~/.hermitcrab/workspaces` | Root for sub-workspaces |
| `registry` | `{}` | Named workspace entries |

### Workspace entry

| Field | Required | Description |
|-------|----------|-------------|
| `path` | Yes | Workspace directory path (relative to root if not absolute) |
| `label` | No | Human-readable label |
| `channelOnly` | Yes | Always `true` — sub-workspaces are channel-only |

## models

Named model definitions with optional provider-specific options:

```json
{
  "models": {
    "main": {
      "model": "ollama/gemma4:e4b"
    },
    "coder": {
      "model": "ollama/qwen3.5:7b",
      "providerOptions": {
        "num_ctx": 32768
      }
    }
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `model` | Yes | Model string |
| `reasoningEffort` | No | Override reasoning effort |
| `providerOptions` | No | Provider-specific request options |

## channels

Channel configuration and behavior.

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "telegram": {},
    "email": {},
    "nostr": {}
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `sendProgress` | `true` | Stream text progress to channel |
| `sendToolHints` | `false` | Stream tool-call hints |

### channels.telegram

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable Telegram channel |
| `token` | `""` | Bot token from BotFather |
| `allowFrom` | `[]` | Allowed user IDs/usernames |
| `proxy` | `null` | HTTP/SOCKS5 proxy URL |
| `replyToMessage` | `false` | Quote original message |

### channels.email

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable email channel |
| `consentGranted` | `false` | Explicit permission to access mailbox |
| `imapHost` | `""` | IMAP server |
| `imapPort` | `993` | IMAP port |
| `imapUsername` | `""` | IMAP username |
| `imapPassword` | `""` | IMAP password |
| `imapMailbox` | `"INBOX"` | IMAP mailbox |
| `smtpHost` | `""` | SMTP server |
| `smtpPort` | `587` | SMTP port |
| `smtpUsername` | `""` | SMTP username |
| `smtpPassword` | `""` | SMTP password |
| `fromAddress` | `""` | Outbound from address |
| `autoReplyEnabled` | `true` | Auto-reply to inbound emails |
| `pollIntervalSeconds` | `30` | Poll interval for new mail |
| `markSeen` | `true` | Mark processed emails as read |
| `maxBodyChars` | `12000` | Truncate inbound body |
| `subjectPrefix` | `"Re: "` | Outbound subject prefix |
| `allowFrom` | `[]` | Allowed sender addresses |

### channels.nostr

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable Nostr channel |
| `privateKey` | `""` | nsec or hex private key |
| `relays` | `[Damus, Primal, WellOrder]` | Bootstrap relays |
| `protocol` | `"nip04"` | `"nip04"` or `"nip17"` |
| `nip17FallbackToConfiguredRelays` | `true` | Fallback to configured relays if relay discovery fails |
| `nip17RelayDiscoveryTimeoutS` | `4.0` | Relay discovery timeout |
| `nip17RelayCacheTtlS` | `600` | Relay cache TTL |
| `allowedPubkeys` | `[]` | Sender allowlist |
| `workspaceBindings` | `{}` | Pubkey-to-workspace mapping |

## providers

LLM provider credentials.

```json
{
  "providers": {
    "anthropic": { "apiKey": "sk-ant-..." },
    "openrouter": { "apiKey": "sk-or-..." },
    "ollama": { "apiBase": "http://localhost:11434" },
    "openai": { "apiKey": "sk-..." }
  }
}
```

Each provider supports: `apiKey`, `apiBase`, `extraHeaders`.

Supported providers: `anthropic`, `openai`, `openrouter`, `ollama`, `deepseek`, `groq`, `gemini`, `zhipu`, `dashscope`, `moonshot`, `minimax`, `vllm`, `nvidia_nim`, `aihubmix`, `siliconflow`, `volcengine`, `openai_oauth`, `openai_codex`, `qwen_oauth`, `github_copilot`, `custom`.

## gateway

Gateway and background service configuration.

```json
{
  "gateway": {
    "host": "0.0.0.0",
    "port": 18790,
    "heartbeat": {
      "enabled": true,
      "intervalS": 1800
    },
    "reminders": {
      "intervalS": 60
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `host` | `0.0.0.0` | Bind address |
| `port` | `18790` | Gateway port |
| `heartbeat.enabled` | `true` | Enable heartbeat service |
| `heartbeat.intervalS` | `1800` | Heartbeat interval (30 min) |
| `reminders.intervalS` | `60` | Reminder polling interval (60s) |

## tools

Tool behavior and configuration.

```json
{
  "tools": {
    "restrictToWorkspace": false,
    "exec": { "timeout": 60 },
    "web": {
      "search": { "apiKey": "", "maxResults": 5 }
    },
    "mcpServers": {}
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `restrictToWorkspace` | `false` | Restrict all file access to workspace |
| `exec.timeout` | `60` | Shell command timeout (seconds) |
| `web.search.apiKey` | `""` | Brave Search API key (optional) |
| `web.search.maxResults` | `5` | Max search results |

### tools.mcpServers

```json
{
  "tools": {
    "mcpServers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx" },
        "toolTimeout": 30
      }
    }
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `command` | stdio | Command to run |
| `args` | stdio | Command arguments |
| `env` | stdio | Extra environment variables |
| `url` | HTTP | Streamable HTTP endpoint URL |
| `headers` | HTTP | Custom HTTP headers |
| `toolTimeout` | No | Per-tool timeout (seconds, default 30) |

## reflection

Reflection system configuration.

```json
{
  "reflection": {
    "promotion": {
      "autoPromote": false,
      "targetFiles": ["AGENTS.md", "SOUL.md", "IDENTITY.md", "TOOLS.md"],
      "maxFileLines": 500,
      "notifyUser": true
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `autoPromote` | `false` | Auto-edit bootstrap files from reflections |
| `targetFiles` | `[AGENTS.md, SOUL.md, IDENTITY.md, TOOLS.md]` | Files to update |
| `maxFileLines` | `500` | Archive old sections if file exceeds this |
| `notifyUser` | `true` | Inform user when bootstrap files are updated |

## Environment variables

Config can be set via environment variables with the `HERMITCRAB_` prefix and `__` delimiter for nesting:

```bash
export HERMITCRAB__PROVIDERS__ANTHROPIC__API_KEY="sk-ant-..."
export HERMITCRAB__AGENTS__DEFAULTS__MODEL="anthropic/claude-opus-4-5"
```
