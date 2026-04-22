# Installation

Install HermitCrab on Linux, macOS, or in Docker.

## System requirements

- Python 3.11 or later
- Internet access for cloud models (or Ollama for local models)
- 1 GB RAM minimum (2 GB recommended)

## Option A: One-line installer (recommended)

The installer creates an isolated virtual environment under `~/.local/share/hermitcrab` and installs HermitCrab without touching your system Python.

```bash
curl -fsSL https://raw.githubusercontent.com/talvasconcelos/hermitcrab/main/scripts/install.sh | bash
```

After installation, reload your shell:

```bash
source ~/.bashrc   # or source ~/.zshrc
```

### Optional: Install a systemd user service

On Linux, you can install and enable a long-running gateway service:

```bash
curl -fsSL https://raw.githubusercontent.com/talvasconcelos/hermitcrab/main/scripts/install.sh | bash -s -- --systemd-user --enable-service --start-service
```

This runs `hermitcrab gateway` via `systemd --user`, which is the correct mode for channels, reminders, and heartbeat-driven work.

## Option B: Manual install

### 1. Install the package

```bash
pip install hermitcrab-ai
```

### 2. Initialize workspace and config

```bash
hermitcrab onboard
```

This creates `~/.hermitcrab/config.json` and bootstraps the workspace directory structure.

### 3. Verify installation

```bash
hermitcrab doctor
```

You should see an "all clear" or a short list of next steps (usually adding a provider API key).

## Option C: Docker

### Build

```bash
docker compose build
```

### Run the gateway

```bash
docker compose up -d hermitcrab-gateway
```

### Run a one-off command

```bash
docker compose run --rm hermitcrab-cli hermitcrab status
```

Both services mount `~/.hermitcrab:/root/.hermitcrab` so your workspace persists across container restarts.

## Option D: From source

```bash
git clone https://github.com/talvasconcelos/hermitcrab.git
cd hermitcrab
uv sync --dev
uv run hermitcrab onboard
uv run hermitcrab agent
```

## After installation

Run the diagnostic check:

```bash
hermitcrab doctor
```

Then pick a model provider and start chatting. See [Quickstart](quickstart.md) for your first conversation.

## Verifying your install

| Check | Command | Expected |
|-------|---------|----------|
| CLI works | `hermitcrab --help` | Lists all commands |
| Config exists | `hermitcrab status` | Shows config path and state |
| Doctor clean | `hermitcrab doctor` | No errors (warnings OK) |
| Agent starts | `hermitcrab agent` | Interactive prompt appears |

## Uninstalling

Remove the virtual environment and config:

```bash
rm -rf ~/.local/share/hermitcrab
rm -rf ~/.hermitcrab
```

If you installed a systemd service:

```bash
systemctl --user disable --now hermitcrab-gateway
rm ~/.config/systemd/user/hermitcrab-gateway.service
systemctl --user daemon-reload
```
