# Manual Testing: Learning Quality

This guide is for dogfooding the `feat/learning-quality` branch and checking whether journaling, reflection, and durable learning promotion actually feel useful in practice.

## 1. Enable Reflection Promotion

Make sure your local config enables reflection promotion:

```json
{
  "reflection": {
    "promotion": {
      "auto_promote": true,
      "target_files": ["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"],
      "max_file_lines": 500,
      "notify_user": true
    }
  }
}
```

## 2. Start The Agent

```bash
uv run hermitcrab agent
```

## 3. Journal Specificity Test

Ask something like:

```text
Refactor hermitcrab/agent/reflection.py to improve promotion routing, and tell me what files changed.
```

Then inspect the journal entry:

```bash
ls journal
```

Expected:

- the user goal is clear
- concrete files or artifacts are mentioned
- outcomes are preserved
- open loops are preserved
- no vague recap like "I worked on it" with no specifics

## 4. Reflection As Learning Material Test

Intentionally correct the agent during a meaningful task:

```text
Don't hand this whole thing to a subagent. Keep ownership, plan first, and only delegate bounded execution.
```

After the session ends or reflection runs, inspect:

```bash
ls memory/reflections
```

Expected reflection content:

- `Observation:`
- `Impact:`
- `Lesson:`
- `Recommended behavior:`

It should read like future learning material, not a lightweight chat summary.

## 5. Promotion Routing Test

Try these prompts in separate sessions if possible.

Tool discipline:

```text
When using memory tools, don't write durable notes with write_file; use the typed memory APIs.
```

Workflow / coordinator policy:

```text
For broad tasks, the main agent should stay responsible and subagents should only do bounded slices.
```

Behavior / style:

```text
Stay direct and avoid filler acknowledgements after corrections.
```

Then inspect:

```bash
ls AGENTS.md SOUL.md TOOLS.md IDENTITY.md bootstrap_promotion_log.md
```

Expected:

- tool discipline goes to `TOOLS.md`
- coordinator/workflow policy goes to `AGENTS.md`
- stable behavioral guidance tends toward `SOUL.md`
- `IDENTITY.md` changes rarely and only for durable self-model constraints
- every successful promotion is logged in `bootstrap_promotion_log.md`

## 6. Duplicate / Conflict Test

Repeat the same correction in a later session.

Expected:

- the bootstrap file does not get the same bullet twice
- near-duplicate guidance in another bootstrap file should also be rejected
- the audit log should remain readable and explicit about what was promoted

## 7. Helpful Inspection Commands

```bash
uv run python -m hermitcrab.cli.commands memory list reflections
```

If you want to inspect files directly:

```bash
ls journal
ls memory/reflections
```

## 8. Red Flags

Watch for any of these:

- journal entries still feel vague days later
- reflections summarize instead of teaching future behavior
- user-specific preferences get promoted into bootstrap files too aggressively
- the same rule is appended multiple times
- a rule lands in the wrong file
- promotion content is too long, noisy, or not bullet-worthy

## 9. Success Criteria

The branch is behaving well if:

- journals are actually useful to reread later
- reflections feel like reusable learning material
- promoted guidance is concise, auditable, and lands in the right file
- memory stays cleaner instead of getting noisier
