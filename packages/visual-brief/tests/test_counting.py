"""Focused regressions for unanswered-thread accounting."""

from __future__ import annotations

import json
from pathlib import Path

from visual_brief.server.registry import count_unanswered_questions


def test_bogus_follow_up_parent_does_not_change_awaiting_count(
    tmp_path: Path,
) -> None:
    """Ignore a queued reply that references no saved thread."""
    run = tmp_path / "bogus-parent"
    run.mkdir()
    content = {
        "updates": [
            {
                "id": "update",
                "lanes": [
                    {
                        "id": "lane",
                        "items": [
                            {
                                "id": "item",
                                "questions": [
                                    {
                                        "id": "q-answered",
                                        "turns": [
                                            {"author": "human"},
                                            {"author": "agent"},
                                        ],
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
    record = {
        "type": "question",
        "anchor_id": "update/lane/item",
        "text": "Reply to nowhere",
        "parent_id": "q-does-not-exist",
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    assert count_unanswered_questions(run) == 0


def test_timestamped_queue_record_folds_answered_undated_legacy_pair(
    tmp_path: Path,
) -> None:
    """Treat a missing legacy asked_at as unknown, not as 1970 queue time."""
    run = tmp_path / "legacy"
    run.mkdir()
    content = {
        "updates": [
            {
                "id": "update",
                "lanes": [
                    {
                        "id": "lane",
                        "questions": [
                            {
                                "question": "Already answered?",
                                "answer": "Yes.",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    record = {
        "timestamp": "2026-07-25T19:00:00Z",
        "type": "question",
        "anchor_id": "update/lane",
        "text": "Already answered?",
        "parent_id": None,
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    assert count_unanswered_questions(run) == 0
