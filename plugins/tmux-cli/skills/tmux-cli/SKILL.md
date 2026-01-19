---
name: tmux-cli
description: CLI utility to communicate with other CLI Agents or Scripts in other tmux panes; use it only when user asks you to communicate with other CLI Agents or Scripts in other tmux panes.
---

# tmux-cli

## Instructions

Use the `tmux-cli` command to communicate with other CLI Agents or Scripts in
other tmux panes. Do `tmux-cli --help` to see how to use it!

This command depends on installing the `claude-code-tools`. If you get an error
indicating that the command is not available, ask the user to install it using:
`uv tool install claude-code-tools`.

## Key Commands

### Execute with Exit Code Detection

Use `tmux-cli execute` when you need to know if a shell command succeeded or
failed:

```bash
tmux-cli execute "make test" --pane=2
# Returns JSON: {"output": "...", "exit_code": 0}

tmux-cli execute "npm install" --pane=ops:1.3 --timeout=60
# Returns exit_code=0 on success, non-zero on failure, -1 on timeout
```

This is useful for:

- Running builds and knowing if they passed
- Running tests and detecting pass/fail
- Multi-step automation that should abort on failure

**Note**: `execute` is for shell commands only, not for agent-to-agent chat.
For communicating with another Claude Code instance, use `send` + `wait_idle` +
`capture` instead.



