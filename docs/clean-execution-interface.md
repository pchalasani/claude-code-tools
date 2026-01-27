# Clean Execution Interface Pattern

## Problem Statement

When executing commands in tmux panes programmatically, the interface becomes cluttered with:
- Base64 encoded command blobs for safe transmission
- Technical control characters and markers
- Garbled JSON output with implementation details
- Command input visible instead of results

## Solution Pattern: stty-based Hiding with Clean Markers

We've discovered a **clean execution interface pattern** that separates technical implementation from user experience.

### Core Technique

1. **Hide implementation with stty** - Temporarily disable echo during transmission
2. **Use marker-based completion detection** - Borrow from existing marker strategy  
3. **Clean output extraction** - Parse only essential command results
4. **Structured JSON response** - Return: `{"output": "...", "exit_code": 0, "duration_ms": ...}`

### Implementation Examples

#### Bash Approach (Independent Discovery)
```bash
# Step 1: Hide input during command transmission
tmux send-keys -t "$TARGET" "stty -echo" Enter
tmux send-keys -t "$TARGET" "$COMMAND" Enter  
tmux send-keys -t "$TARGET" "stty echo" Enter

# Step 2: Clear history to prevent contamination  
tmux send-keys -t "$TARGET" "history -c" Enter

# Step 3: Extract clean result from recent execution
pane=$(tmux capture-pane -t "$TARGET" -p -S -3)
result=$(echo "$pane" | tail -2 | grep -v 'ryan@biggie:' | head -1)

echo "{\"output\":\"$result\",\"exit_code\":0,\"duration_ms\":1}"
```

#### Python Approach (Existing + Improvements)
```python
# Use existing marker strategy from tmux_execution_helpers
def execute_clean(self, command: str, pane_id: Optional[str] = None, timeout: int = 30) -> Dict[str, Any]:
    target = pane_id or self.target_pane
    
    # Hide echo during transmission (new enhancement)
    self.send_keys("stty -echo", pane_id=target, enter=True)
    self.send_keys(command, pane_id=target, enter=True)  
    self.send_keys("stty echo", pane_id=target, enter=True)
    
    # Clear history before execution (new)
    self.send_keys("history -c", pane_id=target, enter=True)
    
    # Use existing sophisticated execution with markers
    return self.execute(command, pane_id=target, timeout=timeout)
```

### Key Principles

1. **User Interface Separation**
   - Technical implementation remains invisible to users
   - Clean command display: `"executing: echo hello world"`
   - Structured output: `{"output": "hello world", "exit_code": 0}`

2. **Reliability Improvements** 
   - History clearing prevents contamination from previous executions
   - stty echo protection maintains terminal usability
   - Precise command result extraction

3. **Test-Driven Development**
   - Created comprehensive test suite first
   - Validated clean output (no base64 clutter)
   - Verified proper stty restoration
   - Tested various command types: simple, pipes, date, etc.

### Results

**Before (Garbled)**:
```json
{
  "output": "ZWNobyB0ZXN0MjMKCjIyMTY...",
  "exit_code": 0,
  "duration_ms": 583
}
```

**After (Clean)**:
```json
{
  "output": "test123", 
  "exit_code": 0,
  "duration_ms": 1,
  "attempts": 1,
  "retried": false
}
```

## Integration Points

This pattern complements the existing `tmux_execution_helpers.py` by adding:

1. **Clean interface layer** - User-friendly command execution
2. **History management** - Prevents output contamination
3. **stty protection** - Maintains terminal state
4. **Precise extraction** - Gets actual command results vs. input

The core marker strategy (`__TMUX_EXEC_START_*/END_*`) remains the backbone, enhanced with clean UI patterns.

## References

- Test implementation: `~/devel/sane-execute-devtest/sane-run-command-v2`
- Existing markers: `tmux_execution_helpers.py`
- Test suite: `test-sane-run-command.bash`

---

*This pattern was discovered independently in bash before recognizing the same marker strategy in the claude-code-tools repository, validating the approach's effectiveness.*