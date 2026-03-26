---
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
