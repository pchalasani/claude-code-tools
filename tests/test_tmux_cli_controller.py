"""Tests for tmux_cli_controller."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_code_tools import tmux_execution_helpers
from claude_code_tools.tmux_cli_controller import CLI, TmuxCLIController
from claude_code_tools.tmux_remote_controller import RemoteTmuxController


class TestLazyRemoteSession:
    """Remote commands must not create a managed session except for launch."""

    def test_status_outside_tmux_does_not_create_session(self, monkeypatch, capsys):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux") as run_tmux:
            CLI().status()
        assert "Remote session: remote-cli-session" in capsys.readouterr().out
        run_tmux.assert_not_called()

    def test_remote_lists_do_not_create_session(self, monkeypatch, capsys):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux", return_value=("", 1)) as run_tmux:
            cli = CLI()
            cli.list_panes()
            cli.list_windows()
        assert "[]" in capsys.readouterr().out
        assert [call.args[0][0] for call in run_tmux.call_args_list] == [
            "list-windows", "list-windows"
        ]

    @pytest.mark.parametrize("target", ("%12", "other:2.0"))
    def test_full_target_operations_do_not_create_managed_session(self, target, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        capture = "__START__\nhello\n__END__:0"

        def run_tmux(args, **_kwargs):
            if args[0] == "capture-pane":
                return capture, 0
            return "", 0

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux) as mocked_run:
            with patch.object(tmux_execution_helpers, "generate_execution_markers", return_value=("__START__", "__END__")):
                with patch("claude_code_tools.tmux_remote_controller.time.sleep"):
                    cli = CLI()
                    cli.send("hello", pane=target, delay_enter=False)
                    assert cli.capture(pane=target) == capture
                    assert cli.wait_idle(pane=target, idle_time=0, timeout=1)
                    assert cli.execute("echo hello", pane=target)["exit_code"] == 0

        commands = [call.args[0] for call in mocked_run.call_args_list]
        assert commands
        assert all(command[0] not in ("has-session", "new-session") for command in commands)
        assert all(command[command.index("-t") + 1] == target for command in commands)

    def test_launch_creates_missing_session_once_and_continues(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        exists = False

        def run_tmux(args, **_kwargs):
            nonlocal exists
            if args[0] == "has-session":
                return ("", 0) if exists else ("can't find session: remote-cli-session", 1)
            if args[0] == "new-session":
                exists = True
                return "remote-cli-session", 0
            if args[0] == "new-window":
                return "remote-cli-session:1|@1", 0
            if args[0] == "set-option":
                return "", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux) as mocked_run:
            cli = CLI()
            assert cli.launch("zsh") == "remote-cli-session:1"
            assert cli.launch("python3") == "remote-cli-session:1"

        commands = [call.args[0] for call in mocked_run.call_args_list]
        assert [command[0] for command in commands] == [
            "has-session", "new-session", "new-window", "set-option",
            "has-session", "new-window", "set-option"
        ]
        assert commands[2][-1] == "zsh"
        assert commands[5][-1] == "python3"
        assert commands[3] == ['set-option', '-w', '-t', '@1', '@tmux_cli_ready', '@1']

    def test_launch_reuses_existing_managed_session(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)

        def run_tmux(args, **_kwargs):
            if args[0] == "has-session":
                return "", 0
            if args[0] == "new-window":
                return "remote-cli-session:1|@1", 0
            if args[0] == "set-option":
                return "", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux) as mocked_run:
            assert CLI().launch("zsh") == "remote-cli-session:1"

        assert [call.args[0][0] for call in mocked_run.call_args_list] == [
            "has-session", "new-window", "set-option"
        ]

    def test_existing_session_supports_default_and_numeric_targets(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)

        def run_tmux(args, **_kwargs):
            if args[0] == "display-message":
                return "@0|@0", 0
            if args[0] == "has-session":
                return "", 0
            if args[0] == "capture-pane":
                return "ready", 0
            return "", 0

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux) as mocked_run:
            cli = CLI()
            assert cli.capture() == "ready"
            cli.send("hello", pane=2, delay_enter=False)

        commands = [call.args[0] for call in mocked_run.call_args_list]
        assert [command[0] for command in commands] == [
            "display-message", "capture-pane", "has-session", "send-keys"
        ]
        assert commands[1][2] == "@0"
        assert commands[3][2] == "remote-cli-session:2"

    @pytest.mark.parametrize("pane", (0, "0"))
    def test_zero_window_index_is_valid(self, monkeypatch, pane):
        monkeypatch.delenv("TMUX", raising=False)

        def run_tmux(args, **_kwargs):
            if args[0] == "has-session":
                return "", 0
            if args[0] == "capture-pane":
                return "zero window", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux) as mocked_run:
            assert CLI().capture(pane=pane) == "zero window"
        assert mocked_run.call_args_list[-1].args[0] == [
            "capture-pane", "-t", "remote-cli-session:0", "-p"
        ]

    @pytest.mark.parametrize("pane", (True, False))
    def test_boolean_window_index_is_rejected_before_tmux(self, monkeypatch, pane):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux") as mocked_run:
            with pytest.raises(ValueError, match="Boolean --pane"):
                CLI().capture(pane=pane)
        mocked_run.assert_not_called()

    def test_attach_existing_session_only_attaches(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux", return_value=("", 0)) as mocked_run:
            with patch("claude_code_tools.tmux_remote_controller.subprocess.run") as mocked_subprocess:
                CLI().attach()

        assert [call.args[0][0] for call in mocked_run.call_args_list] == ["has-session"]
        mocked_subprocess.assert_called_once_with(
            ["tmux", "attach-session", "-t", "remote-cli-session"]
        )

    def test_missing_default_target_fails_without_creating_session(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux", return_value=("can't find session: remote-cli-session", 1)) as mocked_run:
            cli = CLI()
            with pytest.raises(ValueError, match="No target pane/window specified.*managed session"):
                cli.capture()
            with pytest.raises(ValueError, match="Managed session .* does not exist"):
                cli.send("hello", pane=2, delay_enter=False)
            with pytest.raises(ValueError, match="Managed session .* does not exist"):
                cli.attach()

        assert [call.args[0][0] for call in mocked_run.call_args_list] == [
            "display-message", "has-session", "has-session"
        ]

    @pytest.mark.parametrize("failed_action", ("new-session", "new-window"))
    def test_launch_failure_keeps_error_and_restores_target(self, monkeypatch, capsys, failed_action):
        monkeypatch.delenv("TMUX", raising=False)

        def run_tmux(args, **_kwargs):
            action = args[0]
            if action == "has-session":
                return "can't find session: remote-cli-session", 1
            if action == failed_action:
                return f"{action}: permission denied", 17
            if action == "new-session":
                return "remote-cli-session", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux) as mocked_run:
            cli = CLI()
            with pytest.raises(RuntimeError, match=f"tmux {failed_action} failed \\(exit 17\\).*permission denied"):
                cli.launch("zsh")
            assert cli.controller.target_window is None

        assert "Launched" not in capsys.readouterr().out
        expected = ["has-session", "new-session"]
        if failed_action == "new-window":
            expected.append("new-window")
        assert [call.args[0][0] for call in mocked_run.call_args_list] == expected

    def test_failed_window_on_existing_session_preserves_prior_target(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)

        def run_tmux(args, **_kwargs):
            if args[0] == "has-session":
                return "", 0
            if args[0] == "new-window":
                return "cannot create window", 4
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            cli = CLI()
            cli.controller.target_window = "remote-cli-session:3"
            with pytest.raises(RuntimeError, match="cannot create window"):
                cli.launch("zsh")
            assert cli.controller.target_window == "remote-cli-session:3"

    @pytest.mark.parametrize("failed_action", ("new-session", "new-window"))
    def test_launch_empty_success_output_is_not_reported_as_launched(
        self, monkeypatch, capsys, failed_action
    ):
        monkeypatch.delenv("TMUX", raising=False)

        def run_tmux(args, **_kwargs):
            if args[0] == "has-session":
                return "can't find session: remote-cli-session", 1
            if args[0] == failed_action:
                return "", 0
            if args[0] == "new-session":
                return "remote-cli-session", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            cli = CLI()
            with pytest.raises(RuntimeError, match=f"tmux {failed_action} failed \\(exit 0\\)"):
                cli.launch("zsh")
            assert cli.controller.target_window is None
        assert "Launched" not in capsys.readouterr().out

    @pytest.mark.parametrize("method,expected_action", (
        ("default", "display-message"),
        ("numeric", "has-session"),
        ("attach", "has-session"),
        ("launch", "has-session"),
    ))
    @pytest.mark.parametrize("error", ("permission denied", "server connection refused", ""))
    def test_infrastructure_error_is_not_session_absence(
        self, monkeypatch, method, expected_action, error
    ):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux", return_value=(error, 17)) as mocked_run:
            cli = CLI()
            with pytest.raises(RuntimeError, match=f"tmux {expected_action} failed \\(exit 17\\)"):
                if method == "default":
                    cli.capture()
                elif method == "numeric":
                    cli.send("hello", pane=2, delay_enter=False)
                elif method == "attach":
                    cli.attach()
                else:
                    cli.launch("zsh")
            assert cli.controller.target_window is None
        assert [call.args[0][0] for call in mocked_run.call_args_list] == [expected_action]

    @pytest.mark.parametrize("error", (FileNotFoundError("tmux missing"), PermissionError("tmux denied")))
    def test_tmux_tool_error_is_reported(self, monkeypatch, error):
        monkeypatch.delenv("TMUX", raising=False)
        with patch("claude_code_tools.tmux_remote_controller.subprocess.run", side_effect=error):
            with pytest.raises(RuntimeError, match=f"Could not run tmux has-session: {error}"):
                CLI().launch("zsh")

    @pytest.mark.parametrize("method", ("default", "numeric", "attach"))
    def test_no_server_is_missing_session_without_creation(self, monkeypatch, method):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux", return_value=("no server running on /tmp/test", 1)) as mocked_run:
            cli = CLI()
            with pytest.raises(ValueError, match="managed session|Managed session"):
                if method == "default":
                    cli.capture()
                elif method == "numeric":
                    cli.send("hello", pane=2, delay_enter=False)
                else:
                    cli.attach()
        assert len(mocked_run.call_args_list) == 1

    def test_failed_tmux_stderr_is_preserved(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        completed = subprocess.CompletedProcess(
            args=["tmux", "has-session"], returncode=13, stdout="", stderr="socket permission denied\n"
        )
        with patch("claude_code_tools.tmux_remote_controller.subprocess.run", return_value=completed):
            with pytest.raises(RuntimeError, match="socket permission denied"):
                CLI().launch("zsh")

    def test_failed_first_window_stays_unsafe_across_controllers(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        calls = []
        state = {"session": False, "active": "@0", "ready": set(), "fail_window": True}

        def run_tmux(args, **_kwargs):
            action = args[0]
            calls.append(action)
            if action == "has-session":
                return ("", 0) if state["session"] else ("can't find session: remote-cli-session", 1)
            if action == "new-session":
                state["session"] = True
                return "remote-cli-session", 0
            if action == "new-window":
                if state["fail_window"]:
                    return "window permission denied", 18
                state["active"] = "@1"
                return "remote-cli-session:1|@1", 0
            if action == "set-option":
                state["ready"].add(args[3])
                return "", 0
            if action == "display-message":
                active = state["active"]
                return f"{active}|{active if active in state['ready'] else ''}", 0
            if action == "capture-pane":
                return "CONCURRENT-WINDOW", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            with pytest.raises(RuntimeError, match="window permission denied"):
                CLI().launch("zsh")
            fresh_cli = CLI()
            with pytest.raises(ValueError, match="no successfully launched active window"):
                fresh_cli.capture()
            assert "capture-pane" not in calls
            assert "kill-session" not in calls and "kill-window" not in calls

            state["fail_window"] = False
            assert CLI().launch("python3") == "remote-cli-session:1"
            assert CLI().capture() == "CONCURRENT-WINDOW"
            assert calls[-2:] == ["display-message", "capture-pane"]
            assert state["ready"] == {"@1"}

    def test_duplicate_session_race_recovers_only_when_session_exists(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        calls = []

        def run_tmux(args, **_kwargs):
            calls.append(args[0])
            if args[0] == "has-session":
                return ("no server running on /tmp/fake", 1) if calls.count("has-session") == 1 else ("", 0)
            if args[0] == "new-session":
                return "duplicate session: remote-cli-session", 1
            if args[0] == "new-window":
                return "remote-cli-session:1|@1", 0
            if args[0] == "set-option":
                return "", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            assert CLI().launch("zsh") == "remote-cli-session:1"
        assert calls == ["has-session", "new-session", "has-session", "new-window", "set-option"]

    @pytest.mark.parametrize("error", ("permission denied", "duplicate session: remote-cli-session"))
    def test_failed_create_is_not_mistaken_for_race(self, monkeypatch, error):
        monkeypatch.delenv("TMUX", raising=False)
        calls = []

        def run_tmux(args, **_kwargs):
            calls.append(args[0])
            if args[0] == "has-session":
                return "can't find session: remote-cli-session", 1
            if args[0] == "new-session":
                return error, 1
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            with pytest.raises(RuntimeError, match="tmux new-session failed"):
                CLI().launch("zsh")
        assert calls == (["has-session", "new-session", "has-session"] if error.startswith("duplicate") else ["has-session", "new-session"])

    def test_empty_successful_display_message_is_protocol_error(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        with patch.object(RemoteTmuxController, "_run_tmux", return_value=("", 0)):
            with pytest.raises(RuntimeError, match=r"tmux display-message failed \(exit 0\)"):
                CLI().capture()

    def test_failed_ready_mark_keeps_new_window_out_of_default_path(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        actions = []

        def run_tmux(args, **_kwargs):
            actions.append(args[0])
            if args[0] == "has-session":
                return "", 0
            if args[0] == "new-window":
                return "remote-cli-session:1|@1", 0
            if args[0] == "set-option":
                return "mark permission denied", 13
            if args[0] == "display-message":
                return "@1|", 0
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            with pytest.raises(RuntimeError, match="tmux set-option failed.*mark permission denied.*created window @1; command may have started"):
                CLI().launch("zsh")
            with pytest.raises(ValueError, match="no successfully launched active window"):
                CLI().capture()
        assert actions == ["has-session", "new-window", "set-option", "display-message"]

    @pytest.mark.parametrize("diagnostic", ("no such window: @2", "can't find window: @2"))
    def test_short_lived_window_reports_launch_without_caching_or_retrying(
        self, monkeypatch, capsys, diagnostic
    ):
        monkeypatch.delenv("TMUX", raising=False)
        calls = []

        def run_tmux(args, **_kwargs):
            calls.append(args)
            if args[0] == "has-session":
                return "", 0
            if args[0] == "new-window":
                return "remote-cli-session:2|@2", 0
            if args[0] == "set-option":
                return diagnostic, 1
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            cli = CLI()
            cli.controller.target_window = "@1"
            with pytest.raises(RuntimeError, match=r"Command was launched in window @2.*disappeared before its ready mark.*do not retry automatically"):
                cli.launch("true")
            assert cli.controller.target_window == "@1"
        assert [call[0] for call in calls] == ["has-session", "new-window", "set-option"]
        assert calls[1][-1] == "true"
        assert "Launched 'true' in window" not in capsys.readouterr().out

    @pytest.mark.parametrize("diagnostic", ("mark permission denied", "no such window: @99"))
    def test_other_mark_failure_exposes_created_id_without_cleanup(self, monkeypatch, diagnostic):
        monkeypatch.delenv("TMUX", raising=False)
        calls = []

        def run_tmux(args, **_kwargs):
            calls.append(args[0])
            if args[0] == "has-session":
                return "", 0
            if args[0] == "new-window":
                return "remote-cli-session:2|@2", 0
            if args[0] == "set-option":
                return diagnostic, 13
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            cli = CLI()
            with pytest.raises(RuntimeError) as caught:
                cli.launch("zsh")
            assert cli.controller.target_window is None
        message = str(caught.value)
        assert "tmux set-option failed (exit 13)" in message
        assert diagnostic in message
        assert "created window @2; command may have started" in message
        assert calls == ["has-session", "new-window", "set-option"]

    def test_mark_transport_exception_exposes_created_id(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        calls = []

        def run_tmux(args, **_kwargs):
            calls.append(args[0])
            if args[0] == "has-session":
                return "", 0
            if args[0] == "new-window":
                return "remote-cli-session:2|@2", 0
            if args[0] == "set-option":
                raise RuntimeError("Could not run tmux set-option: permission denied")
            raise AssertionError(args)

        with patch.object(RemoteTmuxController, "_run_tmux", side_effect=run_tmux):
            with pytest.raises(RuntimeError, match="created window @2.*command may have started.*permission denied"):
                CLI().launch("zsh")
        assert calls == ["has-session", "new-window", "set-option"]

    def test_plugin_execute_example_has_remote_target(self):
        readme = (Path(__file__).parents[1] / "plugins/tmux-cli/README.md").read_text()
        assert 'tmux-cli execute "pytest tests/" --pane=other:2.0 --timeout 60' in readme


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


class TestRemoteFireFailures:
    """Exercise Fire with an isolated executable that cannot contact live tmux."""

    @pytest.fixture
    def fake_tmux_env(self, tmp_path):
        fake_tmux = tmp_path / "tmux"
        fake_tmux.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$1\" >> \"$FAKE_TMUX_LOG\"\n"
            "case \"$1\" in\n"
            "  has-session)\n"
            "    if [ \"$FAKE_TMUX_MODE\" = permission ]; then echo 'socket permission denied' >&2; exit 13; fi\n"
            "    if [ \"$FAKE_TMUX_MODE\" = server_error ]; then echo 'server connection refused' >&2; exit 14; fi\n"
            "    if [ -f \"$FAKE_TMUX_STATE\" ]; then exit 0; fi\n"
            "    echo 'no server running on /tmp/fake-tmux' >&2; exit 1;;\n"
            "  display-message)\n"
            "    if [ -f \"$FAKE_TMUX_STATE\" ]; then echo '@0|'; exit 0; fi\n"
            "    echo \"can't find session: remote-cli-session\" >&2; exit 1;;\n"
            "  new-session)\n"
            "    if [ \"$FAKE_TMUX_MODE\" = new_session_fail ]; then echo 'session permission denied' >&2; exit 17; fi\n"
            "    : > \"$FAKE_TMUX_STATE\"\n"
            "    echo remote-cli-session; exit 0;;\n"
            "  new-window)\n"
            "    if [ \"$FAKE_TMUX_MODE\" = new_window_fail ]; then echo 'window permission denied' >&2; exit 18; fi\n"
            "    echo 'remote-cli-session:1|@1'; exit 0;;\n"
            "  set-option)\n"
            "    if [ \"$FAKE_TMUX_MODE\" = short_lived ]; then echo 'no such window: @1' >&2; exit 1; fi\n"
            "    exit 0;;\n"
            "  capture-pane) echo ok; exit 0;;\n"
            "esac\n"
            "exit 99\n"
        )
        fake_tmux.chmod(0o755)
        env = os.environ.copy()
        env.pop("TMUX", None)
        env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
        env["FAKE_TMUX_LOG"] = str(tmp_path / "tmux.log")
        env["FAKE_TMUX_STATE"] = str(tmp_path / "tmux.state")
        env["PYTHONPATH"] = str(Path(__file__).parents[1])
        return env, tmp_path / "tmux.log"

    def run_cli(self, env, *args):
        return subprocess.run(
            [sys.executable, "-m", "claude_code_tools.tmux_cli_controller", *args],
            env=env, capture_output=True, text=True, timeout=5,
        )

    @pytest.mark.parametrize("mode,error,commands", (
        ("new_session_fail", "session permission denied", ["has-session", "new-session"]),
        ("new_window_fail", "window permission denied", ["has-session", "new-session", "new-window"]),
        ("permission", "socket permission denied", ["has-session"]),
        ("server_error", "server connection refused", ["has-session"]),
    ))
    def test_launch_failure_exits_nonzero(self, fake_tmux_env, mode, error, commands):
        env, log = fake_tmux_env
        env["FAKE_TMUX_MODE"] = mode
        result = self.run_cli(env, "launch", "zsh")
        assert result.returncode != 0
        assert error in result.stderr
        assert "Launched" not in result.stdout
        assert log.read_text().splitlines() == commands

    def test_first_launch_without_server_succeeds(self, fake_tmux_env):
        env, log = fake_tmux_env
        env["FAKE_TMUX_MODE"] = "normal"
        result = self.run_cli(env, "launch", "zsh")
        assert result.returncode == 0, result.stderr
        assert "Launched 'zsh' in window: remote-cli-session:1" in result.stdout
        assert log.read_text().splitlines() == ["has-session", "new-session", "new-window", "set-option"]

    def test_short_lived_launch_reports_created_window(self, fake_tmux_env):
        env, log = fake_tmux_env
        env["FAKE_TMUX_MODE"] = "short_lived"
        result = self.run_cli(env, "launch", "true")
        assert result.returncode != 0
        assert "Command was launched in window @1" in result.stderr
        assert "do not retry automatically" in result.stderr
        assert "Launched 'true' in window" not in result.stdout
        assert log.read_text().splitlines() == ["has-session", "new-session", "new-window", "set-option"]

    @pytest.mark.parametrize("value", ("True", "False"))
    def test_fire_boolean_window_index_is_rejected(self, fake_tmux_env, value):
        env, log = fake_tmux_env
        result = self.run_cli(env, "capture", f"--pane={value}")
        assert result.returncode != 0
        assert "Boolean --pane is not a window index" in result.stderr
        assert not log.exists()

    def test_fire_zero_window_index_remains_valid(self, fake_tmux_env):
        env, log = fake_tmux_env
        Path(env["FAKE_TMUX_STATE"]).touch()
        result = self.run_cli(env, "capture", "--pane=0")
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout
        assert log.read_text().splitlines() == ["has-session", "capture-pane"]

    def test_failed_launch_does_not_expose_bootstrap_in_next_process(self, fake_tmux_env):
        env, log = fake_tmux_env
        env["FAKE_TMUX_MODE"] = "new_window_fail"
        assert self.run_cli(env, "launch", "zsh").returncode != 0
        result = self.run_cli(env, "capture")
        assert result.returncode != 0
        assert "no successfully launched active window" in result.stderr
        assert log.read_text().splitlines() == ["has-session", "new-session", "new-window", "display-message"]

    @pytest.mark.parametrize("args", (("kill",), ("kill", "--pane=2")))
    def test_kill_missing_session_exits_nonzero(self, fake_tmux_env, args):
        env, log = fake_tmux_env
        result = self.run_cli(env, *args)
        assert result.returncode != 0
        assert "does not exist" in result.stderr
        assert "does not exist" not in result.stdout
        assert log.read_text().splitlines() == (["display-message"] if len(args) == 1 else ["has-session"])

    @pytest.mark.parametrize("args,commands", (
        (("status",), []),
        (("list_panes",), ["list-windows"]),
        (("capture", "--pane=other:2.0"), ["capture-pane"]),
    ))
    def test_noncreating_paths(self, fake_tmux_env, args, commands):
        env, log = fake_tmux_env
        result = self.run_cli(env, *args)
        assert result.returncode == 0, result.stderr
        actual_commands = log.read_text().splitlines() if log.exists() else []
        assert actual_commands == commands


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
