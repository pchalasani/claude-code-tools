---
description: Register this session as a named agent for inter-agent communication
allowed-tools: Bash
arguments:
  - name: agent_name
    description: A human-friendly name for this agent session (e.g., architect, tester, reviewer)
    required: true
---

Run ONLY this ONE command. Do NOT run anything before
or after it. No tmux commands, no queries, no status
checks. The command auto-detects everything it needs.

```bash
msg register $ARGUMENTS
```

This command keeps the legacy notification route. A First-mate-managed
session is registered by the First-mate bootstrap instead:

```bash
msg register --consumer-protocol first-mate.v1 --json $ARGUMENTS
```

Do not switch an existing registration between protocols while it owns an
armed continuation record.

Show the output to the user. You are done.
