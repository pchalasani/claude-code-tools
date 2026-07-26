"""Regression coverage for dated legacy thread identities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from visual_brief.render.threads import normalize_document
from visual_brief.server.counting import merge_pending_followups


def _dated_thread(document: dict[str, Any]) -> dict[str, Any]:
    """Return the explicit-epoch thread from a normalized document."""
    threads = document["updates"][0]["lanes"][0]["questions"]
    return next(
        thread
        for thread in threads
        if thread["turns"][1]["text"] == "Explicit epoch."
    )


def test_undated_prepend_preserves_dated_id_and_reply_target(
    tmp_path: Path,
) -> None:
    """Keep undated pairs out of dated occurrence accounting."""
    anchor = "update/lane"
    questions = [
        {"question": "Repeated?", "answer": "Undated A."},
        {
            "question": "Repeated?",
            "answer": "Explicit epoch.",
            "asked_at": "1970-01-01T00:00:00Z",
        },
        {"question": "Repeated?", "answer": "Undated B."},
    ]
    content = {
        "updates": [
            {
                "id": "update",
                "lanes": [{"id": "lane", "questions": questions}],
            }
        ]
    }
    original_thread_id = _dated_thread(normalize_document(content))["id"]
    questions.insert(
        0,
        {"question": "Repeated?", "answer": "Prepended undated."},
    )
    normalized = normalize_document(content)

    assert _dated_thread(normalized)["id"] == original_thread_id

    run = tmp_path / "dated-reply"
    run.mkdir()
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    record = {
        "timestamp": "2026-07-26T12:00:00Z",
        "type": "question",
        "anchor_id": anchor,
        "text": "Follow up on explicit epoch.",
        "parent_id": original_thread_id,
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    merged = merge_pending_followups(run)

    assert merged is not None
    dated_thread = _dated_thread(merged)
    assert dated_thread["id"] == original_thread_id
    assert [turn["text"] for turn in dated_thread["turns"]] == [
        "Repeated?",
        "Explicit epoch.",
        "Follow up on explicit epoch.",
    ]
