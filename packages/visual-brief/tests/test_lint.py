"""The mechanical checks, and the verbs that run them for free."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from write_support import (
    ANCHOR,
    base_document,
    make_run,
    owner_at,
    queue_line,
    with_thread,
    write_content,
)
from visual_brief.cli import main, render_command
from visual_brief.writes import lint_document, publish_now_command
from visual_brief.writes.lint import lint_command, lint_run

CRAMMED = (
    "Five things need a decision: 1. the port, 2. the daemon, 3. the queue, "
    "4. the badge, 5. the linter."
)


def _document_with(**fields: object) -> dict[str, object]:
    """Return the base document with one item's fields replaced."""
    document = copy.deepcopy(base_document())
    owner = owner_at(document, ANCHOR)
    assert owner is not None
    owner.update(fields)
    return document


def test_an_enumeration_crammed_into_prose_is_reported() -> None:
    """The jumbled wall is caught in glance and in explanation alike."""
    glance_warnings = lint_document(_document_with(glance=CRAMMED))
    explanation_warnings = lint_document(_document_with(explanation=CRAMMED))

    assert any("glance holds an enumeration" in w for w in glance_warnings)
    assert any(
        "explanation holds an enumeration" in w for w in explanation_warnings
    )
    assert all(ANCHOR in warning for warning in glance_warnings)


def test_an_enumeration_crammed_into_a_turn_is_reported() -> None:
    """A turn is one flowing thought too."""
    document = with_thread(
        ANCHOR,
        {
            "id": "q-crammed",
            "anchor": {"kind": "element", "path": ANCHOR},
            "turns": [
                {
                    "author": "human",
                    "text": "What is left?",
                    "at": "2026-07-27T12:00:00Z",
                },
                {"author": "agent", "text": CRAMMED, "at": "2026-07-27T12:01:00Z"},
            ],
        },
    )

    warnings = lint_document(document)

    assert any("turn 2 holds an enumeration" in warning for warning in warnings)


def test_ordinary_prose_is_not_mistaken_for_a_list() -> None:
    """Hyphens, decimals and dates in a sentence are not markers."""
    document = _document_with(
        glance="Version 2.1 shipped on 2026-07-27.",
        explanation=(
            "The range 5 - 7 covers it, the retry budget is 3 - and that is "
            "the whole story - so nothing here is an enumeration."
        ),
        forensics=["1. one 2. two 3. three, which is a list where it belongs"],
    )

    assert lint_document(document) == []


def test_a_legacy_pair_is_reported_with_its_epoch_cost() -> None:
    """The old shape is named, and so is why it misdates the exchange."""
    document = with_thread(
        ANCHOR,
        {"question": "Written the old way?", "answer": "Yes."},
    )

    warnings = lint_document(document)

    assert len(warnings) == 1
    assert "legacy {question, answer} pair" in warnings[0]
    assert "1970 epoch" in warnings[0]


def test_a_turn_dated_at_the_epoch_is_reported() -> None:
    """An invented 1970 timestamp is visible even inside a real thread."""
    document = with_thread(
        ANCHOR,
        {
            "id": "q-epoch",
            "anchor": {"kind": "element", "path": ANCHOR},
            "turns": [
                {
                    "author": "human",
                    "text": "When was this asked?",
                    "at": "1970-01-01T00:00:00Z",
                }
            ],
        },
    )

    warnings = lint_document(document)

    assert len(warnings) == 1
    assert "1970 epoch" in warnings[0]
    assert "q-epoch" in warnings[0]


def test_an_overlong_glance_is_reported() -> None:
    """A glance is a one-line claim, and the check knows how long that is."""
    glance = "The claim runs on and on. " * 12
    document = _document_with(glance=glance)

    warnings = lint_document(document)

    assert len(warnings) == 1
    assert f"glance is {len(glance)} characters" in warnings[0]
    assert "at most 200" in warnings[0]


def test_a_question_left_pending_is_reported(tmp_path: Path) -> None:
    """A queue line older than the newest write names the fold."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    earlier = datetime.now(timezone.utc) - timedelta(hours=1)
    queue_line(
        run_dir,
        "Asked an hour before the last publish",
        timestamp=earlier.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
    )

    warnings = lint_run(run_dir, base_document())

    assert len(warnings) == 1
    assert "1 queued question arrived" in warnings[0]
    assert "visual-brief fold" in warnings[0]


def test_lint_is_advisory_until_strict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warnings go to stderr; only ``--strict`` turns them into a failure."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    write_content(run_dir, _document_with(explanation=CRAMMED))

    assert lint_command(root, None, strict=False) == 0
    captured = capsys.readouterr()
    assert "warning: " in captured.err
    assert captured.out.strip() == "lint: 1 warning"

    assert lint_command(root, None, strict=True) == 2


def test_lint_reports_a_clean_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run with nothing to say about it says exactly that."""
    root = tmp_path / "runs"
    make_run(root)

    assert lint_command(root, None, strict=True) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "lint: clean"
    assert captured.err == ""


def test_render_runs_the_checks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The warning arrives with the render that published the fault."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    write_content(run_dir, with_thread(ANCHOR, {"question": "Old?", "answer": "Yes."}))

    assert render_command(root, "write-run") == 0

    assert "legacy {question, answer} pair" in capsys.readouterr().err


def test_a_verb_runs_the_checks_on_what_it_just_wrote(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Publishing a crammed panel warns immediately, and still publishes."""
    root = tmp_path / "runs"
    make_run(root)
    panel = {
        "headline": "Where things stand",
        "summary": "One lane.",
        "lanes": [
            {
                "id": "state",
                "name": "What works",
                "items": [
                    {
                        "id": "tests",
                        "glance": "The suite runs.",
                        "explanation": CRAMMED,
                        "trust": "verified-by-me",
                    }
                ],
            }
        ],
    }

    assert publish_now_command(root, None, panel) == 0

    captured = capsys.readouterr()
    assert "explanation holds an enumeration" in captured.err
    assert "publish-now: carried 0 conversations" in captured.out


def test_the_lint_command_defaults_to_the_only_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``visual-brief lint`` alone works when one run is unambiguous."""
    root = tmp_path / "runs"
    make_run(root)
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))

    assert main(["lint"]) == 0

    assert capsys.readouterr().out.strip() == "lint: clean"
