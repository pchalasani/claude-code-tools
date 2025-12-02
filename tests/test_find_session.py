"""Comprehensive pytest tests for find_session.py module."""

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock rich library before importing the module
sys.modules['rich'] = MagicMock()
sys.modules['rich.console'] = MagicMock()
sys.modules['rich.table'] = MagicMock()
sys.modules['rich.prompt'] = MagicMock()
sys.modules['rich.box'] = MagicMock()

from claude_code_tools.find_session import (
    AgentConfig,
    get_default_agents,
    load_config,
    search_all_agents,
    display_interactive_ui,
    show_action_menu,
    handle_action,
    main,
)


# ==================== Fixtures ====================


@pytest.fixture
def mock_home_dir(tmp_path, monkeypatch):
    """Mock home directory for file operations."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def mock_config_file(mock_home_dir):
    """Create a mock config file."""
    config_dir = mock_home_dir / ".config" / "find-session"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    config_data = {
        "agents": [
            {
                "name": "claude",
                "display_name": "Claude",
                "home_dir": "/custom/claude",
                "enabled": True
            },
            {
                "name": "codex",
                "display_name": "Codex Pro",
                "home_dir": "/custom/codex",
                "enabled": False
            }
        ]
    }

    config_path.write_text(json.dumps(config_data))
    return config_path


@pytest.fixture
def sample_claude_sessions():
    """Sample Claude sessions as returned by find_claude_sessions."""
    return [
        (
            "session-id-1",  # session_id
            1700000000.0,    # mod_time
            1699000000.0,    # create_time
            150,             # lines
            "project-1",     # project
            "Fixed bug in authentication",  # preview
            "/home/user/project-1",  # cwd
            "main"           # branch
        ),
        (
            "session-id-2",
            1700000100.0,
            1699000100.0,
            200,
            "project-2",
            "Added new feature",
            "/home/user/project-2",
            "feature-branch"
        ),
    ]


@pytest.fixture
def sample_codex_sessions():
    """Sample Codex sessions as returned by find_codex_sessions."""
    return [
        {
            "session_id": "codex-session-1",
            "mod_time": 1700000200.0,
            "lines": 180,
            "project": "codex-project",
            "preview": "Refactored database layer",
            "cwd": "/home/user/codex-project",
            "branch": "develop",
            "file_path": "/home/user/.codex/sessions/codex-session-1.jsonl"
        }
    ]


@pytest.fixture
def mock_rich_available(monkeypatch):
    """Mock rich library as available."""
    monkeypatch.setattr("claude_code_tools.find_session.RICH_AVAILABLE", True)


@pytest.fixture
def mock_rich_unavailable(monkeypatch):
    """Mock rich library as unavailable."""
    monkeypatch.setattr("claude_code_tools.find_session.RICH_AVAILABLE", False)


# ==================== Test get_default_agents ====================


def test_get_default_agents_returns_list():
    """Test that get_default_agents returns a list of AgentConfig."""
    agents = get_default_agents()

    assert isinstance(agents, list)
    assert len(agents) == 2
    assert all(isinstance(agent, AgentConfig) for agent in agents)


def test_get_default_agents_claude_config():
    """Test Claude agent default configuration."""
    agents = get_default_agents()
    claude = next(a for a in agents if a.name == "claude")

    assert claude.name == "claude"
    assert claude.display_name == "Claude"
    assert claude.home_dir is None
    assert claude.enabled is True


def test_get_default_agents_codex_config():
    """Test Codex agent default configuration."""
    agents = get_default_agents()
    codex = next(a for a in agents if a.name == "codex")

    assert codex.name == "codex"
    assert codex.display_name == "Codex"
    assert codex.home_dir is None
    assert codex.enabled is True


# ==================== Test load_config ====================


def test_load_config_returns_defaults_when_no_config(mock_home_dir):
    """Test load_config returns defaults when config file doesn't exist."""
    agents = load_config()

    assert len(agents) == 2
    assert agents[0].name == "claude"
    assert agents[1].name == "codex"


def test_load_config_loads_from_file(mock_config_file):
    """Test load_config successfully loads from config file."""
    agents = load_config()

    assert len(agents) == 2
    assert agents[0].name == "claude"
    assert agents[0].display_name == "Claude"
    assert agents[0].home_dir == "/custom/claude"
    assert agents[0].enabled is True

    assert agents[1].name == "codex"
    assert agents[1].display_name == "Codex Pro"
    assert agents[1].home_dir == "/custom/codex"
    assert agents[1].enabled is False


