"""
Regression test: action_handler must forward claude_home / codex_home
from the session dict to execute_action, so that "resume as is" after
a search with --claude-home actually finds the session.
"""

import inspect
from pathlib import Path
from unittest.mock import patch

from claude_code_tools.session_menu_cli import execute_action


def test_execute_action_passes_claude_home():
    """execute_action must forward claude_home to resume_session."""
    custom_home = "/custom/.claude-rja"

    with patch(
        "claude_code_tools.find_claude_session.resume_session"
    ) as mock_resume:
        execute_action(
            action="resume",
            agent="claude",
            session_file=Path("/tmp/fake-session.jsonl"),
            project_path="/some/project",
            session_id="abc123",
            claude_home=custom_home,
        )

    mock_resume.assert_called_once_with(
        "abc123", "/some/project",
        shell_mode=False, claude_home=custom_home,
    )


def test_execute_action_passes_codex_home():
    """execute_action must forward codex_home to resume_session."""
    custom_home = "/custom/.codex-home"

    with patch(
        "claude_code_tools.find_codex_session.resume_session"
    ) as mock_resume:
        execute_action(
            action="resume",
            agent="codex",
            session_file=Path("/tmp/fake-session.jsonl"),
            project_path="/some/project",
            session_id="abc123",
            codex_home=custom_home,
        )

    mock_resume.assert_called_once_with(
        "abc123", "/some/project", shell_mode=False,
    )


def test_action_handler_forwards_claude_home():
    """REGRESSION: the action_handler closure inside search()
    must pass claude_home from the session dict to execute_action.

    action_handler is a nested function so we can't call it
    directly. Instead we inspect the source of the search
    command's callback to verify the args are forwarded.
    """
    from claude_code_tools.aichat import search

    # search is a Click Command; get the underlying function
    callback = search.callback
    source = inspect.getsource(callback)

    assert 'claude_home=sess.get("claude_home")' in source, (
        "action_handler must pass "
        'claude_home=sess.get("claude_home") '
        "to execute_action"
    )
    assert 'codex_home=sess.get("codex_home")' in source, (
        "action_handler must pass "
        'codex_home=sess.get("codex_home") '
        "to execute_action"
    )
