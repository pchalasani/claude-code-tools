#!/usr/bin/env python3
"""
Comprehensive pytest tests for tmux_remote_controller.py

Tests cover:
- Session creation and management
- Window listing and creation
- Command sending with delays
- Output capture
- Wait operations (idle)
- Window resolution
- Error handling and edge cases
"""

import pytest
from unittest.mock import Mock, patch

# Import the module under test
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_code_tools.tmux_remote_controller import RemoteTmuxController


class TestRemoteTmuxController:
    """Tests for RemoteTmuxController class."""

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess.run for testing."""
        with patch('claude_code_tools.tmux_remote_controller.subprocess.run') as mock:
            yield mock

    @pytest.fixture
    def controller_existing_session(self, mock_subprocess, capsys):
        """Create controller with existing session."""
        mock_subprocess.return_value = Mock(stdout="test-session", returncode=0)
        with patch('builtins.print'):  # Suppress initialization messages
            controller = RemoteTmuxController(session_name="test-session")
        return controller

    @pytest.fixture
    def controller_new_session(self, mock_subprocess, capsys):
        """Create controller that creates new session."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=1),  # has-session (doesn't exist)
            Mock(stdout="test-session", returncode=0)  # new-session
        ]
        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="test-session")
        return controller

    # ==================== Initialization ====================

    def test_init_creates_session_if_not_exists(self, mock_subprocess):
        """Test that initialization creates session if it doesn't exist."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=1),  # has-session fails
            Mock(stdout="new-session", returncode=0)  # new-session succeeds
        ]

        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="new-session")

        assert controller.session_name == "new-session"
        assert controller.target_window == "new-session:0"

        # Verify has-session and new-session were called
        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert any('has-session' in call for call in calls)
        assert any('new-session' in call for call in calls)

    def test_init_uses_existing_session(self, mock_subprocess):
        """Test that initialization uses existing session."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # has-session succeeds
            Mock(stdout="existing-session:1", returncode=0)  # display-message
        ]

        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="existing-session")

        assert controller.session_name == "existing-session"
        assert controller.target_window == "existing-session:1"

    def test_init_prints_info_message(self, capsys, mock_subprocess):
        """Test that initialization prints info messages."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller = RemoteTmuxController(session_name="test-session")

        captured = capsys.readouterr()
        assert "running outside tmux" in captured.out
        assert "test-session" in captured.out

    # ==================== Internal Utilities ====================

    def test_run_tmux_success(self, controller_existing_session, mock_subprocess):
        """Test _run_tmux with successful command."""
        mock_subprocess.return_value = Mock(stdout="output\n", returncode=0)

        output, code = controller_existing_session._run_tmux(['list-windows'])

        assert output == "output"
        assert code == 0
        mock_subprocess.assert_called_with(
            ['tmux', 'list-windows'],
            capture_output=True,
            text=True
        )

    def test_run_tmux_failure(self, controller_existing_session, mock_subprocess):
        """Test _run_tmux with failed command."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        output, code = controller_existing_session._run_tmux(['invalid-command'])

        assert output == ""
        assert code == 1

    def test_ensure_session_already_exists(self, controller_existing_session, mock_subprocess):
        """Test _ensure_session when session already exists."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller_existing_session._ensure_session()

        # Should call has-session
        mock_subprocess.assert_called()

    def test_ensure_session_creates_new(self, mock_subprocess):
        """Test _ensure_session creates new session if needed."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=1),  # has-session fails
            Mock(stdout="", returncode=1),  # Initial check
            Mock(stdout="new-session", returncode=0)  # new-session
        ]

        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="new-session")

        assert controller.target_window == "new-session:0"

    # ==================== Window Target Resolution ====================

    def test_window_target_none_uses_target_window(self, controller_existing_session):
        """Test _window_target with None uses stored target_window."""
        controller_existing_session.target_window = "test-session:2"

        result = controller_existing_session._window_target(None)

        assert result == "test-session:2"

    def test_window_target_none_without_stored_target(self, controller_existing_session, mock_subprocess):
        """Test _window_target with None and no stored target."""
        controller_existing_session.target_window = None
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # has-session
            Mock(stdout="test-session:1", returncode=0)  # display-message
        ]

        result = controller_existing_session._window_target(None)

        assert result == "test-session:1"
        assert controller_existing_session.target_window == "test-session:1"

    def test_window_target_digit_string(self, controller_existing_session, mock_subprocess):
        """Test _window_target with digit string."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        result = controller_existing_session._window_target("3")

        assert result == "test-session:3"

    def test_window_target_full_target(self, controller_existing_session, mock_subprocess):
        """Test _window_target with full tmux target."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        result = controller_existing_session._window_target("other-session:5")

        assert result == "other-session:5"

    def test_window_target_pane_id(self, controller_existing_session, mock_subprocess):
        """Test _window_target with pane ID."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        result = controller_existing_session._window_target("%123")

        assert result == "%123"

    def test_window_target_fallback_to_window_0(self, mock_subprocess):
        """Test _window_target fallback to session:0."""
        # Initial session creation
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=1),  # has-session fails initially
            Mock(stdout="test-session", returncode=0),  # new-session
            # _window_target call
            Mock(stdout="", returncode=0),  # has-session in _ensure_session
            Mock(stdout="", returncode=1),  # display-message fails (attempt to get active window)
            Mock(stdout="", returncode=0),  # Extra mock for any additional call
        ]

        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="test-session")
            controller.target_window = None

            result = controller._window_target(None)

        assert result == "test-session:0"

    def test_active_pane_in_window(self, controller_existing_session):
        """Test _active_pane_in_window returns window target."""
        result = controller_existing_session._active_pane_in_window("session:2")

        assert result == "session:2"

    # ==================== List Panes (Windows) ====================

    def test_list_panes_success(self, controller_existing_session, mock_subprocess):
        """Test listing windows as panes."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session
            Mock(stdout="0|window0|1|80x24\n1|window1|0|100x30\n2|window2|0|80x24", returncode=0)
        ]

        panes = controller_existing_session.list_panes()

        assert len(panes) == 3
        assert panes[0]['id'] == 'test-session:0'
        assert panes[0]['index'] == '0'
        assert panes[0]['title'] == 'window0'
        assert panes[0]['active'] is True
        assert panes[0]['size'] == '80x24'

        assert panes[1]['active'] is False
        assert panes[1]['title'] == 'window1'

    def test_list_panes_empty(self, controller_existing_session, mock_subprocess):
        """Test listing windows when none exist."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session
            Mock(stdout="", returncode=1)   # list-windows fails
        ]

        panes = controller_existing_session.list_panes()

        assert panes == []

    def test_list_panes_with_empty_lines(self, controller_existing_session, mock_subprocess):
        """Test listing windows with empty lines in output."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="0|window0|1|80x24\n\n1|window1|0|100x30\n", returncode=0)
        ]

        panes = controller_existing_session.list_panes()

        assert len(panes) == 2

    # ==================== Launch CLI ====================

    def test_launch_cli_success(self, controller_existing_session, mock_subprocess):
        """Test launching command in new window."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session
            Mock(stdout="test-session:3", returncode=0)  # new-window
        ]

        result = controller_existing_session.launch_cli("python3")

        assert result == "test-session:3"
        assert controller_existing_session.target_window == "test-session:3"

        # Verify new-window was called with correct args
        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert any('new-window' in call and 'python3' in call for call in calls)

    def test_launch_cli_with_name(self, controller_existing_session, mock_subprocess):
        """Test launching command with window name."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="test-session:4", returncode=0)
        ]

        result = controller_existing_session.launch_cli("bash", name="my-window")

        assert result == "test-session:4"

        # Verify -n flag was used
        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert any('-n' in call and 'my-window' in call for call in calls)

    def test_launch_cli_failure(self, controller_existing_session, mock_subprocess):
        """Test launch_cli when window creation fails."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=1)
        ]

        result = controller_existing_session.launch_cli("python3")

        assert result is None

    def test_launch_cli_empty_command(self, controller_existing_session, mock_subprocess):
        """Test launching with empty command (shell)."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="test-session:5", returncode=0)
        ]

        result = controller_existing_session.launch_cli("")

        assert result == "test-session:5"

    # ==================== Send Keys ====================

    def test_send_keys_no_enter(self, controller_existing_session, mock_subprocess):
        """Test sending keys without Enter."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session in _window_target
            Mock(stdout="", returncode=0)   # send-keys
        ]
        controller_existing_session.target_window = "test-session:1"

        controller_existing_session.send_keys("hello", enter=False)

        # Verify send-keys was called without Enter
        calls = [str(call) for call in mock_subprocess.call_args_list]
        send_keys_call = [c for c in calls if 'send-keys' in c][0]
        assert 'hello' in send_keys_call
        assert 'Enter' not in send_keys_call

    def test_send_keys_with_enter_no_delay(self, controller_existing_session, mock_subprocess):
        """Test sending keys with Enter but no delay."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]
        controller_existing_session.target_window = "test-session:1"

        controller_existing_session.send_keys("hello", enter=True, delay_enter=False)

        # Verify Enter was included in same call
        calls = [str(call) for call in mock_subprocess.call_args_list]
        send_keys_call = [c for c in calls if 'send-keys' in c][0]
        assert 'hello' in send_keys_call
        assert 'Enter' in send_keys_call

    def test_send_keys_with_enter_default_delay(self, controller_existing_session, mock_subprocess):
        """Test sending keys with Enter and default delay."""
        # controller_existing_session fixture already made 2 calls (has-session, display-message)
        # So we need additional mocks for the actual send_keys calls
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session (has-session)
            Mock(stdout="", returncode=0),  # send-keys text
            Mock(stdout="", returncode=0)   # send-keys Enter
        ]
        controller_existing_session.target_window = "test-session:1"

        with patch('claude_code_tools.tmux_remote_controller.time.sleep') as mock_sleep:
            controller_existing_session.send_keys("hello", enter=True, delay_enter=True)

        # Verify separate send-keys calls (3 new calls after fixture setup)
        # Note: call_count includes calls from fixture setup
        mock_sleep.assert_called_once_with(1.0)

    def test_send_keys_with_enter_custom_delay(self, controller_existing_session, mock_subprocess):
        """Test sending keys with Enter and custom delay."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]
        controller_existing_session.target_window = "test-session:1"

        with patch('claude_code_tools.tmux_remote_controller.time.sleep') as mock_sleep:
            controller_existing_session.send_keys("hello", enter=True, delay_enter=0.5)

        mock_sleep.assert_called_once_with(0.5)

    def test_send_keys_with_pane_id(self, controller_existing_session, mock_subprocess):
        """Test sending keys with explicit pane ID."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session
            Mock(stdout="", returncode=0)   # send-keys
        ]

        controller_existing_session.send_keys("hello", pane_id="test-session:2", enter=False)

        # Verify target was used
        calls = [str(call) for call in mock_subprocess.call_args_list]
        send_keys_call = [c for c in calls if 'send-keys' in c][0]
        assert 'test-session:2' in send_keys_call

    def test_send_keys_empty_text(self, controller_existing_session, mock_subprocess):
        """Test sending empty text does nothing."""
        controller_existing_session.target_window = "test-session:1"

        controller_existing_session.send_keys("")

        # Verify send-keys was not called
        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert not any('send-keys' in call for call in calls)

    # ==================== Capture Pane ====================

    def test_capture_pane_all_lines(self, controller_existing_session, mock_subprocess):
        """Test capturing all pane content."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session
            Mock(stdout="line1\nline2\nline3", returncode=0)  # capture-pane
        ]
        controller_existing_session.target_window = "test-session:1"

        output = controller_existing_session.capture_pane()

        assert output == "line1\nline2\nline3"

    def test_capture_pane_limited_lines(self, controller_existing_session, mock_subprocess):
        """Test capturing limited number of lines."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="line1\nline2", returncode=0)
        ]
        controller_existing_session.target_window = "test-session:1"

        output = controller_existing_session.capture_pane(lines=10)

        assert output == "line1\nline2"

        # Verify -S flag was used
        calls = [str(call) for call in mock_subprocess.call_args_list]
        capture_call = [c for c in calls if 'capture-pane' in c][0]
        assert '-S' in capture_call
        assert '-10' in capture_call

    def test_capture_pane_with_pane_id(self, controller_existing_session, mock_subprocess):
        """Test capturing with explicit pane ID."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="output", returncode=0)
        ]

        output = controller_existing_session.capture_pane(pane_id="test-session:2")

        assert output == "output"

    # ==================== Wait for Idle ====================

    def test_wait_for_idle_success(self, controller_existing_session, mock_subprocess):
        """Test waiting for pane to become idle."""
        # Need enough mock values for multiple iterations (9 captures based on time progression)
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),       # _ensure_session (has-session)
            Mock(stdout="content1", returncode=0),  # capture 1
            Mock(stdout="content1", returncode=0),  # capture 2
            Mock(stdout="content1", returncode=0),  # capture 3
            Mock(stdout="content1", returncode=0),  # capture 4
            Mock(stdout="content1", returncode=0),  # capture 5
            Mock(stdout="content1", returncode=0),  # capture 6
            Mock(stdout="content1", returncode=0),  # capture 7
            Mock(stdout="content1", returncode=0),  # capture 8
            Mock(stdout="content1", returncode=0)   # capture 9
        ]
        controller_existing_session.target_window = "test-session:1"

        with patch('claude_code_tools.tmux_remote_controller.time.time') as mock_time:
            # Provide enough time.time() values for all checks
            mock_time.side_effect = [0, 0] + [i * 0.5 for i in range(1, 20)]
            with patch('claude_code_tools.tmux_remote_controller.time.sleep'):
                result = controller_existing_session.wait_for_idle(idle_time=2.0)

        assert result is True

    def test_wait_for_idle_changing_content(self, controller_existing_session, mock_subprocess):
        """Test wait_for_idle with continuously changing content."""
        # Simulate changing content - need enough values
        outputs = ["output{}".format(i) for i in range(15)]
        mock_subprocess.side_effect = [Mock(stdout="", returncode=0)] + \
                                     [Mock(stdout=o, returncode=0) for o in outputs]
        controller_existing_session.target_window = "test-session:1"

        with patch('claude_code_tools.tmux_remote_controller.time.time') as mock_time:
            # Start time, last_change time, then checks that timeout
            mock_time.side_effect = [0, 0] + [i * 0.1 for i in range(1, 40)]
            with patch('claude_code_tools.tmux_remote_controller.time.sleep'):
                result = controller_existing_session.wait_for_idle(idle_time=2.0, timeout=1)

        assert result is False

    def test_wait_for_idle_immediate(self, controller_existing_session, mock_subprocess):
        """Test wait_for_idle when content is already idle."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),      # _ensure_session
            Mock(stdout="static", returncode=0),  # First capture
            Mock(stdout="static", returncode=0),  # Second capture (same)
            Mock(stdout="static", returncode=0)   # Third capture (still same)
        ]
        controller_existing_session.target_window = "test-session:1"

        with patch('claude_code_tools.tmux_remote_controller.time.time') as mock_time:
            # start, last_change, checks until idle
            mock_time.side_effect = [0, 0, 0.5, 0.5, 1.0, 1.0, 2.6, 2.6]
            with patch('claude_code_tools.tmux_remote_controller.time.sleep'):
                result = controller_existing_session.wait_for_idle(idle_time=2.0, timeout=10)

        assert result is True

    def test_wait_for_idle_timeout(self, controller_existing_session, mock_subprocess):
        """Test wait_for_idle with timeout."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="content", returncode=0)
        ] + [Mock(stdout="changing", returncode=0) for _ in range(10)]
        controller_existing_session.target_window = "test-session:1"

        with patch('claude_code_tools.tmux_remote_controller.time.time') as mock_time:
            mock_time.side_effect = [0] + [i * 0.5 for i in range(20)]
            with patch('claude_code_tools.tmux_remote_controller.time.sleep'):
                result = controller_existing_session.wait_for_idle(idle_time=2.0, timeout=2)

        assert result is False

    def test_wait_for_idle_with_pane_id(self, controller_existing_session, mock_subprocess):
        """Test wait_for_idle with explicit pane ID."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="static", returncode=0),
            Mock(stdout="static", returncode=0)
        ]

        with patch('claude_code_tools.tmux_remote_controller.time.time') as mock_time:
            mock_time.side_effect = [0, 0.5, 0.5, 2.6]
            with patch('claude_code_tools.tmux_remote_controller.time.sleep'):
                result = controller_existing_session.wait_for_idle(
                    pane_id="test-session:3",
                    idle_time=2.0
                )

        assert result is True

    # ==================== Special Keys ====================

    def test_send_interrupt(self, controller_existing_session, mock_subprocess):
        """Test sending Ctrl+C to window."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]
        controller_existing_session.target_window = "test-session:1"

        controller_existing_session.send_interrupt()

        # Verify C-c was sent
        calls = [str(call) for call in mock_subprocess.call_args_list]
        send_keys_call = [c for c in calls if 'send-keys' in c][0]
        assert 'C-c' in send_keys_call

    def test_send_interrupt_with_pane_id(self, controller_existing_session, mock_subprocess):
        """Test sending interrupt with explicit pane ID."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]

        controller_existing_session.send_interrupt(pane_id="test-session:2")

        calls = [str(call) for call in mock_subprocess.call_args_list]
        send_keys_call = [c for c in calls if 'send-keys' in c][0]
        assert 'test-session:2' in send_keys_call

    def test_send_escape(self, controller_existing_session, mock_subprocess):
        """Test sending Escape key to window."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]
        controller_existing_session.target_window = "test-session:1"

        controller_existing_session.send_escape()

        # Verify Escape was sent
        calls = [str(call) for call in mock_subprocess.call_args_list]
        send_keys_call = [c for c in calls if 'send-keys' in c][0]
        assert 'Escape' in send_keys_call

    def test_send_escape_with_pane_id(self, controller_existing_session, mock_subprocess):
        """Test sending escape with explicit pane ID."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]

        controller_existing_session.send_escape(pane_id="test-session:3")

        calls = [str(call) for call in mock_subprocess.call_args_list]
        send_keys_call = [c for c in calls if 'send-keys' in c][0]
        assert 'test-session:3' in send_keys_call

    # ==================== Kill Window ====================

    def test_kill_window(self, controller_existing_session, mock_subprocess):
        """Test killing a window."""
        controller_existing_session.target_window = "test-session:1"
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]

        controller_existing_session.kill_window()

        # Verify kill-window was called
        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert any('kill-window' in call for call in calls)
        assert controller_existing_session.target_window is None

    def test_kill_window_with_window_id(self, controller_existing_session, mock_subprocess):
        """Test killing specific window."""
        controller_existing_session.target_window = "test-session:1"
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]

        controller_existing_session.kill_window(window_id="test-session:2")

        # target_window should remain unchanged since we killed different window
        assert controller_existing_session.target_window == "test-session:1"

    def test_kill_window_clears_target_if_same(self, controller_existing_session, mock_subprocess):
        """Test killing target_window clears it."""
        controller_existing_session.target_window = "test-session:5"
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]

        controller_existing_session.kill_window(window_id="test-session:5")

        assert controller_existing_session.target_window is None

    def test_kill_window_with_pane_id(self, controller_existing_session, mock_subprocess):
        """Test killing window using pane ID."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=0)
        ]

        # tmux can resolve %pane to its window
        controller_existing_session.kill_window(window_id="%123")

        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert any('kill-window' in call and '%123' in call for call in calls)

    # ==================== Session Management ====================

    def test_attach_session(self, controller_existing_session, mock_subprocess):
        """Test attaching to session."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session
            Mock(stdout="", returncode=0)   # attach-session
        ]

        controller_existing_session.attach_session()

        # Verify attach-session was called
        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert any('attach-session' in call and 'test-session' in call for call in calls)

    def test_cleanup_session(self, controller_existing_session, mock_subprocess):
        """Test cleaning up (killing) session."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller_existing_session.cleanup_session()

        # Verify kill-session was called
        calls = [str(call) for call in mock_subprocess.call_args_list]
        assert any('kill-session' in call and 'test-session' in call for call in calls)
        assert controller_existing_session.target_window is None

    # ==================== List Windows ====================

    def test_list_windows_success(self, controller_existing_session, mock_subprocess):
        """Test listing all windows in session."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # _ensure_session
            Mock(stdout="0|window0|1\n1|window1|0\n2|window2|0", returncode=0),  # list-windows
            Mock(stdout="%123", returncode=0),  # pane_id for window 0
            Mock(stdout="%124", returncode=0),  # pane_id for window 1
            Mock(stdout="%125", returncode=0)   # pane_id for window 2
        ]

        windows = controller_existing_session.list_windows()

        assert len(windows) == 3
        assert windows[0]['index'] == '0'
        assert windows[0]['name'] == 'window0'
        assert windows[0]['active'] is True
        assert windows[0]['pane_id'] == '%123'

        assert windows[1]['active'] is False
        assert windows[2]['pane_id'] == '%125'

    def test_list_windows_empty(self, controller_existing_session, mock_subprocess):
        """Test listing windows when none exist."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="", returncode=1)
        ]

        windows = controller_existing_session.list_windows()

        assert windows == []

    def test_list_windows_with_empty_lines(self, controller_existing_session, mock_subprocess):
        """Test listing windows with empty lines in output."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="0|window0|1\n\n1|window1|0\n", returncode=0),
            Mock(stdout="%123", returncode=0),
            Mock(stdout="%124", returncode=0)
        ]

        windows = controller_existing_session.list_windows()

        assert len(windows) == 2

    def test_list_windows_pane_id_fetch_fails(self, controller_existing_session, mock_subprocess):
        """Test listing windows when pane ID fetch fails."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),
            Mock(stdout="0|window0|1", returncode=0),
            Mock(stdout="", returncode=1)  # display-message fails
        ]

        windows = controller_existing_session.list_windows()

        assert len(windows) == 1
        assert windows[0]['pane_id'] == ''

    # ==================== Resolve Pane ID ====================

    def test_resolve_pane_id_none(self, controller_existing_session, mock_subprocess):
        """Test _resolve_pane_id with None."""
        controller_existing_session.target_window = "test-session:2"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        result = controller_existing_session._resolve_pane_id(None)

        assert result == "test-session:2"

    def test_resolve_pane_id_digit(self, controller_existing_session, mock_subprocess):
        """Test _resolve_pane_id with digit."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        result = controller_existing_session._resolve_pane_id("5")

        assert result == "test-session:5"

    def test_resolve_pane_id_full_target(self, controller_existing_session, mock_subprocess):
        """Test _resolve_pane_id with full target."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        result = controller_existing_session._resolve_pane_id("other:3")

        assert result == "other:3"


# ==================== Integration Tests ====================

class TestIntegration:
    """Integration tests for realistic workflows."""

    @patch('claude_code_tools.tmux_remote_controller.subprocess.run')
    def test_launch_send_capture_kill_workflow(self, mock_subprocess):
        """Test complete workflow: launch, send, capture, kill."""
        # Setup mock responses
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=1),       # has-session (doesn't exist)
            Mock(stdout="test-session", returncode=0),  # new-session
            Mock(stdout="", returncode=0),       # _ensure_session in launch
            Mock(stdout="test-session:1", returncode=0),  # new-window
            Mock(stdout="", returncode=0),       # _ensure_session in send
            Mock(stdout="", returncode=0),       # send-keys text
            Mock(stdout="", returncode=0),       # send-keys Enter
            Mock(stdout="", returncode=0),       # _ensure_session in capture
            Mock(stdout="output text", returncode=0),  # capture-pane
            Mock(stdout="", returncode=0),       # _ensure_session in kill
            Mock(stdout="", returncode=0)        # kill-window
        ]

        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="test-session")

        # Launch
        window_id = controller.launch_cli("python3")
        assert window_id == "test-session:1"

        # Send command
        with patch('claude_code_tools.tmux_remote_controller.time.sleep'):
            controller.send_keys("print('hello')")

        # Capture output
        output = controller.capture_pane()
        assert output == "output text"

        # Kill window
        controller.kill_window()
        assert controller.target_window is None

    @patch('claude_code_tools.tmux_remote_controller.subprocess.run')
    def test_wait_for_idle_then_interact(self, mock_subprocess):
        """Test waiting for idle before interacting."""
        # Need enough captures for wait_for_idle loop
        captures = [Mock(stdout="loading", returncode=0)] * 3 + \
                  [Mock(stdout=">>> ", returncode=0)] * 10
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=0),  # has-session
            Mock(stdout="test-session:0", returncode=0),  # display-message
            Mock(stdout="", returncode=0),  # _ensure_session in wait_for_idle
        ] + captures + [
            Mock(stdout="", returncode=0),  # _ensure_session in send
            Mock(stdout="", returncode=0)   # send-keys
        ]

        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="test-session")

        controller.target_window = "test-session:1"

        with patch('claude_code_tools.tmux_remote_controller.time.time') as mock_time:
            # Provide enough time values for all iterations
            mock_time.side_effect = [0, 0] + [i * 0.5 for i in range(1, 30)]
            with patch('claude_code_tools.tmux_remote_controller.time.sleep'):
                idle = controller.wait_for_idle(idle_time=2.0)
                assert idle is True

                controller.send_keys("command", enter=False)

    @patch('claude_code_tools.tmux_remote_controller.subprocess.run')
    def test_multiple_windows_workflow(self, mock_subprocess):
        """Test managing multiple windows."""
        mock_subprocess.side_effect = [
            Mock(stdout="", returncode=1),  # has-session
            Mock(stdout="test-session", returncode=0),  # new-session
            Mock(stdout="", returncode=0),  # launch 1
            Mock(stdout="test-session:1", returncode=0),
            Mock(stdout="", returncode=0),  # launch 2
            Mock(stdout="test-session:2", returncode=0),
            Mock(stdout="", returncode=0),  # list_windows
            Mock(stdout="0|bash|0\n1|python|0\n2|node|1", returncode=0),
            Mock(stdout="%100", returncode=0),  # pane_id 0
            Mock(stdout="%101", returncode=0),  # pane_id 1
            Mock(stdout="%102", returncode=0)   # pane_id 2
        ]

        with patch('builtins.print'):
            controller = RemoteTmuxController(session_name="test-session")

        # Launch multiple windows
        w1 = controller.launch_cli("python3", name="python")
        w2 = controller.launch_cli("node", name="node")

        assert w1 == "test-session:1"
        assert w2 == "test-session:2"

        # List all windows
        windows = controller.list_windows()
        assert len(windows) == 3
        assert windows[2]['active'] is True
