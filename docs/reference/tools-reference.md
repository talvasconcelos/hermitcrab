# Tools reference

Every built-in tool and what it does.

Tools are always gated by Python. The LLM proposes tool calls; Python validates and executes them.

## File tools

### read_file

Read the contents of a file.

Permission level: `read_only`

### write_file

Create or overwrite a file.

Permission level: `workspace_write`

### edit_file

Make precise replacements in a file. Requires specifying the exact text to replace.

Permission level: `workspace_write`

### list_dir

List the contents of a directory.

Permission level: `read_only`

## Shell

### exec

Run a shell command. Applies safety guards including deny patterns for destructive operations, timeouts, and risk classification.

Permission level: `dangerous_exec`

Timeout: 60 seconds (configurable).

## Web

### web_search

Search the web using DuckDuckGo. No API key required. Optionally supports Brave Search if configured.

Permission level: `network`

### web_fetch

Fetch and extract content from a URL. Content is automatically sanitized to remove injection patterns.

Permission level: `network`

## Memory

### read_memory

Read memory items by category or ID.

Permission level: `read_only`

### search_memory

Search memory items by keyword.

Permission level: `read_only`

### write_fact

Write a fact to `workspace/memory/facts/`.

Permission level: `workspace_write`

### write_decision

Write a decision to `workspace/memory/decisions/`.

Permission level: `workspace_write`

### write_goal

Write a goal to `workspace/memory/goals/`.

Permission level: `workspace_write`

### write_task

Write a task to `workspace/memory/tasks/`.

Permission level: `workspace_write`

### write_reflection

Write a reflection to `workspace/memory/reflections/`.

Permission level: `workspace_write`

## Knowledge

### knowledge_search

Search the knowledge library for reference material.

Permission level: `read_only`

### knowledge_ingest

Save articles or documents to the knowledge library.

Permission level: `workspace_write`

### knowledge_ingest_url

Fetch and save URL content to the knowledge library.

Permission level: `network`

## Lists

### list_show

Show a list's contents with item statuses.

Permission level: `workspace_write`

### add_list_items

Add items to a list.

Permission level: `workspace_write`

### set_list_item_status

Check off or change an item's status (open, done, deferred).

Permission level: `workspace_write`

### remove_list_items

Remove items from a list.

Permission level: `workspace_write`

### delete_list

Delete an entire list.

Permission level: `workspace_write`

## Communication

### message

Reply on the active channel. Used by the agent to send responses back to the user.

Permission level: `coordinator`

Not available to subagents.

## Delegation

### spawn

Launch a subagent for bounded delegated work.

Permission level: `coordinator`

Not available to subagents.

## Scheduling

### cron

Manage recurring cron jobs: list, create, enable, disable, delete.

Permission level: `coordinator`

Not available to subagents.

### reminder

Manage reminder artifacts: create, list, deliver.

Permission level: `workspace_write`

Not available to subagents.

## People

### person_profile

Manage people profiles: create, view, update attributes and interactions.

Permission level: `workspace_write`

Not available to subagents.

## Sessions

### session_search

Search conversation history across current and archived sessions.

Permission level: `read_only`

Not available to subagents.

## MCP

### mcp_*

Dynamically registered tools from configured MCP servers. Each MCP server adds its own set of tools.

Permission level: `network`

Not available to subagents.

## Tool permission summary

| Tool | Permission | Subagent |
|------|-----------|----------|
| read_file | read_only | Yes |
| write_file | workspace_write | Yes |
| edit_file | workspace_write | Yes |
| list_dir | read_only | Yes |
| exec | dangerous_exec | Yes |
| web_search | network | Yes |
| web_fetch | network | Yes |
| message | coordinator | No |
| spawn | coordinator | No |
| cron | coordinator | No |
| reminder | workspace_write | No |
| person_profile | workspace_write | No |
| read_memory | read_only | Yes |
| search_memory | read_only | Yes |
| write_fact | workspace_write | No |
| write_decision | workspace_write | No |
| write_goal | workspace_write | No |
| write_task | workspace_write | No |
| write_reflection | workspace_write | No |
| knowledge_search | read_only | No |
| knowledge_ingest | workspace_write | No |
| knowledge_ingest_url | network | No |
| session_search | read_only | No |
| list_show | workspace_write | No |
| add_list_items | workspace_write | No |
| set_list_item_status | workspace_write | No |
| remove_list_items | workspace_write | No |
| delete_list | workspace_write | No |
| mcp_* | network | No |

## Policy enforcement

The `ToolPermissionPolicy` checks each tool call against the actor's allowed tools and permission levels. On denial:

1. The tool call is rejected
2. An audit event is logged
3. The agent receives a structured hint with alternative tools and safe fallbacks

Subagents use a filtered policy that excludes `coordinator` tools. Profiles (implementation, verification, research, explore) may further restrict permissions.
