# tmux-cli Instructions

A command-line tool for controlling CLI applications running in tmux.
Automatically detects whether you're inside or outside tmux and uses the appropriate mode.

## Auto-Detection
- **Inside tmux (Local Mode)**: Manages windows in your current tmux session (default)
  - Can also manage panes if needed (use `--use-pane` flag)
- **Outside tmux (Remote Mode)**: Creates and manages a separate tmux session with windows

## Prerequisites
- tmux must be installed
- The `tmux-cli` command must be available (installed via `uv tool install`)

## Window and Pane Identification

**Windows (default, recommended):**

All tmux-cli managed windows start with the prefix `tmux-cli-` for easy identification and management.

- Auto-generated names: `tmux-cli-1730559234-123` (timestamp-based)
- Custom names: specified with `--window-name` flag, automatically prefixed
  - Example: `--window-name=my-session` creates window `tmux-cli-my-session`
- Window indices: Can also reference by index (depends on your tmux `base-index` setting)
- Full format: `session:window_name` (e.g., `mysession:tmux-cli-my-session`)

**Managed Window Tracking:**
The `tmux-cli-` prefix allows the tool to:
- Identify which windows it created vs your own windows
- Show them separately in `tmux-cli status`
- Clean them all up at once with `tmux-cli cleanup`

**Panes:**
- Pane number: Can reference by index (depends on your tmux `pane-base-index` setting)
- Full format: `session:window.pane` (e.g., `myapp:1.2`)
- Note: Pane indices shift when panes are closed, making them less stable than windows

## ⚠️ IMPORTANT: Always Launch a Shell First!

**Always launch zsh first** to prevent losing output when commands fail:
```bash
tmux-cli launch "zsh"  # Do this FIRST
tmux-cli send "your-command" --pane=2  # Then run commands
```

If you launch a command directly and it errors, the pane closes immediately and you lose all output!

## Core Commands

### Launch a CLI application
```bash
# Default: Creates a new window
tmux-cli launch "command"
# Example: tmux-cli launch "python3"
# Returns: tmux-cli-1730559234-123

# With custom window name (gets auto-prefixed):
tmux-cli launch "python3" --window-name="my-python"
# Returns: tmux-cli-my-python

# Using panes instead of windows:
tmux-cli launch "python3" --use-pane
# Returns: pane identifier (e.g., session:window.pane format like myapp:1.2)
```

### Send input to a window/pane
```bash
# Send to a window (by name):
tmux-cli send "text" --target=WINDOW_NAME
# Example: tmux-cli send "print('hello')" --target=tmux-cli-my-python

# Or send to a pane:
tmux-cli send "text" --pane=PANE_ID
# Example: tmux-cli send "print('hello')" --pane=3

# By default, there's a 1-second delay between text and Enter.
# This ensures compatibility with various CLI applications.

# To send without Enter:
tmux-cli send "text" --target=WINDOW_NAME --enter=False

# To send immediately without delay:
tmux-cli send "text" --target=WINDOW_NAME --delay-enter=False

# To use a custom delay (in seconds):
tmux-cli send "text" --target=WINDOW_NAME --delay-enter=0.5
```

### Capture output from a window/pane
```bash
# Capture from a window:
tmux-cli capture --target=WINDOW_NAME
# Example: tmux-cli capture --target=tmux-cli-my-python

# Or capture from a pane:
tmux-cli capture --pane=PANE_ID
# Example: tmux-cli capture --pane=2
```

### List all windows
```bash
tmux-cli list_windows
# Shows all windows in the current session with indices, names, and commands
```

### List all panes
```bash
tmux-cli list_panes
# Returns: JSON with pane IDs, indices, and status
```

### Show current tmux status
```bash
tmux-cli status
# Shows current location, tmux-cli managed windows, all windows, and panes
# Example output:
#   Current location: myapp:main.0
#
#   tmux-cli managed windows:
#     tmux-cli-1730559234-123      python3
#     tmux-cli-1730559235-456      zsh
#
#   All windows in session:
#   * 0   main                         zsh
#     1   tmux-cli-1730559234-123      python3
#     2   tmux-cli-1730559235-456      zsh
```

### Kill a window/pane
```bash
# Kill a window (by name):
tmux-cli kill --target=WINDOW_NAME
# Example: tmux-cli kill --target=tmux-cli-my-python

# Or kill a pane:
tmux-cli kill --pane=PANE_ID
# Example: tmux-cli kill --pane=2
```

