"""Appending dated updates without rewriting saved history."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from page_document import embedded_document, iter_threads
from write_support import (
    ANCHOR,
    LANE_ANCHOR,
    base_document,
    make_run,
    queue_line,
    read_content_file,
)
from visual_brief.cli import main
from visual_brief.writes import CliError, add_update_command, fold_command

UPDATE = {
    "id": "review-round-one",
    "timestamp": "2026-07-27 09:00 EDT",
    "headline": "The first round landed",
    "summary": "Recorded as history.",
    "lanes": [],
}

HISTORY = {
    "id": "earlier-history",
    "timestamp": "2026-07-26 17:00 EDT",
    "headline": "The previous update",
    "summary": "Saved behind the formerly pinned panel.",
    "lanes": [],
}

CREATED = {
    "id": "created",
    "timestamp": "Created",
    "headline": "The visual brief run is ready",
    "summary": "The placeholder that preceded the first Now panel.",
    "lanes": [],
}


def _thread_locations(
    document: dict[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """Index every conversation by id with its anchor and complete value."""
    return {
        str(thread["id"]): (path, copy.deepcopy(thread))
        for path, thread in iter_threads(document)
    }


def test_add_update_migrates_now_without_moving_eighteen_conversations(
    tmp_path: Path,
) -> None:
    """The former Now panel becomes immutable history at identical anchors."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    for number in range(9):
        queue_line(
            run_dir,
            f"Lane-level conversation {number + 1}",
            anchor_id=LANE_ANCHOR,
        )
        queue_line(
            run_dir,
            f"Item-level conversation {number + 1}",
            anchor_id=ANCHOR,
        )
    assert fold_command(root, None) == 0
    before = read_content_file(run_dir)
    before.pop("updates_order", None)
    before["updates"].insert(0, copy.deepcopy(CREATED))
    before["updates"].append(copy.deepcopy(HISTORY))
    (run_dir / "content.json").write_text(
        json.dumps(before, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    original_now = copy.deepcopy(before["updates"][1])
    locations = _thread_locations(before)
    assert len(locations) == 18
    assert {path for path, _ in locations.values()} == {
        LANE_ANCHOR,
        ANCHOR,
    }

    assert add_update_command(root, None, copy.deepcopy(UPDATE)) == 0

    after = read_content_file(run_dir)
    assert after["updates"][2] == original_now
    assert after["updates"][2]["timestamp"] == "2026-07-27 12:00 EDT"
    assert _thread_locations(after) == locations
    assert after["updates_order"] == "append"
    assert [update["id"] for update in after["updates"]] == [
        "created",
        "earlier-history",
        "now",
        "review-round-one",
    ]
    assert [
        update["id"]
        for update in embedded_document(
            (run_dir / "index.html").read_text(encoding="utf-8")
        )["updates"]
    ] == ["created", "earlier-history", "now", "review-round-one"]

    next_update = copy.deepcopy(UPDATE)
    next_update["id"] = "review-round-two"
    assert add_update_command(root, None, next_update) == 0
    assert [
        update["id"] for update in read_content_file(run_dir)["updates"]
    ] == [
        "created",
        "earlier-history",
        "now",
        "review-round-one",
        "review-round-two",
    ]


def test_migration_keeps_now_before_history_appended_after_final_publish(
    tmp_path: Path,
) -> None:
    """Chronology, not the former pinned position, decides migration order."""
    root = tmp_path / "runs"
    document = base_document()
    document["updates"].insert(0, copy.deepcopy(CREATED))
    later_history = copy.deepcopy(HISTORY)
    later_history["id"] = "history-after-now"
    later_history["timestamp"] = "2026-07-28 09:00 EDT"
    document["updates"].append(later_history)
    make_run(root, document=document)
    next_update = copy.deepcopy(UPDATE)
    next_update["timestamp"] = "2026-07-29 09:00 EDT"

    assert add_update_command(root, None, next_update) == 0

    saved = read_content_file(root / "write-run")
    assert [update["id"] for update in saved["updates"]] == [
        "created",
        "now",
        "history-after-now",
        "review-round-one",
    ]


def test_migration_preserves_now_position_for_incomparable_dates(
    tmp_path: Path,
) -> None:
    """An undated history label gives migration no basis to move ``now``."""
    root = tmp_path / "runs"
    document = base_document()
    history = copy.deepcopy(HISTORY)
    history["timestamp"] = "Written after the final Now publish"
    document["updates"].append(history)
    make_run(root, document=document)

    assert add_update_command(root, None, copy.deepcopy(UPDATE)) == 0

    assert [
        update["id"]
        for update in read_content_file(root / "write-run")["updates"]
    ] == ["now", "earlier-history", "review-round-one"]


def test_migration_fails_loudly_if_a_saved_anchor_does_not_resolve(
    tmp_path: Path,
) -> None:
    """Appending cannot silently rehome a thread from a damaged old panel."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    queue_line(run_dir, "Keep my exact item anchor.")
    assert fold_command(root, None) == 0
    damaged = read_content_file(run_dir)
    thread = next(iter(iter_threads(damaged)))[1]
    thread["anchor"]["path"] = "now/state/a-different-item"
    content_path = run_dir / "content.json"
    content_path.write_text(
        json.dumps(damaged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    before = content_path.read_bytes()

    with pytest.raises(
        CliError,
        match=r"anchor\.path must be 'now/state/tests'",
    ):
        add_update_command(root, None, copy.deepcopy(UPDATE))

    assert content_path.read_bytes() == before


def test_add_update_appends_through_the_only_publish_verb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI appends one supplied dated update and leaves history alone."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = copy.deepcopy(base_document()["updates"][0])
    monkeypatch.setenv("VISUAL_BRIEF_HOME", str(root))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(UPDATE)))

    assert main(["add-update", "-"]) == 0

    updates = read_content_file(run_dir)["updates"]
    assert updates[0] == before
    assert updates[1] == UPDATE
    saved = read_content_file(run_dir)
    assert saved["updates_order"] == "append"
    delivered = embedded_document(
        (run_dir / "index.html").read_text(encoding="utf-8")
    )
    assert [update["id"] for update in delivered["updates"]] == [
        "now",
        "review-round-one",
    ]


def test_add_update_accepts_an_empty_compatible_document(
    tmp_path: Path,
) -> None:
    """An older empty timeline accepts its first immutable update."""
    root = tmp_path / "runs"
    document = {
        "title": "Empty timeline",
        "summary": "No update has been published yet.",
        "updates": [],
    }
    run_dir = make_run(root, document=document)

    assert add_update_command(root, None, copy.deepcopy(UPDATE)) == 0

    saved = read_content_file(run_dir)
    assert saved["updates_order"] == "append"
    assert saved["updates"] == [UPDATE]


def test_concurrent_add_updates_preserve_every_publish(
    tmp_path: Path,
) -> None:
    """The per-run transaction lock prevents lost concurrent updates."""
    root = tmp_path / "runs"
    make_run(root)
    environment = os.environ.copy()
    environment["VISUAL_BRIEF_HOME"] = str(root)
    processes: list[subprocess.Popen[str]] = []
    expected_ids: list[str] = []
    for number in range(12):
        update = copy.deepcopy(UPDATE)
        update_id = f"concurrent-{number}"
        update["id"] = update_id
        expected_ids.append(update_id)
        update_path = tmp_path / f"{update_id}.json"
        update_path.write_text(json.dumps(update), encoding="utf-8")
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "visual_brief.cli",
                    "add-update",
                    "--run",
                    "write-run",
                    "--file",
                    str(update_path),
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    results = [process.communicate(timeout=30) for process in processes]

    warning = (
        "warning: add-update is for compatibility imports; "
        "normal briefings use publish\n"
    )
    assert [
        (process.returncode, stdout, stderr)
        for process, (stdout, stderr) in zip(processes, results, strict=True)
        if process.returncode != 0 or stderr != warning
    ] == []
    saved_ids = {
        update["id"] for update in read_content_file(root / "write-run")["updates"]
    }
    assert saved_ids == {"now", *expected_ids}


def test_add_update_accepts_now_as_an_ordinary_id(tmp_path: Path) -> None:
    """No id is reserved after the former Now panel is gone."""
    root = tmp_path / "runs"
    document = base_document()
    document["updates"][0]["id"] = "created"
    make_run(root, document=document)
    update = copy.deepcopy(UPDATE)
    update["id"] = "now"

    assert add_update_command(root, None, update) == 0
    assert add_update_command(root, None, copy.deepcopy(UPDATE)) == 0
    saved = read_content_file(root / "write-run")
    assert saved["updates_order"] == "append"
    assert [entry["id"] for entry in saved["updates"]] == [
        "created",
        "now",
        "review-round-one",
    ]


def test_add_update_refuses_a_duplicate_id(tmp_path: Path) -> None:
    """Appending the same id twice would rewrite history in place."""
    root = tmp_path / "runs"
    make_run(root)

    assert add_update_command(root, None, copy.deepcopy(UPDATE)) == 0
    with pytest.raises(CliError, match="already exists"):
        add_update_command(root, None, copy.deepcopy(UPDATE))


def test_add_update_warns_that_normal_reports_use_publish(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The compatibility writer directs ordinary reporting to publish."""
    root = tmp_path / "runs"
    make_run(root)

    assert add_update_command(root, None, copy.deepcopy(UPDATE)) == 0

    assert "normal briefings use publish" in capsys.readouterr().err


def test_add_update_requires_an_object(tmp_path: Path) -> None:
    """A list of updates is not one update and fails before any write."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    before = (run_dir / "content.json").read_bytes()

    with pytest.raises(CliError, match="must be a JSON object"):
        add_update_command(root, None, [UPDATE])

    assert (run_dir / "content.json").read_bytes() == before


def test_add_update_requires_an_id(tmp_path: Path) -> None:
    """An immutable update needs an identity before it can be appended."""
    root = tmp_path / "runs"
    make_run(root)
    update = copy.deepcopy(UPDATE)
    del update["id"]

    with pytest.raises(CliError, match="must carry a non-empty id"):
        add_update_command(root, None, update)


def test_add_update_requires_a_timestamp(tmp_path: Path) -> None:
    """A dated update without a date is refused rather than invented."""
    root = tmp_path / "runs"
    make_run(root)
    update = copy.deepcopy(UPDATE)
    del update["timestamp"]

    with pytest.raises(CliError, match="must carry a timestamp"):
        add_update_command(root, None, update)


def test_add_update_reports_validation_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """An invalid update fails without changing the saved page."""
    root = tmp_path / "runs"
    run_dir = make_run(root)
    broken = copy.deepcopy(UPDATE)
    broken["lanes"] = [
        {
            "id": "lane",
            "name": "Lane",
            "items": [
                {
                    "id": "item",
                    "glance": "A claim.",
                    "explanation": "An explanation.",
                    "trust": "pretty-sure",
                }
            ],
        }
    ]
    before = (run_dir / "content.json").read_bytes()

    with pytest.raises(CliError, match="trust is not a recognized trust chip"):
        add_update_command(root, None, broken)

    assert (run_dir / "content.json").read_bytes() == before
