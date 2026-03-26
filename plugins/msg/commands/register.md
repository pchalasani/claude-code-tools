---
description: Register this session as a named agent for inter-agent communication
allowed-tools: Bash
arguments:
  - name: agent_name
    description: A human-friendly name for this agent session (e.g., architect, tester, reviewer)
    required: true
---

Register this agent session for inter-agent communication
using the msg system.

Run:

```bash
msg register $ARGUMENTS
```

This will auto-detect the tmux pane and agent type.
After registration, other agents can send messages
to this session using the registered name.

After running the command, display a summary to the user
confirming what was registered. Include the name, pane
address, session ID, and agent type as shown in the
command output.
