"""Folding queued questions into the page without losing or editing them."""

from __future__ import annotations

import copy
import errno
import json
import os
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
from visual_brief.server.counting import (
    count_unanswered_questions,
    merge_pending_followups,
)
from visual_brief.writes import fold as fold_module
from visual_brief.writes import CliError, answer_command, fold_command

ASKED = "Why does teh fold  copy bytes, exactly?"


def _only_thread(run_dir: Path, anchor: str = ANCHOR) -> dict[str, object]:
    """Return the single conversation saved at one anchor."""
    threads = threads_at(read_content_file(run_dir), anchor)
    assert len(threads) == 1
    return threads[0]


def test_fold_copies_queue_text_where_a_paraphrase_duplicates_it(
    tmp_path: Path,
) -> None:
    """Retyping the question phantom-duplicates it; folding cannot."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    record = queue_line(run_dir, ASKED)

    tidied = with_thread(
        ANCHOR,
        {
            "id": "q-hand-written",
            "anchor": {"kind": "element", "path": ANCHOR},
            "turns": [
                {
                    "author": "human",
                    "text": "Why does the fold copy bytes, exactly?",
                    "at": record["timestamp"],
                }
            ],
        },
    )
    write_content(run_dir, tidied)
    assert count_unanswered_questions(run_dir) == 2

    write_content(run_dir, base_document())
    assert fold_command(root, None) == 0

    thread = _only_thread(run_dir)
    turns = thread["turns"]
    assert turns == [
        {"author": "human", "text": ASKED, "at": record["timestamp"]}
    ]
    assert count_unanswered_questions(run_dir) == 1


def test_fold_reports_each_folded_thread_with_its_anchor_and_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The output carries what an agent needs in order to answer."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)

    assert fold_command(root, None) == 0

    thread_id = _only_thread(run_dir)["id"]
    output = capsys.readouterr().out
    assert thread_id in output
    assert ANCHOR in output
    assert ASKED in output
    assert "fold: folded 1 (1 new, 0 replies)" in output


def test_folding_twice_changes_no_byte(tmp_path: Path) -> None:
    """A second fold is a no-op, so a retry cannot duplicate anything."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    assert fold_command(root, None) == 0
    folded = (run_dir / "content.json").read_bytes()

    assert fold_command(root, None) == 0

    assert (run_dir / "content.json").read_bytes() == folded


