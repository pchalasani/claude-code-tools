---
name: msg
description: >-
  Inter-agent communication via the msg CLI.
  Use this when you need to send messages to other
  agent sessions, read incoming messages, or
  coordinate with other agents in tmux panes.
---

# msg: Inter-Agent Communication

You can communicate with other coding agent sessions
(Claude Code or Codex CLI) using the `msg` CLI tool.

## Registration

Before sending or receiving messages, register yourself:

```bash
msg register <your-name>
```

This auto-detects your tmux pane. You only need to do
this once per session.

First-mate bootstraps its managed sessions with the closed protocol value:

```bash
msg register --consumer-protocol first-mate.v1 --json <your-name>
```

The default remains `legacy`.

## Sending Messages

Send a message directly to another agent:

```bash
msg send <agent-name> "Your message here"
```

Send to multiple agents:

```bash
msg send agent1,agent2 "Message for both of you"
```

## Replying

```bash
msg reply <agent-name> "Your reply here"
```

## Receiving Messages

Check your inbox:

```bash
msg inbox
```

This shows all unread messages grouped by thread and
marks them as read.

For a `first-mate.v1` registration, never use that legacy read-and-mark path.
Invoke `$first-mate`; its helper repeatedly peeks a bounded page, fsyncs the
recipient journal, then explicitly acknowledges exact delivery IDs. The
helper owns assignment-generation reconciliation and refreshes an existing
continuation heartbeat every 45 seconds with a 90-second TTL. The hooks never
create or replace a generation.

Plugin-native `PostToolUse`, `Stop`, and `UserPromptSubmit` hooks keep an armed
responsibility in the agent loop. A stale heartbeat routes to recovery; it
does not clear responsibility. First-mate delivery never uses tmux prompt
injection.

Codex treats plugin hooks as non-managed code and skips changed definitions
until the user reviews and trusts the current hash in `/hooks`. Never bypass
that trust decision. A plugin upgrade requires a fresh review when hook bytes
change. That trust covers the hook definition, not imported adapter bytes; the
release evidence and First-mate doctor must separately verify the complete
plugin payload and reject same-version tree drift.

## Other Commands

```bash
msg list          # List registered agents
msg threads       # List active threads
msg status        # Check system health
```

## Guidelines

- Keep messages concise -- they consume context in the
  receiving agent's session.
- When replying, include enough context that the
  recipient understands without re-reading the full
  thread.
- If you need to share code or file paths, reference
  them in the message text rather than pasting large
  blocks.
