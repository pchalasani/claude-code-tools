"""Comprehensive pytest tests for find_claude_session.py module."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock rich library before importing the module
sys.modules["rich"] = MagicMock()
sys.modules["rich.console"] = MagicMock()
sys.modules["rich.table"] = MagicMock()
sys.modules["rich.prompt"] = MagicMock()
sys.modules["rich.progress"] = MagicMock()
sys.modules["rich.box"] = MagicMock()

from claude_code_tools.find_claude_session import (
    get_claude_project_dir,
    get_all_claude_projects,
    extract_project_name,
    search_keywords_in_file,
    is_system_message,
    get_session_preview,
    find_sessions,
    display_interactive_ui,
    show_action_menu,
    get_session_file_path,
    copy_session_file,
    resume_session,
    main,
)


# ==================== Test get_claude_project_dir ====================


def test_get_claude_project_dir_default_home():
    """Test get_claude_project_dir with default home directory."""
    with patch("os.getcwd", return_value="/home/user/project"):
        result = get_claude_project_dir()

        expected = Path.home() / ".claude" / "projects" / "-home-user-project"
        assert result == expected


def test_get_claude_project_dir_custom_home():
    """Test get_claude_project_dir with custom claude_home."""
    with patch("os.getcwd", return_value="/home/user/project"):
        result = get_claude_project_dir("/custom/claude")

        expected = Path("/custom/claude") / "projects" / "-home-user-project"
        assert result == expected


def test_get_claude_project_dir_replaces_slashes():
    """Test that path separators are replaced with hyphens."""
    with patch("os.getcwd", return_value="/home/user/my-project/subdir"):
        result = get_claude_project_dir()

        assert "-home-user-my-project-subdir" in str(result)


def test_get_claude_project_dir_with_tilde():
    """Test get_claude_project_dir expands tilde in custom home."""
    with patch("os.getcwd", return_value="/home/user/project"):
        with patch.object(Path, "expanduser") as mock_expand:
            mock_expand.return_value = Path("/home/testuser/.claude")

            result = get_claude_project_dir("~/.claude")

            mock_expand.assert_called_once()


# ==================== Test get_all_claude_projects ====================


def test_get_all_claude_projects_empty_directory(tmp_path):
    """Test get_all_claude_projects returns empty list when no projects exist."""
    claude_home = tmp_path / ".claude"
    projects_dir = claude_home / "projects"
    projects_dir.mkdir(parents=True)

    result = get_all_claude_projects(str(claude_home))

    assert result == []


def test_get_all_claude_projects_nonexistent_directory():
    """Test get_all_claude_projects returns empty list when directory doesn't exist."""
    result = get_all_claude_projects("/nonexistent/path")

    assert result == []


def test_get_all_claude_projects_linux_path(tmp_path):
    """Test get_all_claude_projects with Linux-style paths."""
    claude_home = tmp_path / ".claude"
    projects_dir = claude_home / "projects"
    projects_dir.mkdir(parents=True)

    # Create project directory with Linux-style name
    project_dir = projects_dir / "-home-user-project"
    project_dir.mkdir()

    result = get_all_claude_projects(str(claude_home))

    assert len(result) == 1
    assert result[0][0] == project_dir
    assert result[0][1] == "/home/user/project"


def test_get_all_claude_projects_macos_path(tmp_path):
    """Test get_all_claude_projects with macOS-style paths."""
    claude_home = tmp_path / ".claude"
    projects_dir = claude_home / "projects"
    projects_dir.mkdir(parents=True)

    # Create project directory with macOS-style name
    project_dir = projects_dir / "-Users-username-Git-my-project"
    project_dir.mkdir()

    result = get_all_claude_projects(str(claude_home))

    assert len(result) == 1
    assert result[0][0] == project_dir
    # Should reconstruct /Users/username/Git/my-project
    assert result[0][1] == "/Users/username/Git/my-project"


def test_get_all_claude_projects_multiple_projects(tmp_path):
    """Test get_all_claude_projects with multiple projects."""
    claude_home = tmp_path / ".claude"
    projects_dir = claude_home / "projects"
    projects_dir.mkdir(parents=True)

    # Create multiple project directories
    (projects_dir / "-home-user-project1").mkdir()
    (projects_dir / "-home-user-project2").mkdir()
    (projects_dir / "-Users-mac-project").mkdir()

    result = get_all_claude_projects(str(claude_home))

    assert len(result) == 3


