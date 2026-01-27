#!/usr/bin/env python
"""
Manual integration test for hide_transmission feature.

Run this in an actual tmux session:
    python tests/manual_test_hide_transmission.py

This will create a test pane and verify the hide_transmission functionality works correctly.
"""

import sys
import tempfile
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_code_tools.tmux_cli_controller import TmuxCLIController


def get_controller():
    """Get a controller with a target pane."""
    ctrl = get_controller()

    # Try to get current pane
    import subprocess
    try:
        pane = subprocess.check_output(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            text=True
        ).strip()
        ctrl.target_pane = pane
    except:
        # Not in tmux, try to find any session
        try:
            sessions = subprocess.check_output(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                text=True
            ).strip().split('\n')
            if sessions and sessions[0]:
                ctrl.target_pane = f"{sessions[0]}:0.0"
            else:
                raise ValueError("No tmux sessions found. Please run from within tmux or create a session first.")
        except subprocess.CalledProcessError:
            raise ValueError("tmux is not running. Please start tmux first.")

    return ctrl


def test_basic_execution():
    """Test basic command execution with both modes."""
    print("Test 1: Basic execution...")

    ctrl = get_controller()

    # Test visible mode
    result = ctrl.execute("echo 'visible test'", hide_transmission=False)
    assert result["exit_code"] == 0
    assert "visible test" in result["output"]
    print("  ✓ Visible mode works")

    # Test hidden mode
    result = ctrl.execute("echo 'hidden test'", hide_transmission=True)
    assert result["exit_code"] == 0
    assert "hidden test" in result["output"]
    print("  ✓ Hidden mode works")


def test_single_execution_not_double():
    """Critical test: Verify commands execute only once."""
    print("\nTest 2: Single execution (not double)...")

    ctrl = get_controller()

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        test_file = f.name

    try:
        # Write to file with hidden transmission
        result = ctrl.execute(
            f"echo 'single_execution' >> {test_file}",
            hide_transmission=True
        )
        assert result["exit_code"] == 0

        # Read file and count lines
        content = Path(test_file).read_text()
        lines = [line for line in content.strip().split('\n') if line]

        if len(lines) != 1:
            print(f"  ✗ FAILED: Expected 1 line, got {len(lines)}: {lines}")
            return False

        assert lines[0] == "single_execution"
        print("  ✓ Command executed exactly once (not doubled)")
        return True
    finally:
        Path(test_file).unlink(missing_ok=True)


def test_exit_codes():
    """Test exit code capture in both modes."""
    print("\nTest 3: Exit code capture...")

    ctrl = get_controller()

    # Success command - visible
    result = ctrl.execute("true", hide_transmission=False)
    assert result["exit_code"] == 0
    print("  ✓ Visible mode: exit code 0 captured")

    # Failure command - visible
    result = ctrl.execute("false", hide_transmission=False)
    assert result["exit_code"] == 1
    print("  ✓ Visible mode: exit code 1 captured")

    # Success command - hidden
    result = ctrl.execute("true", hide_transmission=True)
    assert result["exit_code"] == 0
    print("  ✓ Hidden mode: exit code 0 captured")

    # Failure command - hidden
    result = ctrl.execute("false", hide_transmission=True)
    assert result["exit_code"] == 1
    print("  ✓ Hidden mode: exit code 1 captured")


def test_terminal_echo_restoration():
    """Test that terminal echo is properly restored."""
    print("\nTest 4: Terminal echo restoration...")

    ctrl = get_controller()

    # Execute with hidden transmission
    result = ctrl.execute("echo 'test1'", hide_transmission=True)
    assert result["exit_code"] == 0

    # Verify echo is restored by running visible command
    result = ctrl.execute("echo 'test2'", hide_transmission=False)
    assert result["exit_code"] == 0
    print("  ✓ Terminal echo restored after hidden execution")

    # Test with failing command
    result = ctrl.execute("false", hide_transmission=True)
    assert result["exit_code"] == 1

    # Verify echo still works
    result = ctrl.execute("echo 'test3'", hide_transmission=False)
    assert result["exit_code"] == 0
    print("  ✓ Terminal echo restored even after command failure")


def test_file_operations():
    """Test file operations to ensure single execution."""
    print("\nTest 5: File operations (single execution check)...")

    ctrl = get_controller()

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        test_file = f.name

    try:
        # Create file with multiple operations
        result = ctrl.execute(
            f"echo 'first' > {test_file} && echo 'second' >> {test_file}",
            hide_transmission=True
        )
        assert result["exit_code"] == 0

        # Read and verify
        content = Path(test_file).read_text()
        lines = [line for line in content.strip().split('\n') if line]

        if lines != ["first", "second"]:
            print(f"  ✗ FAILED: Expected ['first', 'second'], got {lines}")
            return False

        print("  ✓ File operations executed once (not duplicated)")
        return True
    finally:
        Path(test_file).unlink(missing_ok=True)


def test_complex_commands():
    """Test complex commands with pipes and redirects."""
    print("\nTest 6: Complex commands...")

    ctrl = get_controller()

    # Test with loop
    result = ctrl.execute(
        "for i in 1 2 3; do echo $i; done",
        hide_transmission=True
    )
    assert result["exit_code"] == 0
    assert "1" in result["output"]
    assert "2" in result["output"]
    assert "3" in result["output"]
    print("  ✓ Loop command works")

    # Test with pipe
    result = ctrl.execute(
        "echo 'hello world' | wc -w",
        hide_transmission=True
    )
    assert result["exit_code"] == 0
    assert "2" in result["output"]
    print("  ✓ Piped command works")


def test_backward_compatibility():
    """Test that default behavior is unchanged."""
    print("\nTest 7: Backward compatibility...")

    ctrl = get_controller()

    # Not specifying hide_transmission should work as before
    result = ctrl.execute("echo 'backward_compatible'")
    assert result["exit_code"] == 0
    assert "backward_compatible" in result["output"]
    print("  ✓ Default behavior (hide_transmission=False) works")


def test_consecutive_executions():
    """Test multiple consecutive hidden executions."""
    print("\nTest 8: Consecutive hidden executions...")

    ctrl = get_controller()

    for i in range(5):
        result = ctrl.execute(
            f"echo 'iteration_{i}'",
            hide_transmission=True
        )
        assert result["exit_code"] == 0
        assert f"iteration_{i}" in result["output"]

    print("  ✓ Five consecutive hidden executions successful")


def main():
    """Run all manual tests."""
    print("=" * 70)
    print("Manual Integration Tests for hide_transmission Feature")
    print("=" * 70)

    tests = [
        test_basic_execution,
        test_single_execution_not_double,
        test_exit_codes,
        test_terminal_echo_restoration,
        test_file_operations,
        test_complex_commands,
        test_backward_compatibility,
        test_consecutive_executions,
    ]

    failed = []

    for test in tests:
        try:
            result = test()
            if result is False:
                failed.append(test.__name__)
        except Exception as e:
            print(f"  ✗ FAILED with exception: {e}")
            failed.append(test.__name__)

    print("\n" + "=" * 70)
    if not failed:
        print("✓ All tests passed!")
        print("=" * 70)
        return 0
    else:
        print(f"✗ {len(failed)} test(s) failed:")
        for name in failed:
            print(f"  - {name}")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