def test_load_config_handles_json_decode_error(mock_home_dir):
    """Test load_config returns defaults on JSON decode error."""
    config_dir = mock_home_dir / ".config" / "find-session"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    config_path.write_text("{ invalid json }")

    agents = load_config()

    # Should fall back to defaults
    assert len(agents) == 2
    assert agents[0].name == "claude"


def test_load_config_handles_key_error(mock_home_dir):
    """Test load_config returns defaults on KeyError (missing required fields)."""
    config_dir = mock_home_dir / ".config" / "find-session"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    # Missing 'name' field
    config_data = {"agents": [{"display_name": "Test"}]}
    config_path.write_text(json.dumps(config_data))

    agents = load_config()

    # Should fall back to defaults
    assert len(agents) == 2


def test_load_config_handles_io_error(mock_home_dir):
    """Test load_config returns defaults on IOError."""
    config_dir = mock_home_dir / ".config" / "find-session"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    config_path.write_text('{"agents": []}')

    # Make file unreadable
    config_path.chmod(0o000)

    try:
        agents = load_config()
        # Should fall back to defaults
        assert len(agents) == 2
    finally:
        # Restore permissions for cleanup
        config_path.chmod(0o644)


def test_load_config_uses_title_case_for_missing_display_name(mock_home_dir):
    """Test load_config uses title case for display_name if not provided."""
    config_dir = mock_home_dir / ".config" / "find-session"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"

    config_data = {
        "agents": [
            {"name": "custom-agent", "enabled": True}
        ]
    }
    config_path.write_text(json.dumps(config_data))

    agents = load_config()

    assert agents[0].display_name == "Custom-Agent"


# ==================== Test search_all_agents ====================


@patch("claude_code_tools.find_session.get_codex_home")
@patch("claude_code_tools.find_session.find_codex_sessions")
@patch("claude_code_tools.find_session.find_claude_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_combines_results(
    mock_load_config,
    mock_find_claude,
    mock_find_codex,
    mock_get_codex_home,
    sample_claude_sessions,
    sample_codex_sessions,
):
    """Test search_all_agents combines results from all agents."""
    # Setup mocks
    mock_load_config.return_value = [
        AgentConfig("claude", "Claude", None, True),
        AgentConfig("codex", "Codex", None, True),
    ]
    mock_find_claude.return_value = sample_claude_sessions
    mock_find_codex.return_value = sample_codex_sessions
    mock_codex_home = Mock()
    mock_codex_home.exists.return_value = True
    mock_get_codex_home.return_value = mock_codex_home

    # Search
    results = search_all_agents(["test"], num_matches=10)

    # Verify results
    assert len(results) == 3
    assert results[0]["agent"] == "codex"  # Most recent
    assert results[1]["agent"] == "claude"  # session-id-2
    assert results[2]["agent"] == "claude"  # session-id-1


@patch("claude_code_tools.find_session.find_claude_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_filters_by_agent(
    mock_load_config,
    mock_find_claude,
    sample_claude_sessions,
):
    """Test search_all_agents filters by specified agents."""
    mock_load_config.return_value = [
        AgentConfig("claude", "Claude", None, True),
        AgentConfig("codex", "Codex", None, True),
    ]
    mock_find_claude.return_value = sample_claude_sessions

    # Search only Claude
    results = search_all_agents(["test"], agents=["claude"])

    # Should only have Claude sessions
    assert all(r["agent"] == "claude" for r in results)
    assert len(results) == 2


@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_filters_disabled_agents(
    mock_load_config,
):
    """Test search_all_agents skips disabled agents."""
    mock_load_config.return_value = [
        AgentConfig("claude", "Claude", None, True),
        AgentConfig("codex", "Codex", None, False),  # Disabled
    ]

    with patch("claude_code_tools.find_session.find_claude_sessions") as mock_find_claude:
        mock_find_claude.return_value = []

        results = search_all_agents(["test"])

        # Should only search Claude
        mock_find_claude.assert_called_once()