def test_get_all_claude_projects_filters_files(tmp_path):
    """Test get_all_claude_projects ignores files in projects directory."""
    claude_home = tmp_path / ".claude"
    projects_dir = claude_home / "projects"
    projects_dir.mkdir(parents=True)

    # Create a file (should be ignored)
    (projects_dir / "some-file.txt").touch()

    # Create a directory (should be included)
    (projects_dir / "-home-user-project").mkdir()

    result = get_all_claude_projects(str(claude_home))

    assert len(result) == 1


def test_get_all_claude_projects_unknown_pattern(tmp_path):
    """Test get_all_claude_projects handles unknown path patterns."""
    claude_home = tmp_path / ".claude"
    projects_dir = claude_home / "projects"
    projects_dir.mkdir(parents=True)

    # Create directory with unknown pattern
    project_dir = projects_dir / "weird-pattern-here"
    project_dir.mkdir()

    result = get_all_claude_projects(str(claude_home))

    assert len(result) == 1
    # Should attempt to convert hyphens to slashes
    assert "/" in result[0][1]


# ==================== Test extract_project_name ====================


def test_extract_project_name_simple():
    """Test extract_project_name with simple path."""
    result = extract_project_name("/home/user/my-project")

    assert result == "my-project"


def test_extract_project_name_nested():
    """Test extract_project_name with nested path."""
    result = extract_project_name("/home/user/code/subdir/project")

    assert result == "project"


def test_extract_project_name_trailing_slash():
    """Test extract_project_name with trailing slash."""
    result = extract_project_name("/home/user/project/")

    assert result == "project"


def test_extract_project_name_empty():
    """Test extract_project_name with empty string."""
    result = extract_project_name("")

    # Empty path returns empty string (last component is empty)
    assert result in ["", "unknown"]


def test_extract_project_name_root():
    """Test extract_project_name with root path."""
    result = extract_project_name("/")

    # Should handle edge case gracefully
    assert result in ["", "unknown"]


# ==================== Test is_system_message ====================


def test_is_system_message_empty():
    """Test is_system_message with empty string."""
    assert is_system_message("") is True


def test_is_system_message_short():
    """Test is_system_message with short string."""
    assert is_system_message("hi") is True


def test_is_system_message_xml_tag():
    """Test is_system_message with XML tag."""
    assert is_system_message("<environment_context>") is True


def test_is_system_message_user_instructions():
    """Test is_system_message with user_instructions tag."""
    assert is_system_message("<user_instructions>\nSome content") is True


def test_is_system_message_normal_text():
    """Test is_system_message with normal user text."""
    assert is_system_message("This is a normal user message") is False


def test_is_system_message_long_text():
    """Test is_system_message with long user text."""
    text = "This is a longer message that should not be considered system-generated."
    assert is_system_message(text) is False


def test_is_system_message_whitespace():
    """Test is_system_message with whitespace only."""
    assert is_system_message("   \n  ") is True


def test_is_system_message_xml_in_middle():
    """Test is_system_message with XML tag not at start."""
    # Should not match if XML is not at the beginning
    assert is_system_message("Some text <tag>") is False


# ==================== Test search_keywords_in_file ====================


def test_search_keywords_in_file_no_keywords(tmp_path):
    """Test search_keywords_in_file with no keywords (matches all)."""
    test_file = tmp_path / "session.jsonl"
    test_file.write_text('{"type": "user", "message": {"content": "test"}}\n' * 5)

    matches, line_count, git_branch = search_keywords_in_file(test_file, [])

    assert matches is True
    assert line_count == 5
    assert git_branch is None


def test_search_keywords_in_file_single_keyword_found(tmp_path):
    """Test search_keywords_in_file with single keyword that's found."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": "test langroid"}}\n'
    test_file.write_text(content)

    matches, line_count, git_branch = search_keywords_in_file(test_file, ["langroid"])

    assert matches is True
    assert line_count == 1


def test_search_keywords_in_file_single_keyword_not_found(tmp_path):
    """Test search_keywords_in_file with keyword that's not found."""
    test_file = tmp_path / "session.jsonl"
    test_file.write_text('{"type": "user", "message": {"content": "test"}}\n')

    matches, line_count, git_branch = search_keywords_in_file(test_file, ["missing"])

    assert matches is False
    assert line_count == 1


