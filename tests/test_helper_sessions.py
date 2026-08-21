"""Helper-session discovery and marking regressions."""

from __future__ import annotations

import json
from pathlib import Path

from claude_code_tools.helper_sessions import (
    is_helper_session,
    mark_new_helper_session,
    run_and_mark_helper_fork,
    snapshot_session_files,
)


def _write_session(path: Path, session_id: str) -> None:
    """Write a minimal resumable Claude transcript."""
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": session_id,
                "message": {"content": "question"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "sessionId": session_id,
                "message": {"content": "answer"},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_marks_the_only_new_fork_as_helper(tmp_path: Path) -> None:
    """A headless fork created after the snapshot receives helper metadata."""
    source = tmp_path / "source.jsonl"
    fork = tmp_path / "fork.jsonl"
    _write_session(source, "source")
    snapshot = snapshot_session_files(source)
    _write_session(fork, "fork")
    original_tail = fork.read_text(encoding="utf-8").splitlines()[1]

    marked = mark_new_helper_session(snapshot, session_id="fork")

    assert marked == fork
    assert is_helper_session(fork)
    lines = fork.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["sessionType"] == "helper"
    assert lines[1] == original_tail
    assert not is_helper_session(source)


def test_result_id_selects_fork_among_concurrent_new_sessions(
    tmp_path: Path,
) -> None:
    """A returned session ID prevents an unrelated new file being marked."""
    source = tmp_path / "source.jsonl"
    expected = tmp_path / "expected.jsonl"
    unrelated = tmp_path / "unrelated.jsonl"
    _write_session(source, "source")
    snapshot = snapshot_session_files(source)
    _write_session(expected, "expected")
    _write_session(unrelated, "unrelated")

    marked = mark_new_helper_session(snapshot, session_id="expected")

    assert marked == expected
    assert is_helper_session(expected)
    assert not is_helper_session(unrelated)


def test_ambiguous_new_sessions_are_not_guessed(tmp_path: Path) -> None:
    """Without a returned ID, concurrent new transcripts remain untouched."""
    source = tmp_path / "source.jsonl"
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    _write_session(source, "source")
    snapshot = snapshot_session_files(source)
    _write_session(first, "first")
    _write_session(second, "second")

    assert mark_new_helper_session(snapshot, session_id="missing") is None
    assert not is_helper_session(first)
    assert not is_helper_session(second)


def test_missing_result_id_does_not_mark_only_new_session(
    tmp_path: Path,
) -> None:
    """A concurrent session cannot be mistaken for a failed helper fork."""
    source = tmp_path / "source.jsonl"
    unrelated = tmp_path / "unrelated.jsonl"
    _write_session(source, "source")
    snapshot = snapshot_session_files(source)
    _write_session(unrelated, "unrelated")

    assert mark_new_helper_session(snapshot) is None
    assert not is_helper_session(unrelated)


def test_malformed_first_record_is_not_a_helper(tmp_path: Path) -> None:
    """Malformed transcripts are handled without exceptions."""
    session_file = tmp_path / "broken.jsonl"
    session_file.write_text("not-json\n", encoding="utf-8")

    assert not is_helper_session(session_file)


def test_failed_headless_operation_still_marks_its_fork(tmp_path: Path) -> None:
    """A nonzero client result cannot leave its newly created fork visible."""
    source = tmp_path / "source.jsonl"
    fork = tmp_path / "failed-fork.jsonl"
    _write_session(source, "source")

    def failed_operation() -> tuple[int, str]:
        _write_session(fork, "failed-fork")
        return 17, "failed-fork"

    result = run_and_mark_helper_fork(source, failed_operation)

    assert result == 17
    assert is_helper_session(fork)
