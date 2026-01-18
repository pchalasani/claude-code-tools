"""Tests for tmux_cli_controller."""
from unittest.mock import patch, MagicMock, call
import time

import pytest

from claude_code_tools.tmux_cli_controller import TmuxCLIController


class TestFormatPaneIdentifier:
    """Tests for format_pane_identifier method."""

    def test_empty_pane_id_returns_empty(self):
        """Empty pane ID returns empty string."""
        controller = TmuxCLIController()
        result = controller.format_pane_identifier("")
        assert result == ""

    def test_none_pane_id_returns_none(self):
        """None pane ID returns None."""
        controller = TmuxCLIController()
        result = controller.format_pane_identifier(None)
        assert result is None

    @patch.object(TmuxCLIController, '_run_tmux_command')
    def test_empty_outputs_fallback_to_pane_id(self, mock_run):
        """When tmux returns empty outputs, fallback to pane_id."""
        # Simulate tmux returning code 0 but empty outputs (the bug scenario)
        mock_run.return_value = ("", 0)

        controller = TmuxCLIController()
        result = controller.format_pane_identifier("%123")

        # Should fallback to the original pane_id, not return ":."
        assert result == "%123"

    @patch.object(TmuxCLIController, '_run_tmux_command')
    def test_partial_empty_outputs_fallback_to_pane_id(self, mock_run):
        """When some tmux outputs are empty, fallback to pane_id."""
        # First call returns session name, second returns empty, third returns pane
        mock_run.side_effect = [
            ("mysession", 0),
            ("", 0),  # Empty window index
            ("2", 0)
        ]

        controller = TmuxCLIController()
        result = controller.format_pane_identifier("%123")

        # Should fallback to the original pane_id
        assert result == "%123"

    @patch.object(TmuxCLIController, '_run_tmux_command')
    def test_valid_outputs_format_correctly(self, mock_run):
        """When all outputs are valid, format correctly."""
        mock_run.side_effect = [
            ("mysession", 0),
            ("1", 0),
            ("2", 0)
        ]

        controller = TmuxCLIController()
        result = controller.format_pane_identifier("%123")

        assert result == "mysession:1.2"

    @patch.object(TmuxCLIController, '_run_tmux_command')
    def test_error_code_fallback_to_pane_id(self, mock_run):
        """When tmux returns error code, fallback to pane_id."""
        mock_run.return_value = ("", 1)

        controller = TmuxCLIController()
        result = controller.format_pane_identifier("%123")

        assert result == "%123"


class TestCreatePane:
    """Tests for create_pane method."""

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_empty_output_returns_none(self, mock_window, mock_run):
        """When split-window returns empty output, return None."""
        mock_window.return_value = "@1"
        mock_run.return_value = ("", 0)  # Empty output with code 0

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result is None

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_invalid_pane_id_returns_none(self, mock_window, mock_run):
        """When split-window returns invalid pane ID, return None."""
        mock_window.return_value = "@1"
        mock_run.return_value = ("invalid", 0)  # Invalid pane ID format

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result is None

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_valid_pane_id_returned(self, mock_window, mock_run):
        """When split-window returns valid pane ID, return it."""
        mock_window.return_value = "@1"
        mock_run.return_value = ("%123", 0)

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result == "%123"
        assert controller.target_pane == "%123"

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_error_code_returns_none(self, mock_window, mock_run):
        """When split-window fails, return None."""
        mock_window.return_value = "@1"
        mock_run.return_value = ("%123", 1)  # Error code

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result is None

class TestExecute:
    """Tests for execute method."""

    @patch.object(TmuxCLIController, 'capture_pane')
    @patch.object(TmuxCLIController, 'send_keys')
    def test_execute_successful_command(self, mock_send, mock_capture):
        """Execute returns output and exit code for successful command."""
        # Simulate captured output with markers
        mock_capture.return_value = """__TMUX_EXEC_START_12345__
hello world
__TMUX_EXEC_END_12345__:0"""

        controller = TmuxCLIController()
        controller.target_pane = "%1"

        result = controller.execute("echo 'hello world'", timeout=5)

        assert result["output"] == "hello world"
        assert result["exit_code"] == 0
        # Should have called send_keys with wrapped command
        assert mock_send.called

    @patch.object(TmuxCLIController, 'capture_pane')
    @patch.object(TmuxCLIController, 'send_keys')
    def test_execute_failed_command(self, mock_send, mock_capture):
        """Execute returns non-zero exit code for failed command."""
        mock_capture.return_value = """__TMUX_EXEC_START_12345__
ls: cannot access '/nonexistent': No such file or directory
__TMUX_EXEC_END_12345__:2"""

        controller = TmuxCLIController()
        controller.target_pane = "%1"

        result = controller.execute("ls /nonexistent", timeout=5)

        assert "No such file or directory" in result["output"]
        assert result["exit_code"] == 2

    @patch('time.sleep')  # Speed up test
    @patch.object(TmuxCLIController, 'capture_pane')
    @patch.object(TmuxCLIController, 'send_keys')
    def test_execute_timeout(self, mock_send, mock_capture, mock_sleep):
        """Execute returns exit_code=-1 on timeout."""
        # Simulate output without end marker (command still running)
        mock_capture.return_value = """__TMUX_EXEC_START_12345__
partial output..."""

        controller = TmuxCLIController()
        controller.target_pane = "%1"

        result = controller.execute("sleep 100", timeout=1)

        assert result["exit_code"] == -1

    def test_execute_requires_target_pane(self):
        """Execute raises ValueError if no target pane specified."""
        controller = TmuxCLIController()

        with pytest.raises(ValueError, match="No target pane specified"):
            controller.execute("pwd")
