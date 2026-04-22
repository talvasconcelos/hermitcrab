# Gateway operations

Run and manage the long-running HermitCrab gateway service.

## What the gateway does

The gateway is the always-on service that powers:

- **Channel connections** — Nostr, Telegram, email listening
- **Cron service** — scheduled job execution
- **Heartbeat service** — periodic agent wake-ups for task review
- **Reminder service** — filesystem-backed reminder delivery
- **Session timeout monitoring** — automatic session cleanup after inactivity
- **Multi-workspace routing** — inbound message routing to isolated workspaces

The gateway is required for any channel-based interaction. It is not needed for local CLI sessions (`hermitcrab agent`).

## Starting the gateway

### Foreground (testing)

```bash
hermitcrab gateway
```

Runs in the foreground with INFO-level logging. Press Ctrl+C to stop.

### Verbose logging

```bash
hermitcrab gateway --verbose
```

Sets log level to DEBUG. Useful for troubleshooting channel connections.

### Custom port

```bash
hermitcrab gateway --port 18791
```

The default port is 18790.

### Custom log level

```bash
hermitcrab gateway --log-level DEBUG
```

Options: TRACE, DEBUG, INFO, WARNING, ERROR.

## Running as a systemd service

The one-line installer can set up a user-level systemd service:

```bash
curl -fsSL https://raw.githubusercontent.com/talvasconcelos/hermitcrab/main/scripts/install.sh | bash -s -- --systemd-user --enable-service --start-service
```

### Manual service setup

Create `~/.config/systemd/user/hermitcrab-gateway.service`:

```ini
[Unit]
Description=HermitCrab Gateway
After=network-online.target

[Service]
Type=simple
ExecStart=/home/%i/.local/share/hermitcrab/bin/hermitcrab gateway
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermitcrab-gateway
```

Check status:

```bash
systemctl --user status hermitcrab-gateway
journalctl --user -u hermitcrab-gateway -f
```

## Running in Docker

```bash
docker compose up -d hermitcrab-gateway
```

Logs:

```bash
docker compose logs -f hermitcrab-gateway
```

Stop:

```bash
docker compose stop hermitcrab-gateway
```

## Startup status

When the gateway starts, it prints:

```
HermitCrab gateway starting
  Channels: nostr, telegram
  Multi-workspace: active (2 workspaces)
  Cron jobs: 3 registered
  Heartbeat: every 1800s
  Reminders: polling every 60s
  Port: 18790
```

## Services in the gateway

### Cron service

- Manages scheduled jobs persisted to JSON
- Schedule types: `at` (one-shot), `every` (interval), `cron` (expression)
- Executes jobs through the agent loop with full tool access
- Job responses optionally delivered to channels

### Heartbeat service

- Reads `workspace/HEARTBEAT.md` for active tasks
- Phase 1: lightweight model decides skip/run via virtual tool call
- Phase 2: if "run", executes active tasks through agent loop
- Can be disabled with `<!-- HEARTBEAT_DISABLED -->`
- Bypass mode with `<!-- HEARTBEAT_DIRECT -->` skips the LLM check

### Reminder service

- Polls `workspace/reminders/` for due reminders
- Default polling interval: 60 seconds
- Delivers via the appropriate channel
- Archives delivered reminders

### Session timeout service

- Monitors all active sessions for inactivity
- Default timeout: 30 minutes
- Triggers session archival and background cognition
- Runs across all workspaces in multi-workspace mode

## Containment boundaries

### Admin-owned surfaces

These are always admin-owned and never delegated to sub-workspaces:

- Cron service and job definitions
- Heartbeat service and `HEARTBEAT.md`
- Config file (`config.json`)
- CLI commands
- Provider configuration

### Workspace-isolated surfaces

Each workspace (admin and sub-workspaces) gets its own:

- Memory store
- Session manager
- Knowledge library
- People profiles
- Lists
- Reminders
- Scratchpads

### Failure isolation

- Individual channel failures do not affect other channels
- Per-workspace agent loop failures do not affect other workspaces
- Cron job failures are logged; subsequent jobs continue
- Heartbeat failures are non-fatal; next cycle retries
- Reminder delivery failures are logged; the reminder is not lost

## Monitoring the gateway

### Check running status

```bash
hermitcrab status
```

Reports gateway-relevant information: provider status, skill availability, audit trail summary.

### Check audit trail

```bash
hermitcrab audit
hermitcrab audit --event tool.policy_denied
hermitcrab audit --limit 50
```

### Check logs

```bash
# systemd
journalctl --user -u hermitcrab-gateway -f

# Docker
docker compose logs -f hermitcrab-gateway

# Foreground
Output is on stdout
```

## Stopping the gateway

```bash
# systemd
systemctl --user stop hermitcrab-gateway

# Docker
docker compose stop hermitcrab-gateway

# Foreground
Ctrl+C
```

On shutdown, the gateway:

1. Stops all services (cron, heartbeat, reminders, timeout monitor)
2. Closes all agent loops (admin and workspace)
3. Stops all channels cleanly
4. Exits

## Upgrading

Stop the gateway, upgrade the package, restart:

```bash
systemctl --user stop hermitcrab-gateway
pip install --upgrade hermitcrab-ai
hermitcrab onboard  # picks up new config fields
systemctl --user start hermitcrab-gateway
```
