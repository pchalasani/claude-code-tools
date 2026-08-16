"""Interactive ambiguity selection for ``aichat move-account``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from tests.test_move_account import ENC, UUID_A, UUID_B, _write_session


def _reports_stdin_tty(_stream: object) -> bool:
    """Make Click's isolated stdin behave like an interactive terminal."""
    return True


@pytest.fixture()
def ambiguous_homes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    """Create two source accounts with the same name and one target."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    older = tmp_path / ".claude-work"
    newer = tmp_path / ".claude-other"
    target = tmp_path / ".claude"
    (target / "projects").mkdir(parents=True)
    older_file = _write_session(older, UUID_A, title="same-name")
    newer_file = _write_session(newer, UUID_B, title="same-name")
    os.utime(older_file, (1_700_000_000, 1_700_000_000))
    os.utime(newer_file, (1_700_000_001, 1_700_000_001))
    return older, newer, target


def test_move_account_selects_source_home_from_shared_table(
    ambiguous_homes: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A numbered choice identifies both transcript and source account."""
    older, newer, target = ambiguous_homes
    monkeypatch.setattr(
        "click.testing._NamedTextIOWrapper.isatty",
        _reports_stdin_tty,
    )
    result = CliRunner().invoke(
        main,
        [
            "move-account",
            "same-name",
            "--to",
            str(target),
            "--agent",
            "claude",
            "--keep",
        ],
        input="2\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Source home" in result.output
    assert UUID_A in result.output
    assert UUID_B in result.output
    assert (target / "projects" / ENC / f"{UUID_A}.jsonl").exists()
    assert not (target / "projects" / ENC / f"{UUID_B}.jsonl").exists()
    assert (older / "projects" / ENC / f"{UUID_A}.jsonl").exists()
    assert (newer / "projects" / ENC / f"{UUID_B}.jsonl").exists()


def test_move_account_cancellation_preserves_all_accounts(
    ambiguous_homes: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the picker leaves both sources and target untouched."""
    older, newer, target = ambiguous_homes
    monkeypatch.setattr(
        "click.testing._NamedTextIOWrapper.isatty",
        _reports_stdin_tty,
    )
    result = CliRunner().invoke(
        main,
        [
            "move-account",
            "same-name",
            "--to",
            str(target),
            "--agent",
            "claude",
        ],
        input="q\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "selection cancelled" in result.output.lower()
    assert (older / "projects" / ENC / f"{UUID_A}.jsonl").exists()
    assert (newer / "projects" / ENC / f"{UUID_B}.jsonl").exists()
    assert list((target / "projects").rglob("*.jsonl")) == []


def test_move_account_accepts_exact_transcript_path(
    tmp_path: Path,
) -> None:
    """A direct path selects exactly that transcript in its source home."""
    source = tmp_path / "claude-source"
    target = tmp_path / "claude-target"
    (target / "projects").mkdir(parents=True)
    selected = _write_session(source, UUID_A, title="direct-source")

    result = CliRunner().invoke(
        main,
        [
            "move-account",
            str(selected),
            "--from",
            str(source),
            "--to",
            str(target),
            "--agent",
            "claude",
            "--keep",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert selected.exists()
    assert (target / "projects" / ENC / f"{UUID_A}.jsonl").exists()


def test_move_account_path_rejects_conflicting_agent(
    tmp_path: Path,
) -> None:
    """A path's transcript content must agree with the requested agent."""
    source = tmp_path / "claude-source"
    target = tmp_path / "codex-target"
    (target / "sessions").mkdir(parents=True)
    selected = _write_session(source, UUID_A)

    result = CliRunner().invoke(
        main,
        [
            "move-account",
            str(selected),
            "--from",
            str(source),
            "--to",
            str(target),
            "--agent",
            "codex",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "session file is claude" in result.output
    assert selected.exists()