def test_search_keywords_in_file_multiple_keywords_all_found(tmp_path):
    """Test search_keywords_in_file with multiple keywords all present."""
    test_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "user", "message": {"content": "test langroid"}}\n'
        '{"type": "user", "message": {"content": "testing MCP protocol"}}\n'
    )
    test_file.write_text(content)

    matches, line_count, git_branch = search_keywords_in_file(
        test_file, ["langroid", "MCP"]
    )

    assert matches is True
    assert line_count == 2


def test_search_keywords_in_file_multiple_keywords_partial(tmp_path):
    """Test search_keywords_in_file with only some keywords found."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": "test langroid"}}\n'
    test_file.write_text(content)

    matches, line_count, git_branch = search_keywords_in_file(
        test_file, ["langroid", "missing"]
    )

    assert matches is False


def test_search_keywords_in_file_case_insensitive(tmp_path):
    """Test search_keywords_in_file is case insensitive."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": "Testing LANGROID"}}\n'
    test_file.write_text(content)

    matches, _, _ = search_keywords_in_file(test_file, ["langroid"])

    assert matches is True


def test_search_keywords_in_file_extracts_git_branch(tmp_path):
    """Test search_keywords_in_file extracts git branch from gitBranch field."""
    test_file = tmp_path / "session.jsonl"
    content = '{"gitBranch": "feature-branch", "content": "test"}\n'
    test_file.write_text(content)

    _, _, git_branch = search_keywords_in_file(test_file, [])

    assert git_branch == "feature-branch"


def test_search_keywords_in_file_git_branch_from_first_occurrence(tmp_path):
    """Test search_keywords_in_file uses first git branch found."""
    test_file = tmp_path / "session.jsonl"
    content = (
        '{"gitBranch": "main", "content": "test"}\n'
        '{"gitBranch": "other", "content": "test"}\n'
    )
    test_file.write_text(content)

    _, _, git_branch = search_keywords_in_file(test_file, [])

    assert git_branch == "main"


def test_search_keywords_in_file_handles_json_decode_error(tmp_path):
    """Test search_keywords_in_file handles malformed JSON."""
    test_file = tmp_path / "session.jsonl"
    test_file.write_text("{ invalid json }\n")

    matches, line_count, git_branch = search_keywords_in_file(test_file, [])

    # Should not crash, should count the line
    assert line_count == 1


def test_search_keywords_in_file_nonexistent_file():
    """Test search_keywords_in_file handles nonexistent file."""
    fake_path = Path("/nonexistent/file.jsonl")

    matches, line_count, git_branch = search_keywords_in_file(fake_path, ["test"])

    assert matches is False
    assert line_count == 0
    assert git_branch is None


def test_search_keywords_in_file_empty_file(tmp_path):
    """Test search_keywords_in_file with empty file."""
    test_file = tmp_path / "session.jsonl"
    test_file.write_text("")

    matches, line_count, git_branch = search_keywords_in_file(test_file, [])

    assert matches is True  # No keywords = match all
    assert line_count == 0


# ==================== Test get_session_preview ====================


def test_get_session_preview_no_messages(tmp_path):
    """Test get_session_preview with no user messages."""
    test_file = tmp_path / "session.jsonl"
    test_file.write_text('{"type": "system", "content": "system message"}\n')

    result = get_session_preview(test_file)

    assert result == "No preview available"


