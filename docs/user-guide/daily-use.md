# Daily use

How to work with HermitCrab day-to-day.

## Starting a session

### Interactive mode (local terminal)

```bash
hermitcrab agent
```

Opens a chat prompt. Type messages and press Enter. The agent remembers past sessions.

### One-shot mode

```bash
hermitcrab agent -m "Summarize the latest news about AI"
```

Sends a single message and prints the response. Useful for scripts and automation.

### Resume last session

```bash
hermitcrab agent --continue
```

Or the short form:

```bash
hermitcrab agent -c
```

## How to talk to HermitCrab

### Natural language works

```
What meetings do I have today?
```
```
Add "buy groceries" to my shopping list.
```
```
Remind me to call Sarah tomorrow at 3pm.
```

The agent interprets intent and uses the right tools automatically.

### Be specific when it matters

```
Search for Python best practices for async error handling and save the top 3 articles to my knowledge library.
```

More specific requests produce better tool usage.

### Reference past context

```
What did we decide about the project structure last week?
```

HermitCrab searches memory and session history to find prior conversations and decisions.

## What the agent can do

### Remember things

Say "remember that..." or just state a fact:

```
My doctor's name is Dr. Silva and her office is in Cascais.
```

The agent stores this as a structured fact in `workspace/memory/facts/`.

### Track tasks and goals

```
I need to finish the tax return by April 30.
```

Creates a task with a deadline. Tasks track status: open, in_progress, done, deferred.

```
My goal is to run 3 times per week.
```

Creates a long-term goal tracked over time.

### Manage lists

```
Create a grocery list with milk, eggs, bread, and cheese.
```

Lists support adding items, checking them off, and removing them.

### Search the web

```
What's the weather in Lisbon this weekend?
```

Uses DuckDuckGo search and can fetch URL content.

### Run commands

```
How much space is my Docker using?
```

Executes shell commands through safety guards. Dangerous operations are blocked.

### Work with files

```
Create a README.md for my new project.
```

Reads, writes, and edits files within configured access paths.

### Delegate complex work

```
Build a simple Flask app with a health check endpoint and save it.
```

The agent can spawn subagents for longer or specialized work while staying responsive.

## Ending a session

### Clean exit

Type any of these:

```
exit
quit
/exit
/quit
:q
```

The agent finalizes the session, archives scratchpads, and runs background cognition (journal, optional distillation, reflection).

### Interrupt

Press `Ctrl+C` at any time. The agent performs clean shutdown.

### Walk away

After 30 minutes of inactivity, the session times out automatically. Background cognition runs on the next gateway cycle or agent start.

## Multi-line input

Press `Ctrl+J` to insert a newline without submitting. Useful for pasting code or writing longer prompts.

## Canceling work in progress

If the agent is running a long task, just type a new message and press Enter. The current work is cancelled and the agent switches to your new request.

## Understanding response formatting

Responses may include:

- **Bold text** for emphasis
- Code blocks for commands and code
- Tables for structured data
- Bullet lists for multi-part answers

Rendered Markdown in the CLI. Raw text is available with `--no-markdown`.
