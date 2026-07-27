"""Publishing the Now panel, and appending dated history."""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

import pytest

from write_support import (
    ANCHOR,
    LANE_ANCHOR,
    base_document,
    make_run,
    owner_at,
    queue_line,
    read_content_file,
    threads_at,
)
from visual_brief.cli import main
from visual_brief.writes import (
    CliError,
    add_update_command,
    answer_command,
    fold_command,
    publish_now_command,
)

PANEL = {
    "headline": "Where the work stands",
    "summary": "Two features are usable; one decision is waiting.",
    "lanes": [
        {
            "id": "state",
            "name": "What works",
            "items": [
                {
                    "id": "tests",
                    "glance": "The suite runs end to end.",
                    "explanation": "Rewritten on this publish.",
                    "trust": "verified-by-me",
                }
            ],
        }
    ],
}


def _panel(**changes: object) -> dict[str, object]:
    """Return a copy of the panel with the given fields replaced."""
    panel = copy.deepcopy(PANEL)
    panel.update(changes)
    return panel


def _now(run_dir: Path) -> dict[str, object]:
    """Return the saved Now update."""
    updates = read_content_file(run_dir)["updates"]
    now = [update for update in updates if update["id"] == "now"]
    assert len(now) == 1
    return now[0]


def _with_conversation(root: Path) -> tuple[Path, str]:
    """Build a run whose Now panel carries one answered conversation."""
    run_dir = make_run(root)
    queue_line(run_dir, "Does the conversation survive a republish?")
    assert fold_command(root, None) == 0
    thread_id = str(threads_at(read_content_file(run_dir), ANCHOR)[0]["id"])
    assert answer_command(root, None, thread_id, "That is the question.") == 0
    return run_dir, thread_id


def test_publish_now_rewrites_in_place_and_stamps_the_clock(
    tmp_path: Path,
) -> None:
    """One Now panel, always: republishing replaces it rather than adding."""
    root = tmp_path / "runs"
    run_dir = make_run(root)

    assert publish_now_command(root, None, _panel()) == 0
    assert publish_now_command(root, None, _panel(headline="Later")) == 0

    updates = read_content_file(run_dir)["updates"]
    assert [update["id"] for update in updates].count("now") == 1
    now = _now(run_dir)
    assert now["headline"] == "Later"
    assert now["timestamp"] != base_document()["updates"][0]["timestamp"]
    assert now["timestamp"].startswith("20")


def test_publish_now_forces_the_reserved_id_and_keeps_a_given_stamp(
    tmp_path: Path,
) -> None:
    """A panel written with the wrong id still lands as the Now panel."""
    root = tmp_path / "runs"
    run_dir = make_run(root)

    assert publish_now_command(
        root,
        None,
        _panel(id="current-state", timestamp="just after the review"),
    ) == 0

    now = _now(run_dir)
    assert now["id"] == "now"
    assert now["timestamp"] == "just after the review"
    assert [update["id"] for update in read_content_file(run_dir)["updates"]] == [
        "now"
    ]


def test_publish_now_carries_a_conversation_onto_the_surviving_anchor(
    tmp_path: Path,
) -> None:
    """Rewriting the panel does not throw away what was said under it."""
    root = tmp_path / "runs"
    run_dir, thread_id = _with_conversation(root)

    assert publish_now_command(root, None, _panel()) == 0

    carried = threads_at(read_content_file(run_dir), ANCHOR)
    assert [thread["id"] for thread in carried] == [thread_id]
    assert len(carried[0]["turns"]) == 2


def test_publish_now_prints_a_conversation_it_cannot_carry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dropped anchor loses the item, never the conversation's text."""
    root = tmp_path / "runs"
    run_dir, thread_id = _with_conversation(root)
    replacement = _panel()
    replacement["lanes"][0]["items"][0]["id"] = "coverage"

    assert publish_now_command(root, None, replacement) == 0

    assert threads_at(read_content_file(run_dir), "now/state/coverage") == []
    error = capsys.readouterr().err
    assert "could not be carried forward" in error
    assert thread_id in error
    assert "Does the conversation survive a republish?" in error
    assert "That is the question." in error


def test_publish_now_leaves_a_conversation_the_panel_already_carries(
    tmp_path: Path,
) -> None:
    """An agent that copied the thread across does not get it twice."""
    root = tmp_path / "runs"
    run_dir, _ = _with_conversation(root)
    existing = threads_at(read_content_file(run_dir), ANCHOR)[0]
    replacement = _panel()
    replacement["lanes"][0]["items"][0]["questions"] = [existing]

    assert publish_now_command(root, None, replacement) == 0

    assert len(threads_at(read_content_file(run_dir), ANCHOR)) == 1


def test_publish_now_keeps_the_pages_copy_over_a_stale_panel_copy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A panel written before the answer does not erase the answer."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, "Does the conversation survive a republish?")
    assert fold_command(root, None) == 0
    stale = copy.deepcopy(threads_at(read_content_file(run_dir), ANCHOR)[0])
    thread_id = str(stale["id"])
    assert answer_command(root, None, thread_id, "It survives.") == 0
    replacement = _panel()
    replacement["lanes"][0]["items"][0]["questions"] = [stale]

    assert publish_now_command(root, None, replacement) == 0

    carried = threads_at(read_content_file(run_dir), ANCHOR)
    assert [thread["id"] for thread in carried] == [thread_id]
    assert [turn["text"] for turn in carried[0]["turns"]] == [
        "Does the conversation survive a republish?",
        "It survives.",
    ]
    assert "carried 1 conversation," in capsys.readouterr().out