@patch("claude_code_tools.find_session.find_claude_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_respects_num_matches(
    mock_load_config,
    mock_find_claude,
):
    """Test search_all_agents limits results to num_matches."""
    mock_load_config.return_value = [
        AgentConfig("claude", "Claude", None, True),
    ]

    # Create 20 sessions
    many_sessions = [
        (
            f"session-{i}",
            1700000000.0 + i,
            1699000000.0 + i,
            100,
            "project",
            "preview",
            "/home/user/project",
            "main"
        )
        for i in range(20)
    ]
    mock_find_claude.return_value = many_sessions

    results = search_all_agents(["test"], num_matches=5)

    assert len(results) == 5


@patch("claude_code_tools.find_session.find_claude_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_handles_missing_branch(
    mock_load_config,
    mock_find_claude,
):
    """Test search_all_agents handles sessions without branch field."""
    mock_load_config.return_value = [
        AgentConfig("claude", "Claude", None, True),
    ]

    # Session without branch (7 elements instead of 8)
    sessions_no_branch = [
        (
            "session-1",
            1700000000.0,
            1699000000.0,
            100,
            "project",
            "preview",
            "/home/user/project",
            # No branch
        )
    ]
    mock_find_claude.return_value = sessions_no_branch

    results = search_all_agents(["test"])

    assert results[0]["branch"] == ""


@patch("claude_code_tools.find_session.get_codex_home")
@patch("claude_code_tools.find_session.find_codex_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_skips_nonexistent_codex_home(
    mock_load_config,
    mock_find_codex,
    mock_get_codex_home,
):
    """Test search_all_agents skips Codex when home doesn't exist."""
    mock_load_config.return_value = [
        AgentConfig("codex", "Codex", None, True),
    ]
    mock_codex_home = Mock()
    mock_codex_home.exists.return_value = False
    mock_get_codex_home.return_value = mock_codex_home

    results = search_all_agents(["test"])

    # Should return empty list
    assert len(results) == 0
    # Should not call find_codex_sessions
    mock_find_codex.assert_not_called()


@patch("claude_code_tools.find_session.find_claude_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_uses_custom_claude_home(
    mock_load_config,
    mock_find_claude,
):
    """Test search_all_agents uses custom Claude home directory."""
    mock_load_config.return_value = [
        AgentConfig("claude", "Claude", "/custom/claude", True),
    ]
    mock_find_claude.return_value = []

    search_all_agents(["test"], claude_home="/override/claude")

    # Should use override home
    mock_find_claude.assert_called_once_with(
        ["test"], global_search=False, claude_home="/override/claude"
    )


@patch("claude_code_tools.find_session.get_codex_home")
@patch("claude_code_tools.find_session.find_codex_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_uses_custom_codex_home(
    mock_load_config,
    mock_find_codex,
    mock_get_codex_home,
):
    """Test search_all_agents uses custom Codex home directory."""
    mock_load_config.return_value = [
        AgentConfig("codex", "Codex", "/custom/codex", True),
    ]
    mock_codex_home = Mock()
    mock_codex_home.exists.return_value = True
    mock_get_codex_home.return_value = mock_codex_home
    mock_find_codex.return_value = []

    search_all_agents(["test"], codex_home="/override/codex")

    # Should use override home
    mock_get_codex_home.assert_called_once_with("/override/codex")


# ==================== Test display_interactive_ui ====================


