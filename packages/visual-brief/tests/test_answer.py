"""Answering a conversation: one dated agent turn, never a pair."""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from write_support import (
    ANCHOR,
    base_document,
    make_run,
    queue_line,
    read_content_file,
    threads_at,
    with_thread,
    write_content,
)
from visual_brief.cli import main
from visual_brief.render.threads import normalize_document
from visual_brief.server.counting import merge_pending_followups
from visual_brief.writes import CliError, answer_command, fold_command
from visual_brief.writes.lint import lint_document

ASKED = "How does an answer get its timestamp?"


def _folded_thread_id(root: Path, run_dir: Path) -> str:
    """Queue one question, fold it, and return the thread it created."""
    queue_line(run_dir, ASKED)
    assert fold_command(root, None) == 0
    threads = threads_at(read_content_file(run_dir), ANCHOR)
    assert len(threads) == 1
    return str(threads[0]["id"])


def _saved_thread(run_dir: Path) -> dict[str, object]:
    """Return the single conversation saved on the page."""
    threads = threads_at(read_content_file(run_dir), ANCHOR)
    assert len(threads) == 1
    return threads[0]


def test_answer_appends_a_real_agent_turn(tmp_path: Path) -> None:
    """The answer is a turn by ``agent``, stamped from the real clock."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    thread_id = _folded_thread_id(root, run_dir)

    assert answer_command(root, None, thread_id, "From the clock.") == 0

    thread = _saved_thread(run_dir)
    assert "question" not in thread and "answer" not in thread
    turns = thread["turns"]
    assert [turn["author"] for turn in turns] == ["human", "agent"]
    assert turns[1]["text"] == "From the clock."
    written = datetime.fromisoformat(turns[1]["at"].replace("Z", "+00:00"))
    assert written.tzinfo is not None
    assert datetime.now(timezone.utc) - written < timedelta(minutes=5)
    asked = datetime.fromisoformat(turns[0]["at"].replace("Z", "+00:00"))
    assert asked <= written


def test_the_pair_format_lands_at_1970_where_answer_does_not(
    tmp_path: Path,
) -> None:
    """The old shape misdates the exchange; the verb cannot write it."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    write_content(
        run_dir,
        with_thread(
            ANCHOR,
            {"question": ASKED, "answer": "Written the old way."},
        ),
    )
    hand_written = read_content_file(run_dir)
    converted = normalize_document(hand_written)
    epoch_turns = threads_at(converted, ANCHOR)[0]["turns"]
    assert all(turn["at"].startswith("1970") for turn in epoch_turns)
    assert any("1970 epoch" in warning for warning in lint_document(hand_written))

    write_content(run_dir, base_document())
    thread_id = _folded_thread_id(root, run_dir)
    assert answer_command(root, None, thread_id, "Written by the verb.") == 0

    turns = _saved_thread(run_dir)["turns"]
    assert not any(turn["at"].startswith("1970") for turn in turns)
    assert lint_document(read_content_file(run_dir)) == []


def test_answer_refuses_a_thread_that_is_not_on_the_page(
    tmp_path: Path,
) -> None:
    """An invented thread id is an error rather than a new conversation."""
    root = tmp_path / "runs"
    make_run(root)

    with pytest.raises(CliError, match="unknown thread: q-invented"):
        answer_command(root, None, "q-invented", "No home for this.")


def test_answer_points_at_fold_for_a_thread_still_in_the_queue(
    tmp_path: Path,
) -> None:
    """A question the agent read off the page but never folded says so."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    pending = normalize_document(read_content_file(run_dir))
    merged = merge_pending_followups(run_dir)
    assert merged is not None
    pending_id = str(threads_at(merged, ANCHOR)[0]["id"])
    assert threads_at(pending, ANCHOR) == []

    with pytest.raises(CliError, match="visual-brief fold"):
        answer_command(root, None, pending_id, "Too early.")


def test_answer_refuses_empty_text(tmp_path: Path) -> None:
    """Whitespace is not an answer."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    thread_id = _folded_thread_id(root, run_dir)

    with pytest.raises(CliError, match="must not be empty"):
        answer_command(root, None, thread_id, "   \n ")


def test_answer_reads_a_long_answer_from_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-`` takes the answer from standard input, so nothing needs quoting."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    thread_id = _folded_thread_id(root, run_dir)
    answer = 'It survives "quotes", $shell, and\nnewlines.'
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))
    monkeypatch.setattr(sys, "stdin", io.StringIO(answer))

    assert main(["answer", thread_id, "-"]) == 0

    assert _saved_thread(run_dir)["turns"][1]["text"] == answer


def test_answer_reads_a_long_answer_from_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--file`` takes the answer from a file for the same reason."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    thread_id = _folded_thread_id(root, run_dir)
    answer_path = tmp_path / "answer.txt"
    answer_path.write_text("Read from a file.\n", encoding="utf-8")
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))

    assert main(["answer", thread_id, "--file", str(answer_path)]) == 0

    assert _saved_thread(run_dir)["turns"][1]["text"] == "Read from a file."


def test_answer_needs_exactly_one_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Neither zero sources nor two of them is a usable command."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    thread_id = _folded_thread_id(root, run_dir)
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))

    assert main(["answer", thread_id]) == 2
    assert "give one of --text" in capsys.readouterr().err

    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin"))
    assert main(["answer", thread_id, "--text", "inline", "-"]) == 2
    assert "exactly one of --text" in capsys.readouterr().err


def test_answer_renders_the_page_it_wrote(tmp_path: Path) -> None:
    """The open page carries the answer without a separate render."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    thread_id = _folded_thread_id(root, run_dir)

    assert answer_command(root, None, thread_id, "Rendered right away.") == 0

    page = (run_dir / "index.html").read_text(encoding="utf-8")
    assert json.dumps("Rendered right away.")[1:-1] in page
