# tmux-cli Instructions

A command-line tool for controlling CLI applications running in tmux windows.
Automatically detects whether you're inside or outside tmux and uses the appropriate mode.

## Auto-Detection
- **Inside tmux (Local Mode)**: Manages windows in your current tmux session
- **Outside tmux (Remote Mode)**: Creates and manages a separate tmux session with windows

## Prerequisites
- tmux must be installed
- The `tmux-cli` command must be available (installed via `uv tool install`)

## Window Identification

All tmux-cli managed windows start with the prefix `tmux-cli-` for easy identification and management.

- Auto-generated names: `tmux-cli-1730559234-123` (timestamp-based)
- Custom names: specified with `--window-name` flag, automatically prefixed
  - Example: `--window-name=my-session` creates window `tmux-cli-my-session`
- Full format: `session:window_name` (e.g., `mysession:tmux-cli-my-session`)

**Managed Window Tracking:**
The `tmux-cli-` prefix allows the tool to:
- Identify which windows it created vs your own windows
- Show them separately in `tmux-cli status`
- Clean them all up at once with `tmux-cli cleanup`

## ⚠️ IMPORTANT: Always Launch a Shell First!

**Always launch zsh first** to prevent losing output when commands fail:

```bash
tmux-cli launch "zsh"
# Returns: tmux-cli-1730559234-123
```

If you launch a command directly and it errors, the window closes immediately and you lose all output!

## Core Commands

### Launch a CLI application
```bash
# Creates a new window in the background
tmux-cli launch "command"
# Example: tmux-cli launch "python3"
# Returns: tmux-cli-1730559234-123

# With custom window name:
tmux-cli launch "python3" --window-name=my-python
# Returns: tmux-cli-my-python
```

### Send input to a window
```bash
# Send to a window (by name):
tmux-cli send "text" --target=WINDOW_NAME
# Example: tmux-cli send "print('hello')" --target=tmux-cli-my-python

# By default, there's a 1-second delay between text and Enter.
# This ensures compatibility with various CLI applications.

# To send without Enter:
tmux-cli send "text" --target=WINDOW_NAME --enter=False

# To send immediately without delay:
tmux-cli send "text" --target=WINDOW_NAME --delay-enter=False

# To use a custom delay (in seconds):
tmux-cli send "text" --target=WINDOW_NAME --delay-enter=0.5
```

### Capture output from a window
```bash
# Capture from a window:
tmux-cli capture --target=WINDOW_NAME
# Example: tmux-cli capture --target=tmux-cli-my-python

# Capture last N lines:
tmux-cli capture --target=WINDOW_NAME --lines=10
```

### List all windows
```bash
tmux-cli list_windows
# Shows all windows in the current session with indices, names, and commands
```

### Show current tmux status
```bash
tmux-cli status
# Shows current location, tmux-cli managed windows, and all windows
# Example output:
#   Current location: myapp:main
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

### Kill a window
```bash
# Kill a window (by name):
tmux-cli kill --target=WINDOW_NAME
# Example: tmux-cli kill --target=tmux-cli-my-python
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
```

### Send escape key
```bash
# To a window:
tmux-cli escape --target=WINDOW_NAME
# Example: tmux-cli escape --target=tmux-cli-my-python
# Useful for exiting vim-like applications
```

### Show help
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

### Attach to managed session
```bash
tmux-cli attach
# Attaches to the managed session
```

### List windows (remote)
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
- Windows launch in the background (don't steal focus)

## Window Benefits

- **Stable names**: Window names don't change when other windows are created/destroyed
- **Independent**: Windows don't affect your current workspace layout
- **Auto-tracked**: All managed windows have `tmux-cli-` prefix
- **Bulk cleanup**: Remove all with `tmux-cli cleanup`
- **Custom names**: Easy identification with `--window-name`

## Error Handling

If you see "Could not resolve window: xxx", check:
1. Did you save the window name from `launch`?
2. Is the window still running? Use `tmux-cli status` to check
3. Did you spell the window name correctly?

## Examples

### Interactive Python REPL
```bash
# Launch Python in a named window
WIN=$(tmux-cli launch "python3" --window-name=repl)

# Send commands
tmux-cli send "import sys" --target=$WIN
tmux-cli send "print(sys.version)" --target=$WIN

# Get output
tmux-cli capture --target=$WIN --lines=5

# Clean up
tmux-cli kill --target=$WIN
```

### Running a script
```bash
# Always use a shell first!
WIN=$(tmux-cli launch "zsh" --window-name=script-runner)

# Run the script
tmux-cli send "python my_script.py" --target=$WIN

# Wait a bit
sleep 5

# Check output
tmux-cli capture --target=$WIN

# Clean up
tmux-cli kill --target=$WIN
```

### Multiple concurrent sessions
```bash
# Launch multiple windows
WIN1=$(tmux-cli launch "zsh" --window-name=task1)
WIN2=$(tmux-cli launch "zsh" --window-name=task2)
WIN3=$(tmux-cli launch "zsh" --window-name=task3)

# Run different tasks
tmux-cli send "python task1.py" --target=$WIN1
tmux-cli send "python task2.py" --target=$WIN2
tmux-cli send "python task3.py" --target=$WIN3

# Check status of all
tmux-cli status

# Clean up all at once
tmux-cli cleanup
```