def test_display_interactive_ui_returns_none_without_rich(mock_rich_unavailable):
    """Test display_interactive_ui returns None when rich is unavailable."""
    result = display_interactive_ui([], [])

    assert result is None


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_shows_no_sessions_message(mock_rich_available):
    """Test display_interactive_ui shows message when no sessions found."""
    from claude_code_tools import find_session

    with patch.object(find_session, 'Console') as mock_console_class:
        mock_console = Mock()
        mock_console_class.return_value = mock_console

        result = display_interactive_ui([], ["test"])

        assert result is None
        mock_console.print.assert_called_with("[red]No sessions found[/red]")


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_displays_table(mock_rich_available):
    """Test display_interactive_ui displays table with sessions."""
    from claude_code_tools import find_session

    sessions = [
        {
            "agent_display": "Claude",
            "session_id": "session-1",
            "project": "project-1",
            "branch": "main",
            "mod_time": 1700000000.0,
            "lines": 150,
            "preview": "Test preview"
        }
    ]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        mock_prompt_class.ask.return_value = "1"

        result = display_interactive_ui(sessions, ["test"], num_matches=10)

        # Verify table was created and printed
        mock_table_class.assert_called_once()
        mock_console.print.assert_any_call(mock_table)
        assert result == sessions[0]


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_handles_empty_input(mock_rich_available):
    """Test display_interactive_ui handles empty input (cancel)."""
    from claude_code_tools import find_session

    sessions = [{
        "agent_display": "Claude",
        "session_id": "test",
        "mod_time": 1700000000.0,
        "project": "test-project",
        "lines": 100,
        "preview": "test preview"
    }]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        mock_prompt_class.ask.return_value = ""

        result = display_interactive_ui(sessions, ["test"], num_matches=10)

        assert result is None
        mock_console.print.assert_any_call("[yellow]Cancelled[/yellow]")


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_handles_keyboard_interrupt(mock_rich_available):
    """Test display_interactive_ui handles KeyboardInterrupt."""
    from claude_code_tools import find_session

    sessions = [{
        "agent_display": "Claude",
        "session_id": "test",
        "mod_time": 1700000000.0,
        "project": "test-project",
        "lines": 100,
        "preview": "test preview"
    }]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        mock_prompt_class.ask.side_effect = KeyboardInterrupt()

        result = display_interactive_ui(sessions, ["test"], num_matches=10)

        assert result is None
        mock_console.print.assert_any_call("\n[yellow]Cancelled[/yellow]")


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_handles_eof_error(mock_rich_available):
    """Test display_interactive_ui handles EOFError."""
    from claude_code_tools import find_session

    sessions = [{
        "agent_display": "Claude",
        "session_id": "test",
        "mod_time": 1700000000.0,
        "project": "test-project",
        "lines": 100,
        "preview": "test preview"
    }]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        mock_prompt_class.ask.side_effect = EOFError()

        result = display_interactive_ui(sessions, ["test"], num_matches=10)

        assert result is None
        mock_console.print.assert_any_call("\n[yellow]Cancelled (EOF)[/yellow]")


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_handles_invalid_choice(mock_rich_available):
    """Test display_interactive_ui handles invalid choice and retries."""
    from claude_code_tools import find_session

    sessions = [{
        "agent_display": "Claude",
        "session_id": "test",
        "mod_time": 1700000000.0,
        "project": "test-project",
        "lines": 100,
        "preview": "test preview"
    }]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        # First return invalid, then valid
        mock_prompt_class.ask.side_effect = ["999", "1"]

        result = display_interactive_ui(sessions, ["test"], num_matches=10)

        assert result == sessions[0]
        # Should show error message
        mock_console.print.assert_any_call("[red]Invalid choice. Please try again.[/red]")


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_handles_non_numeric_input(mock_rich_available):
    """Test display_interactive_ui handles non-numeric input."""
    from claude_code_tools import find_session

    sessions = [{
        "agent_display": "Claude",
        "session_id": "test",
        "mod_time": 1700000000.0,
        "project": "test-project",
        "lines": 100,
        "preview": "test preview"
    }]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        # First return non-numeric, then valid
        mock_prompt_class.ask.side_effect = ["abc", "1"]

        result = display_interactive_ui(sessions, ["test"], num_matches=10)

        assert result == sessions[0]
        # Should show error message
        mock_console.print.assert_any_call("[red]Invalid choice. Please try again.[/red]")


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_uses_stderr_in_shell_mode(mock_rich_available):
    """Test display_interactive_ui uses stderr console in shell mode."""
    from claude_code_tools import find_session

    sessions = [{
        "agent_display": "Claude",
        "session_id": "test",
        "mod_time": 1700000000.0,
        "project": "test-project",
        "lines": 100,
        "preview": "test preview"
    }]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        mock_prompt_class.ask.return_value = ""

        display_interactive_ui(sessions, ["test"], stderr_mode=True, num_matches=10)

        # Should create console with stderr
        mock_console_class.assert_called_once_with(file=sys.stderr)


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_limits_displayed_sessions(mock_rich_available):
    """Test display_interactive_ui limits displayed sessions to num_matches."""
    from claude_code_tools import find_session

    # Create 20 sessions with all required fields
    sessions = [
        {
            "agent_display": "Claude",
            "session_id": f"test-{i}",
            "mod_time": 1700000000.0 + i,
            "project": f"project-{i}",
            "lines": 100,
            "preview": "test preview"
        }
        for i in range(20)
    ]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        mock_prompt_class.ask.return_value = ""

        display_interactive_ui(sessions, ["test"], num_matches=5)

        # Should only display 5 rows in table
        assert mock_table.add_row.call_count == 5


