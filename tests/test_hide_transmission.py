"""
Tests for the hide_transmission parameter in execute() method.

These tests verify that:
1. Commands execute correctly with hide_transmission=True
2. Terminal state is restored even on errors
3. Commands with side effects only execute once
4. Both visible and hidden modes produce identical results
"""

import pytest
import tempfile
import time
from pathlib import Path
from claude_code_tools.tmux_cli_controller import TmuxCLIController


@pytest.fixture
def controller():
    """Create a TmuxCLIController with a test pane."""
    ctrl = TmuxCLIController()

    # Create a new session for testing
    session_name = f"test_hide_transmission_{int(time.time())}"
    ctrl.create_session(session_name, detached=True)
    ctrl.target_pane = f"{session_name}:0.0"

    yield ctrl

    # Cleanup
    try:
        ctrl.kill_session(session_name)
    except Exception:
        pass


class TestHideTransmission:
    """Test suite for hide_transmission parameter."""

    def test_basic_command_hidden(self, controller):
        """Test that basic commands work with hide_transmission=True."""
        result = controller.execute("echo 'test'", hide_transmission=True)

        assert result["exit_code"] == 0
        assert "test" in result["output"]

    def test_basic_command_visible(self, controller):
        """Test that basic commands work with hide_transmission=False (default)."""
        result = controller.execute("echo 'test'", hide_transmission=False)

        assert result["exit_code"] == 0
        assert "test" in result["output"]

    def test_output_identical_both_modes(self, controller):
        """Test that output is identical in both visible and hidden modes."""
        # Run same command in both modes
        result_visible = controller.execute("echo 'identical'", hide_transmission=False)
        result_hidden = controller.execute("echo 'identical'", hide_transmission=True)

        # Clean output should be the same
        assert "identical" in result_visible["output"]
        assert "identical" in result_hidden["output"]
        assert result_visible["exit_code"] == result_hidden["exit_code"]

    def test_single_execution_not_double(self, controller):
        """Critical test: Verify commands execute only once, not twice."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            test_file = f.name

        try:
            # Write to file with hidden transmission
            result = controller.execute(
                f"echo 'single' >> {test_file}",
                hide_transmission=True
            )
            assert result["exit_code"] == 0

            # Read file and verify only one line
            content = Path(test_file).read_text()
            lines = [line for line in content.strip().split('\n') if line]

            assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {lines}"
            assert lines[0] == "single"
        finally:
            Path(test_file).unlink(missing_ok=True)

    def test_exit_code_preserved_hidden(self, controller):
        """Test that exit codes are correctly captured with hide_transmission."""
        # Success command
        result = controller.execute("true", hide_transmission=True)
        assert result["exit_code"] == 0

        # Failure command
        result = controller.execute("false", hide_transmission=True)
        assert result["exit_code"] == 1

    def test_exit_code_preserved_visible(self, controller):
        """Test that exit codes work the same in visible mode."""
        # Success command
        result = controller.execute("true", hide_transmission=False)
        assert result["exit_code"] == 0

        # Failure command
        result = controller.execute("false", hide_transmission=False)
        assert result["exit_code"] == 1

    def test_complex_command_hidden(self, controller):
        """Test that complex commands work with hide_transmission."""
        result = controller.execute(
            "for i in 1 2 3; do echo $i; done",
            hide_transmission=True
        )

        assert result["exit_code"] == 0
        assert "1" in result["output"]
        assert "2" in result["output"]
        assert "3" in result["output"]

    def test_stderr_captured_hidden(self, controller):
        """Test that stderr is captured with hide_transmission."""
        result = controller.execute(
            "echo 'error message' >&2",
            hide_transmission=True
        )

        assert result["exit_code"] == 0
        assert "error message" in result["output"]

    def test_multiline_output_hidden(self, controller):
        """Test multiline output with hide_transmission."""
        result = controller.execute(
            "printf 'line1\\nline2\\nline3\\n'",
            hide_transmission=True
        )

        assert result["exit_code"] == 0
        assert "line1" in result["output"]
        assert "line2" in result["output"]
        assert "line3" in result["output"]

    def test_special_characters_hidden(self, controller):
        """Test that special characters work with hide_transmission."""
        result = controller.execute(
            "echo 'special: $USER @host !bang'",
            hide_transmission=True
        )

        assert result["exit_code"] == 0
        assert "special:" in result["output"]

    def test_timeout_respected_hidden(self, controller):
        """Test that timeout is respected with hide_transmission."""
        result = controller.execute(
            "sleep 5",
            hide_transmission=True,
            timeout=1
        )

        # Should timeout
        assert result["exit_code"] == -1

    def test_file_operations_single_execution(self, controller):
        """Test file operations to ensure single execution."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            test_file = f.name

        try:
            # Create file with specific content
            result = controller.execute(
                f"echo 'first' > {test_file} && echo 'second' >> {test_file}",
                hide_transmission=True
            )
            assert result["exit_code"] == 0

            # Read and verify
            content = Path(test_file).read_text()
            lines = [line for line in content.strip().split('\n') if line]

            # Should be exactly ["first", "second"], not duplicated
            assert lines == ["first", "second"], f"Got: {lines}"
        finally:
            Path(test_file).unlink(missing_ok=True)

    def test_api_call_simulation_single_execution(self, controller):
        """Simulate API call to ensure it's not duplicated."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
            log_file = f.name

        try:
            # Simulate API call by appending timestamp to file
            result = controller.execute(
                f"date +%s%N >> {log_file}",
                hide_transmission=True
            )
            assert result["exit_code"] == 0

            # Count lines - should be exactly 1
            content = Path(log_file).read_text()
            lines = [line for line in content.strip().split('\n') if line]

            assert len(lines) == 1, f"API 'called' {len(lines)} times instead of 1"
        finally:
            Path(log_file).unlink(missing_ok=True)

    def test_terminal_echo_restored_on_success(self, controller):
        """Test that terminal echo is restored after successful execution."""
        # Execute with hidden transmission
        result = controller.execute("echo 'test'", hide_transmission=True)
        assert result["exit_code"] == 0

        # Verify echo is working by testing with visible execution
        result = controller.execute("echo 'verify_echo'", hide_transmission=False)
        assert result["exit_code"] == 0
        assert "verify_echo" in result["output"]

    def test_terminal_echo_restored_on_failure(self, controller):
        """Test that terminal echo is restored even when command fails."""
        # Execute failing command with hidden transmission
        result = controller.execute("false", hide_transmission=True)
        assert result["exit_code"] == 1

        # Verify echo is still working
        result = controller.execute("echo 'echo_works'", hide_transmission=False)
        assert result["exit_code"] == 0
        assert "echo_works" in result["output"]

    def test_backward_compatibility(self, controller):
        """Test that default behavior (hide_transmission=False) is unchanged."""
        # Not specifying hide_transmission should work as before
        result = controller.execute("echo 'backward_compatible'")

        assert result["exit_code"] == 0
        assert "backward_compatible" in result["output"]

    def test_with_custom_pane(self, controller):
        """Test hide_transmission with custom pane_id."""
        # Create a second pane
        second_pane = controller.split_window(vertical=True)

        try:
            result = controller.execute(
                "echo 'custom_pane'",
                pane_id=second_pane,
                hide_transmission=True
            )

            assert result["exit_code"] == 0
            assert "custom_pane" in result["output"]
        finally:
            controller.kill_pane(second_pane)

    def test_consecutive_hidden_executions(self, controller):
        """Test multiple consecutive executions with hide_transmission."""
        for i in range(3):
            result = controller.execute(
                f"echo 'iteration_{i}'",
                hide_transmission=True
            )

            assert result["exit_code"] == 0
            assert f"iteration_{i}" in result["output"]

    def test_alternating_visible_hidden(self, controller):
        """Test alternating between visible and hidden transmissions."""
        # Hidden
        result = controller.execute("echo 'hidden1'", hide_transmission=True)
        assert result["exit_code"] == 0

        # Visible
        result = controller.execute("echo 'visible'", hide_transmission=False)
        assert result["exit_code"] == 0

        # Hidden again
        result = controller.execute("echo 'hidden2'", hide_transmission=True)
        assert result["exit_code"] == 0


class TestEdgeCases:
    """Edge cases and error conditions."""

    def test_empty_command(self, controller):
        """Test handling of empty command."""
        result = controller.execute("", hide_transmission=True)
        # Should still work, just return immediately
        assert "exit_code" in result

    def test_command_with_quotes(self, controller):
        """Test command with various quote types."""
        result = controller.execute(
            '''echo "double" 'single' `backtick echo nested` ''',
            hide_transmission=True
        )
        assert result["exit_code"] == 0

    def test_very_long_output(self, controller):
        """Test handling of very long output."""
        result = controller.execute(
            "seq 1 1000",
            hide_transmission=True
        )

        assert result["exit_code"] == 0
        assert "1000" in result["output"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
