# Reminders and cron

Schedule one-shot reminders and recurring automated tasks.

## Reminders

Reminders are one-shot notifications delivered at a specific time.

### Create a reminder

Tell the agent in natural language:

```
Remind me to call the dentist tomorrow at 10am.
```
```
Remind me to pick up the dry cleaning this Friday at 6pm.
```

The agent creates a reminder artifact in `workspace/reminders/`.

### How reminders work

- Reminder files are stored in `workspace/reminders/`
- The gateway service polls for due reminders every 60 seconds by default
- When a reminder is due, the agent delivers it on the channel you used to create it
- Delivered reminders are moved to an archived state

### Reminder delivery

Reminders arrive on the channel they were created on:

- CLI — printed in your active session
- Nostr — sent as a DM
- Telegram — sent as a bot message
- Email — sent as a reply

### Create a reminder manually

Use the reminder tool directly:

```
I want to set a reminder for March 15 at 9am: "Submit the quarterly report"
```

## Cron jobs

Cron jobs are recurring tasks that run on a schedule.

### Create a cron job

Tell the agent in natural language:

```
Every morning at 9am, check Hacker News and send me a summary.
```
```
Run my backup script every Sunday at midnight.
```

### Schedule types

| Type | Description | Example |
|------|-------------|---------|
| `at` | One-shot at a specific time | "Run at 2026-04-20T09:00:00" |
| `every` | Fixed interval | "Every 30 minutes" |
| `cron` | Cron expression | "0 9 * * 1-5" (weekdays at 9am) |

### Manage cron jobs

```
List my scheduled cron jobs.
```
```
Disable the morning summary job.
```
```
Delete the backup job.
```

### How cron jobs execute

1. The cron service runs inside the gateway
2. At the scheduled time, the job fires
3. The job text is sent to the agent loop for processing
4. The agent runs tools and produces a response
5. The response is optionally delivered to channels

Cron jobs run with full access to tools, memory, and subagents.

### Cron job examples

```
Every weekday at 8am, tell me my schedule for the day based on my calendar notes.
```
```
Every 6 hours, check if any tasks are overdue and notify me.
```
```
0 0 * * 0 — Every Sunday at midnight, summarize my week's journal entries.
```

## Heartbeat

The heartbeat service periodically wakes the agent to review active work.

### How it works

- Runs every 30 minutes by default
- Reads `workspace/HEARTBEAT.md` for active tasks and context
- A lightweight model decides whether to skip or run a check
- If "run", the agent processes active tasks through the normal loop

### Disable heartbeat

Add this marker to `workspace/HEARTBEAT.md`:

```markdown
<!-- HEARTBEAT_DISABLED -->
```

### Bypass LLM check

For direct execution without the LLM skip/run decision:

```markdown
<!-- HEARTBEAT_DIRECT -->
```

## Configuration

### Reminder polling interval

```json
{
  "gateway": {
    "reminders": {
      "intervalS": 60
    }
  }
}
```

### Heartbeat interval

```json
{
  "gateway": {
    "heartbeat": {
      "enabled": true,
      "intervalS": 1800
    }
  }
}
```

## Troubleshooting

### Reminders not firing

- Ensure the gateway is running: `hermitcrab gateway`
- Check reminder files exist in `workspace/reminders/`
- Run `hermitcrab status` to verify reminder service status
- Check logs for delivery errors

### Cron jobs not executing

- List jobs with: "Show my cron jobs"
- Verify the gateway is running
- Check that the job is enabled
- Review the audit log for job execution: `hermitcrab audit --event cron.executed`