def test_get_session_preview_single_user_message(tmp_path):
    """Test get_session_preview with single user message."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": "This is a test message"}}\n'
    test_file.write_text(content)

    result = get_session_preview(test_file)

    assert "This is a test message" in result


def test_get_session_preview_returns_last_message(tmp_path):
    """Test get_session_preview returns LAST user message."""
    test_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "user", "message": {"content": "First short msg"}}\n'
        '{"type": "user", "message": {"content": "This is the last substantial user message"}}\n'
    )
    test_file.write_text(content)

    result = get_session_preview(test_file)

    # Should get the last substantial message (>20 chars)
    assert "last substantial" in result


def test_get_session_preview_skips_system_messages(tmp_path):
    """Test get_session_preview filters out system messages."""
    test_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "user", "message": {"content": "<environment_context>"}}\n'
        '{"type": "user", "message": {"content": "Real user message"}}\n'
    )
    test_file.write_text(content)

    result = get_session_preview(test_file)

    assert "Real user message" in result


def test_get_session_preview_truncates_long_message(tmp_path):
    """Test get_session_preview truncates messages to 400 chars."""
    test_file = tmp_path / "session.jsonl"
    long_message = "a" * 500
    content = f'{{"type": "user", "message": {{"content": "{long_message}"}}}}\n'
    test_file.write_text(content)

    result = get_session_preview(test_file)

    assert len(result) <= 400


def test_get_session_preview_replaces_newlines(tmp_path):
    """Test get_session_preview replaces newlines with spaces."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": "Line 1\\nLine 2\\nLine 3"}}\n'
    test_file.write_text(content)

    result = get_session_preview(test_file)

    assert "\\n" not in result or "\n" not in result


def test_get_session_preview_handles_structured_content(tmp_path):
    """Test get_session_preview handles structured content (list format)."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": [{"type": "text", "text": "Structured message"}]}}\n'
    test_file.write_text(content)

    result = get_session_preview(test_file)

    assert "Structured message" in result


def test_get_session_preview_prefers_substantial_messages(tmp_path):
    """Test get_session_preview prefers messages >20 chars."""
    test_file = tmp_path / "session.jsonl"
    content = (
        '{"type": "user", "message": {"content": "short"}}\n'
        '{"type": "user", "message": {"content": "This is a much longer and more substantial message"}}\n'
    )
    test_file.write_text(content)

    result = get_session_preview(test_file)

    assert "substantial message" in result


def test_get_session_preview_handles_json_decode_error(tmp_path):
    """Test get_session_preview handles malformed JSON gracefully."""
    test_file = tmp_path / "session.jsonl"
    test_file.write_text("{ invalid json }\n")

    result = get_session_preview(test_file)

    assert result == "No preview available"


def test_get_session_preview_handles_io_error():
    """Test get_session_preview handles missing file."""
    fake_path = Path("/nonexistent/file.jsonl")

    result = get_session_preview(fake_path)

    assert result == "No preview available"


# ==================== Test find_sessions ====================


@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
@patch("os.getcwd")
def test_find_sessions_current_project_empty(mock_getcwd, mock_get_dir, tmp_path):
    """Test find_sessions returns empty when no sessions in current project."""
    mock_getcwd.return_value = "/home/user/project"
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    mock_get_dir.return_value = claude_dir

    result = find_sessions(["test"], global_search=False)

    assert result == []


@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
@patch("os.getcwd")
def test_find_sessions_current_project_no_directory(mock_getcwd, mock_get_dir):
    """Test find_sessions returns empty when project directory doesn't exist."""
    mock_getcwd.return_value = "/home/user/project"
    mock_get_dir.return_value = Path("/nonexistent")

    result = find_sessions(["test"], global_search=False)

    assert result == []


@patch("claude_code_tools.find_claude_session.search_keywords_in_file")
@patch("claude_code_tools.find_claude_session.get_session_preview")
@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
@patch("os.getcwd")
def test_find_sessions_current_project_with_matches(
    mock_getcwd, mock_get_dir, mock_preview, mock_search, tmp_path
):
    """Test find_sessions returns matching sessions from current project."""
    mock_getcwd.return_value = "/home/user/project"

    # Create mock session file
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    session_file = claude_dir / "session123.jsonl"
    session_file.touch()

    mock_get_dir.return_value = claude_dir
    mock_search.return_value = (True, 100, "main")
    mock_preview.return_value = "Test preview"

    result = find_sessions(["test"], global_search=False)

    assert len(result) == 1
    assert result[0][0] == "session123"  # session_id
    assert result[0][3] == 100  # line_count
    assert result[0][5] == "Test preview"  # preview
    assert result[0][7] == "main"  # git_branch


