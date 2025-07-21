# tmux-cli Instructions

A command-line tool for controlling CLI applications running in tmux panes.

## Prerequisites
- tmux must be installed and you must be inside a tmux session
- The `tmux-cli` command must be available (installed via `uv tool install`)

## Core Commands

### Launch a CLI application
```bash
tmux-cli launch "command"
# Example: tmux-cli launch "python3"
# Returns: pane ID (e.g., %48)
```

### Send input to a pane
```bash
tmux-cli send "text" --pane=PANE_ID
# Example: tmux-cli send "print('hello')" --pane=%48
```

### Capture output from a pane
```bash
tmux-cli capture --pane=PANE_ID
# Example: tmux-cli capture --pane=%48
```

### List all panes
```bash
tmux-cli list_panes
# Returns: JSON with pane IDs, indices, and status
```

### Kill a pane
```bash
tmux-cli kill --pane=PANE_ID
# Example: tmux-cli kill --pane=%48
```

### Send interrupt (Ctrl+C)
```bash
tmux-cli interrupt --pane=PANE_ID
```

## Typical Workflow

1. Launch a shell first (recommended):
   ```bash
   tmux-cli launch "bash"  # Returns pane ID
   ```

2. Run your command in the shell:
   ```bash
   tmux-cli send "python script.py" --pane=%48
   ```

3. Interact with the program:
   ```bash
   tmux-cli send "user input" --pane=%48
   tmux-cli capture --pane=%48  # Check output
   ```

4. Clean up when done:
   ```bash
   tmux-cli kill --pane=%48
   ```

## Tips
- Always save the pane ID returned by `launch`
- Use `capture` to check the current state before sending input
- Pane IDs can be like `%48` or pane indices like `1`, `2`
- If you launch a command directly (not via shell), the pane closes when
  the command exits