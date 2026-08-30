"""
Regression tests for which subcommands skip auto-indexing.

The bug: the `main` group callback ran `auto_index()` before every
subcommand, walking every session file in both agent homes (~13.6k
files on the reporter's machine, 2-30s depending on the backlog).
`trim-in-place` paid that cost even though it never queries the index,
and it runs under the `>trim` hook's time budget - so `>trim` timed
out on indexing rather than on the ~0.3s trim itself.

Commands that never read the index must not pay for it.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_auto_index():
    """Mock auto_index so no real indexing runs."""
    with patch("claude_code_tools.search_index.auto_index") as mock:
        mock.return_value = {
            "indexed": 0,
            "skipped": 0,
            "failed": 0,
            "total_files": 0,
        }
        yield mock


def _invoke(runner, argv):
    """Run the CLI with sys.argv patched (the callback reads it)."""
    with patch.object(sys, "argv", ["aichat"] + argv):
        return runner.invoke(main, argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["search", "--help"],
        # Click treats a group-level ``--`` as ending only the group's
        # options, so this still renders search help.
        ["--", "search", "--help"],
        # Routed to the synthesized ``menu`` subcommand, whose name never
        # appears in argv, so there is no anchor to truncate from.
        ["--", "--help"],
        ["trim-in-place", "--help"],
        ["port", "--help"],
        ["resolve", "--help"],
        ["build-index", "--help"],
    ],
)
def test_index_free_commands_do_not_auto_index(
    runner, mock_auto_index, argv
):
    """These never query the index, so they must not build it."""
    _invoke(runner, argv)
    mock_auto_index.assert_not_called()


def test_help_after_a_home_named_like_a_subcommand_does_not_index(
    runner, mock_auto_index, tmp_path, monkeypatch
):
    """A ``--claude-home`` value that happens to equal the subcommand name
    must not be mistaken for the subcommand when locating the help flag."""
    (tmp_path / "resume").mkdir()
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        runner, ["--claude-home", "resume", "--", "resume", "--help"]
    )
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    mock_auto_index.assert_not_called()


def test_search_still_auto_indexes_for_a_real_query(runner, mock_auto_index):
    """A real search reads the index, so it still refreshes it first."""
    _invoke(runner, ["search", "query"])
    mock_auto_index.assert_called_once()


def test_search_literal_help_query_still_auto_indexes(runner, mock_auto_index):
    """A query after ``--`` is data, not a help request."""
    _invoke(runner, ["search", "--", "--help"])
    mock_auto_index.assert_called_once()


def test_trim_in_place_skips_indexing_on_a_real_run(
    runner, mock_auto_index, tmp_path
):
    """Not just --help: an actual trim must skip indexing too.

    A skip keyed off `--help` alone would leave the real `>trim` path
    paying the full cost, which is the bug this guards.
    """
    session = tmp_path / "s.jsonl"
    session.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"},'
        '"uuid":"a","timestamp":"2026-01-01T00:00:00Z"}\n'
    )

    result = _invoke(
        runner,
        ["trim-in-place", str(session), "--json", "--dry-run"],
    )

    mock_auto_index.assert_not_called()
    assert result.exit_code == 0, result.output
    assert '"dry_run": true' in result.output


def test_trim_in_place_still_works_without_the_index(
    runner, mock_auto_index, tmp_path
):
    """Skipping the index must not break path resolution - the command
    resolves a path directly and never consults the index."""
    session = tmp_path / "s.jsonl"
    session.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"},'
        '"uuid":"a","timestamp":"2026-01-01T00:00:00Z"}\n'
    )

    result = _invoke(
        runner,
        ["trim-in-place", str(session), "--json", "--dry-run"],
    )

    assert str(Path(session).resolve()) in result.output
