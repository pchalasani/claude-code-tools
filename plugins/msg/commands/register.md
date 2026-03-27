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

Show the output to the user. You are done.
