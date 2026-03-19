"""
Regression tests for --claude-home / --codex-home resolution in
the aichat CLI group callback.

The bug: when --claude-home is placed AFTER the subcommand name
(e.g. `aichat search --claude-home ~/.claude-rja`), the Click
group callback didn't see it, so auto_index ran against the
default ~/.claude instead of the user-specified home.

The fix scans sys.argv to find --claude-home regardless of
position. CliRunner doesn't set sys.argv, so we patch it.
"""

import sys
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main


def _get_auto_index_home(mock, key="claude_home"):
    """Extract a home dir kwarg from a mock auto_index call."""
    mock.assert_called_once()
    kw = mock.call_args
    return str(
        kw.kwargs.get(key) or kw[1].get(key)
    )


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_auto_index():
    """Mock auto_index so no real indexing runs."""
    with patch(
        "claude_code_tools.search_index.auto_index"
    ) as mock:
        mock.return_value = {
            "indexed": 0,
            "skipped": 0,
            "failed": 0,
            "total_files": 0,
        }
        yield mock


class TestClaudeHomeResolution:
    """Verify --claude-home is respected regardless of argv position."""

    def test_claude_home_after_subcommand(
        self, runner, mock_auto_index, tmp_path
    ):
        """REGRESSION: `aichat search --claude-home /alt` must use /alt.

        Previously auto_index received None and fell back to ~/.claude.
        """
        alt_home = tmp_path / ".claude-alt"
        alt_home.mkdir()
        argv = [
            "aichat", "search",
            "--claude-home", str(alt_home), "--help",
        ]
        with patch.object(sys, "argv", argv):
            runner.invoke(main, argv[1:])

        assert _get_auto_index_home(mock_auto_index) == str(
            alt_home
        )

    def test_claude_home_before_subcommand(
        self, runner, mock_auto_index, tmp_path
    ):
        """--claude-home before subcommand (group-level) works."""
        alt_home = tmp_path / ".claude-alt"
        alt_home.mkdir()
        argv = [
            "aichat",
            "--claude-home", str(alt_home), "search", "--help",
        ]
        with patch.object(sys, "argv", argv):
            runner.invoke(main, argv[1:])

        assert _get_auto_index_home(mock_auto_index) == str(
            alt_home
        )

    def test_env_var_used_when_no_cli_arg(
        self, runner, mock_auto_index, tmp_path
    ):
        """CLAUDE_CONFIG_DIR env var used when no CLI arg."""
        alt_home = tmp_path / ".claude-env"
        alt_home.mkdir()
        argv = ["aichat", "search", "--help"]
        with (
            patch.object(sys, "argv", argv),
            patch.dict(
                "os.environ",
                {"CLAUDE_CONFIG_DIR": str(alt_home)},
            ),
        ):
            runner.invoke(main, argv[1:])

        assert _get_auto_index_home(mock_auto_index) == str(
            alt_home
        )

    def test_cli_arg_overrides_env_var(
        self, runner, mock_auto_index, tmp_path
    ):
        """--claude-home should override CLAUDE_CONFIG_DIR."""
        env_home = tmp_path / ".claude-env"
        env_home.mkdir()
        cli_home = tmp_path / ".claude-cli"
        cli_home.mkdir()
        argv = [
            "aichat", "search",
            "--claude-home", str(cli_home), "--help",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.dict(
                "os.environ",
                {"CLAUDE_CONFIG_DIR": str(env_home)},
            ),
        ):
            runner.invoke(main, argv[1:])

        actual = _get_auto_index_home(mock_auto_index)
        assert actual == str(cli_home), (
            f"CLI arg should override env var, got {actual}"
        )

    def test_codex_home_after_subcommand(
        self, runner, mock_auto_index, tmp_path
    ):
        """--codex-home after subcommand should also be resolved."""
        alt_codex = tmp_path / ".codex-alt"
        alt_codex.mkdir()
        argv = [
            "aichat", "search",
            "--codex-home", str(alt_codex), "--help",
        ]
        with patch.object(sys, "argv", argv):
            runner.invoke(main, argv[1:])

        assert _get_auto_index_home(
            mock_auto_index, key="codex_home"
        ) == str(alt_codex)
