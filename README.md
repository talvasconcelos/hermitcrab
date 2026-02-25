<!-- <div align="center">
  <img src="hermitcrab_logo.png" alt="hermitcrab" width="500">
  <h1>hermitcrab: Ultra-Lightweight Personal AI Assistant</h1>
  <p>
    <a href="https://pypi.org/project/hermitcrab-ai/"><img src="https://img.shields.io/pypi/v/hermitcrab-ai" alt="PyPI"></a>
    <a href="https://pepy.tech/project/hermitcrab-ai"><img src="https://static.pepy.tech/badge/hermitcrab-ai" alt="Downloads"></a>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</div> -->

Forked from [nanobot](https://github.com/HKUDS/nanobot)
Original work © [https://github.com/HKUDS]. MIT licensed.
HermitCrab adds atomic Obsidian-style memory, Nostr-first comms, distillation, hermit-crab portability.

🐈 **hermitcrab** is an **ultra-lightweight** personal AI assistant inspired by [OpenClaw](https://github.com/openclaw/openclaw) 

⚡️ Delivers core agent functionality in just **~4,000** lines of code — **99% smaller** than Clawdbot's 430k+ lines.

📏 Real-time line count: **3,955 lines** (run `bash core_agent_lines.sh` to verify anytime)

## 📢 News

- **2026-02-21** 🎉 Released **v0.1.4.post1** — new providers, media support across channels, and major stability improvements. See [release notes](https://github.com/HKUDS/hermitcrab/releases/tag/v0.1.4.post1) for details.
- **2026-02-18** ⚡️ hermitcrab now supports VolcEngine, MCP custom auth headers, and Anthropic prompt caching.
- **2026-02-17** 🎉 Released **v0.1.4** — MCP support, progress streaming, new providers, and multiple channel improvements. Please see [release notes](https://github.com/HKUDS/hermitcrab/releases/tag/v0.1.4) for details.
- **2026-02-16** 🦞 hermitcrab now integrates a [ClawHub](https://clawhub.ai) skill — search and install public agent skills.
- **2026-02-15** 🔑 hermitcrab now supports OpenAI Codex provider with OAuth login support.
- **2026-02-14** 🔌 hermitcrab now supports MCP! See [MCP section](#mcp-model-context-protocol) for details.
- **2026-02-13** 🎉 Released **v0.1.3.post7** — includes security hardening and multiple improvements. **Please upgrade to the latest version to address security issues**. See [release notes](https://github.com/HKUDS/hermitcrab/releases/tag/v0.1.3.post7) for more details.
- **2026-02-12** 🧠 Redesigned memory system — Less code, more reliable. Join the [discussion](https://github.com/HKUDS/hermitcrab/discussions/566) about it!
- **2026-02-11** ✨ Enhanced CLI experience and added MiniMax support!

<details>
<summary>Earlier news</summary>

- **2026-02-10** 🎉 Released **v0.1.3.post6** with improvements! Check the updates [notes](https://github.com/HKUDS/hermitcrab/releases/tag/v0.1.3.post6) and our [roadmap](https://github.com/HKUDS/hermitcrab/discussions/431).
- **2026-02-08** 🔧 Refactored Providers—adding a new LLM provider now takes just 2 simple steps! Check [here](#providers).
- **2026-02-07** 🚀 Released **v0.1.3.post5** with Qwen support & several key improvements! Check [here](https://github.com/HKUDS/hermitcrab/releases/tag/v0.1.3.post5) for details.
- **2026-02-04** 🚀 Released **v0.1.3.post4** with multi-provider & Docker support! Check [here](https://github.com/HKUDS/hermitcrab/releases/tag/v0.1.3.post4) for details.
- **2026-02-03** ⚡ Integrated vLLM for local LLM support and improved natural language task scheduling!
- **2026-02-02** 🎉 hermitcrab officially launched! Welcome to try 🐈 hermitcrab!

</details>

## Key Features of hermitcrab:

🪶 **Ultra-Lightweight**: Just ~4,000 lines of core agent code — 99% smaller than Clawdbot.

🔬 **Research-Ready**: Clean, readable code that's easy to understand, modify, and extend for research.

⚡️ **Lightning Fast**: Minimal footprint means faster startup, lower resource usage, and quicker iterations.

💎 **Easy-to-Use**: One-click to deploy and you're ready to go.

## 🏗️ Architecture

<p align="center">
  <img src="hermitcrab_arch.png" alt="hermitcrab architecture" width="800">
</p>

## ✨ Features

<table align="center">
  <tr align="center">
    <th><p align="center">📈 24/7 Real-Time Market Analysis</p></th>
    <th><p align="center">🚀 Full-Stack Software Engineer</p></th>
    <th><p align="center">📅 Smart Daily Routine Manager</p></th>
    <th><p align="center">📚 Personal Knowledge Assistant</p></th>
  </tr>
  <tr>
    <td align="center"><p align="center"><img src="case/search.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/code.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/scedule.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/memory.gif" width="180" height="400"></p></td>
  </tr>
  <tr>
    <td align="center">Discovery • Insights • Trends</td>
    <td align="center">Develop • Deploy • Scale</td>
    <td align="center">Schedule • Automate • Organize</td>
    <td align="center">Learn • Memory • Reasoning</td>
  </tr>
</table>

## 📦 Install

**Install from source** (latest features, recommended for development)

```bash
git clone https://github.com/HKUDS/hermitcrab.git
cd hermitcrab
pip install -e .
```

**Install with [uv](https://github.com/astral-sh/uv)** (stable, fast)

```bash
uv tool install hermitcrab-ai
```

**Install from PyPI** (stable)

```bash
pip install hermitcrab-ai
```

## 🚀 Quick Start

> [!TIP]
> Set your API key in `~/.hermitcrab/config.json`.
> Get API keys: [OpenRouter](https://openrouter.ai/keys) (Global) · [Brave Search](https://brave.com/search/api/) (optional, for web search)

**1. Initialize**

```bash
hermitcrab onboard
```

**2. Configure** (`~/.hermitcrab/config.json`)

Add or merge these **two parts** into your config (other options have defaults).

*Set your API key* (e.g. OpenRouter, recommended for global users):
```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  }
}
```

*Set your model*:
```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5"
    }
  }
}
```

**3. Chat**

```bash
hermitcrab agent
```

That's it! You have a working AI assistant in 2 minutes.

## 💬 Chat Apps

Connect hermitcrab to your favorite chat platform.

**Primary Channel: Nostr** (decentralized, encrypted DMs)  
**Fallback Channels: Telegram, Email**

| Channel | What you need |
|---------|---------------|
| **Nostr** | Private key (nsec), optional: allowed pubkeys |
| **Telegram** | Bot token from @BotFather |
| **Email** | IMAP/SMTP credentials |

<details>
<summary><b>Nostr</b> (Primary - Encrypted DMs via NIP-04)</summary>

**Nostr** is a decentralized protocol for censorship-resistant communication. HermitCrab uses NIP-04 for encrypted direct messages.

**1. Generate a Nostr keypair**

```bash
# Install pynostr if not already installed
pip install pynostr

# Generate new keypair
python -c "from pynostr.key import PrivateKey; k = PrivateKey(); print(f'nsec: {k.bech32()}'); print(f'npub: {k.public_key.bech32()}')"
```

Save both keys:
- **nsec** (private key) — Keep secret! Add to config
- **npub** (public key) — Share with users who want to DM your bot

**2. Configure**

```json
{
  "channels": {
    "nostr": {
      "enabled": true,
      "private_key": "nsec1...",  // Your private key (nsec or hex)
      "relays": [
        "wss://relay.damus.io",
        "wss://relay.primal.net",
        "wss://nostr-pub.wellorder.net"
      ],
      "protocol": "nip04",
      "allowed_pubkeys": ["npub1...", "npub2..."]  // Optional: restrict to specific users
    }
  }
}
```

> **Security:** Set `allowed_pubkeys` to restrict who can message your bot. If empty, anyone can send DMs (not recommended for production).

**3. Run**

```bash
# Gateway mode (listens for DMs continuously)
hermitcrab gateway

# Or CLI listen mode (listen for DMs from specific pubkey)
hermitcrab agent --nostr-pubkey npub1...
```

**4. Test with a Nostr client**

- Install a Nostr client (Damus, Primal, Amethyst, etc.)
- Send an encrypted DM to your bot's npub
- Bot will respond via encrypted DM

</details>

<details>
<summary><b>Telegram</b> (Recommended)</summary>



**1. Create a bot**

- Open Telegram, search `@BotFather`

- Send `/newbot`, follow prompts

- Copy the token



**2. Configure**



```json

{

  "channels": {

    "telegram": {

      "enabled": true,

      "token": "YOUR_BOT_TOKEN",

      "allowFrom": ["YOUR_USER_ID"]

    }

  }

}

```



> You can find your **User ID** in Telegram settings. It is shown as `@yourUserId`.

> Copy this value **without the `@` symbol** and paste it into the config file.



**3. Run**



```bash

hermitcrab gateway

```



</details>



<details>

<summary><b>Email</b></summary>



Give hermitcrab its own email account. It polls **IMAP** for incoming mail and replies via **SMTP**.



**1. Get credentials (Gmail example)**

- Create a dedicated Gmail account for your bot

- Enable 2-Step Verification → Create an [App Password](https://myaccount.google.com/apppasswords)



**2. Configure**



```json

{

  "channels": {

    "email": {

      "enabled": true,

      "consentGranted": true,

      "imapHost": "imap.gmail.com",

      "imapPort": 993,

      "imapUsername": "your-email@gmail.com",

      "imapPassword": "your-app-password",

      "smtpHost": "smtp.gmail.com",

      "smtpPort": 587,

      "smtpUsername": "your-email@gmail.com",

      "smtpPassword": "your-app-password",

      "fromAddress": "your-email@gmail.com"

    }

  }

}

```



**3. Run**



```bash

hermitcrab gateway

```



</details>

**3. Run**

```bash
hermitcrab gateway
```

</details>

## 🌐 Agent Social Network

🐈 hermitcrab is capable of linking to the agent social network (agent community). **Just send one message and your hermitcrab joins automatically!**

| Platform | How to Join (send this message to your bot) |
|----------|-------------|
| [**Moltbook**](https://www.moltbook.com/) | `Read https://moltbook.com/skill.md and follow the instructions to join Moltbook` |
| [**ClawdChat**](https://clawdchat.ai/) | `Read https://clawdchat.ai/skill.md and follow the instructions to join ClawdChat` |

Simply send the command above to your hermitcrab (via CLI or any chat channel), and it will handle the rest.

## ⚙️ Configuration

Config file: `~/.hermitcrab/config.json`

### Providers

> [!TIP]
> - **Groq** provides free voice transcription via Whisper. If configured, Telegram voice messages will be automatically transcribed.
> - **Zhipu Coding Plan**: If you're on Zhipu's coding plan, set `"apiBase": "https://open.bigmodel.cn/api/coding/paas/v4"` in your zhipu provider config.
> - **MiniMax (Mainland China)**: If your API key is from MiniMax's mainland China platform (minimaxi.com), set `"apiBase": "https://api.minimaxi.com/v1"` in your minimax provider config.
> - **VolcEngine Coding Plan**: If you're on VolcEngine's coding plan, set `"apiBase": "https://ark.cn-beijing.volces.com/api/coding/v3"` in your volcengine provider config.

| Provider | Purpose | Get API Key |
|----------|---------|-------------|
| `custom` | Any OpenAI-compatible endpoint (direct, no LiteLLM) | — |
| `openrouter` | LLM (recommended, access to all models) | [openrouter.ai](https://openrouter.ai) |
| `anthropic` | LLM (Claude direct) | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | LLM (GPT direct) | [platform.openai.com](https://platform.openai.com) |
| `deepseek` | LLM (DeepSeek direct) | [platform.deepseek.com](https://platform.deepseek.com) |
| `groq` | LLM + **Voice transcription** (Whisper) | [console.groq.com](https://console.groq.com) |
| `gemini` | LLM (Gemini direct) | [aistudio.google.com](https://aistudio.google.com) |
| `minimax` | LLM (MiniMax direct) | [platform.minimaxi.com](https://platform.minimaxi.com) |
| `aihubmix` | LLM (API gateway, access to all models) | [aihubmix.com](https://aihubmix.com) |
| `siliconflow` | LLM (SiliconFlow/硅基流动) | [siliconflow.cn](https://siliconflow.cn) |
| `volcengine` | LLM (VolcEngine/火山引擎) | [volcengine.com](https://www.volcengine.com) |
| `dashscope` | LLM (Qwen) | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| `moonshot` | LLM (Moonshot/Kimi) | [platform.moonshot.cn](https://platform.moonshot.cn) |
| `zhipu` | LLM (Zhipu GLM) | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `vllm` | LLM (local, any OpenAI-compatible server) | — |
| `openai_codex` | LLM (Codex, OAuth) | `hermitcrab provider login openai-codex` |
| `github_copilot` | LLM (GitHub Copilot, OAuth) | `hermitcrab provider login github-copilot` |

<details>
<summary><b>OpenAI Codex (OAuth)</b></summary>

Codex uses OAuth instead of API keys. Requires a ChatGPT Plus or Pro account.

**1. Login:**
```bash
hermitcrab provider login openai-codex
```

**2. Set model** (merge into `~/.hermitcrab/config.json`):
```json
{
  "agents": {
    "defaults": {
      "model": "openai-codex/gpt-5.1-codex"
    }
  }
}
```

**3. Chat:**
```bash
hermitcrab agent -m "Hello!"
```

> Docker users: use `docker run -it` for interactive OAuth login.

</details>

<details>
<summary><b>Custom Provider (Any OpenAI-compatible API)</b></summary>

Connects directly to any OpenAI-compatible endpoint — LM Studio, llama.cpp, Together AI, Fireworks, Azure OpenAI, or any self-hosted server. Bypasses LiteLLM; model name is passed as-is.

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "your-model-name"
    }
  }
}
```

> For local servers that don't require a key, set `apiKey` to any non-empty string (e.g. `"no-key"`).

</details>

<details>
<summary><b>vLLM (local / OpenAI-compatible)</b></summary>

Run your own model with vLLM or any OpenAI-compatible server, then add to config:

**1. Start the server** (example):
```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

**2. Add to config** (partial — merge into `~/.hermitcrab/config.json`):

*Provider (key can be any non-empty string for local):*
```json
{
  "providers": {
    "vllm": {
      "apiKey": "dummy",
      "apiBase": "http://localhost:8000/v1"
    }
  }
}
```

*Model:*
```json
{
  "agents": {
    "defaults": {
      "model": "meta-llama/Llama-3.1-8B-Instruct"
    }
  }
}
```

</details>

<details>
<summary><b>Adding a New Provider (Developer Guide)</b></summary>

hermitcrab uses a **Provider Registry** (`hermitcrab/providers/registry.py`) as the single source of truth.
Adding a new provider only takes **2 steps** — no if-elif chains to touch.

**Step 1.** Add a `ProviderSpec` entry to `PROVIDERS` in `hermitcrab/providers/registry.py`:

```python
ProviderSpec(
    name="myprovider",                   # config field name
    keywords=("myprovider", "mymodel"),  # model-name keywords for auto-matching
    env_key="MYPROVIDER_API_KEY",        # env var for LiteLLM
    display_name="My Provider",          # shown in `hermitcrab status`
    litellm_prefix="myprovider",         # auto-prefix: model → myprovider/model
    skip_prefixes=("myprovider/",),      # don't double-prefix
)
```

**Step 2.** Add a field to `ProvidersConfig` in `hermitcrab/config/schema.py`:

```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = ProviderConfig()
```

That's it! Environment variables, model prefixing, config matching, and `hermitcrab status` display will all work automatically.

**Common `ProviderSpec` options:**

| Field | Description | Example |
|-------|-------------|---------|
| `litellm_prefix` | Auto-prefix model names for LiteLLM | `"dashscope"` → `dashscope/qwen-max` |
| `skip_prefixes` | Don't prefix if model already starts with these | `("dashscope/", "openrouter/")` |
| `env_extras` | Additional env vars to set | `(("ZHIPUAI_API_KEY", "{api_key}"),)` |
| `model_overrides` | Per-model parameter overrides | `(("kimi-k2.5", {"temperature": 1.0}),)` |
| `is_gateway` | Can route any model (like OpenRouter) | `True` |
| `detect_by_key_prefix` | Detect gateway by API key prefix | `"sk-or-"` |
| `detect_by_base_keyword` | Detect gateway by API base URL | `"openrouter"` |
| `strip_model_prefix` | Strip existing prefix before re-prefixing | `True` (for AiHubMix) |

</details>


### MCP (Model Context Protocol)

> [!TIP]
> The config format is compatible with Claude Desktop / Cursor. You can copy MCP server configs directly from any MCP server's README.

hermitcrab supports [MCP](https://modelcontextprotocol.io/) — connect external tool servers and use them as native agent tools.

Add MCP servers to your `config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      },
      "my-remote-mcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer xxxxx"
        }
      }
    }
  }
}
```

Two transport modes are supported:

| Mode | Config | Example |
|------|--------|---------|
| **Stdio** | `command` + `args` | Local process via `npx` / `uvx` |
| **HTTP** | `url` + `headers` (optional) | Remote endpoint (`https://mcp.example.com/sse`) |

Use `toolTimeout` to override the default 30s per-call timeout for slow servers:

```json
{
  "tools": {
    "mcpServers": {
      "my-slow-server": {
        "url": "https://example.com/mcp/",
        "toolTimeout": 120
      }
    }
  }
}
```

MCP tools are automatically discovered and registered on startup. The LLM can use them alongside built-in tools — no extra configuration needed.




### Security

> [!TIP]
> For production deployments, set `"restrictToWorkspace": true` in your config to sandbox the agent.

| Option | Default | Description |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `false` | When `true`, restricts **all** agent tools (shell, file read/write/edit, list) to the workspace directory. Prevents path traversal and out-of-scope access. |
| `channels.*.allowFrom` | `[]` (allow all) | Whitelist of user IDs. Empty = allow everyone; non-empty = only listed users can interact. |


## CLI Reference

| Command | Description |
|---------|-------------|
| `hermitcrab onboard` | Initialize config & workspace |
| `hermitcrab agent -m "..."` | Chat with the agent |
| `hermitcrab agent` | Interactive chat mode |
| `hermitcrab agent --no-markdown` | Show plain-text replies |
| `hermitcrab agent --logs` | Show runtime logs during chat |
| `hermitcrab gateway` | Start the gateway |
| `hermitcrab status` | Show status |
| `hermitcrab provider login openai-codex` | OAuth login for providers |
| `hermitcrab channels login` | Link channels (deprecated) |
| `hermitcrab channels status` | Show channel status |

Interactive mode exits: `exit`, `quit`, `/exit`, `/quit`, `:q`, or `Ctrl+D`.

<details>
<summary><b>Scheduled Tasks (Cron)</b></summary>

```bash
# Add a job
hermitcrab cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"
hermitcrab cron add --name "hourly" --message "Check status" --every 3600

# List jobs
hermitcrab cron list

# Remove a job
hermitcrab cron remove <job_id>
```

</details>

<details>
<summary><b>Heartbeat (Periodic Tasks)</b></summary>

The gateway wakes up every 30 minutes and checks `HEARTBEAT.md` in your workspace (`~/.hermitcrab/workspace/HEARTBEAT.md`). If the file has tasks, the agent executes them and delivers results to your most recently active chat channel.

**Setup:** edit `~/.hermitcrab/workspace/HEARTBEAT.md` (created automatically by `hermitcrab onboard`):

```markdown
## Periodic Tasks

- [ ] Check weather forecast and send a summary
- [ ] Scan inbox for urgent emails
```

The agent can also manage this file itself — ask it to "add a periodic task" and it will update `HEARTBEAT.md` for you.

> **Note:** The gateway must be running (`hermitcrab gateway`) and you must have chatted with the bot at least once so it knows which channel to deliver to.

</details>

## 🐳 Docker

> [!TIP]
> The `-v ~/.hermitcrab:/root/.hermitcrab` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.

### Docker Compose

```bash
docker compose run --rm hermitcrab-cli onboard   # first-time setup
vim ~/.hermitcrab/config.json                     # add API keys
docker compose up -d hermitcrab-gateway           # start gateway
```

```bash
docker compose run --rm hermitcrab-cli agent -m "Hello!"   # run CLI
docker compose logs -f hermitcrab-gateway                   # view logs
docker compose down                                      # stop
```

### Docker

```bash
# Build the image
docker build -t hermitcrab .

# Initialize config (first time only)
docker run -v ~/.hermitcrab:/root/.hermitcrab --rm hermitcrab onboard

# Edit config on host to add API keys
vim ~/.hermitcrab/config.json

# Run gateway (connects to enabled channels, e.g. Telegram)
docker run -v ~/.hermitcrab:/root/.hermitcrab -p 18790:18790 hermitcrab gateway

# Or run a single command
docker run -v ~/.hermitcrab:/root/.hermitcrab --rm hermitcrab agent -m "Hello!"
docker run -v ~/.hermitcrab:/root/.hermitcrab --rm hermitcrab status
```

## 🐧 Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

**1. Find the hermitcrab binary path:**

```bash
which hermitcrab   # e.g. /home/user/.local/bin/hermitcrab
```

**2. Create the service file** at `~/.config/systemd/user/hermitcrab-gateway.service` (replace `ExecStart` path if needed):

```ini
[Unit]
Description=HermitCrab Gateway
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/hermitcrab gateway
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=%h

[Install]
WantedBy=default.target
```

**3. Enable and start:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermitcrab-gateway
```

**Common operations:**

```bash
systemctl --user status hermitcrab-gateway        # check status
systemctl --user restart hermitcrab-gateway       # restart after config changes
journalctl --user -u hermitcrab-gateway -f        # follow logs
```

If you edit the `.service` file itself, run `systemctl --user daemon-reload` before restarting.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```

## 📁 Project Structure

```
hermitcrab/
├── agent/          # 🧠 Core agent logic
│   ├── loop.py     #    Agent loop (LLM ↔ tool execution)
│   ├── context.py  #    Prompt builder
│   ├── memory.py   #    Persistent memory
│   ├── skills.py   #    Skills loader
│   ├── subagent.py #    Background task execution
│   └── tools/      #    Built-in tools (incl. spawn)
├── skills/         # 🎯 Bundled skills (github, weather, tmux...)
├── channels/       # 📱 Chat channel integrations
├── bus/            # 🚌 Message routing
├── cron/           # ⏰ Scheduled tasks
├── heartbeat/      # 💓 Proactive wake-up
├── providers/      # 🤖 LLM providers (OpenRouter, etc.)
├── session/        # 💬 Conversation sessions
├── config/         # ⚙️ Configuration
└── cli/            # 🖥️ Commands
```

## 🤝 Contribute & Roadmap

PRs welcome! The codebase is intentionally small and readable. 🤗

**Roadmap** — Pick an item and [open a PR](https://github.com/HKUDS/hermitcrab/pulls)!

- [ ] **Multi-modal** — See and hear (images, voice, video)
- [ ] **Long-term memory** — Never forget important context
- [ ] **Better reasoning** — Multi-step planning and reflection
- [ ] **More integrations** — Calendar and more
- [ ] **Self-improvement** — Learn from feedback and mistakes

### Contributors

<a href="https://github.com/HKUDS/hermitcrab/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=HKUDS/hermitcrab&max=100&columns=12&updated=20260210" alt="Contributors" />
</a>


## ⭐ Star History

<div align="center">
  <a href="https://star-history.com/#HKUDS/hermitcrab&Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=HKUDS/hermitcrab&type=Date&theme=dark" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=HKUDS/hermitcrab&type=Date" />
      <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=HKUDS/hermitcrab&type=Date" style="border-radius: 15px; box-shadow: 0 0 30px rgba(0, 217, 255, 0.3);" />
    </picture>
  </a>
</div>

<p align="center">
  <em> Thanks for visiting ✨ hermitcrab!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.hermitcrab&style=for-the-badge&color=00d4ff" alt="Views">
</p>


<p align="center">
  <sub>hermitcrab is for educational, research, and technical exchange purposes only</sub>
</p>