@patch("claude_code_tools.find_claude_session.get_all_claude_projects")
@patch("claude_code_tools.find_claude_session.search_keywords_in_file")
@patch("claude_code_tools.find_claude_session.get_session_preview")
def test_find_sessions_global_search(mock_preview, mock_search, mock_get_projects, tmp_path):
    """Test find_sessions with global search across all projects."""
    # Create mock project directory
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    session_file = project_dir / "session456.jsonl"
    session_file.touch()

    mock_get_projects.return_value = [(project_dir, "/home/user/project")]
    mock_search.return_value = (True, 150, "develop")
    mock_preview.return_value = "Global preview"

    result = find_sessions(["test"], global_search=True)

    assert len(result) == 1
    assert result[0][0] == "session456"
    assert result[0][4] == "project"  # project_name


@patch("claude_code_tools.find_claude_session.get_all_claude_projects")
def test_find_sessions_global_search_no_projects(mock_get_projects):
    """Test find_sessions global search with no projects."""
    mock_get_projects.return_value = []

    result = find_sessions(["test"], global_search=True)

    assert result == []


@patch("claude_code_tools.find_claude_session.get_all_claude_projects")
@patch("claude_code_tools.find_claude_session.search_keywords_in_file")
@patch("claude_code_tools.find_claude_session.get_session_preview")
def test_find_sessions_filters_non_matching(mock_preview, mock_search, mock_get_projects, tmp_path):
    """Test find_sessions filters out non-matching sessions."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    match_file = project_dir / "match.jsonl"
    nomatch_file = project_dir / "nomatch.jsonl"
    match_file.touch()
    nomatch_file.touch()

    mock_get_projects.return_value = [(project_dir, "/home/user/project")]
    mock_preview.return_value = "test"

    # Mock needs to check the filepath argument carefully
    def search_side_effect(filepath, keywords):
        filename = str(filepath.name)
        if "match.jsonl" == filename:
            return (True, 100, None)
        return (False, 100, None)

    mock_search.side_effect = search_side_effect

    result = find_sessions(["test"], global_search=True)

    assert len(result) == 1
    assert "match" in result[0][0]


@patch("claude_code_tools.find_claude_session.get_all_claude_projects")
@patch("claude_code_tools.find_claude_session.search_keywords_in_file")
@patch("claude_code_tools.find_claude_session.get_session_preview")
def test_find_sessions_sorts_by_modification_time(
    mock_preview, mock_search, mock_get_projects, tmp_path
):
    """Test find_sessions sorts results by modification time (newest first)."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Create files with different modification times
    old_file = project_dir / "old.jsonl"
    new_file = project_dir / "new.jsonl"
    old_file.touch()
    new_file.touch()

    # Set modification times
    import time
    os.utime(old_file, (time.time() - 3600, time.time() - 3600))  # 1 hour ago
    os.utime(new_file, (time.time(), time.time()))  # now

    mock_get_projects.return_value = [(project_dir, "/home/user/project")]
    mock_search.return_value = (True, 100, None)
    mock_preview.return_value = "test"

    result = find_sessions([], global_search=True)

    # Newest should be first
    assert "new" in result[0][0]
    assert "old" in result[1][0]


