# Agent loop

The core message-processing loop in HermitCrab.

## Overview

`AgentLoop` (in `agent/loop.py`) is the main processing engine. It receives messages, assembles context, calls the LLM, executes tool calls, and produces responses.

## Job classes

The loop routes work to the right model based on job class:

| Job class | Purpose | Default model |
|-----------|---------|---------------|
| `INTERACTIVE_RESPONSE` | User-facing replies | Primary model (required) |
| `JOURNAL_SYNTHESIS` | Narrative session summary | Cheap local (falls back to primary) |
| `DISTILLATION` | Extract facts/tasks/goals | Local only (null = skip) |
| `REFLECTION` | Self-analysis and patterns | Medium model (falls back to primary) |
| `SUMMARISATION` | Session/context summarization | Falls back to primary |
| `SUBAGENT` | Background delegated work | Primary (or dedicated subagent model) |

## Turn flow

### 1. Context assembly

`ContextBuilder` assembles the prompt from:

- Bootstrap files (`AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `TOOLS.md`)
- Session history (last N messages, configurable via `memoryWindow`)
- Memory context (facts, decisions, goals, tasks — bounded by char and item limits)
- Channel-specific prompt overlays (if present)
- People profiles (if relevant to the conversation)

### 2. LLM call

The provider is resolved based on the job class and primary model. The LLM is called with streaming support. Retries use exponential backoff (configurable: `llmMaxRetries`, `llmRetryBaseDelayS`).

### 3. Tool execution

If the LLM returns tool calls:

1. Each tool call is validated against `ToolPermissionPolicy`
2. Approved tools execute within configured limits
3. Results are fed back to the LLM for the next iteration
4. Maximum `maxToolIterations` (default 40) prevents infinite loops
5. Identical tool cycle detection (default 2 repeats) catches stuck loops

### 4. Response delivery

The final text response is published to the message bus. The `ChannelManager` dispatches it to the user's channel.

## Session lifecycle

### Session start

1. A message arrives on the bus
2. The gateway routes it to the correct workspace
3. `SessionManager` loads or creates the session
4. `ContextBuilder` assembles prompt from session history

### Session end

Sessions end when the user exits or after `inactivityTimeoutS` (default 30 minutes) of silence. On session end:

1. Session is archived to `workspace/sessions/archive/`
2. Scratchpad is archived to `workspace/scratchpads/archive/`
3. Background cognition is scheduled:
   - Journal synthesis always runs
   - Distillation runs if `enableDistillation` is true
   - Reflection always runs

### Background cognition

Scheduled jobs run asynchronously:

- **Journal** — narrative summary of what happened in the session
- **Distillation** (optional) — proposes facts, tasks, goals, decisions
- **Reflection** — self-analysis: mistakes, patterns, contradictions

Each job uses its configured model (from `jobModels`). Distillation proposals are not automatically authoritative — direct memory writes always win.

## Loop safety

### Timeouts

- Max loop execution: `maxLoopSeconds` (default 300 = 5 minutes)
- Shell command timeout: `tools.exec.timeout` (default 60 seconds)
- LLM retry base delay: `llmRetryBaseDelayS` (default 0.6 seconds)

### Loop detection

- Max tool iterations: `maxToolIterations` (default 40)
- Identical tool cycle detection: `maxIdenticalToolCycles` (default 2 repeats)

### Cancellation

The user can cancel active work by typing a new message or pressing `Ctrl+C`. `cancel_active_work()` is called to interrupt the current turn.

## Provider resolution

The loop resolves the model for each job:

1. Check `jobModels.<job_class>` for a job-specific model
2. If empty/null, apply job-specific fallback rules
3. Resolve the model string through the provider registry
4. Match provider config by prefix or keyword
5. Call the provider via LiteLLM or dedicated implementation

## Key invariants

- The LLM is always untrusted — Python validates all tool calls
- Memory writes are always deterministic and file-based
- Session history is always bounded (configurable window)
- Background cognition never blocks the main loop
- Tool execution is always policy-gated
