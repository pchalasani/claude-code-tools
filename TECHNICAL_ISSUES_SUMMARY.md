# Technical Issues Summary: execute_clean() Implementation

## The Fatal Bug

**Double Execution**: Every command runs twice
```python
# Line 686: First execution
self.send_keys(command, pane_id=target, enter=True, delay_enter=False)

# Line 693: Second execution
return self.execute(command, pane_id=target, timeout=timeout)
```

**Why this breaks everything**:
- `echo "hello" > file.txt` writes to file twice
- `rm important_file.txt` deletes file, then fails on second attempt
- `curl -X POST api/endpoint` sends duplicate API requests
- Performance is 2x slower than necessary

## The Architectural Problem

**Misunderstood Integration Pattern**:
- Current code treats `execute()` as a black box monitoring tool
- Should integrate with the marker system components directly
- Bypasses established architecture instead of extending it

## Specific Technical Flaws

### 1. stty Manipulation Ineffectiveness
- Hides the wrong execution (manual one that gets ignored)
- Doesn't hide the marker-wrapped execution (the one that gets returned)
- Net result: No actual hiding achieved

### 2. History Clearing Wrong Timing
```python
# Current (broken) order:
manual_execution() → history_clear() → marker_execution()
```
History clearing happens between executions, not before both.

### 3. Missing Error Handling
No try/finally to ensure `stty echo` restoration:
```python
self.send_keys("stty -echo", ...)
# If this fails, terminal echo is permanently disabled
self.send_keys(command, ...)
self.send_keys("stty echo", ...)
```

### 4. Output Contamination
Second execution sees artifacts from first execution in the pane buffer.

## What Should Have Been Done

```python
def execute_clean(self, command: str, pane_id: Optional[str] = None, timeout: int = 30):
    # Import marker system components
    from .tmux_execution_helpers import generate_execution_markers, wrap_command_with_markers

    # Generate markers (like execute() does)
    start_marker, end_marker = generate_execution_markers()
    wrapped_command = wrap_command_with_markers(command, start_marker, end_marker)

    # Clear history first
    self.send_keys("history -c", pane_id=target, enter=True)

    # Hide ONLY the marker-wrapped command transmission
    self.send_keys("stty -echo", pane_id=target, enter=True)
    self.send_keys(wrapped_command, pane_id=target, enter=True)
    self.send_keys("stty echo", pane_id=target, enter=True)

    # Use existing polling (don't call execute() again!)
    return poll_for_completion(capture_fn, start_marker, end_marker, timeout)
```

## Impact Assessment

**Severity**: CRITICAL - Cannot be deployed
**User Impact**: Data corruption, duplicate operations, performance degradation
**Fix Complexity**: Complete rewrite required

## Root Cause

Fundamental misunderstanding of how the existing `execute()` method works and how to properly integrate with the marker system architecture.