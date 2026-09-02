"""Tests for tmux_cli_controller."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_code_tools import tmux_execution_helpers
from claude_code_tools.tmux_cli_controller import TmuxCLIController


@pytest.mark.parametrize("plugin", ("msg", "tmux-cli"))
def test_codex_plugin_default_prompt_is_a_nonempty_string(plugin: str) -> None:
    manifest_path = (
        Path(__file__).parents[1]
        / "plugins"
        / plugin
        / ".codex-plugin"
        / "plugin.json"
    )

    default_prompt = json.loads(manifest_path.read_text())["interface"][
        "defaultPrompt"
    ]

    assert isinstance(default_prompt, str)
    assert default_prompt.strip()


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
        mock_run.side_effect = [
            ("", 0),  # list-panes
            ("", 0),  # split-window
        ]

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result is None

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_invalid_pane_id_returns_none(self, mock_window, mock_run):
        """When split-window returns invalid pane ID, return None."""
        mock_window.return_value = "@1"
        mock_run.side_effect = [
            ("", 0),  # list-panes
            ("invalid", 0),  # split-window
        ]

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result is None

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_valid_pane_id_returned(self, mock_window, mock_run):
        """When split-window returns valid pane ID, return it."""
        mock_window.return_value = "@1"
        mock_run.side_effect = [
            ("", 0),  # list-panes
            ("%123", 0),  # split-window
            ("%123", 0),  # display-message verification
        ]

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result == "%123"
        assert controller.target_pane == "%123"
        assert mock_run.call_args_list[0].args[0][0] == "list-panes"
        assert mock_run.call_args_list[1].args[0][0] == "split-window"
        assert mock_run.call_args_list[2].args[0] == [
            "display-message", "-t", "%123", "-p", "#{pane_id}"
        ]

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_error_code_returns_none(self, mock_window, mock_run):
        """When split-window fails, return None."""
        mock_window.return_value = "@1"
        mock_run.side_effect = [
            ("", 0),  # list-panes
            ("%123", 1),  # split-window
        ]

        controller = TmuxCLIController()
        result = controller.create_pane()

        assert result is None


class TestSendKeys:
    """Native tmux failures must propagate to the CLI process."""

    @patch.object(TmuxCLIController, "_run_tmux_command")
    def test_send_keys_raises_when_native_tmux_fails(self, mock_run):
        mock_run.return_value = ("tmux error", 1)

        controller = TmuxCLIController()

        with pytest.raises(RuntimeError, match="tmux send-keys failed"):
            controller.send_keys("hello", pane_id="%1", delay_enter=False)

    @patch("time.sleep")
    @patch.object(TmuxCLIController, "_run_tmux_command")
    def test_delayed_enter_raises_when_native_tmux_fails(
        self, mock_run, _mock_sleep,
    ):
        mock_run.side_effect = [("", 0), ("tmux error", 1)]

        controller = TmuxCLIController()

        with pytest.raises(RuntimeError, match="tmux Enter failed"):
            controller.send_keys(
                "hello", pane_id="%1", delay_enter=0.01, verify_enter=False,
            )

    @patch("time.sleep")
    @patch.object(TmuxCLIController, "capture_pane", return_value="unchanged")
    @patch.object(TmuxCLIController, "_run_tmux_command", return_value=("", 0))
    def test_delayed_enter_raises_after_verification_retries(
        self, mock_run, _mock_capture, _mock_sleep,
    ):
        with pytest.raises(RuntimeError, match="tmux Enter was not accepted"):
            TmuxCLIController().send_keys(
                "hello", pane_id="%1", delay_enter=0.01, max_retries=2,
            )
        assert mock_run.call_count == 3


class TestCLIExitFailures:
    """The installed CLI process exits nonzero when native tmux fails."""

    @pytest.mark.parametrize(
        ("fake_mode", "delay_enter", "expected_error"),
        (
            ("all", "False", "tmux send-keys failed"),
            ("enter", "0.001", "tmux Enter failed"),
        ),
    )
    def test_send_native_failure_exits_nonzero(
        self,
        tmp_path: Path,
        fake_mode: str,
        delay_enter: str,
        expected_error: str,
    ) -> None:
        fake_tmux = tmp_path / "tmux"
        fake_tmux.write_text(
            "#!/bin/sh\n"
            "if [ \"$FAKE_TMUX_MODE\" = all ]; then exit 17; fi\n"
            "case \"$*\" in *Enter) exit 17;; esac\n"
            "exit 0\n"
        )
        fake_tmux.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "FAKE_TMUX_MODE": fake_mode,
                "PATH": f"{tmp_path}{os.pathsep}{env['PATH']}",
                "TMUX": "/tmp/fake-tmux,1,0",
                "TMUX_PANE": "%1",
            }
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_code_tools.tmux_cli_controller",
                "send",
                "hello",
                "--pane=%1",
                f"--delay-enter={delay_enter}",
            ],
            capture_output=True,
            env=env,
            text=True,
            timeout=5,
        )

        assert result.returncode != 0
        assert expected_error in result.stderr


class TestExecute:
    """Tests for execute method."""

    @pytest.fixture(autouse=True)
    def fixed_markers(self, monkeypatch):
        """Pin the per-execution markers so captured-output fixtures match.

        execute() generates a unique marker pair for every call, so a test
        fixture cannot hardcode them without pinning the generator.
        """
        monkeypatch.setattr(
            tmux_execution_helpers,
            "generate_execution_markers",
            lambda: ("__TMUX_EXEC_START_12345__", "__TMUX_EXEC_END_12345__"),
        )

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


class TestListPanes:
    """Tests for list_panes output parsing."""

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_malformed_line_is_skipped(self, mock_window, mock_run):
        """A line without the expected fields is skipped, not indexed into."""
        mock_window.return_value = "@1"
        mock_run.return_value = ("invalid", 0)

        panes = TmuxCLIController().list_panes()

        assert panes == []

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_well_formed_lines_are_parsed(self, mock_window, mock_run):
        """Complete tmux output is parsed into pane dicts."""
        mock_window.return_value = "@1"
        mock_run.return_value = (
            "%1|0|title-a|1|80x24|zsh\n%2|1|title-b|0|80x24|vim", 0
        )

        panes = TmuxCLIController().list_panes()

        assert [p['id'] for p in panes] == ["%1", "%2"]
        assert panes[0]['active'] is True
        assert panes[1]['command'] == "vim"

    @patch.object(TmuxCLIController, '_run_tmux_command')
    @patch.object(TmuxCLIController, 'get_current_window_id')
    def test_malformed_line_does_not_drop_valid_ones(self, mock_window, mock_run):
        """One bad line does not discard the panes around it."""
        mock_window.return_value = "@1"
        mock_run.return_value = ("garbage\n%2|1|title-b|0|80x24|vim", 0)

        panes = TmuxCLIController().list_panes()

        assert [p['id'] for p in panes] == ["%2"]
