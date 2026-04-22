# Security and permissions

How HermitCrab protects you by default and how to customize controls.

## Core security model

Python is the source of truth. The LLM proposes actions; Python enforces them.

Every tool call is validated and authorized before execution. The model never has direct access to files, shell, or external services.

## Tool permission levels

Tools are classified by risk:

| Level | Description | Examples |
|-------|-------------|----------|
| `read_only` | Safe reads | `read_file`, `list_dir`, `read_memory`, `search_memory` |
| `workspace_write` | Writes within workspace | `write_file`, `edit_file`, `write_fact`, `write_task` |
| `network` | External network access | `web_search`, `web_fetch`, MCP tools |
| `dangerous_exec` | Shell execution (guarded) | `exec` |
| `coordinator` | Orchestration tools | `message`, `spawn`, `cron` |

Subagents use a filtered policy that excludes `coordinator` tools and may further restrict `dangerous_exec`.

## Shell execution safety

The `exec` tool applies multiple safety layers:

### Deny patterns

These operations are always blocked:

- Disk destruction (`dd` to devices, `mkfs`)
- Fork bombs and mass process creation
- System shutdown and reboot commands
- Known exfiltration patterns

### Risk classification

Commands are classified as safe, moderate, or dangerous. The policy system can redirect or block based on risk level.

### Timeout

Commands timeout after 60 seconds by default. Configure in:

```json
{
  "tools": {
    "exec": {
      "timeout": 60
    }
  }
}
```

### Working directory

Commands run within the workspace directory unless otherwise specified.

## File access controls

### Workspace restriction

When enabled, all file access is restricted to the workspace directory:

```json
{
  "tools": {
    "restrictToWorkspace": true
  }
}
```

This prevents reads or writes outside `~/.hermitcrab/workspace/`.

### Allowed paths

File tools (`read_file`, `write_file`, `edit_file`, `list_dir`) only access paths within the allowed set. By default, the workspace directory is the root of allowed paths.

## Channel access control

### Nostr

Sender pubkeys must be explicitly allowed:

```json
{
  "channels": {
    "nostr": {
      "allowedPubkeys": ["a1b2c3...", "d4e5f6..."]
    }
  }
}
```

- `[]` — strict deny-all (no one can message)
- `["*"]` — open mode (anyone can message)
- `["pubkey1", "pubkey2"]` — allowlist (only listed senders)

Unknown pubkeys are denied by default. No silent fallback.

### Telegram

Allowed users by ID or username:

```json
{
  "channels": {
    "telegram": {
      "allowFrom": ["123456789", "@username"]
    }
  }
}
```

Empty list allows all. Populated list acts as an allowlist.

### Email

Allowed sender addresses:

```json
{
  "channels": {
    "email": {
      "allowFrom": ["trusted@example.com"]
    }
  }
}
```

Empty list allows all inbound senders.

## Policy enforcement

### Tool registry

The `ToolPermissionPolicy` enforces allowed tools and permissions per actor. Policy denials include structured hints with alternative tools and safe fallbacks.

### Subagent isolation

Subagents receive:

- Bounded tool set (filtered by profile)
- No access to coordinator tools (`message`, `spawn`)
- No direct channel access
- Compact task brief, not full conversation history

## Audit trail

Every significant action is logged to `workspace/logs/audit.jsonl`:

- Tool policy denials
- Tool policy redirections
- Routing decisions (multi-workspace)
- Significant events

Review with:

```bash
hermitcrab audit
hermitcrab audit --event tool.policy_denied
```

Auto-rotation at 256KB with archive directory (max 5 archives).

## Config file protection

Config at `~/.hermitcrab/config.json` is created with restricted filesystem permissions (chmod 600/700). It contains API keys and private keys — protect it accordingly.

## Secrets handling

- API keys and private keys live only in `config.json`
- No secrets are written to memory, sessions, or knowledge
- Audit logs do not include credential values
- Environment variable prefixes (`HERMITCRAB_`) can inject config at runtime without writing to disk

## Web content sanitization

Web content fetched via `web_fetch` is automatically sanitized to remove:

- Hidden HTML elements with instructions
- Encoded payloads
- Prompt injection patterns

This prevents adversarial web content from influencing agent behavior.

## MCP server isolation

MCP servers connect via `AsyncExitStack`. Each server runs in an isolated context. Server tools are dynamically registered and subject to the same policy enforcement as built-in tools.

## Runtime safety defaults

| Setting | Default | Purpose |
|---------|---------|---------|
| LLM retry backoff | Exponential, 0.6s base | Prevents rapid retry loops |
| Max loop time | 5 minutes | Prevents runaway execution |
| Max tool iterations | 40 | Prevents tool-call loops |
| Identical tool cycle detection | 2 repeats max | Detects stuck loops |
| Memory context injection | Bounded (10K chars, 20 items/cat) | Prevents context flooding |
| Reflection auto-promotion | Disabled (safer) | Prevents uncontrolled self-editing |
