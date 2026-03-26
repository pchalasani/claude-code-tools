---
description: Check and respond to inter-agent messages
allowed-tools: Bash
---

You have been notified of new inter-agent messages.

1. Read your inbox:

```bash
msg inbox
```

2. Read the output carefully. It contains messages from
   other agent sessions addressed to you.

3. If a reply is needed, send it:

```bash
msg reply <sender-name> "your reply here"
```

Replace `<sender-name>` with the name of the agent you
are replying to (shown in the inbox output).

4. After replying, continue with whatever you were doing
   before the notification.

Keep replies concise to avoid consuming context.
