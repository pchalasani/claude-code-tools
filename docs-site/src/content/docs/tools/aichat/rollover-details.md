---
title: "Rollover Details"
---

This page is a deep dive into how the **rollover**
resume strategy works. For a higher-level overview,
see the [Resume](../resume/) page.

:::note[Installation]
Part of the `claude-code-tools` package.
See [Quick Start](../../../getting-started/) for
installation.
:::

## How rollover works

Rollover creates a fresh session with full context
available while preserving the entire history chain.
Here is the step-by-step flow:

```
  ┌─────────────────────────┐
  │  aichat resume          │  <-- User triggers
  │  or aichat search       │      rollover
  └───────────┬─────────────┘
              |
              v
  ┌────────────────────────────────────────┐
  │  1. TRACE LINEAGE                      │
  │     Follow parent_session_file         │
  │     pointers backwards                 │
  │                                        │
  │     ghi789 --> def456 --> abc123       │
  │                           (original)   │
  └────────────────────────────────────────┘
              |
              v
  ┌────────────────────────────────────────┐
  │  2. BUILD PROMPT                       │
  │     - Chronological list of all        │
  │       ancestor session files           │
  │     - Instructions to extract context  │
  │     - Optional: work summary from      │
  │       another agent                    │
  └────────────────────────────────────────┘
              |
              v
  ┌────────────────────────────────────────┐
  │  3. CREATE NEW SESSION (jkl012)        │
  │     - Work summary already in prompt,  │
  │       OR                               │
  │     - User asks agent to recover       │
  │       specific parts of prior work     │
  │       (using session-search skill or   │
  │       session-searcher sub-agent)      │
  └────────────────────────────────────────┘
              |
              v
  ┌────────────────────────────────────────┐
  │  4. INJECT METADATA                    │
  │     First line of jkl012.jsonl:        │
  │                                        │
  │     {                                  │
  │       "continue_metadata": {           │
  │         "parent_session_file":         │
  │           "/path/to/ghi789.jsonl",     │
  │         "parent_session_id":           │
  │           "ghi789-...",                │
  │         "continued_at":                │
  │           "2025-12-19T..."             │
  │       }                                │
  │     }                                  │
  └────────────────────────────────────────┘
              |
              v
  ┌────────────────────────────────────────┐
  │  5. RESUME INTERACTIVELY               │
  │     claude --resume jkl012             │
  │     Fresh context, full history access │
  └────────────────────────────────────────┘
```

## The result: a linked chain

After rollover, sessions form a linked list. Each
child points back to its parent via
`continue_metadata.parent_session_file`:

```
abc123.jsonl <-- def456.jsonl <-- ghi789.jsonl <-- jkl012.jsonl
(original)      (trimmed)        (rollover)       (NEW SESSION)
     |               |               |                  |
     └───────────────┴───────────────┴──────────────────┘
     Agent can read any ancestor on demand
```

The agent can traverse this chain to find any prior
context. It does this using the `session-search`
skill or the `session-searcher` sub-agent (both
available via the `aichat`
[plugin](../../../getting-started/plugins/)).

## Lineage injection

When a rollover session is created, two types of
lineage information are injected:

### Session lineage

A chronological listing of all ancestor sessions is
injected into the first user message of the new
session. This gives the agent immediate visibility
into the full history chain:

- Which sessions came before
- The file path of each ancestor
- When each session was created

### Truncated-message pointers

When using **trim** or **smart-trim** (rather than
rollover), each truncated message carries a pointer
to the specific message index in the parent session.
This means full details can always be looked up if
needed:

```json
{
  "truncated": true,
  "original_session": "/path/to/parent.jsonl",
  "original_index": 42
}
```

## Parent pointers (`continue_metadata`)

Every derived session (whether trimmed or rolled
over) carries a `continue_metadata` block as the
first line of its JSONL file:

```json
{
  "continue_metadata": {
    "parent_session_file": "/path/to/parent.jsonl",
    "parent_session_id": "abc123-def456-...",
    "continued_at": "2025-12-19T14:30:00Z"
  }
}
```

This metadata is used by:

- `aichat lineage` to trace the full chain
- `aichat find-original` to find the root session
- `aichat find-derived` to find children of a session
- The search index to understand session relationships

## Quick vs. context rollover

There are two rollover modes:

### Quick rollover

```bash
aichat rollover abc123 --quick
```

Injects lineage pointers only. The new session starts
immediately with a fresh context window. The user can
then instruct the agent to pull specific context from
ancestor sessions as needed.

:::tip
Quick rollover is the most commonly used mode. It
starts fast and you retain full control over what
context the agent retrieves.
:::

### Context rollover

```bash
aichat rollover abc123
```

Before starting the new session, a headless agent
reads the parent session and extracts a work summary.
This summary is injected into the new session's first
message so the agent has immediate context.

You can customize what gets extracted:

```bash
aichat rollover abc123 \
  -p "Focus on the database migration changes"
```

## Viewing lineage

Use `aichat lineage` to inspect the chain for any
session:

```bash
# Show lineage for a specific session
aichat lineage abc123-def456

# Show lineage for the latest session
aichat lineage

# Get lineage as JSON (for scripts)
aichat lineage abc123 --json
```