# ==================== Test show_action_menu ====================


@patch("builtins.input")
def test_show_action_menu_returns_resume_by_default(mock_input):
    """Test show_action_menu returns 'resume' on empty input."""
    mock_input.return_value = ""

    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    result = show_action_menu(session)

    assert result == "resume"


@patch("builtins.input")
def test_show_action_menu_returns_resume_on_choice_1(mock_input):
    """Test show_action_menu returns 'resume' on choice 1."""
    mock_input.return_value = "1"

    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    result = show_action_menu(session)

    assert result == "resume"


@patch("builtins.input")
def test_show_action_menu_returns_path_on_choice_2(mock_input):
    """Test show_action_menu returns 'path' on choice 2."""
    mock_input.return_value = "2"

    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    result = show_action_menu(session)

    assert result == "path"


@patch("builtins.input")
def test_show_action_menu_returns_copy_on_choice_3(mock_input):
    """Test show_action_menu returns 'copy' on choice 3."""
    mock_input.return_value = "3"

    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    result = show_action_menu(session)

    assert result == "copy"


@patch("builtins.input")
def test_show_action_menu_returns_none_on_invalid_choice(mock_input):
    """Test show_action_menu returns None on invalid choice."""
    mock_input.return_value = "99"

    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    result = show_action_menu(session)

    assert result is None


@patch("builtins.input")
def test_show_action_menu_handles_keyboard_interrupt(mock_input):
    """Test show_action_menu handles KeyboardInterrupt."""
    mock_input.side_effect = KeyboardInterrupt()

    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    result = show_action_menu(session)

    assert result is None


@patch("builtins.input")
def test_show_action_menu_shows_branch_if_present(mock_input, capsys):
    """Test show_action_menu displays branch if present."""
    mock_input.return_value = "1"

    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project",
        "branch": "feature-branch"
    }

    show_action_menu(session)

    captured = capsys.readouterr()
    assert "Branch: feature-branch" in captured.out


def test_show_action_menu_stderr_mode():
    """Test show_action_menu uses stderr in stderr_mode."""
    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    with patch("sys.stdin.readline", return_value="1\n"):
        with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
            show_action_menu(session, stderr_mode=True)

            output = mock_stderr.getvalue()
            assert "Session:" in output


def test_show_action_menu_stderr_mode_with_stdin_readline(capsys):
    """Test show_action_menu in stderr mode reads from stdin."""
    session = {
        "session_id": "test-session-id",
        "agent_display": "Claude",
        "project": "test-project"
    }

    with patch("sys.stdin.readline", return_value="1\n"):
        result = show_action_menu(session, stderr_mode=True)

    assert result == "resume"
    captured = capsys.readouterr()
    assert "Enter choice" in captured.err


# ==================== Test handle_action ====================


@patch("claude_code_tools.find_session.resume_claude_session")
def test_handle_action_resume_claude(mock_resume):
    """Test handle_action calls resume_claude_session for Claude agent."""
    session = {
        "agent": "claude",
        "session_id": "test-session",
        "cwd": "/home/user/project",
        "claude_home": "/custom/claude"
    }

    handle_action(session, "resume", shell_mode=False)

    mock_resume.assert_called_once_with(
        "test-session",
        "/home/user/project",
        shell_mode=False,
        claude_home="/custom/claude"
    )


@patch("claude_code_tools.find_session.resume_codex_session")
def test_handle_action_resume_codex(mock_resume):
    """Test handle_action calls resume_codex_session for Codex agent."""
    session = {
        "agent": "codex",
        "session_id": "test-session",
        "cwd": "/home/user/project"
    }

    handle_action(session, "resume", shell_mode=True)

    mock_resume.assert_called_once_with(
        "test-session",
        "/home/user/project",
        shell_mode=True
    )


@patch("claude_code_tools.find_session.get_claude_session_file_path")
def test_handle_action_path_claude(mock_get_path, capsys):
    """Test handle_action prints path for Claude agent."""
    mock_get_path.return_value = "/path/to/session.jsonl"

    session = {
        "agent": "claude",
        "session_id": "test-session",
        "cwd": "/home/user/project",
        "claude_home": "/custom/claude"
    }

    handle_action(session, "path")

    captured = capsys.readouterr()
    assert "/path/to/session.jsonl" in captured.out
    mock_get_path.assert_called_once_with(
        "test-session",
        "/home/user/project",
        claude_home="/custom/claude"
    )


