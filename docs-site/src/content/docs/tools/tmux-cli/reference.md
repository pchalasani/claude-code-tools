---
title: "Command Reference"
---

Complete reference for all `tmux-cli` commands.

## Target Identification

In local mode, panes can be specified in two ways:

- **Pane number only** -- e.g. `2` (refers to pane 2 in
  the current window).
- **Full format** -- `session:window.pane`, e.g.
  `myapp:1.2` (for any pane in any session).

In remote mode, `launch` returns a window target such as
`remote-cli-session:1`. Pass that returned target as `--pane` for subsequent
commands. A bare number like `1` means a window index in the managed session,
not a pane index. An explicit full pane target such as `%12` or `myapp:1.2`
can also address an existing pane. Window indices can change when windows move.
Numeric `--pane=2` examples below show local mode; in remote mode, use the
target returned by `launch`.

## Core Commands

### launch

Launch a CLI application in a new tmux pane (local mode) or window (remote mode).

```bash
tmux-cli launch "command"
# Example: tmux-cli launch "python3"
# Local return: pane identifier (e.g. myapp:1.2)
# Remote return: window target (e.g. remote-cli-session:1)
```

:::caution
Always launch `zsh` first, then run commands via `send`.
If a directly launched command exits before its remote window is marked ready,
`launch` reports that it was launched but has no live target. It does not retry
the command. Launch a shell first to keep output available.
:::

### send

Send input text to a pane.

```bash
tmux-cli send "text" --pane=PANE_ID
```

Options:

| Flag | Description |
|------|-------------|
| `--enter=False` | Send text without pressing Enter |
| `--delay-enter=False` | Send immediately (no delay) |
| `--delay-enter=0.5` | Custom delay in seconds |

By default there is a 1.5-second delay between text and
Enter, plus automatic Enter key verification with retry
(up to 3 attempts).

### capture

Capture the current output from a pane.

```bash
tmux-cli capture --pane=PANE_ID
```

### list_panes

List all panes in the current window.

```bash
tmux-cli list_panes
# Returns: JSON with pane IDs, indices, and status
```

### status

Show current tmux status and all panes.

```bash
tmux-cli status
```

Example output:

```
Current location: myapp:1.2
Panes in current window:
 * myapp:1.0   zsh          zsh
   myapp:1.1   python3      python3
   myapp:1.2   vim          main.py
```

### kill

Kill a pane.

```bash
tmux-cli kill --pane=PANE_ID
```

:::note
You cannot kill your own pane -- this safety guard
prevents accidentally terminating your session.
:::

### interrupt

Send `Ctrl+C` to a pane.

```bash
tmux-cli interrupt --pane=PANE_ID
```

### escape

Send the Escape key to a pane (useful for exiting
vim-like applications or Claude).

```bash
tmux-cli escape --pane=PANE_ID
```

### wait_idle

Wait until a pane has no output changes for a given
duration.

```bash
tmux-cli wait_idle --pane=PANE_ID
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--idle-time` | `2.0` | Seconds of silence before idle |
| `--timeout` | `30` | Max seconds to wait |

```bash
# Custom idle time and timeout
tmux-cli wait_idle --pane=2 --idle-time=3.0 --timeout=60
```

:::tip
Use `wait_idle` instead of polling with repeated
`capture` calls. Send a command, wait for idle, then
capture the result.
:::

### execute

Run a shell command and get both the output and exit
code. Ideal for build/test automation.

```bash
tmux-cli execute "pytest tests/" --pane=2
# Returns JSON: {"output": "...", "exit_code": 0}
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--timeout` | `30` | Max seconds (returns `exit_code=-1` on timeout) |

**Python API:**

```python
from claude_code_tools.tmux_cli_controller import (
    TmuxCLIController,
)

ctrl = TmuxCLIController()
result = ctrl.execute("make test", pane_id="ops:1.2")
# Returns: {"output": "...", "exit_code": 0}
```

**Why use `execute` instead of `send` + `capture`?**

- **Reliable exit codes** -- Know definitively if a
  command succeeded or failed.
- **No output parsing** -- No need to guess success by
  looking for "error" in text.
- **Proper automation** -- Build pipelines that abort on
  failure or retry on transient errors.

**When NOT to use `execute`:**

- Agent-to-agent communication (Claude Code does not
  return exit codes).
- Interactive REPL sessions (use `send` + `wait_idle`
  instead).
- Long-running processes you want to monitor
  incrementally.

### help

Display built-in documentation.

```bash
tmux-cli help
```

## Remote Mode Commands

These commands are only available when running **outside
tmux**:

The managed session is created by `launch` when needed. `status`,
`list_panes`, and `list_windows` do not create it. You can address an
existing pane in any session with a full target such as `%12` or
`myapp:1.2`; without a target, an active window from a successful
managed `launch` is required. `attach` requires that session to exist.

### attach

Open the managed tmux session to view live.

```bash
tmux-cli attach
```

### cleanup

Kill the entire managed session and all its windows.

```bash
tmux-cli cleanup
```

### list_windows

Show all windows in the managed session.

```bash
tmux-cli list_windows
```

## Tips

- Save the pane identifier or window target returned by `launch` and use it
  as `--pane` while it exists.
- Use `capture` to check the current state before
  sending input.
- Use `status` to see all available panes and their
  current state.
- In local mode, pane identifiers can be full format
  (`myapp:1.2`) or just indices (`1`, `2`).
- In remote mode, bare numbers (`0`, `1`) are managed window indices; `launch`
  returns `session:window_index` (for example, `remote-cli-session:1`).
- The tool prevents you from killing your own
  pane/window to avoid accidentally terminating your
  session.