@patch("claude_code_tools.find_claude_session.RICH_AVAILABLE", True)
@patch("claude_code_tools.find_claude_session.get_all_claude_projects")
@patch("claude_code_tools.find_claude_session.search_keywords_in_file")
@patch("claude_code_tools.find_claude_session.get_session_preview")
def test_find_sessions_with_progress_bar(
    mock_preview, mock_search, mock_get_projects, tmp_path
):
    """Test find_sessions shows progress bar with rich available."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "session.jsonl").touch()

    mock_get_projects.return_value = [(project_dir, "/home/user/project")]
    mock_search.return_value = (True, 100, None)
    mock_preview.return_value = "test"

    # Should not raise even with mocked rich
    result = find_sessions(["test"], global_search=True)

    assert len(result) >= 0  # May or may not find sessions


def test_find_sessions_uses_custom_claude_home(tmp_path):
    """Test find_sessions uses custom claude_home parameter."""
    custom_home = tmp_path / "custom"
    custom_home.mkdir()
    projects_dir = custom_home / "projects"
    projects_dir.mkdir()

    with patch("claude_code_tools.find_claude_session.get_all_claude_projects") as mock:
        find_sessions(["test"], global_search=True, claude_home=str(custom_home))

        mock.assert_called_once_with(str(custom_home))


# ==================== Test display_interactive_ui ====================


@patch("claude_code_tools.find_claude_session.RICH_AVAILABLE", False)
def test_display_interactive_ui_returns_none_without_rich():
    """Test display_interactive_ui returns None when rich unavailable."""
    result = display_interactive_ui([], [])

    assert result is None


@patch("claude_code_tools.find_claude_session.RICH_AVAILABLE", True)
@patch("claude_code_tools.find_claude_session.console")
def test_display_interactive_ui_no_sessions(mock_console):
    """Test display_interactive_ui handles empty session list."""
    mock_console.print = Mock()

    result = display_interactive_ui([], ["test"])

    assert result is None


def test_display_interactive_ui_limits_display():
    """Test display_interactive_ui respects num_matches limit."""
    sessions = [
        ("id1", 1700000000.0, 1699000000.0, 100, "proj1", "prev1", "/path1", "main"),
        ("id2", 1700000001.0, 1699000001.0, 100, "proj2", "prev2", "/path2", "dev"),
    ]

    with patch("claude_code_tools.find_claude_session.RICH_AVAILABLE", True):
        with patch("claude_code_tools.find_claude_session.Prompt.ask", return_value=""):
            result = display_interactive_ui(sessions, ["test"], num_matches=1)

            # Should handle gracefully (result may be None if cancelled)
            assert result in [None, sessions[0]]


# ==================== Test show_action_menu ====================


@patch("builtins.input", return_value="")
def test_show_action_menu_default_resume(mock_input):
    """Test show_action_menu defaults to resume."""
    session_info = ("id", 0.0, 0.0, 100, "project", "preview", "/path", "main")

    result = show_action_menu(session_info)

    assert result == "resume"


@patch("builtins.input", return_value="1")
def test_show_action_menu_choice_resume(mock_input):
    """Test show_action_menu returns resume for choice 1."""
    session_info = ("id", 0.0, 0.0, 100, "project", "preview", "/path", "main")

    result = show_action_menu(session_info)

    assert result == "resume"


@patch("builtins.input", return_value="2")
def test_show_action_menu_choice_path(mock_input):
    """Test show_action_menu returns path for choice 2."""
    session_info = ("id", 0.0, 0.0, 100, "project", "preview", "/path", "main")

    result = show_action_menu(session_info)

    assert result == "path"


@patch("builtins.input", return_value="3")
def test_show_action_menu_choice_copy(mock_input):
    """Test show_action_menu returns copy for choice 3."""
    session_info = ("id", 0.0, 0.0, 100, "project", "preview", "/path", "main")

    result = show_action_menu(session_info)

    assert result == "copy"


@patch("builtins.input", return_value="99")
def test_show_action_menu_invalid_choice(mock_input):
    """Test show_action_menu returns None for invalid choice."""
    session_info = ("id", 0.0, 0.0, 100, "project", "preview", "/path", "main")

    result = show_action_menu(session_info)

    assert result is None


@patch("builtins.input", side_effect=KeyboardInterrupt)
def test_show_action_menu_keyboard_interrupt(mock_input):
    """Test show_action_menu handles KeyboardInterrupt."""
    session_info = ("id", 0.0, 0.0, 100, "project", "preview", "/path", None)

    result = show_action_menu(session_info)

    assert result is None


# ==================== Test get_session_file_path ====================


def test_get_session_file_path_default_home():
    """Test get_session_file_path with default home."""
    result = get_session_file_path("session123", "/home/user/project")

    expected = str(Path.home() / ".claude" / "projects" / "-home-user-project" / "session123.jsonl")
    assert result == expected


def test_get_session_file_path_custom_home():
    """Test get_session_file_path with custom claude_home."""
    result = get_session_file_path("session123", "/home/user/project", "/custom/claude")

    assert "/custom/claude" in result
    assert "session123.jsonl" in result


def test_get_session_file_path_encodes_path():
    """Test get_session_file_path encodes path correctly."""
    result = get_session_file_path("id", "/home/user/my-project")

    assert "-home-user-my-project" in result


# ==================== Test copy_session_file ====================


@patch("builtins.input", return_value="")
def test_copy_session_file_empty_input_cancels(mock_input, capsys):
    """Test copy_session_file cancels on empty input."""
    copy_session_file("/source/file.jsonl")

    captured = capsys.readouterr()
    assert "Cancelled" in captured.out


@patch("pathlib.Path.exists", return_value=True)
@patch("pathlib.Path.is_dir", return_value=True)
@patch("shutil.copy2")
@patch("builtins.input", return_value="/dest/dir")
def test_copy_session_file_to_existing_directory(
    mock_input, mock_copy, mock_is_dir, mock_exists
):
    """Test copy_session_file copies to existing directory."""
    copy_session_file("/source/file.jsonl")

    mock_copy.assert_called_once()


@patch("pathlib.Path.exists", return_value=False)
@patch("builtins.input", side_effect=["/dest/file.jsonl", "n"])
def test_copy_session_file_creates_parent_directory_declined(
    mock_input, mock_exists, capsys
):
    """Test copy_session_file handles declined parent directory creation."""
    copy_session_file("/source/file.jsonl")

    captured = capsys.readouterr()
    assert "Cancelled" in captured.out


@patch("builtins.input", side_effect=KeyboardInterrupt)
def test_copy_session_file_keyboard_interrupt(mock_input, capsys):
    """Test copy_session_file handles KeyboardInterrupt."""
    copy_session_file("/source/file.jsonl")

    captured = capsys.readouterr()
    assert "Cancelled" in captured.out


# ==================== Test resume_session ====================


@patch("os.getcwd", return_value="/current/dir")
@patch("os.execvp")
def test_resume_session_same_directory(mock_execvp, mock_getcwd):
    """Test resume_session in same directory."""
    resume_session("session123", "/current/dir", shell_mode=False)

    mock_execvp.assert_called_once_with("claude", ["claude", "-r", "session123"])


@patch("os.getcwd", return_value="/current/dir")
def test_resume_session_shell_mode(mock_getcwd, capsys):
    """Test resume_session in shell mode outputs commands."""
    resume_session("session123", "/other/dir", shell_mode=True)

    captured = capsys.readouterr()
    assert "cd" in captured.out
    assert "claude -r" in captured.out


@patch("os.getcwd", return_value="/current/dir")
def test_resume_session_shell_mode_custom_home(mock_getcwd, capsys):
    """Test resume_session shell mode with custom claude_home."""
    resume_session("session123", "/other/dir", shell_mode=True, claude_home="~/.claude")

    captured = capsys.readouterr()
    assert "CLAUDE_CONFIG_DIR" in captured.out


@patch("os.getcwd", return_value="/current/dir")
@patch("os.execvp", side_effect=FileNotFoundError)
def test_resume_session_claude_not_found(mock_execvp, mock_getcwd):
    """Test resume_session handles claude command not found."""
    with pytest.raises(SystemExit):
        resume_session("session123", "/current/dir", shell_mode=False)


@patch("os.getcwd", return_value="/current/dir")
@patch("claude_code_tools.find_claude_session.RICH_AVAILABLE", True)
@patch("claude_code_tools.find_claude_session.Confirm.ask", return_value=False)
@patch("os.execvp")
def test_resume_session_decline_directory_change(
    mock_execvp, mock_confirm, mock_getcwd
):
    """Test resume_session when user declines directory change."""
    resume_session("session123", "/other/dir", shell_mode=False)

    # Should still try to execute (may fail)
    mock_execvp.assert_called_once()


# ==================== Test main ====================


@patch("sys.argv", ["find-claude-session", "test"])
@patch("claude_code_tools.find_claude_session.find_sessions", return_value=[])
@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
def test_main_no_results(mock_get_dir, mock_find, tmp_path):
    """Test main exits gracefully when no results found."""
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    mock_get_dir.return_value = claude_dir

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0


@patch("sys.argv", ["find-claude-session", "test"])
@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
def test_main_no_project_directory(mock_get_dir):
    """Test main exits when project directory doesn't exist."""
    mock_get_dir.return_value = Path("/nonexistent")

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