def test_a_second_fold_after_an_edited_turn_writes_no_second_thread(
    tmp_path: Path,
) -> None:
    """A hand-fixed turn keeps its id, so the next fold must not clone it."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    assert fold_command(root, None) == 0
    document = read_content_file(run_dir)
    thread = threads_at(document, ANCHOR)[0]
    thread_id = str(thread["id"])
    thread["turns"][0]["text"] = "Why does the fold copy bytes, exactly?"
    write_content(run_dir, document)

    assert fold_command(root, None) == 0

    saved = threads_at(read_content_file(run_dir), ANCHOR)
    assert [entry["id"] for entry in saved] == [thread_id]

    queue_line(run_dir, "Asked after the edit")
    assert fold_command(root, None) == 0
    arrived = [
        turn["text"]
        for entry in threads_at(read_content_file(run_dir), ANCHOR)
        for turn in entry["turns"]
    ]
    assert "Asked after the edit" in arrived


def test_a_repeated_thread_id_is_named_rather_than_written(
    tmp_path: Path,
) -> None:
    """Two conversations under one id say which id, and where both sit."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    assert fold_command(root, None) == 0
    document = read_content_file(run_dir)
    threads = threads_at(document, ANCHOR)
    thread_id = str(threads[0]["id"])
    threads.append(copy.deepcopy(threads[0]))
    (run_dir / "content.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    damaged = (run_dir / "content.json").read_bytes()

    with pytest.raises(CliError) as failure:
        answer_command(root, None, thread_id, "Which one is this?")

    message = str(failure.value)
    assert "two conversations carry the id" in message
    assert thread_id in message and ANCHOR in message
    assert (run_dir / "content.json").read_bytes() == damaged


def test_fold_appends_a_reply_to_the_thread_it_names(tmp_path: Path) -> None:
    """A queued reply continues its own conversation, in order."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    assert fold_command(root, None) == 0
    thread_id = _only_thread(run_dir)["id"]
    follow_up = queue_line(run_dir, "And what about replies?", parent_id=thread_id)

    assert fold_command(root, None) == 0

    thread = _only_thread(run_dir)
    assert [turn["text"] for turn in thread["turns"]] == [
        ASKED,
        follow_up["text"],
    ]


def test_fold_says_every_turn_a_new_thread_arrives_with(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reply to a still-pending question is folded, and is also said."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    pending = merge_pending_followups(run_dir)
    assert pending is not None
    thread_id = str(threads_at(pending, ANCHOR)[0]["id"])
    follow_up = queue_line(
        run_dir, "And before you answer that?", parent_id=thread_id
    )

    assert fold_command(root, None) == 0

    thread = _only_thread(run_dir)
    assert [turn["text"] for turn in thread["turns"]] == [
        ASKED,
        follow_up["text"],
    ]
    output = capsys.readouterr().out
    assert ASKED in output
    assert follow_up["text"] in output
    assert "fold: folded 2 (1 new, 1 replies)" in output


def test_fold_never_guesses_an_unknown_parent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reply to a thread that is not on the page is reported, not placed."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, "A reply to nothing", parent_id="q-does-not-exist")
    before = (run_dir / "content.json").read_bytes()

    assert fold_command(root, None) == 0

    assert (run_dir / "content.json").read_bytes() == before
    error = capsys.readouterr().err
    assert "left in the queue" in error
    assert "q-does-not-exist" in error


def test_fold_leaves_a_stale_anchor_line_in_the_queue(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A question whose anchor is gone is reported and kept, not relocated."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, "About an item that left", anchor_id="now/state/gone")
    content_before = (run_dir / "content.json").read_bytes()
    queue_before = (run_dir / "questions.jsonl").read_bytes()

    assert fold_command(root, None) == 0

    assert (run_dir / "content.json").read_bytes() == content_before
    assert (run_dir / "questions.jsonl").read_bytes() == queue_before
    error = capsys.readouterr().err
    assert "now/state/gone" in error
    assert "About an item that left" in error


def test_fold_loses_nothing_when_a_question_arrives_mid_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queue line appended while the fold writes survives to the next fold."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    published = fold_module.save_document
    raced: list[str] = []

    def racing_save(target: Path, data: object) -> Path:
        """Append a live question in the moment before the file lands."""
        if not raced:
            raced.append("yes")
            queue_line(target, "Asked while the fold was writing")
        return published(target, data)

    monkeypatch.setattr(fold_module, "save_document", racing_save)

    assert fold_command(root, None) == 0
    saved = [
        turn["text"]
        for thread in threads_at(read_content_file(run_dir), ANCHOR)
        for turn in thread["turns"]
    ]
    assert saved == [ASKED]

    monkeypatch.setattr(fold_module, "save_document", published)
    assert fold_command(root, None) == 0

    saved = [
        turn["text"]
        for thread in threads_at(read_content_file(run_dir), ANCHOR)
        for turn in thread["turns"]
    ]
    assert sorted(saved) == sorted([ASKED, "Asked while the fold was writing"])


def test_an_interrupted_write_leaves_the_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure at the rename leaves the last valid content and no litter."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)
    content_before = (run_dir / "content.json").read_bytes()
    index_before = (run_dir / "index.html").read_bytes()
    real_replace = os.replace

    def interrupted(source: object, target: object) -> None:
        """Fail exactly as a killed process fails: at the rename."""
        if str(target).endswith("content.json"):
            raise OSError(errno.EIO, "interrupted")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", interrupted)

    with pytest.raises(CliError, match="cannot write run"):
        fold_command(root, None)

    assert (run_dir / "content.json").read_bytes() == content_before
    assert (run_dir / "index.html").read_bytes() == index_before
    assert [
        entry.name for entry in run_dir.iterdir() if entry.name.endswith(".tmp")
    ] == []


def test_a_verb_uses_the_only_run_and_asks_when_there_are_two(
    tmp_path: Path,
) -> None:
    """``--run`` is optional exactly while it is unambiguous."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, ASKED)

    assert fold_command(root, None) == 0

    make_run(root, "second-run")
    with pytest.raises(CliError, match="--run is required"):
        fold_command(root, None)
    assert fold_command(root, "write-run") == 0


def test_a_verb_rejects_an_unknown_run(tmp_path: Path) -> None:
    """Naming a run that does not exist is an error, not a new run."""
    root = tmp_path / "runs"
    make_run(root)

    with pytest.raises(CliError, match="unknown run: absent-run"):
        fold_command(root, "absent-run")


def test_a_verb_refuses_damaged_content_instead_of_ignoring_it(
    tmp_path: Path,
) -> None:
    """A content file that is not an object stops the verb, loudly."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    (run_dir / "content.json").write_text("[]", encoding="utf-8")
    queue_line(run_dir, ASKED)

    with pytest.raises(CliError, match="must be an object"):
        fold_command(root, None)
