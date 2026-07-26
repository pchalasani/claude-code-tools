"""Focused regressions for unanswered-thread accounting."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from visual_brief.server.counting import (
    merge_pending_followups,
    reply_target_error,
)
from visual_brief.server.queue import MAX_QUESTION_LENGTH
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
    encoded_content = json.dumps(content).encode()
    (run / "content.json").write_bytes(encoded_content)
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


def test_accepted_reply_is_not_reanchored_when_its_target_disappears(
    tmp_path: Path,
) -> None:
    """Leave a raced reply orphaned instead of assigning an unrelated owner."""
    run = tmp_path / "orphaned-reply"
    run.mkdir()
    content = {
        "updates": [
            {
                "id": "replacement",
                "lanes": [{"id": "survivor", "questions": []}],
            }
        ]
    }
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    record = {
        "timestamp": "2026-07-25T20:00:00Z",
        "type": "question",
        "anchor_id": "removed/lane/item",
        "text": "Accepted before replacement",
        "parent_id": "q-removed",
        "content_generation": "previous-generation",
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    assert merge_pending_followups(run) is None
    assert count_unanswered_questions(run) == 0


def test_queued_reply_with_prior_legacy_id_keeps_its_thread(
    tmp_path: Path,
) -> None:
    """Map a persisted pre-migration parent ID to the stable legacy thread."""
    run = tmp_path / "legacy-id-migration"
    run.mkdir()
    anchor = "update/lane"
    content = {
        "updates": [
            {
                "id": "update",
                "lanes": [
                    {
                        "id": "lane",
                        "questions": [
                            {
                                "question": "Repeated?",
                                "answer": "First answer.",
                                "asked_at": "2026-07-25T19:00:00Z",
                            },
                            {
                                "question": "Repeated?",
                                "answer": "Second answer.",
                                "asked_at": "2026-07-25T20:00:00Z",
                            },
                        ],
                    }
                ],
            }
        ]
    }
    encoded_content = json.dumps(content).encode()
    (run / "content.json").write_bytes(encoded_content)
    prior_identity = f"{anchor}\0Repeated?\0{1}".encode()
    prior_digest = hashlib.sha256(prior_identity).hexdigest()[:12]
    record = {
        "timestamp": "2026-07-25T21:00:00Z",
        "type": "question",
        "anchor_id": anchor,
        "text": "Follow-up for the second answer",
        "parent_id": f"q-{prior_digest}",
        "content_generation": hashlib.sha256(encoded_content).hexdigest(),
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    merged = merge_pending_followups(run)

    assert merged is not None
    threads = merged["updates"][0]["lanes"][0]["questions"]
    assert len(threads) == 2
    assert [turn["text"] for turn in threads[1]["turns"]] == [
        "Repeated?",
        "Second answer.",
        "Follow-up for the second answer",
    ]


@pytest.mark.parametrize("text", ["", " \t ", "x" * (MAX_QUESTION_LENGTH + 1)])
def test_invalid_queued_question_text_is_ignored(
    tmp_path: Path,
    text: str,
) -> None:
    """Do not merge or count queue text rejected at submission time."""
    run = tmp_path / "invalid-text"
    run.mkdir()
    content = {
        "updates": [
            {
                "id": "update",
                "lanes": [{"id": "lane", "questions": []}],
            }
        ]
    }
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    record = {
        "timestamp": "2026-07-25T20:00:00Z",
        "type": "question",
        "anchor_id": "update/lane",
        "text": text,
        "parent_id": None,
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    assert merge_pending_followups(run) is None
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


def test_equivalent_utc_timestamps_fold_the_same_saved_reply(
    tmp_path: Path,
) -> None:
    """Treat Z and an explicit UTC offset as the same folded instant."""
    run = tmp_path / "equivalent-instants"
    run.mkdir()
    anchor = "update/lane/item"
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
                                        "id": "q-same-instant",
                                        "turns": [
                                            {
                                                "author": "human",
                                                "text": "Initial?",
                                                "at": "2026-07-25T19:00:00Z",
                                            },
                                            {
                                                "author": "agent",
                                                "text": "Initial answer.",
                                                "at": "2026-07-25T19:01:00Z",
                                            },
                                            {
                                                "author": "human",
                                                "text": "Already folded?",
                                                "at": (
                                                    "2026-07-25T20:00:00"
                                                    "+00:00"
                                                ),
                                            },
                                            {
                                                "author": "agent",
                                                "text": "Yes.",
                                                "at": "2026-07-25T20:01:00Z",
                                            },
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
        "timestamp": "2026-07-25T20:00:00Z",
        "type": "question",
        "anchor_id": anchor,
        "text": "Already folded?",
        "parent_id": "q-same-instant",
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    assert merge_pending_followups(run) is None
    assert count_unanswered_questions(run) == 0


def test_pending_parentless_thread_accepts_and_merges_a_reply(
    tmp_path: Path,
) -> None:
    """Recognize the stable ID exposed for a queued parentless thread."""
    run = tmp_path / "pending-parent"
    run.mkdir()
    anchor = "update/lane"
    content = {
        "updates": [
            {
                "id": "update",
                "lanes": [{"id": "lane", "questions": []}],
            }
        ]
    }
    content_path = run / "content.json"
    content_path.write_text(json.dumps(content), encoding="utf-8")
    first = {
        "timestamp": "2026-07-25T20:00:00Z",
        "type": "question",
        "anchor_id": anchor,
        "text": "New queued thread?",
        "parent_id": None,
    }
    queue_path = run / "questions.jsonl"
    queue_path.write_text(f"{json.dumps(first)}\n", encoding="utf-8")
    merged = merge_pending_followups(run)
    assert merged is not None
    thread = merged["updates"][0]["lanes"][0]["questions"][0]
    thread_id = thread["id"]
    assert reply_target_error(run, thread_id, anchor) is None

    second = {
        "timestamp": "2026-07-25T20:01:00Z",
        "type": "question",
        "anchor_id": anchor,
        "text": "Immediate follow-up",
        "parent_id": thread_id,
    }
    queue_path.write_text(
        f"{json.dumps(first)}\n{json.dumps(second)}\n",
        encoding="utf-8",
    )
    merged = merge_pending_followups(run)
    assert merged is not None
    turns = merged["updates"][0]["lanes"][0]["questions"][0]["turns"]
    assert [turn["text"] for turn in turns] == [
        "New queued thread?",
        "Immediate follow-up",
    ]
