"""Tests for run discovery and dashboard rendering."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from visual_brief.server.dashboard import render_dashboard
from visual_brief.server.queue import MAX_QUEUE_RECORD_BYTES
from visual_brief.server.registry import (
    RunInfo,
    count_unanswered_questions,
    discover_runs,
    resolve_run_path,
    validate_run_id,
)


@pytest.mark.parametrize(
    "run_id",
    ["../../etc", "a/b", "A", "", "x" * 60, ".", "-x", "x-"],
)
def test_hostile_run_ids_are_rejected(run_id: str) -> None:
    """Reject every hostile run-id shape from the contract."""
    with pytest.raises(ValueError):
        validate_run_id(run_id)


@pytest.mark.parametrize("run_id", ["ab", "a-b", "brief-17", "a" * 40])
def test_valid_run_ids_are_accepted(run_id: str) -> None:
    """Accept identifiers that exactly match the run-id contract."""
    assert validate_run_id(run_id) == run_id


def test_resolved_run_path_stays_in_root(tmp_path: Path) -> None:
    """Reject both lexical traversal and a symlink escaping the root."""
    with pytest.raises(ValueError):
        resolve_run_path(tmp_path, "../../etc")

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_run_path(tmp_path, "escape")


def test_self_referential_run_symlink_is_skipped(tmp_path: Path) -> None:
    """A symlink loop cannot break discovery or direct path resolution."""
    (tmp_path / "loop").symlink_to("loop", target_is_directory=True)

    assert discover_runs(tmp_path) == []
    with pytest.raises(ValueError, match="run path may not be a symlink"):
        resolve_run_path(tmp_path, "loop")


def test_case_mismatched_run_directory_is_rejected(tmp_path: Path) -> None:
    """Do not resolve an invalid-cased entry through a valid lowercase id."""
    actual = tmp_path / "CaseRun"
    actual.mkdir()
    requested = tmp_path / "caserun"
    try:
        same_entry = requested.samefile(actual)
    except FileNotFoundError:
        pytest.skip("filesystem is case-sensitive")
    if not same_entry:
        pytest.skip("filesystem is case-sensitive")

    with pytest.raises(ValueError, match="exactly match"):
        resolve_run_path(tmp_path, "caserun")


def test_discover_runs_handles_zero_and_damaged_runs(tmp_path: Path) -> None:
    """List degraded runs while tolerating missing and malformed metadata."""
    assert discover_runs(tmp_path) == []

    missing = tmp_path / "missing"
    missing.mkdir()
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "meta.json").write_text("{bad", encoding="utf-8")
    (malformed / "content.json").write_text("{also bad", encoding="utf-8")
    (malformed / "questions.jsonl").write_bytes(b"\xff")

    runs = discover_runs(tmp_path)
    assert {run.run_id for run in runs} == {"missing", "malformed"}
    assert all(run.degraded for run in runs)
    assert all("metadata unavailable" in run.label for run in runs)
    dashboard = render_dashboard(runs, 8765)
    assert "missing (metadata unavailable)" in dashboard
    assert "malformed (metadata unavailable)" in dashboard


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00+14:00",
        "0001-01-01T00:00:00+23:59",
        "9999-12-31T23:59:59-14:00",
        "9999-12-31T23:59:59-23:59",
    ],
)
def test_boundary_timestamp_offsets_degrade_without_crashing(
    tmp_path: Path,
    timestamp: str,
) -> None:
    """UTC conversion underflow and overflow cannot break discovery."""
    run = _make_run(tmp_path, "boundary-run", "Boundary")
    metadata = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    metadata["updated_at"] = timestamp
    (run / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")

    discovered = discover_runs(tmp_path)

    assert len(discovered) == 1
    assert discovered[0].degraded
    assert "Boundary" in render_dashboard(discovered, 8765)


def test_discover_runs_orders_newest_activity_first(tmp_path: Path) -> None:
    """Order registry results by actual file activity."""
    older = _make_run(tmp_path, "older-run", "Older")
    newer = _make_run(tmp_path, "newer-run", "Newer")
    old_time = 1_700_000_000
    new_time = old_time + 100
    for path in (older, *(older.iterdir())):
        os.utime(path, (old_time, old_time))
    for path in (newer, *(newer.iterdir())):
        os.utime(path, (new_time, new_time))

    assert [run.run_id for run in discover_runs(tmp_path)] == [
        "newer-run",
        "older-run",
    ]


def test_unanswered_questions_match_anchor_and_text(tmp_path: Path) -> None:
    """Count only questions without an exact anchor-and-text answer."""
    run = _make_run(tmp_path, "answer-run", "Answers")
    content = {
        "updates": [
            {
                "id": "update",
                "lanes": [
                    {
                        "id": "lane",
                        "questions": [
                            {"question": "Lane question?", "answer": "Yes."}
                        ],
                        "items": [
                            {
                                "id": "item",
                                "questions": [
                                    {
                                        "question": "Item question?",
                                        "answer": "Also yes.",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    records = [
        {
            "type": "question",
            "anchor_id": "update/lane",
            "text": "Lane question?",
        },
        {
            "type": "question",
            "anchor_id": "update/lane/item",
            "text": "Item question?",
        },
        {
            "type": "question",
            "anchor_id": "update/lane",
            "text": "Still waiting?",
        },
        {
            "type": "question",
            "anchor_id": "different/anchor",
            "text": "Lane question?",
        },
    ]
    queue = "\n".join(json.dumps(record) for record in records) + "\n"
    (run / "questions.jsonl").write_text(queue, encoding="utf-8")

    assert count_unanswered_questions(run) == 2


def test_queue_keeps_valid_lines_before_incomplete_utf8(tmp_path: Path) -> None:
    """An incomplete UTF-8 record does not hide earlier questions."""
    run = _make_run(tmp_path, "partial-run", "Partial")
    valid = {
        "type": "question",
        "anchor_id": "update/lane",
        "text": "Still there?",
    }
    queue = json.dumps(valid).encode("utf-8") + b"\n\xe2"
    (run / "questions.jsonl").write_bytes(queue)

    assert count_unanswered_questions(run) == 1


def test_queue_skips_oversized_record_and_keeps_later_question(
    tmp_path: Path,
) -> None:
    """A huge queue record is discarded without hiding later records."""
    run = _make_run(tmp_path, "large-run", "Large")
    oversized = (
        b'{"text":"' + (b"x" * (MAX_QUEUE_RECORD_BYTES + 1)) + b'"}\n'
    )
    valid = {
        "type": "question",
        "anchor_id": "update/lane",
        "text": "After the large record?",
    }
    (run / "questions.jsonl").write_bytes(
        oversized + json.dumps(valid).encode("utf-8") + b"\n"
    )

    assert count_unanswered_questions(run) == 1


def test_dashboard_handles_zero_runs() -> None:
    """Render a useful empty status board with timed refresh."""
    dashboard = render_dashboard([], 8765)

    assert "No visual brief runs yet" in dashboard
    assert "setInterval" in dashboard
    assert "5000" in dashboard


def test_dashboard_shows_waiting_badge_and_both_links() -> None:
    """Show context, waiting state, and both routing forms."""
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    run = RunInfo(
        run_id="my-run",
        label="<My brief>",
        cwd=None,
        repo="sample/repo",
        branch="feature",
        created_at=None,
        updated_at=None,
        activity_at=now - timedelta(minutes=2),
        unanswered_count=1,
    )

    dashboard = render_dashboard([run], 8765, now=now)

    assert "&lt;My brief&gt;" in dashboard
    assert "waiting on you · 1 question" in dashboard
    assert "sample/repo · feature" in dashboard
    assert "http://my-run.localhost:8765/" in dashboard
    assert "http://localhost:8765/r/my-run/" in dashboard
    assert "2 minutes ago" in dashboard


def _make_run(root: Path, run_id: str, label: str) -> Path:
    """Create one minimally valid run directory."""
    run = root / run_id
    run.mkdir()
    metadata = {
        "run_id": run_id,
        "label": label,
        "cwd": "/tmp/example",
        "repo": "repo",
        "branch": "main",
        "created_at": "2023-01-01T00:00:00Z",
        "updated_at": "2023-01-01T00:00:00Z",
    }
    (run / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    (run / "content.json").write_text('{"updates":[]}', encoding="utf-8")
    return run
