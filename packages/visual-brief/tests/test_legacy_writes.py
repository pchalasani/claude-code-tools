"""Old ``{question, answer}`` pairs survive the verbs that pass over them.

The daemon recognizes an undated legacy pair as the queue line it came from
by matching loosely: the pair carries no instant, so any instant matches. A
verb that wrote the conversion back would destroy that match — the pair would
land dated 1970, its queue line would no longer be seen as folded, and the
question would return as a phantom duplicate. These tests hold that shut.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from write_support import (
    ANCHOR,
    make_run,
    queue_line,
    read_content_file,
    threads_at,
    with_thread,
)
from visual_brief.render.threads import normalize_document
from visual_brief.server.counting import count_unanswered_questions
from visual_brief.writes import (
    add_update_command,
    answer_command,
    fold_command,
)
from visual_brief.writes.lint import lint_command, lint_run

ASKED = "Does an old pair keep its place?"
PAIR = {"question": ASKED, "answer": "Answered long ago."}
HISTORY = {
    "id": "round-one",
    "timestamp": "2026-07-27 09:00 EDT",
    "headline": "The first round landed",
    "summary": "Recorded as history.",
    "lanes": [],
}
def _legacy_run(root: Path) -> tuple[Path, str, dict[str, Any]]:
    """Build a run holding one undated pair and the queue line behind it.

    Args:
        root: Directory that will hold the run.

    Returns:
        The run directory, the id the pair converts to, and the queue record.
        The line is dated before the page was written, which is what the
        pending-queue check looks at.
    """
    run_dir = make_run(root, document=with_thread(ANCHOR, PAIR))
    earlier = datetime.now(timezone.utc) - timedelta(hours=1)
    record = queue_line(
        run_dir,
        ASKED,
        timestamp=earlier.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
    )
    converted = threads_at(
        normalize_document(read_content_file(run_dir)), ANCHOR
    )
    assert count_unanswered_questions(run_dir) == 0
    return run_dir, str(converted[0]["id"]), record


def test_a_verb_leaves_an_untouched_legacy_pair_byte_for_byte(
    tmp_path: Path,
) -> None:
    """Publishing history does not quietly migrate the rest of the page."""
    root = tmp_path / "runs"
    run_dir, _, _ = _legacy_run(root)

    assert add_update_command(root, None, HISTORY) == 0

    assert threads_at(read_content_file(run_dir), ANCHOR) == [PAIR]
    assert count_unanswered_questions(run_dir) == 0
    assert fold_command(root, None) == 0
    assert threads_at(read_content_file(run_dir), ANCHOR) == [PAIR]
    assert count_unanswered_questions(run_dir) == 0


def test_answering_a_legacy_pair_dates_it_from_its_own_queue_line(
    tmp_path: Path,
) -> None:
    """The one pair a verb must convert keeps the instant it was asked at."""
    root = tmp_path / "runs"
    run_dir, thread_id, record = _legacy_run(root)

    assert answer_command(root, None, thread_id, "Answered by the verb.") == 0

    saved = threads_at(read_content_file(run_dir), ANCHOR)
    assert [entry["id"] for entry in saved] == [thread_id]
    turns = saved[0]["turns"]
    assert [turn["at"] for turn in turns[:2]] == [record["timestamp"]] * 2
    assert turns[2]["text"] == "Answered by the verb."
    assert count_unanswered_questions(run_dir) == 0

    assert fold_command(root, None) == 0

    assert [
        entry["id"] for entry in threads_at(read_content_file(run_dir), ANCHOR)
    ] == [thread_id]
    assert count_unanswered_questions(run_dir) == 0


def test_a_matched_legacy_pair_is_left_alone_by_the_checks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The pair no verb may rewrite is not nagged about on every write.

    Only ``answer`` converts a pair safely, and an answered pair must not be
    answered again — so a warning here would be one no verb can clear, and
    obeying it by hand is what brings the phantom duplicate back.
    """
    root = tmp_path / "runs"
    run_dir, _, _ = _legacy_run(root)

    assert lint_command(root, None, strict=True) == 0
    assert fold_command(root, None) == 0
    assert add_update_command(root, None, HISTORY) == 0
    assert lint_command(root, None, strict=True) == 0

    captured = capsys.readouterr()
    assert "legacy {question, answer} pair" not in captured.err
    assert threads_at(read_content_file(run_dir), ANCHOR) == [PAIR]


def test_a_legacy_pair_no_queue_line_matches_is_still_reported(
    tmp_path: Path,
) -> None:
    """Where nothing is at stake the old shape is still worth naming."""
    root = tmp_path / "runs"
    run_dir = make_run(root, document=with_thread(ANCHOR, PAIR))

    warnings = lint_run(run_dir, read_content_file(run_dir))

    assert len(warnings) == 1
    assert "legacy {question, answer} pair" in warnings[0]
    assert "1970 epoch" in warnings[0]


def test_a_legacy_pairs_queue_line_is_never_reported_as_still_pending(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A question already on the page is folded, so nothing nags about it."""
    root = tmp_path / "runs"
    run_dir, _, _ = _legacy_run(root)

    assert fold_command(root, None) == 0

    captured = capsys.readouterr()
    assert "left in the queue" not in captured.err
    assert "fold: folded 0 (0 new, 0 replies)" in captured.out
    assert not any(
        "queued question" in warning
        for warning in lint_run(run_dir, read_content_file(run_dir))
    )
