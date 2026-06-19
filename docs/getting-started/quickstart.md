# Quickstart

Get HermitCrab running and have your first conversation in under 2 minutes.

## 1. Install and set up

If you haven't installed HermitCrab yet, see [Installation](installation.md).

```bash
hermitcrab setup
```

This creates `~/.hermitcrab/config.json`, bootstraps the system state, and prepares the owner identity. The identity-based layout is the normal HermitCrab layout: solo owner use works out of the box, and additional identities can be added later by the admin.

For non-interactive installs, pass safe defaults:

```bash
hermitcrab setup --yes --provider ollama --model ollama/llama3.2:3b
```

`hermitcrab onboard` remains available as the lower-level/scriptable bootstrap command.

## 2. Configure or change models

HermitCrab uses named models so admins do not need to hand-edit JSON for common setup.

### Local model (free, private)

Install [Ollama](https://ollama.com), then pull a model:

```bash
ollama pull llama3.2:3b
hermitcrab model add main ollama/llama3.2:3b --provider ollama
hermitcrab model set-default main
```

### Cloud model (OpenRouter)

Set `HERMITCRAB_OPENROUTER_API_KEY` in your shell or secret manager, then run:

```bash
hermitcrab model add main anthropic/claude-sonnet-4 --provider openrouter --api-key-env HERMITCRAB_OPENROUTER_API_KEY
hermitcrab model set-default main
```

### Inspect and verify model setup

```bash
hermitcrab model list
hermitcrab model test main
hermitcrab doctor
```

You should see your selected provider/model marked as configured and ready.

## 3. Start chatting as the owner/admin

```bash
hermitcrab agent
```

You'll see a welcome banner with your model, available tools, and skills. Type a message:

```
What can you help me with?
```

The CLI is the admin surface. Normal users should interact through configured channels, preferably Nostr DMs from any client.

## 4. Add another identity when needed

Solo use does not require any extra identities. If you do want another user, the admin creates and routes it:

```bash
hermitcrab user add alice --label Alice
hermitcrab user models alice --interactive main
hermitcrab user route nostr alice <alice-npub-or-hex-pubkey>
hermitcrab user status alice
```

## 5. Try key interactions

### Ask it to use the terminal

```
What's my disk usage? Show the top 5 largest directories in my home folder.
```

The agent runs shell commands through a safety layer and shows you the results.

### Ask it to remember something

```
Remember that my daughter's soccer practice is every Tuesday at 4pm.
```

HermitCrab writes this to `identities/<name>/memory/facts/` as a structured Markdown note.

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

## 6. One-shot mode

Send a single message without entering interactive mode:

```bash
hermitcrab agent -m "What's the weather in Lisbon today?"
```

Useful for scripting, cron jobs, or piping output.

## 7. Run the gateway (channels + reminders)

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
