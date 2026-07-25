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