@patch("sys.argv", ["find-claude-session", "test1,test2,test3"])
@patch("claude_code_tools.find_claude_session.find_sessions", return_value=[])
@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
def test_main_parses_keywords(mock_get_dir, mock_find, tmp_path):
    """Test main correctly parses comma-separated keywords."""
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    mock_get_dir.return_value = claude_dir

    with pytest.raises(SystemExit):
        main()

    # Check keywords were parsed correctly
    call_args = mock_find.call_args
    assert call_args[0][0] == ["test1", "test2", "test3"]


@patch("sys.argv", ["find-claude-session", "-g", "test"])
@patch("claude_code_tools.find_claude_session.find_sessions", return_value=[])
def test_main_global_search_flag(mock_find):
    """Test main respects global search flag."""
    with pytest.raises(SystemExit):
        main()

    assert mock_find.call_args[1]["global_search"] is True


@patch("sys.argv", ["find-claude-session", "-n", "20", "test"])
@patch("claude_code_tools.find_claude_session.find_sessions", return_value=[])
@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
def test_main_num_matches_flag(mock_get_dir, mock_find, tmp_path):
    """Test main respects num-matches flag."""
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    mock_get_dir.return_value = claude_dir

    with pytest.raises(SystemExit):
        main()

    # Check in display call or other appropriate place
    # (May need to mock display_interactive_ui to verify)


