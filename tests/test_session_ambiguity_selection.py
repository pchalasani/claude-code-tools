"""Structured ambiguity and shared Rich selection regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools.aichat import main
from claude_code_tools.session_resolution import (
    SessionQueryAmbiguity,
    resolve_session_query,
)
from tests.resolve_session_helpers import FakeHome, claude_home, codex_home

__all__ = ["claude_home", "codex_home"]


def _reports_stdin_tty(_stream: object) -> bool:
    """Make Click's isolated stdin behave like an interactive terminal."""
    return True


def _root_args(claude: FakeHome, codex: FakeHome) -> list[str]:
    """Return root options that isolate both agent homes."""
    return [
        "--claude-home",
        str(claude.path),
        "--codex-home",
        str(codex.path),
    ]


def _snapshot(paths: tuple[Path, ...]) -> dict[Path, bytes]:
    """Capture exact bytes for a small set of session files."""
    return {path: path.read_bytes() for path in paths}


def test_shared_resolver_exposes_structured_newest_first_ambiguity(
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Action callers receive records, total count, and display cap."""
    with pytest.raises(SessionQueryAmbiguity) as error_info:
        resolve_session_query(
            "Shared Plan",
            claude_home=str(claude_home.path),
            codex_home=str(codex_home.path),
        )

    error = error_info.value
    assert error.query == "Shared Plan"
    assert error.match_count == 2
    assert error.display_limit == 25
    assert [record.session_id for record in error.records] == [
        claude_home.ids[1],
        claude_home.ids[0],
    ]
    assert "Ambiguous session 'Shared Plan'" in str(error)


def test_ambiguity_records_preserve_discovered_symlinks(
    tmp_path: Path,
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """A selected mutation targets an in-home link, not its referent."""
    links = claude_home.files[:2]
    for index, link in enumerate(links):
        external = tmp_path / f"external-{index}.jsonl"
        external.write_bytes(link.read_bytes())
        link.unlink()
        link.symlink_to(external)

    with pytest.raises(SessionQueryAmbiguity) as error_info:
        resolve_session_query(
            "Shared Plan",
            agent="claude",
            claude_home=str(claude_home.path),
            codex_home=str(codex_home.path),
        )

    selected_paths = {
        Path(record.session_file) for record in error_info.value.records
    }
    assert selected_paths == {path.absolute() for path in links}
    assert all(path.is_symlink() for path in selected_paths)


def test_info_ambiguity_renders_shared_table_and_uses_selection(
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A human action can choose a numbered candidate and continue."""
    monkeypatch.setattr(
        "click.testing._NamedTextIOWrapper.isatty",
        _reports_stdin_tty,
    )
    result = CliRunner().invoke(
        main,
        [*_root_args(claude_home, codex_home), "info", "Shared Plan"],
        input="2\n",
        env={"COLUMNS": "240"},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    for heading in (
        "Agent",
        "Why it matched",
        "Project / cwd",
        "Session ID",
        "Modified",
    ):
        assert heading in result.output
    assert "Which session do you want to use?" in result.output
    assert f"Session: {claude_home.ids[0]}" in result.output


def test_info_json_never_prompts_even_with_tty(
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Machine-readable modes retain deterministic ambiguity failure."""
    monkeypatch.setattr(
        "click.testing._NamedTextIOWrapper.isatty",
        _reports_stdin_tty,
    )
    result = CliRunner().invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "info",
            "Shared Plan",
            "--json",
        ],
        input="1\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Ambiguous session" in result.output
    assert "Which session" not in result.output
    assert "Why it matched" not in result.output


def test_delete_selection_then_confirmation_deletes_only_selected(
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection precedes confirmation and targets only the chosen file."""
    monkeypatch.setattr(
        "click.testing._NamedTextIOWrapper.isatty",
        _reports_stdin_tty,
    )
    result = CliRunner().invoke(
        main,
        [*_root_args(claude_home, codex_home), "delete", "Shared Plan"],
        input="2\nyes\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert not claude_home.files[0].exists()
    assert claude_home.files[1].exists()
    assert claude_home.files[2].exists()
    assert str(claude_home.files[0]) in result.output
    assert "SESSION DELETION CONFIRMATION" in result.output


@pytest.mark.parametrize(("input_text", "force"), [("q\n", False), ("", True)])
def test_delete_selection_cancellation_and_eof_do_not_mutate(
    input_text: str,
    force: bool,
    claude_home: FakeHome,
    codex_home: FakeHome,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation and EOF happen before any destructive action."""
    monkeypatch.setattr(
        "click.testing._NamedTextIOWrapper.isatty",
        _reports_stdin_tty,
    )
    before = _snapshot(claude_home.files)
    arguments = [
        *_root_args(claude_home, codex_home),
        "delete",
        "Shared Plan",
    ]
    if force:
        arguments.append("--force")
    result = CliRunner().invoke(
        main,
        arguments,
        input=input_text,
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "selection cancelled" in result.output.lower()
    assert _snapshot(claude_home.files) == before


def test_delete_non_tty_ambiguity_never_accepts_piped_selection(
    claude_home: FakeHome,
    codex_home: FakeHome,
) -> None:
    """Piped input cannot select a destructive target."""
    before = _snapshot(claude_home.files)
    result = CliRunner().invoke(
        main,
        [
            *_root_args(claude_home, codex_home),
            "delete",
            "Shared Plan",
            "--force",
        ],
        input="2\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "Ambiguous session" in result.output
    assert "Which session" not in result.output
    assert _snapshot(claude_home.files) == before
