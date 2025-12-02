#!/usr/bin/env python3
"""
Comprehensive pytest tests for tmux_cli_controller.py

Tests cover:
- Session/window/pane queries
- Pane creation and management
- Command sending with delays
- Output capture
- Wait operations (prompt, idle)
- Error handling and edge cases
- Pane identifier resolution
"""

import pytest
from unittest.mock import Mock, patch

# Import the module under test
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_code_tools.tmux_cli_controller import (
    TmuxCLIController,
    CLI,
    _load_help_text
)


class TestTmuxCLIController:
    """Tests for TmuxCLIController class."""

    @pytest.fixture
    def controller(self):
        """Create a controller instance for testing."""
        return TmuxCLIController(session_name="test-session", window_name="test-window")

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess.run for testing."""
        with patch('claude_code_tools.tmux_cli_controller.subprocess.run') as mock:
            yield mock

    # ==================== Basic Command Execution ====================

    def test_run_tmux_command_success(self, controller, mock_subprocess):
        """Test successful tmux command execution."""
        mock_subprocess.return_value = Mock(stdout="output_text\n", returncode=0)

        output, code = controller._run_tmux_command(['list-panes'])

        assert output == "output_text"
        assert code == 0
        mock_subprocess.assert_called_once_with(
            ['tmux', 'list-panes'],
            capture_output=True,
            text=True
        )

    def test_run_tmux_command_failure(self, controller, mock_subprocess):
        """Test failed tmux command execution."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        output, code = controller._run_tmux_command(['invalid-command'])

        assert output == ""
        assert code == 1

    # ==================== Session/Window/Pane Queries ====================

    def test_get_current_session(self, controller, mock_subprocess):
        """Test getting current session name."""
        mock_subprocess.return_value = Mock(stdout="my-session\n", returncode=0)

        result = controller.get_current_session()

        assert result == "my-session"
        mock_subprocess.assert_called_with(
            ['tmux', 'display-message', '-p', '#{session_name}'],
            capture_output=True,
            text=True
        )

    def test_get_current_session_not_in_tmux(self, controller, mock_subprocess):
        """Test getting session when not in tmux."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        result = controller.get_current_session()

        assert result is None

    def test_get_current_window(self, controller, mock_subprocess):
        """Test getting current window name."""
        mock_subprocess.return_value = Mock(stdout="my-window\n", returncode=0)

        result = controller.get_current_window()

        assert result == "my-window"

    def test_get_current_pane(self, controller, mock_subprocess):
        """Test getting current pane ID."""
        mock_subprocess.return_value = Mock(stdout="%123\n", returncode=0)

        result = controller.get_current_pane()

        assert result == "%123"

    def test_get_current_pane_index(self, controller, mock_subprocess):
        """Test getting current pane index."""
        mock_subprocess.return_value = Mock(stdout="2\n", returncode=0)

        result = controller.get_current_pane_index()

        assert result == "2"

    def test_get_pane_command(self, controller, mock_subprocess):
        """Test getting command running in pane."""
        mock_subprocess.return_value = Mock(stdout="python3\n", returncode=0)

        result = controller.get_pane_command("%123")

        assert result == "python3"
        mock_subprocess.assert_called_with(
            ['tmux', 'display-message', '-t', '%123', '-p', '#{pane_current_command}'],
            capture_output=True,
            text=True
        )

    @patch.dict('os.environ', {'TMUX_PANE': '%456'})
    def test_get_current_window_id_from_env(self, controller, mock_subprocess):
        """Test getting window ID from TMUX_PANE env var."""
        mock_subprocess.return_value = Mock(stdout="@789\n", returncode=0)

        result = controller.get_current_window_id()

        assert result == "@789"
        mock_subprocess.assert_called_with(
            ['tmux', 'display-message', '-t', '%456', '-p', '#{window_id}'],
            capture_output=True,
            text=True
        )

    @patch.dict('os.environ', {}, clear=True)
    def test_get_current_window_id_fallback(self, controller, mock_subprocess):
        """Test getting window ID fallback when no TMUX_PANE."""
        mock_subprocess.return_value = Mock(stdout="@999\n", returncode=0)

        result = controller.get_current_window_id()

        assert result == "@999"

    # ==================== Pane Identifier Resolution ====================

    def test_resolve_pane_identifier_pane_id(self, controller):
        """Test resolving pane ID format."""
        result = controller.resolve_pane_identifier("%123")
        assert result == "%123"

    def test_resolve_pane_identifier_digit(self, controller, mock_subprocess):
        """Test resolving digit (pane index) format."""
        mock_subprocess.return_value = Mock(
            stdout="%123|2|title|1|80x24|bash",
            returncode=0
        )

        result = controller.resolve_pane_identifier("2")

        assert result == "%123"

    def test_resolve_pane_identifier_session_window_pane(self, controller, mock_subprocess):
        """Test resolving session:window.pane format."""
        mock_subprocess.return_value = Mock(stdout="%456\n", returncode=0)

        result = controller.resolve_pane_identifier("mysession:1.2")

        assert result == "%456"
        mock_subprocess.assert_called_with(
            ['tmux', 'display-message', '-t', 'mysession:1.2', '-p', '#{pane_id}'],
            capture_output=True,
            text=True
        )

    def test_resolve_pane_identifier_invalid(self, controller, mock_subprocess):
        """Test resolving invalid identifier."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        result = controller.resolve_pane_identifier("invalid:format")

        assert result is None

    def test_resolve_pane_identifier_none(self, controller):
        """Test resolving None identifier."""
        result = controller.resolve_pane_identifier(None)
        assert result is None

    def test_format_pane_identifier_success(self, controller, mock_subprocess):
        """Test formatting pane ID to session:window.pane."""
        mock_subprocess.side_effect = [
            Mock(stdout="mysession", returncode=0),
            Mock(stdout="1", returncode=0),
            Mock(stdout="2", returncode=0)
        ]

        result = controller.format_pane_identifier("%123")

        assert result == "mysession:1.2"
        assert mock_subprocess.call_count == 3

    def test_format_pane_identifier_failure(self, controller, mock_subprocess):
        """Test formatting pane ID when tmux calls fail."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        result = controller.format_pane_identifier("%123")

        assert result == "%123"  # Fallback to original

    # ==================== List Panes ====================

    def test_list_panes_success(self, controller, mock_subprocess):
        """Test listing panes successfully."""
        mock_subprocess.side_effect = [
            Mock(stdout="%123|0|pane1|1|80x24|bash\n%124|1|pane2|0|80x24|python3", returncode=0),
            Mock(stdout="test-session:1.0", returncode=0),
            Mock(stdout="test-session:1.1", returncode=0)
        ]

        panes = controller.list_panes()

        assert len(panes) == 2
        assert panes[0]['id'] == '%123'
        assert panes[0]['index'] == '0'
        assert panes[0]['title'] == 'pane1'
        assert panes[0]['active'] is True
        assert panes[0]['size'] == '80x24'
        assert panes[0]['command'] == 'bash'
        assert panes[1]['active'] is False

    def test_list_panes_empty(self, controller, mock_subprocess):
        """Test listing panes when none exist."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        panes = controller.list_panes()

        assert panes == []

    def test_list_panes_without_session(self, mock_subprocess):
        """Test listing panes without session/window specified."""
        controller = TmuxCLIController()
        mock_subprocess.return_value = Mock(stdout="%123|0|pane1|1|80x24|bash", returncode=0)

        with patch.object(controller, 'format_pane_identifier', return_value='session:0.0'):
            panes = controller.list_panes()

        assert len(panes) == 1

    # ==================== Create Pane ====================

    @patch.dict('os.environ', {'TMUX_PANE': '%456'})
    def test_create_pane_vertical(self, controller, mock_subprocess):
        """Test creating a vertical pane split."""
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),  # get_current_window_id
            Mock(stdout="%999\n", returncode=0)  # split-window
        ]

        pane_id = controller.create_pane(vertical=True, size=50)

        assert pane_id == "%999"
        assert controller.target_pane == "%999"
        # Check split-window was called with correct flags
        calls = mock_subprocess.call_args_list
        assert any('-h' in str(call) for call in calls)

    def test_create_pane_horizontal(self, controller, mock_subprocess):
        """Test creating a horizontal pane split."""
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),
            Mock(stdout="%999\n", returncode=0)
        ]

        pane_id = controller.create_pane(vertical=False, size=30)

        assert pane_id == "%999"
        # Check split-window was called with -v (vertical split = horizontal layout)
        calls = mock_subprocess.call_args_list
        assert any('-v' in str(call) for call in calls)

    def test_create_pane_with_command(self, controller, mock_subprocess):
        """Test creating pane with start command."""
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),
            Mock(stdout="%999\n", returncode=0)
        ]

        pane_id = controller.create_pane(start_command="python3")

        assert pane_id == "%999"

    def test_create_pane_failure(self, controller, mock_subprocess):
        """Test pane creation failure."""
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),
            Mock(stdout="", returncode=1)
        ]

        pane_id = controller.create_pane()

        assert pane_id is None

    # ==================== Select Pane ====================

    def test_select_pane_by_id(self, controller):
        """Test selecting pane by ID."""
        controller.select_pane(pane_id="%123")
        assert controller.target_pane == "%123"

    def test_select_pane_by_index(self, controller, mock_subprocess):
        """Test selecting pane by index."""
        mock_subprocess.return_value = Mock(
            stdout="%123|0|pane1|1|80x24|bash\n%124|1|pane2|0|80x24|python3",
            returncode=0
        )

        with patch.object(controller, 'format_pane_identifier', return_value='s:0.0'):
            controller.select_pane(pane_index=1)

        assert controller.target_pane == "%124"

    # ==================== Send Keys ====================

    def test_send_keys_no_enter(self, controller, mock_subprocess):
        """Test sending keys without Enter."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.send_keys("hello", enter=False)

        mock_subprocess.assert_called_once_with(
            ['tmux', 'send-keys', '-t', '%123', 'hello'],
            capture_output=True,
            text=True
        )

    def test_send_keys_with_enter_no_delay(self, controller, mock_subprocess):
        """Test sending keys with Enter but no delay."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.send_keys("hello", enter=True, delay_enter=False)

        mock_subprocess.assert_called_once_with(
            ['tmux', 'send-keys', '-t', '%123', 'hello', 'Enter'],
            capture_output=True,
            text=True
        )

    def test_send_keys_with_enter_default_delay(self, controller, mock_subprocess):
        """Test sending keys with Enter and default delay."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        with patch('claude_code_tools.tmux_cli_controller.time.sleep') as mock_sleep:
            controller.send_keys("hello", enter=True, delay_enter=True)

        assert mock_subprocess.call_count == 2
        mock_sleep.assert_called_once_with(1.0)

    def test_send_keys_with_enter_custom_delay(self, controller, mock_subprocess):
        """Test sending keys with Enter and custom delay."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        with patch('claude_code_tools.tmux_cli_controller.time.sleep') as mock_sleep:
            controller.send_keys("hello", enter=True, delay_enter=0.5)

        mock_sleep.assert_called_once_with(0.5)

    def test_send_keys_with_pane_id(self, controller, mock_subprocess):
        """Test sending keys with explicit pane ID."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.send_keys("hello", pane_id="%456", enter=False)

        mock_subprocess.assert_called_once_with(
            ['tmux', 'send-keys', '-t', '%456', 'hello'],
            capture_output=True,
            text=True
        )

    def test_send_keys_no_target(self, controller):
        """Test sending keys without target pane raises error."""
        with pytest.raises(ValueError, match="No target pane specified"):
            controller.send_keys("hello")

    # ==================== Capture Pane ====================

    def test_capture_pane_all_lines(self, controller, mock_subprocess):
        """Test capturing all pane content."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="line1\nline2\nline3", returncode=0)

        output = controller.capture_pane()

        assert output == "line1\nline2\nline3"
        mock_subprocess.assert_called_once_with(
            ['tmux', 'capture-pane', '-t', '%123', '-p'],
            capture_output=True,
            text=True
        )

    def test_capture_pane_limited_lines(self, controller, mock_subprocess):
        """Test capturing limited number of lines."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="line1\nline2", returncode=0)

        output = controller.capture_pane(lines=10)

        assert output == "line1\nline2"
        # Check that -S -10 was included
        call_args = mock_subprocess.call_args[0][0]
        assert '-S' in call_args
        assert '-10' in call_args

    def test_capture_pane_no_target(self, controller):
        """Test capturing pane without target raises error."""
        with pytest.raises(ValueError, match="No target pane specified"):
            controller.capture_pane()

    # ==================== Wait for Prompt ====================

    def test_wait_for_prompt_found(self, controller, mock_subprocess):
        """Test waiting for prompt that appears."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="$ ", returncode=0)

        with patch('claude_code_tools.tmux_cli_controller.time.sleep'):
            result = controller.wait_for_prompt(r'\$', timeout=5)

        assert result is True

    def test_wait_for_prompt_timeout(self, controller, mock_subprocess):
        """Test waiting for prompt that doesn't appear."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="loading...", returncode=0)

        with patch('claude_code_tools.tmux_cli_controller.time.time') as mock_time:
            mock_time.side_effect = [0, 0.1, 0.2, 5.1]  # Simulate timeout
            result = controller.wait_for_prompt(r'\$', timeout=5, check_interval=0.1)

        assert result is False

    def test_wait_for_prompt_no_target(self, controller):
        """Test wait_for_prompt without target raises error."""
        with pytest.raises(ValueError, match="No target pane specified"):
            controller.wait_for_prompt(r'\$')

    # ==================== Wait for Idle ====================

    def test_wait_for_idle_success(self, controller, mock_subprocess):
        """Test waiting for pane to become idle."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="static content", returncode=0)

        with patch('claude_code_tools.tmux_cli_controller.time.time') as mock_time:
            # Simulate time progression: start=0, changes at 0.5, idle check at 2.6
            mock_time.side_effect = [0, 0, 0.5, 0.5, 1.0, 1.0, 2.6]
            with patch('claude_code_tools.tmux_cli_controller.time.sleep'):
                result = controller.wait_for_idle(idle_time=2.0)

        assert result is True

    def test_wait_for_idle_changing_content(self, controller, mock_subprocess):
        """Test wait_for_idle with continuously changing content."""
        controller.target_pane = "%123"
        # Simulate changing content - need more values as each iteration makes 1 call
        outputs = ["output{}".format(i) for i in range(15)]
        mock_subprocess.side_effect = [Mock(stdout=o, returncode=0) for o in outputs]

        with patch('claude_code_tools.tmux_cli_controller.time.time') as mock_time:
            # Never reach idle_time threshold
            mock_time.side_effect = [0, 0] + [i * 0.1 for i in range(1, 30)]
            with patch('claude_code_tools.tmux_cli_controller.time.sleep'):
                result = controller.wait_for_idle(idle_time=2.0, timeout=1)

        assert result is False

    def test_wait_for_idle_no_target(self, controller):
        """Test wait_for_idle without target raises error."""
        with pytest.raises(ValueError, match="No target pane specified"):
            controller.wait_for_idle()

    # ==================== Kill Pane ====================

    def test_kill_pane_success(self, controller, mock_subprocess):
        """Test killing a pane."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.kill_pane()

        assert controller.target_pane is None
        mock_subprocess.assert_called_with(
            ['tmux', 'kill-pane', '-t', '%123'],
            capture_output=True,
            text=True
        )

    def test_kill_pane_with_explicit_id(self, controller, mock_subprocess):
        """Test killing pane with explicit pane ID."""
        controller.target_pane = "%123"
        mock_subprocess.side_effect = [
            Mock(stdout="%999", returncode=0),  # get_current_pane
            Mock(stdout="", returncode=0)       # kill-pane
        ]

        controller.kill_pane(pane_id="%456")

        # target_pane should remain unchanged since we killed different pane
        assert controller.target_pane == "%123"

    def test_kill_pane_self_protection(self, controller, mock_subprocess):
        """Test that killing own pane is prevented."""
        mock_subprocess.return_value = Mock(stdout="%123", returncode=0)

        with pytest.raises(ValueError, match="Cannot kill own pane"):
            controller.kill_pane(pane_id="%123")

    def test_kill_pane_no_target(self, controller):
        """Test kill_pane without target raises error."""
        with pytest.raises(ValueError, match="No target pane specified"):
            controller.kill_pane()

    # ==================== Resize Pane ====================

    def test_resize_pane_up(self, controller, mock_subprocess):
        """Test resizing pane upward."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.resize_pane('up', amount=10)

        mock_subprocess.assert_called_with(
            ['tmux', 'resize-pane', '-t', '%123', '-U', '10'],
            capture_output=True,
            text=True
        )

    def test_resize_pane_invalid_direction(self, controller):
        """Test resizing with invalid direction raises error."""
        controller.target_pane = "%123"

        with pytest.raises(ValueError, match="Invalid direction"):
            controller.resize_pane('diagonal')

    def test_resize_pane_no_target(self, controller):
        """Test resize_pane without target raises error."""
        with pytest.raises(ValueError, match="No target pane specified"):
            controller.resize_pane('up')

    # ==================== Focus Pane ====================

    def test_focus_pane(self, controller, mock_subprocess):
        """Test focusing a pane."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.focus_pane()

        mock_subprocess.assert_called_with(
            ['tmux', 'select-pane', '-t', '%123'],
            capture_output=True,
            text=True
        )

    def test_focus_pane_no_target(self, controller):
        """Test focus_pane without target raises error."""
        with pytest.raises(ValueError, match="No target pane specified"):
            controller.focus_pane()

    # ==================== Special Keys ====================

    def test_send_interrupt(self, controller, mock_subprocess):
        """Test sending Ctrl+C to pane."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.send_interrupt()

        mock_subprocess.assert_called_with(
            ['tmux', 'send-keys', '-t', '%123', 'C-c'],
            capture_output=True,
            text=True
        )

    def test_send_escape(self, controller, mock_subprocess):
        """Test sending Escape key to pane."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.send_escape()

        mock_subprocess.assert_called_with(
            ['tmux', 'send-keys', '-t', '%123', 'Escape'],
            capture_output=True,
            text=True
        )

    def test_clear_pane(self, controller, mock_subprocess):
        """Test clearing pane screen."""
        controller.target_pane = "%123"
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        controller.clear_pane()

        mock_subprocess.assert_called_with(
            ['tmux', 'send-keys', '-t', '%123', 'C-l'],
            capture_output=True,
            text=True
        )

    # ==================== Launch CLI ====================

    def test_launch_cli_success(self, controller, mock_subprocess):
        """Test launching CLI in new pane."""
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),  # get_current_window_id
            Mock(stdout="%999", returncode=0),  # create_pane
            Mock(stdout="test-session", returncode=0),  # format - session
            Mock(stdout="1", returncode=0),             # format - window
            Mock(stdout="2", returncode=0)              # format - pane
        ]

        result = controller.launch_cli("python3", vertical=True, size=50)

        assert result == "test-session:1.2"

    def test_launch_cli_failure(self, controller, mock_subprocess):
        """Test launch_cli when pane creation fails."""
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),
            Mock(stdout="", returncode=1)
        ]

        result = controller.launch_cli("python3")

        assert result is None


class TestCLI:
    """Tests for CLI unified interface class."""

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess.run for testing."""
        with patch('claude_code_tools.tmux_cli_controller.subprocess.run') as mock:
            yield mock

    # ==================== Initialization and Mode Detection ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_cli_init_local_mode(self, mock_subprocess):
        """Test CLI initialization in local mode (inside tmux)."""
        cli = CLI()

        assert cli.in_tmux is True
        assert cli.mode == 'local'
        assert isinstance(cli.controller, TmuxCLIController)

    @patch.dict('os.environ', {}, clear=True)
    def test_cli_init_remote_mode(self, mock_subprocess):
        """Test CLI initialization in remote mode (outside tmux)."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)  # Session doesn't exist

        with patch('claude_code_tools.tmux_remote_controller.RemoteTmuxController') as mock_remote:
            cli = CLI(session="test-session")

            assert cli.in_tmux is False
            assert cli.mode == 'remote'
            mock_remote.assert_called_once_with(session_name="test-session")

    # ==================== Status Display ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_status_in_tmux(self, capsys, mock_subprocess):
        """Test status display when inside tmux."""
        mock_subprocess.side_effect = [
            Mock(stdout="my-session", returncode=0),  # get_current_session
            Mock(stdout="my-window", returncode=0),   # get_current_window
            Mock(stdout="0", returncode=0),           # get_current_pane_index
            Mock(stdout="%123|0|pane1|1|80x24|bash", returncode=0),  # list_panes
            Mock(stdout="my-session", returncode=0),  # format_pane_identifier - session
            Mock(stdout="0", returncode=0),           # format_pane_identifier - window
            Mock(stdout="0", returncode=0)            # format_pane_identifier - pane
        ]

        cli = CLI()
        cli.status()

        captured = capsys.readouterr()
        assert "my-session:my-window.0" in captured.out
        assert "my-session:0.0" in captured.out

    @patch.dict('os.environ', {}, clear=True)
    def test_status_not_in_tmux(self, capsys, mock_subprocess):
        """Test status display when not inside tmux."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        with patch('claude_code_tools.tmux_remote_controller.RemoteTmuxController') as mock_remote:
            mock_instance = Mock()
            mock_instance.session_name = "remote-session"
            mock_remote.return_value = mock_instance

            cli = CLI(session="remote-session")
            cli.status()

        captured = capsys.readouterr()
        assert "Not currently in tmux" in captured.out
        assert "remote-session" in captured.out

    # ==================== Launch Command ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_launch_local_mode(self, capsys, mock_subprocess):
        """Test launching command in local mode."""
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),
            Mock(stdout="%999", returncode=0),
            Mock(stdout="session:1.2", returncode=0)
        ]

        cli = CLI()
        with patch.object(cli.controller, 'format_pane_identifier', return_value='session:1.2'):
            pane_id = cli.launch("python3", vertical=True, size=50)

        assert pane_id == "session:1.2"
        captured = capsys.readouterr()
        assert "Launched 'python3'" in captured.out

    # ==================== Send Text ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_send_local_mode(self, capsys, mock_subprocess):
        """Test sending text in local mode."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        cli = CLI()
        cli.controller.target_pane = "%123"

        with patch('claude_code_tools.tmux_cli_controller.time.sleep'):
            cli.send("hello", enter=True)

        captured = capsys.readouterr()
        assert "Text sent" in captured.out

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_send_with_pane_resolution(self, capsys, mock_subprocess):
        """Test sending text with pane identifier resolution."""
        mock_subprocess.side_effect = [
            Mock(stdout="%123|2|title|1|80x24|bash", returncode=0),  # list_panes for resolve
            Mock(stdout="session", returncode=0),  # format_pane_identifier - session
            Mock(stdout="0", returncode=0),        # format_pane_identifier - window
            Mock(stdout="2", returncode=0),        # format_pane_identifier - pane
            Mock(stdout="", returncode=0),         # send_keys text
            Mock(stdout="", returncode=0)          # send_keys Enter
        ]

        cli = CLI()

        with patch('claude_code_tools.tmux_cli_controller.time.sleep'):
            cli.send("hello", pane="2")

        captured = capsys.readouterr()
        assert "Text sent" in captured.out

    # ==================== Capture Output ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_capture_local_mode(self, capsys, mock_subprocess):
        """Test capturing output in local mode."""
        mock_subprocess.return_value = Mock(stdout="captured text", returncode=0)

        cli = CLI()
        cli.controller.target_pane = "%123"
        content = cli.capture()

        assert content == "captured text"
        captured = capsys.readouterr()
        assert "captured text" in captured.out

    # ==================== Interrupt/Escape ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_interrupt_local_mode(self, capsys, mock_subprocess):
        """Test sending interrupt in local mode."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        cli = CLI()
        cli.controller.target_pane = "%123"
        cli.interrupt()

        captured = capsys.readouterr()
        assert "Sent interrupt signal" in captured.out

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_escape_local_mode(self, capsys, mock_subprocess):
        """Test sending escape in local mode."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        cli = CLI()
        cli.controller.target_pane = "%123"
        cli.escape()

        captured = capsys.readouterr()
        assert "Sent escape key" in captured.out

    # ==================== Kill Pane/Window ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_kill_local_mode(self, capsys, mock_subprocess):
        """Test killing pane in local mode."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        cli = CLI()
        cli.controller.target_pane = "%123"
        cli.kill()

        captured = capsys.readouterr()
        assert "Pane killed" in captured.out

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_kill_self_protection(self, capsys, mock_subprocess):
        """Test kill with self-protection in local mode."""
        mock_subprocess.return_value = Mock(stdout="%123", returncode=0)

        cli = CLI()
        cli.controller.target_pane = "%123"

        with patch.object(cli.controller, 'kill_pane', side_effect=ValueError("Cannot kill own pane")):
            cli.kill(pane="%123")

        captured = capsys.readouterr()
        assert "Cannot kill own pane" in captured.out

    # ==================== Wait for Idle ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_wait_idle_success(self, capsys, mock_subprocess):
        """Test waiting for idle successfully."""
        mock_subprocess.return_value = Mock(stdout="static", returncode=0)

        cli = CLI()
        cli.controller.target_pane = "%123"

        with patch.object(cli.controller, 'wait_for_idle', return_value=True):
            result = cli.wait_idle(idle_time=1.0)

        assert result is True
        captured = capsys.readouterr()
        assert "Pane is idle" in captured.out

    # ==================== Remote Mode Specific ====================

    @patch.dict('os.environ', {}, clear=True)
    def test_attach_remote_mode(self, mock_subprocess):
        """Test attach in remote mode."""
        mock_subprocess.return_value = Mock(stdout="", returncode=0)

        with patch('claude_code_tools.tmux_remote_controller.RemoteTmuxController') as mock_remote:
            mock_instance = Mock()
            mock_remote.return_value = mock_instance

            cli = CLI()
            cli.attach()

            mock_instance.attach_session.assert_called_once()

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_attach_local_mode_not_available(self, capsys, mock_subprocess):
        """Test that attach is not available in local mode."""
        cli = CLI()
        cli.attach()

        captured = capsys.readouterr()
        assert "only available in remote mode" in captured.out

    # ==================== Help Text ====================

    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_help_local_mode(self, capsys, mock_subprocess):
        """Test help display in local mode."""
        mock_subprocess.side_effect = [
            Mock(stdout="my-session", returncode=0),
            Mock(stdout="my-window", returncode=0),
            Mock(stdout="0", returncode=0),
            Mock(stdout="", returncode=0)
        ]

        cli = CLI()
        cli.help()

        captured = capsys.readouterr()
        assert "MODE: LOCAL" in captured.out
        assert "TMUX STATUS" in captured.out

    @patch.dict('os.environ', {}, clear=True)
    def test_help_remote_mode(self, capsys, mock_subprocess):
        """Test help display in remote mode."""
        mock_subprocess.return_value = Mock(stdout="", returncode=1)

        with patch('claude_code_tools.tmux_remote_controller.RemoteTmuxController'):
            cli = CLI(session="test-session")
            cli.help()

        captured = capsys.readouterr()
        assert "MODE: REMOTE" in captured.out
        assert "REMOTE MODE SPECIFIC COMMANDS" in captured.out