### Clean up all tmux-cli windows
```bash
tmux-cli cleanup
# Kills all windows created by tmux-cli (identified by 'tmux-cli-' prefix)
```

### Send interrupt (Ctrl+C)
```bash
# To a window:
tmux-cli interrupt --target=WINDOW_NAME
# Example: tmux-cli interrupt --target=tmux-cli-my-python

# Or to a pane:
tmux-cli interrupt --pane=PANE_ID
# Example: tmux-cli interrupt --pane=2
```

### Send escape key
```bash
# To a window:
tmux-cli escape --target=WINDOW_NAME
# Example: tmux-cli escape --target=tmux-cli-my-python

# Or to a pane:
tmux-cli escape --pane=PANE_ID
# Example: tmux-cli escape --pane=3
# Useful for exiting vim-like applications
```

### Wait for pane to become idle
```bash
tmux-cli wait_idle --pane=PANE_ID
# Example: tmux-cli wait_idle --pane=2
# Waits until no output changes for 2 seconds (default)

# Custom idle time and timeout:
tmux-cli wait_idle --pane=2 --idle-time=3.0 --timeout=60
```

### Get help
```bash
tmux-cli help
# Displays this documentation
```

## Typical Workflow

1. **ALWAYS launch a shell first** (prefer zsh) - this prevents losing output on errors:
   ```bash
   tmux-cli launch "zsh"  # Returns: tmux-cli-1730559234-123
   # Save this window name for later use!
   ```

2. Run your command in the shell:
   ```bash
   tmux-cli send "python script.py" --target=tmux-cli-1730559234-123
   ```

3. Interact with the program:
   ```bash
   tmux-cli send "user input" --target=tmux-cli-1730559234-123
   tmux-cli capture --target=tmux-cli-1730559234-123  # Check output
   ```

4. Clean up when done:
   ```bash
   tmux-cli kill --target=tmux-cli-1730559234-123
   # Or clean up all tmux-cli windows at once:
   tmux-cli cleanup
   ```

## Using Custom Window Names

For easier reference, use custom names (automatically prefixed with 'tmux-cli-'):

```bash
# Launch with a custom name:
tmux-cli launch "zsh" --window-name=my-dev
# Returns: tmux-cli-my-dev

# Now you can use the shorter name:
tmux-cli send "python script.py" --target=tmux-cli-my-dev
tmux-cli capture --target=tmux-cli-my-dev
tmux-cli kill --target=tmux-cli-my-dev
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

**Window Management:**
- All tmux-cli managed windows have the `tmux-cli-` prefix for easy tracking
- Always save the window name returned by `launch` for later reference
- Use custom window names with `--window-name` for easier identification (auto-prefixed)
- Window names are stable - they don't change when other windows close
- Use `tmux-cli status` to see all tmux-cli managed windows separately
- Use `tmux-cli cleanup` to remove all tmux-cli windows at once
- Use `tmux-cli list_windows` to see all windows in the current session

**General Usage:**
- Use `capture` to check the current state before sending input
- If you launch a command directly (not via shell), the window closes when the command exits
- Windows are isolated from your current workspace
- Both `--target` and `--pane` parameters are supported

**Using Panes:**
- Use `--use-pane` flag with `launch` to create panes instead of windows
- Pane identifiers: `session:window.pane` format (like `myapp:1.2`) or just indices like `1`, `2`
- Note: Pane indices shift when panes are closed, making them less stable than windows
- Panes modify your current window layout; windows don't

## Avoiding Polling
Instead of repeatedly checking with `capture`, use `wait_idle`:
```bash
# Send command to a CLI application
tmux-cli send "analyze this code" --target=my-session

# Wait for it to finish (no output for 3 seconds)
tmux-cli wait_idle --target=my-session --idle-time=3.0

# Now capture the result
tmux-cli capture --target=my-session
```

## Windows vs Panes

**Windows (default):**
- Names don't change when other windows are created/destroyed
- Independent of your current workspace layout
- Can be accessed from any window in the session
- Auto-tracked via `tmux-cli-` prefix
- Bulk cleanup with `tmux-cli cleanup`
- Custom names supported: `--window-name=my-session` → `tmux-cli-my-session`

**Panes:**
- Share space within your current window
- Indices shift when panes are closed (e.g., closing a pane renumbers all subsequent panes)
- Useful when you want to see multiple things side-by-side
- Use `--use-pane` flag with launch command