def test_handle_action_path_codex(capsys):
    """Test handle_action prints path for Codex agent."""
    session = {
        "agent": "codex",
        "file_path": "/path/to/codex/session.jsonl"
    }

    handle_action(session, "path")

    captured = capsys.readouterr()
    assert "/path/to/codex/session.jsonl" in captured.out


def test_handle_action_path_codex_no_file_path(capsys):
    """Test handle_action handles missing file_path for Codex."""
    session = {
        "agent": "codex"
    }

    handle_action(session, "path")

    captured = capsys.readouterr()
    assert "Unknown" in captured.out


@patch("claude_code_tools.find_session.copy_claude_session_file")
@patch("claude_code_tools.find_session.get_claude_session_file_path")
def test_handle_action_copy_claude(mock_get_path, mock_copy):
    """Test handle_action copies Claude session file."""
    mock_get_path.return_value = "/path/to/session.jsonl"

    session = {
        "agent": "claude",
        "session_id": "test-session",
        "cwd": "/home/user/project",
        "claude_home": "/custom/claude"
    }

    handle_action(session, "copy")

    mock_get_path.assert_called_once_with(
        "test-session",
        "/home/user/project",
        claude_home="/custom/claude"
    )
    mock_copy.assert_called_once_with("/path/to/session.jsonl")


@patch("claude_code_tools.find_session.copy_codex_session_file")
def test_handle_action_copy_codex(mock_copy):
    """Test handle_action copies Codex session file."""
    session = {
        "agent": "codex",
        "file_path": "/path/to/codex/session.jsonl"
    }

    handle_action(session, "copy")

    mock_copy.assert_called_once_with("/path/to/codex/session.jsonl")


@patch("claude_code_tools.find_session.copy_codex_session_file")
def test_handle_action_copy_codex_no_file_path(mock_copy):
    """Test handle_action handles missing file_path when copying Codex."""
    session = {
        "agent": "codex"
    }

    handle_action(session, "copy")

    mock_copy.assert_called_once_with("")


# ==================== Test main ====================


@patch("claude_code_tools.find_session.handle_action")
@patch("claude_code_tools.find_session.show_action_menu")
@patch("claude_code_tools.find_session.display_interactive_ui")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_with_keywords(
    mock_search,
    mock_display_ui,
    mock_show_menu,
    mock_handle_action,
    mock_rich_available,
):
    """Test main function with keywords."""
    mock_search.return_value = [
        {
            "agent": "claude",
            "session_id": "test-session",
            "mod_time": 1700000000.0
        }
    ]
    mock_display_ui.return_value = mock_search.return_value[0]
    mock_show_menu.return_value = "resume"

    with patch("sys.argv", ["find-session", "test,keywords"]):
        main()

    mock_search.assert_called_once()
    assert mock_search.call_args[0][0] == ["test", "keywords"]