class TestLoadHelpText:
    """Tests for _load_help_text function."""

    def test_load_help_text_returns_string(self):
        """Test that _load_help_text returns a string."""
        result = _load_help_text()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_load_help_text_contains_basic_usage(self):
        """Test that help text contains basic usage information."""
        result = _load_help_text()
        assert "tmux-cli" in result.lower()
        assert "launch" in result.lower() or "usage" in result.lower()


# ==================== Integration Tests ====================

class TestIntegration:
    """Integration tests for realistic workflows."""

    @patch('claude_code_tools.tmux_cli_controller.subprocess.run')
    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_launch_and_interact_workflow(self, mock_subprocess):
        """Test complete workflow: launch, send, capture, kill."""
        # Setup mock responses
        mock_subprocess.side_effect = [
            Mock(stdout="@789", returncode=0),      # get_current_window_id
            Mock(stdout="%999", returncode=0),      # create_pane
            Mock(stdout="session", returncode=0),   # format_pane_identifier - session
            Mock(stdout="1", returncode=0),         # format_pane_identifier - window
            Mock(stdout="2", returncode=0),         # format_pane_identifier - pane
            Mock(stdout="", returncode=0),          # send_keys text
            Mock(stdout="", returncode=0),          # send_keys Enter
            Mock(stdout="output", returncode=0),    # capture_pane
            Mock(stdout="", returncode=0)           # kill_pane
        ]

        controller = TmuxCLIController()

        # Launch
        pane_id = controller.launch_cli("python3")
        assert pane_id == "session:1.2"

        # Send command
        with patch('claude_code_tools.tmux_cli_controller.time.sleep'):
            controller.send_keys("print('hello')")

        # Capture output
        output = controller.capture_pane()
        assert output == "output"

        # Kill pane
        controller.kill_pane()
        assert controller.target_pane is None

    @patch('claude_code_tools.tmux_cli_controller.subprocess.run')
    @patch.dict('os.environ', {'TMUX': 'tmux-session,123,0'})
    def test_wait_for_prompt_then_interact(self, mock_subprocess):
        """Test waiting for prompt before sending commands."""
        controller = TmuxCLIController()
        controller.target_pane = "%123"

        # First capture shows loading, second shows prompt
        mock_subprocess.side_effect = [
            Mock(stdout="Loading...", returncode=0),
            Mock(stdout=">>> ", returncode=0),
            Mock(stdout="", returncode=0),  # send_keys
            Mock(stdout="", returncode=0)   # send_keys Enter
        ]

        with patch('claude_code_tools.tmux_cli_controller.time.sleep'):
            found = controller.wait_for_prompt(r'>>>', timeout=5)
            assert found is True

            controller.send_keys("command")
