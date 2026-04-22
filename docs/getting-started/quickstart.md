# Quickstart

Get HermitCrab running and have your first conversation in under 2 minutes.

## 1. Install and onboard

If you haven't installed HermitCrab yet, see [Installation](installation.md).

```bash
hermitcrab onboard
```

This creates your config at `~/.hermitcrab/config.json` and bootstraps the workspace directory.

## 2. Configure a provider

HermitCrab needs an LLM provider to work. Choose one:

### Local model (free, private)

Install [Ollama](https://ollama.com), then pull a model:

```bash
ollama pull gemma4:e4b
```

Edit `~/.hermitcrab/config.json`:

```json
{
  "providers": {
    "ollama": {
      "apiKey": "",
      "apiBase": "http://localhost:11434"
    }
  },
  "agents": {
    "defaults": {
      "model": "ollama/gemma4:e4b"
    }
  }
}
```

### Cloud model (OpenRouter)

Get an API key from [openrouter.ai](https://openrouter.ai/keys), then edit `~/.hermitcrab/config.json`:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-..."
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4"
    }
  }
}
```

### Cloud model (Anthropic direct)

```json
{
  "providers": {
    "anthropic": {
      "apiKey": "sk-ant-..."
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5"
    }
  }
}
```

### Verify provider setup

```bash
hermitcrab status
```

You should see your selected provider marked as configured and ready.

## 3. Start chatting

```bash
hermitcrab agent
```

You'll see a welcome banner with your model, available tools, and skills. Type a message:

```
What can you help me with?
```

## 4. Try key interactions

### Ask it to use the terminal

```
What's my disk usage? Show the top 5 largest directories in my home folder.
```

The agent runs shell commands through a safety layer and shows you the results.

### Ask it to remember something

```
Remember that my daughter's soccer practice is every Tuesday at 4pm.
```

HermitCrab writes this to `workspace/memory/facts/` as a structured Markdown note.

### Set a reminder

```
Remind me to call the dentist tomorrow at 10am.
```

The agent creates a reminder artifact that the gateway service will deliver at the scheduled time.

### Create a task

```
I need to file my taxes by April 30. Add that as a task.
```

Tasks track status (open, in_progress, done, deferred) and deadlines.

### Interrupt the agent

If the agent is taking too long, just type a new message and press Enter. The current task is cancelled and the agent switches to your new instructions. `Ctrl+C` also works.

## 5. One-shot mode

Send a single message without entering interactive mode:

```bash
hermitcrab agent -m "What's the weather in Lisbon today?"
```

Useful for scripting, cron jobs, or piping output.

## 6. Run the gateway (channels + reminders)

To enable channels, reminders, and heartbeat:

```bash
hermitcrab gateway
```

The gateway runs in the foreground. It starts:

- Configured channels (Nostr, Telegram, email)
- Cron service for scheduled jobs
- Heartbeat service for periodic agent wake-ups
- Reminder service for delivering scheduled reminders

Run it in the background with `systemd --user` or `docker compose up -d hermitcrab-gateway`.

## Next steps

- [Daily use](../user-guide/daily-use.md) — get comfortable with day-to-day interactions
- [Channels](../user-guide/channels.md) — connect Nostr, Telegram, or email
- [Sessions and memory](../user-guide/sessions-and-memory.md) — understand how memory works
- [Reminders and cron](../user-guide/reminders-and-cron.md) — automate recurring tasks