def test_publish_now_prints_the_turns_only_the_panels_copy_held(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stale panel copy that also says something new says it on stderr."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, "Does the conversation survive a republish?")
    assert fold_command(root, None) == 0
    diverged = copy.deepcopy(threads_at(read_content_file(run_dir), ANCHOR)[0])
    thread_id = str(diverged["id"])
    assert answer_command(root, None, thread_id, "It survives.") == 0
    diverged["turns"].append(
        {
            "author": "agent",
            "text": "Written into the panel by hand.",
            "at": "2027-01-01T00:00:00Z",
        }
    )
    replacement = _panel()
    replacement["lanes"][0]["items"][0]["questions"] = [diverged]

    assert publish_now_command(root, None, replacement) == 0

    saved = threads_at(read_content_file(run_dir), ANCHOR)
    assert [turn["text"] for turn in saved[0]["turns"]] == [
        "Does the conversation survive a republish?",
        "It survives.",
    ]
    captured = capsys.readouterr()
    assert "could not be carried forward" in captured.err
    assert thread_id in captured.err
    assert "Written into the panel by hand." in captured.err
    assert "1 not carried" in captured.out


def test_publish_now_will_not_hang_one_conversation_from_two_anchors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A copy filed under another anchor is reported, never duplicated."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, "Does the conversation survive a republish?")
    assert fold_command(root, None) == 0
    moved = copy.deepcopy(threads_at(read_content_file(run_dir), ANCHOR)[0])
    thread_id = str(moved["id"])
    moved["anchor"]["path"] = "now/state/coverage"
    replacement = _panel()
    replacement["lanes"][0]["items"].append(
        {
            "id": "coverage",
            "glance": "The branches are covered.",
            "explanation": "Carried a copy of its own.",
            "trust": "verified-by-me",
            "questions": [moved],
        }
    )

    assert publish_now_command(root, None, replacement) == 0

    document = read_content_file(run_dir)
    assert threads_at(document, ANCHOR) == []
    assert [
        thread["id"] for thread in threads_at(document, "now/state/coverage")
    ] == [thread_id]
    captured = capsys.readouterr()
    assert "cannot hang from two anchors" in captured.err
    assert "1 not carried" in captured.out


def test_publish_now_carries_a_lane_conversation_too(tmp_path: Path) -> None:
    """Conversations hanging from a lane are carried like an item's."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, "A question about the whole lane", anchor_id=LANE_ANCHOR)
    assert fold_command(root, None) == 0

    assert publish_now_command(root, None, _panel()) == 0

    assert len(threads_at(read_content_file(run_dir), LANE_ANCHOR)) == 1


def test_publish_now_refuses_a_panel_that_is_not_an_object(
    tmp_path: Path,
) -> None:
    """A list of lanes is not a panel, and says so before any write."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = (run_dir / "content.json").read_bytes()

    with pytest.raises(CliError, match="must be a JSON object"):
        publish_now_command(root, None, [PANEL])

    assert (run_dir / "content.json").read_bytes() == before


def test_publish_now_reports_the_validator_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """An unrecognized trust chip fails the panel, not the saved page."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    broken = _panel()
    broken["lanes"][0]["items"][0]["trust"] = "pretty-sure"
    before = (run_dir / "content.json").read_bytes()

    with pytest.raises(CliError, match="trust is not a recognized trust chip"):
        publish_now_command(root, None, broken)

    assert (run_dir / "content.json").read_bytes() == before


def test_add_update_appends_dated_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History is appended in order, with the Now panel left alone."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    update = {
        "id": "review-round-one",
        "timestamp": "2026-07-27 09:00 EDT",
        "headline": "The first round landed",
        "summary": "Recorded as history.",
        "lanes": [],
    }
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(update)))

    assert main(["add-update", "-"]) == 0

    assert [item["id"] for item in read_content_file(run_dir)["updates"]] == [
        "now",
        "review-round-one",
    ]
    assert owner_at(read_content_file(run_dir), ANCHOR) is not None


def test_add_update_refuses_the_now_id(tmp_path: Path) -> None:
    """History never carries the reserved id."""
    root = tmp_path / "runs"
    make_run(root)

    with pytest.raises(CliError, match="publish-now"):
        add_update_command(
            root,
            None,
            {
                "id": "now",
                "timestamp": "2026-07-27",
                "headline": "Wrong id",
                "summary": "Belongs to the panel.",
                "lanes": [],
            },
        )


def test_add_update_refuses_a_duplicate_id(tmp_path: Path) -> None:
    """Appending the same id twice would rewrite history in place."""
    root = tmp_path / "runs"
    make_run(root)
    update = {
        "id": "round-one",
        "timestamp": "2026-07-27",
        "headline": "Once",
        "summary": "Only once.",
        "lanes": [],
    }

    assert add_update_command(root, None, update) == 0
    with pytest.raises(CliError, match="already exists"):
        add_update_command(root, None, update)


def test_add_update_requires_a_timestamp(tmp_path: Path) -> None:
    """A dated update without a date is refused rather than invented."""
    root = tmp_path / "runs"
    make_run(root)

    with pytest.raises(CliError, match="must carry a timestamp"):
        add_update_command(
            root,
            None,
            {
                "id": "undated",
                "headline": "No date",
                "summary": "Missing its timestamp.",
                "lanes": [],
            },
        )
