"""Test session ID resolution for trim-session and smart-trim."""

import os
import subprocess

SUBPROCESS_TIMEOUT_SECONDS = 10


def test_trim_session_no_args_error_message():
    """Test trim-session's error when CLAUDE_SESSION_ID is not set."""
    # Make sure CLAUDE_SESSION_ID is not set
    env = os.environ.copy()
    env.pop("CLAUDE_SESSION_ID", None)

    result = subprocess.run(
        ["trim-session"],
        capture_output=True,
        text=True,
        env=env,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    # Should fail with exit code 1
    assert result.returncode == 1

    # Should show helpful error message
    assert "CLAUDE_SESSION_ID not set" in result.stderr
    assert "Usage:" in result.stderr


def test_smart_trim_no_args_error_message():
    """Test smart-trim's error when CLAUDE_SESSION_ID is not set."""
    # Make sure CLAUDE_SESSION_ID is not set
    env = os.environ.copy()
    env.pop("CLAUDE_SESSION_ID", None)

    result = subprocess.run(
        ["smart-trim"],
        capture_output=True,
        text=True,
        env=env,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    # Should fail with exit code 1
    assert result.returncode == 1

    # Should show helpful error message
    assert "CLAUDE_SESSION_ID not set" in result.stderr
    assert "Usage:" in result.stderr


def test_trim_session_invalid_session_id():
    """Test that trim-session with invalid session ID shows proper error."""
    result = subprocess.run(
        ["trim-session", "nonexistent-session-id-12345"],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    # Should fail with exit code 1
    assert result.returncode == 1

    # Should show error about session not found
    assert "not found" in result.stderr.lower()


def test_smart_trim_invalid_session_id():
    """Test that smart-trim with invalid session ID shows proper error."""
    result = subprocess.run(
        ["smart-trim", "nonexistent-session-id-12345"],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    # Should fail with exit code 1
    assert result.returncode == 1

    # Should show error about session not found
    assert "not found" in result.stderr.lower()
