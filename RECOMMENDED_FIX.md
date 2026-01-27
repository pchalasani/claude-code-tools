# Recommended Fix: execute_clean() Implementation

## Option A: Proper Integration with Marker System (Recommended)

```python
def execute_clean(self, command: str, pane_id: Optional[str] = None, timeout: int = 30) -> Dict[str, Any]:
    """
    Execute command with clean user interface - no technical clutter visible.

    This enhanced method provides a clean execution experience by integrating
    properly with the existing marker system while hiding transmission details.
    """
    from .tmux_execution_helpers import (
        generate_execution_markers,
        wrap_command_with_markers,
        poll_for_completion,
    )

    target = pane_id or self.target_pane
    if not target:
        raise ValueError("No target pane specified")

    # Generate unique markers (same pattern as execute())
    start_marker, end_marker = generate_execution_markers()
    wrapped_command = wrap_command_with_markers(command, start_marker, end_marker)

    start_time = time.time()

    try:
        # Clear history before execution to prevent contamination
        self.send_keys("history -c", pane_id=target, enter=True, delay_enter=True)

        # Hide the command transmission (including markers)
        self.send_keys("stty -echo", pane_id=target, enter=True, delay_enter=True)
        self.send_keys(wrapped_command, pane_id=target, enter=True, delay_enter=False)
        self.send_keys("stty echo", pane_id=target, enter=True, delay_enter=True)

        # Use existing polling mechanism for completion
        result = poll_for_completion(
            capture_fn=lambda lines: self.capture_pane(pane_id=target, lines=lines),
            start_marker=start_marker,
            end_marker=end_marker,
            timeout=timeout,
        )

        # Add enhanced metadata
        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "output": result["output"],
            "exit_code": result["exit_code"],
            "duration_ms": duration_ms,
            "attempts": 1,
            "retried": False,
        }

    except Exception as e:
        # Ensure terminal state is restored on any failure
        try:
            self.send_keys("stty echo", pane_id=target, enter=True, delay_enter=True)
        except:
            pass  # Don't mask the original exception
        raise
```

## Option B: Enhance Existing execute() Method

Instead of a separate method, add a parameter to the existing `execute()` method:

```python
def execute(self, command: str, pane_id: Optional[str] = None, timeout: int = 30,
           hide_transmission: bool = False) -> Dict[str, Any]:
    """
    Execute command with optional clean interface.

    Args:
        command: Shell command to execute
        pane_id: Target pane (uses self.target_pane if not specified)
        timeout: Maximum seconds to wait for completion
        hide_transmission: If True, hide command transmission with stty
    """
    # ... existing implementation ...

    if hide_transmission:
        # Clear history and hide transmission
        self.send_keys("history -c", pane_id=target, enter=True, delay_enter=True)
        self.send_keys("stty -echo", pane_id=target, enter=True, delay_enter=True)

    # Send wrapped command
    self.send_keys(wrapped_command, pane_id=target, enter=True, delay_enter=False)

    if hide_transmission:
        self.send_keys("stty echo", pane_id=target, enter=True, delay_enter=True)

    # ... rest of existing implementation ...
```

## Option C: Remove execute_clean() Entirely

Given the architectural complexity, consider:
1. Document the existing clean output capabilities of the marker system
2. Provide examples of how to parse clean output from existing `execute()` method
3. Add utility functions for common parsing tasks

## Key Principles for Any Fix

### 1. Single Execution
- Command should execute exactly once
- No manual execution followed by marker-based execution

### 2. Proper Integration
- Use existing marker system components directly
- Don't bypass established architecture patterns
- Leverage existing `tmux_execution_helpers`

### 3. Error Handling
- Always restore terminal state with try/finally
- Handle stty command failures gracefully
- Don't leave terminal in broken state

### 4. Timing Consistency
- Use consistent `delay_enter` parameters
- Measure timing from start to actual completion
- Account for stty and history operations in timing

## Testing Approach

```python
def test_execute_clean():
    controller = TmuxCLIController()

    # Test 1: Single execution (most critical)
    result = controller.execute_clean("echo 'test' > /tmp/test_file && echo 'done'")
    assert result["output"].strip() == "done"

    # Verify file was written only once
    with open("/tmp/test_file", "r") as f:
        content = f.read().strip()
        assert content == "test"  # Should be "test", not "test\ntest"

    # Test 2: Error handling doesn't break terminal
    try:
        controller.execute_clean("nonexistent_command")
    except:
        pass

    # Verify echo is still working
    result = controller.execute("echo 'echo_test'")
    assert "echo_test" in result["output"]
```

## Migration Strategy

1. **Phase 1**: Fix the double execution bug
2. **Phase 2**: Add proper error handling
3. **Phase 3**: Enhance return format with metadata
4. **Phase 4**: Add comprehensive tests
5. **Phase 5**: Update documentation to match implementation