@patch("claude_code_tools.find_session.search_all_agents")
def test_main_no_results(mock_search, mock_rich_available):
    """Test main function when no sessions found."""
    from claude_code_tools import find_session

    mock_search.return_value = []

    with patch.object(find_session, 'Console') as mock_console_class:
        mock_console = Mock()
        mock_console_class.return_value = mock_console

        with patch("sys.argv", ["find-session", "nonexistent"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Should print message via Console
        assert mock_console.print.called


@patch("claude_code_tools.find_session.search_all_agents")
def test_main_no_results_without_rich(mock_search, mock_rich_unavailable, capsys):
    """Test main function without rich when no sessions found."""
    mock_search.return_value = []

    with patch("sys.argv", ["find-session", "nonexistent"]):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "No sessions found" in captured.err


@patch("rich.console.Console")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_global_search_flag(mock_search, mock_console_class, mock_rich_available):
    """Test main function with global search flag."""
    mock_search.return_value = []
    mock_console = Mock()
    mock_console_class.return_value = mock_console

    with patch("sys.argv", ["find-session", "-g"]):
        with pytest.raises(SystemExit):
            main()

    assert mock_search.call_args[1]["global_search"] is True


@patch("rich.console.Console")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_num_matches_flag(mock_search, mock_console_class, mock_rich_available):
    """Test main function with num_matches flag."""
    mock_search.return_value = []
    mock_console = Mock()
    mock_console_class.return_value = mock_console

    with patch("sys.argv", ["find-session", "-n", "20"]):
        with pytest.raises(SystemExit):
            main()

    assert mock_search.call_args[1]["num_matches"] == 20


@patch("rich.console.Console")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_agents_filter(mock_search, mock_console_class, mock_rich_available):
    """Test main function with agents filter."""
    mock_search.return_value = []
    mock_console = Mock()
    mock_console_class.return_value = mock_console

    with patch("sys.argv", ["find-session", "--agents", "claude", "codex"]):
        with pytest.raises(SystemExit):
            main()

    assert mock_search.call_args[1]["agents"] == ["claude", "codex"]


@patch("rich.console.Console")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_custom_claude_home(mock_search, mock_console_class, mock_rich_available):
    """Test main function with custom Claude home."""
    mock_search.return_value = []
    mock_console = Mock()
    mock_console_class.return_value = mock_console

    with patch("sys.argv", ["find-session", "--claude-home", "/custom/claude"]):
        with pytest.raises(SystemExit):
            main()

    assert mock_search.call_args[1]["claude_home"] == "/custom/claude"


@patch("rich.console.Console")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_custom_codex_home(mock_search, mock_console_class, mock_rich_available):
    """Test main function with custom Codex home."""
    mock_search.return_value = []
    mock_console = Mock()
    mock_console_class.return_value = mock_console

    with patch("sys.argv", ["find-session", "--codex-home", "/custom/codex"]):
        with pytest.raises(SystemExit):
            main()

    assert mock_search.call_args[1]["codex_home"] == "/custom/codex"


@patch("claude_code_tools.find_session.handle_action")
@patch("claude_code_tools.find_session.show_action_menu")
@patch("claude_code_tools.find_session.display_interactive_ui")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_shell_mode(
    mock_search,
    mock_display_ui,
    mock_show_menu,
    mock_handle_action,
    mock_rich_available,
):
    """Test main function in shell mode."""
    session = {"agent": "claude", "session_id": "test"}
    mock_search.return_value = [session]
    mock_display_ui.return_value = session
    mock_show_menu.return_value = "resume"

    with patch("sys.argv", ["find-session", "--shell", "test"]):
        main()

    # Should pass stderr_mode=True to UI
    assert mock_display_ui.call_args[1]["stderr_mode"] is True
    assert mock_show_menu.call_args[1]["stderr_mode"] is True
    # Should pass shell_mode=True to action handler
    assert mock_handle_action.call_args[1]["shell_mode"] is True


@patch("claude_code_tools.find_session.search_all_agents")
def test_main_fallback_display_without_rich(mock_search, mock_rich_unavailable, capsys):
    """Test main function fallback display when rich unavailable."""
    sessions = [
        {
            "agent_display": "Claude",
            "session_id": "test-session-1",
            "project": "project-1",
            "branch": "main"
        },
        {
            "agent_display": "Codex",
            "session_id": "test-session-2",
            "project": "project-2",
            "branch": "develop"
        }
    ]
    mock_search.return_value = sessions

    with patch("sys.argv", ["find-session", "test"]):
        main()

    captured = capsys.readouterr()
    assert "Matching sessions:" in captured.out
    assert "[Claude]" in captured.out
    assert "[Codex]" in captured.out


@patch("claude_code_tools.find_session.display_interactive_ui")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_user_cancels_selection(
    mock_search,
    mock_display_ui,
    mock_rich_available,
):
    """Test main function when user cancels session selection."""
    mock_search.return_value = [{"agent": "claude", "session_id": "test"}]
    mock_display_ui.return_value = None  # User cancelled

    with patch("sys.argv", ["find-session", "test"]):
        main()  # Should not raise, just exit gracefully


@patch("claude_code_tools.find_session.handle_action")
@patch("claude_code_tools.find_session.show_action_menu")
@patch("claude_code_tools.find_session.display_interactive_ui")
@patch("claude_code_tools.find_session.search_all_agents")
def test_main_user_cancels_action_menu(
    mock_search,
    mock_display_ui,
    mock_show_menu,
    mock_handle_action,
    mock_rich_available,
):
    """Test main function when user cancels action menu."""
    session = {"agent": "claude", "session_id": "test"}
    mock_search.return_value = [session]
    mock_display_ui.return_value = session
    mock_show_menu.return_value = None  # User cancelled

    with patch("sys.argv", ["find-session", "test"]):
        main()

    # Should not call handle_action
    mock_handle_action.assert_not_called()


# ==================== Test Edge Cases ====================


def test_agent_config_dataclass():
    """Test AgentConfig dataclass initialization."""
    config = AgentConfig(
        name="test",
        display_name="Test Agent",
        home_dir="/test/home",
        enabled=False
    )

    assert config.name == "test"
    assert config.display_name == "Test Agent"
    assert config.home_dir == "/test/home"
    assert config.enabled is False


def test_agent_config_default_enabled():
    """Test AgentConfig has enabled=True by default."""
    config = AgentConfig(
        name="test",
        display_name="Test Agent",
        home_dir=None
    )

    assert config.enabled is True


@patch("claude_code_tools.find_session.find_claude_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_empty_keywords(mock_load_config, mock_find_claude):
    """Test search_all_agents handles empty keywords list."""
    mock_load_config.return_value = [
        AgentConfig("claude", "Claude", None, True),
    ]
    mock_find_claude.return_value = []

    results = search_all_agents([])

    # Should still call search with empty list
    mock_find_claude.assert_called_once_with([], global_search=False, claude_home=None)


@patch("claude_code_tools.find_session.get_codex_home")
@patch("claude_code_tools.find_session.find_codex_sessions")
@patch("claude_code_tools.find_session.load_config")
def test_search_all_agents_codex_missing_branch(
    mock_load_config,
    mock_find_codex,
    mock_get_codex_home,
):
    """Test search_all_agents handles Codex sessions without branch."""
    mock_load_config.return_value = [
        AgentConfig("codex", "Codex", None, True),
    ]
    mock_codex_home = Mock()
    mock_codex_home.exists.return_value = True
    mock_get_codex_home.return_value = mock_codex_home

    # Codex session without branch
    mock_find_codex.return_value = [
        {
            "session_id": "codex-1",
            "mod_time": 1700000000.0,
            "lines": 100,
            "project": "test",
            "preview": "preview",
            "cwd": "/home/user/test"
            # No branch field
        }
    ]

    results = search_all_agents(["test"])

    assert results[0]["branch"] == ""


def test_main_empty_keywords():
    """Test main function with empty keywords string."""
    with patch("claude_code_tools.find_session.search_all_agents") as mock_search:
        mock_search.return_value = []

        with patch("sys.argv", ["find-session", ""]):
            with pytest.raises(SystemExit):
                main()

        # Should pass empty list
        assert mock_search.call_args[0][0] == []


def test_main_keywords_with_whitespace():
    """Test main function handles keywords with extra whitespace."""
    with patch("claude_code_tools.find_session.search_all_agents") as mock_search:
        mock_search.return_value = []

        with patch("sys.argv", ["find-session", " test , keyword , "]):
            with pytest.raises(SystemExit):
                main()

        # Should strip whitespace
        assert mock_search.call_args[0][0] == ["test", "keyword"]


@pytest.mark.skip(reason="Requires rich library for integration testing")
def test_display_interactive_ui_missing_optional_fields(mock_rich_available):
    """Test display_interactive_ui handles missing optional fields gracefully."""
    from claude_code_tools import find_session

    # Session with minimal fields (no branch)
    sessions = [
        {
            "agent_display": "Claude",
            "session_id": "test",
            "project": "project",
            # No branch
            "mod_time": 1700000000.0,
            "lines": 100,
            "preview": "test"
        }
    ]

    with patch.object(find_session, 'Console') as mock_console_class, \
         patch.object(find_session, 'Table') as mock_table_class, \
         patch.object(find_session, 'Prompt') as mock_prompt_class:

        mock_console = Mock()
        mock_console_class.return_value = mock_console
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        mock_prompt_class.ask.return_value = "1"

        result = display_interactive_ui(sessions, [], num_matches=10)

        # Should handle missing branch gracefully
        assert result == sessions[0]
        # Check that N/A was used for branch
        call_args = mock_table.add_row.call_args
        assert "N/A" in call_args[0]  # branch_display should be N/A
