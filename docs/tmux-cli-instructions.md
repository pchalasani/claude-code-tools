# tmux-cli Instructions

A command-line tool for controlling CLI applications running in tmux panes/windows.
Automatically detects whether you're inside or outside tmux and uses the appropriate mode.

## Auto-Detection
- **Inside tmux (Local Mode)**: Manages panes in your current tmux window
- **Outside tmux (Remote Mode)**: Manages windows in a separate tmux session, creating it when `launch` first needs it

Remote `status`, `list_panes`, and `list_windows` do not create a session.
An explicit full pane target (such as `%12` or `myapp:1.2`) works without a
managed session. Commands without a target require an active window from a
successful managed `launch`; otherwise, launch a window first or pass `--pane`.
`attach` requires the managed session to already exist.

## Prerequisites
- tmux must be installed
- The `tmux-cli` command must be available (installed via `uv tool install`)

## Target Identification

In local mode, panes can be specified in two simple ways:
- Just the pane number (e.g., `2`) - refers to pane 2 in the current window
- Full format: `session:window.pane` (e.g., `myapp:1.2`) - for any pane in any session

In remote mode, `launch` returns a window target such as `remote-cli-session:1`.
Use the returned target as `--pane`. A bare number such as `1` means a managed
window index, not a pane index; window indices can change if windows move.
Numeric `--pane=2` examples below show local mode unless labeled as remote.

## ⚠️ IMPORTANT: Always Launch a Shell First!

**Always launch zsh first** to prevent losing output when commands fail:
```bash
tmux-cli launch "zsh"  # Do this FIRST; remote example returns remote-cli-session:1
tmux-cli send "your-command" --pane=remote-cli-session:1  # Use the actual returned target
```

If a directly launched command exits before its remote window is marked ready,
`launch` reports that the command was launched but has no live target. It does
not retry the command. Launch a shell first to keep output available.

## Core Commands

### Launch a CLI application
```bash
tmux-cli launch "command"
# Example: tmux-cli launch "python3"
# Local return: pane identifier (e.g., myapp:1.2)
# Remote return: window target (e.g., remote-cli-session:1)
```

### Send input to a pane
```bash
tmux-cli send "text" --pane=PANE_ID
# Example: tmux-cli send "print('hello')" --pane=3

# By default, there's a 1.5-second delay between text and Enter,
# plus automatic Enter key verification with retry (up to 3 attempts).
# This ensures reliability with various CLI applications.

# To send without Enter:
tmux-cli send "text" --pane=PANE_ID --enter=False

# To send immediately without delay:
tmux-cli send "text" --pane=PANE_ID --delay-enter=False

# To use a custom delay (in seconds):
tmux-cli send "text" --pane=PANE_ID --delay-enter=0.5
```

### Capture output from a pane
```bash
tmux-cli capture --pane=PANE_ID
# Example: tmux-cli capture --pane=2
```

### List all panes
```bash
tmux-cli list_panes
# Returns: JSON with pane IDs, indices, and status
```

### Show current tmux status
```bash
tmux-cli status
# Shows current location and all panes in current window
# Example output:
#   Current location: myapp:1.2
#   Panes in current window:
#    * myapp:1.0       zsh                  zsh
#      myapp:1.1       python3              python3  
#      myapp:1.2       vim                  main.py
```

### Kill a pane
```bash
tmux-cli kill --pane=PANE_ID
# Example: tmux-cli kill --pane=2

# SAFETY: You cannot kill your own pane - this will give an error
# to prevent accidentally terminating your session
```

### Send interrupt (Ctrl+C)
```bash
tmux-cli interrupt --pane=PANE_ID
# Example: tmux-cli interrupt --pane=2
```

### Send escape key
```bash
tmux-cli escape --pane=PANE_ID
# Example: tmux-cli escape --pane=3
# Useful for exiting Claude or vim-like applications
```

### Wait for pane to become idle
```bash
tmux-cli wait_idle --pane=PANE_ID
# Example: tmux-cli wait_idle --pane=2
# Waits until no output changes for 2 seconds (default)

# Custom idle time and timeout:
tmux-cli wait_idle --pane=2 --idle-time=3.0 --timeout=60
```

### Execute command and get exit code

Run a shell command and get both the output and exit code. Ideal for build/test
automation where you need to know if a command succeeded or failed.

```bash
tmux-cli execute "pytest tests/" --pane=2
# Returns JSON: {"output": "...", "exit_code": 0}

# With custom timeout (default is 30 seconds)
tmux-cli execute "long_running_script.sh" --pane=2 --timeout=120

# Timeout returns exit_code=-1
```

**Python API:**

```python
from claude_code_tools.tmux_cli_controller import TmuxCLIController

ctrl = TmuxCLIController()
result = ctrl.execute("make test", pane_id="ops:1.2")
# Returns: {"output": "...", "exit_code": 0}
```

**Why use `execute()` instead of `send_keys()` + `capture_pane()`?**

- **Reliable exit codes**: Know definitively if a command succeeded or failed
- **No output parsing**: Don't guess success by looking for "error" in text
- **Proper automation**: Build pipelines that abort on failure, retry on transient
  errors, or continue on success

**When NOT to use `execute()`:**

- Agent-to-agent communication (Claude Code doesn't return exit codes)
- Interactive REPL sessions (use `send_keys()` + `wait_for_idle()` instead)
- Long-running processes you want to monitor incrementally

### Get help
```bash
tmux-cli help
# Displays this documentation
```

## Typical Workflow

1. **ALWAYS launch a shell first** (prefer zsh) - this prevents losing output on errors:
   ```bash
   tmux-cli launch "zsh"  # Remote: returns a window target - DO THIS FIRST!
   ```

2. Run your command in the shell:
   ```bash
   tmux-cli send "python script.py" --pane=remote-cli-session:1  # Use the actual returned target
   ```

3. Interact with the program:
   ```bash
   tmux-cli send "user input" --pane=remote-cli-session:1
   tmux-cli capture --pane=remote-cli-session:1  # Check output
   ```

4. Clean up when done:
   ```bash
   tmux-cli kill --pane=remote-cli-session:1
   ```

## Remote Mode Specific Commands

These commands are only available when running outside tmux:

### Attach to session
```bash
tmux-cli attach
# Opens the managed tmux session to view live
```

### Clean up session
```bash
tmux-cli cleanup
# Kills the entire managed session and all its windows
```

### List windows
```bash
tmux-cli list_windows
# Shows all windows in the managed session
```

## Tips
- Always save the pane/window identifier returned by `launch`
- Use `capture` to check the current state before sending input
- Use `status` to see all available panes and their current state
- In local mode: Pane identifiers can be session:window.pane format (like `myapp:1.2`) or just pane indices like `1`, `2`
- In remote mode: Bare numbers like `0`, `1` are window indices; `launch` returns
  a `session:window_index` target, such as `remote-cli-session:1`
- If you launch a command directly (not via shell), the pane/window closes when
  the command exits
- **IMPORTANT**: The tool prevents you from killing your own pane/window to avoid
  accidentally terminating your session

## Avoiding Polling
Instead of repeatedly checking with `capture`, use `wait_idle`:
```bash
# Send command to a CLI application
tmux-cli send "analyze this code" --pane=2

# Wait for it to finish (no output for 3 seconds)
tmux-cli wait_idle --pane=2 --idle-time=3.0

# Now capture the result
tmux-cli capture --pane=2
```
