# Add `hide_transmission` parameter to `execute()` method

## Summary

Adds optional `hide_transmission` parameter to `TmuxCLIController.execute()` to hide command transmission from viewport when controlling remote tmux panes.

## Problem

When controlling tmux panes remotely, `execute()` sends marker-wrapped commands like:
```bash
echo __TMUX_EXEC_START_xxx__; { command; } 2>&1; echo __TMUX_EXEC_END_xxx__:$?
```

Users watching the controlled pane see this technical scaffolding being typed, which creates poor UX for:
- Screen sharing and demos
- IDE integrations
- Pair programming
- Any scenario where the controlled pane is visible to users

## Solution

Add `hide_transmission` parameter that uses `stty -echo` to hide keystroke echo:

```python
# Default: technical scaffolding visible (unchanged behavior)
result = controller.execute("make test")

# Hide technical scaffolding from viewport
result = controller.execute("make test", hide_transmission=True)
```

## How It Works

Since `send_keys()` sends strings as **keystrokes** to the remote pane:
1. Enable `stty -echo` in target pane (hides keystroke echo)
2. Send marker-wrapped command (keystrokes not echoed to screen)
3. Restore `stty echo` (re-enable echo)
4. Command output remains visible (only the typing was hidden)

## Implementation

- Uses `try/finally` to ensure terminal echo restoration even on errors
- Backward compatible (default: `hide_transmission=False`)
- No changes to existing functionality
- Single execution (no double execution)

## Testing

Run demo to see the difference:
```bash
python demo_hide_transmission_proper.py
```

Watch the created demo pane to see:
- **Visible mode**: Wrapped command visible as it's typed
- **Hidden mode**: Clean - only command output visible

## Files Changed

- `claude_code_tools/tmux_cli_controller.py` - Added `hide_transmission` parameter
- `demo_hide_transmission_proper.py` - Demo script
- `tests/manual_test_hide_transmission.py` - Manual integration tests
- `tests/test_hide_transmission.py` - Unit tests

## Backward Compatibility

✅ Fully backward compatible - existing code works unchanged.

Users opt-in to clean viewport when needed:
```python
result = controller.execute("command", hide_transmission=True)
```