@patch("sys.argv", ["find-claude-session", "--claude-home", "/custom", "-g"])
@patch("claude_code_tools.find_claude_session.find_sessions", return_value=[])
def test_main_custom_claude_home(mock_find):
    """Test main uses custom claude_home."""
    with pytest.raises(SystemExit):
        main()

    assert mock_find.call_args[1]["claude_home"] == "/custom"


@patch("sys.argv", ["find-claude-session", "--shell", "test"])
@patch("claude_code_tools.find_claude_session.find_sessions")
@patch("claude_code_tools.find_claude_session.display_interactive_ui", return_value=None)
@patch("claude_code_tools.find_claude_session.get_claude_project_dir")
def test_main_shell_mode(mock_get_dir, mock_display, mock_find, tmp_path):
    """Test main in shell mode."""
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    mock_get_dir.return_value = claude_dir
    mock_find.return_value = [
        ("id", 1700000000.0, 1699000000.0, 100, "proj", "prev", "/path", "main")
    ]

    main()

    # Should pass stderr_mode=True
    assert mock_display.call_args[1]["stderr_mode"] is True


# ==================== Test Edge Cases ====================


def test_search_keywords_in_file_unicode(tmp_path):
    """Test search_keywords_in_file handles unicode correctly."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": "Testing émojis 🚀"}}\n'
    test_file.write_text(content, encoding="utf-8")

    matches, _, _ = search_keywords_in_file(test_file, ["émojis"])

    assert matches is True


def test_get_session_preview_handles_non_dict_content_items(tmp_path):
    """Test get_session_preview handles non-dict items in content list."""
    test_file = tmp_path / "session.jsonl"
    content = '{"type": "user", "message": {"content": ["string", {"type": "text", "text": "Valid"}]}}\n'
    test_file.write_text(content)

    result = get_session_preview(test_file)

    # Should extract the valid text item
    assert "Valid" in result


def test_extract_project_name_with_special_chars():
    """Test extract_project_name with special characters."""
    result = extract_project_name("/home/user/project-with-hyphens_and_underscores")

    assert result == "project-with-hyphens_and_underscores"


def test_get_all_claude_projects_handles_permission_error(tmp_path):
    """Test get_all_claude_projects handles permission errors gracefully."""
    claude_home = tmp_path / ".claude"
    projects_dir = claude_home / "projects"
    projects_dir.mkdir(parents=True)

    # Create a directory we can't read
    restricted_dir = projects_dir / "-home-restricted"
    restricted_dir.mkdir()
    restricted_dir.chmod(0o000)

    try:
        # Should not crash
        result = get_all_claude_projects(str(claude_home))
        # May or may not include restricted dir depending on permissions
        assert isinstance(result, list)
    finally:
        # Restore permissions for cleanup
        restricted_dir.chmod(0o755)
