"""Real-browser regression coverage for queue-backed thread folding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from browser_support import Browser, browser_session
from visual_brief.render import render_content
from visual_brief.server.counting import merge_pending_followups
from visual_brief.server.queue import build_question_record
from visual_brief.server.registry import count_unanswered_questions


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def _item(data: dict[str, Any], anchor: str) -> dict[str, Any]:
    """Return the item identified by one three-part anchor."""
    update_id, lane_id, item_id = anchor.split("/")
    return next(
        item
        for update in data["updates"]
        if update["id"] == update_id
        for lane in update["lanes"]
        if lane["id"] == lane_id
        for item in lane["items"]
        if item["id"] == item_id
    )


def test_browser_reply_survives_pending_thread_fold(
    browser: Browser,
    tmp_path: Path,
) -> None:
    """Keep a page-submitted reply when its pending parent becomes stable."""
    anchor = "current-update/what-changed/differential-reader-check"
    question = "Does this survive folding?"
    reply = "This reply came from the reloaded page."
    form = f'form.question-box[data-anchor-id="{anchor}"]:not(.reply-box)'
    ask = f'.ask-button[data-target="ask-{anchor}"]'
    browser.batch(
        [
            ["click", ask],
            ["type", f"{form} textarea", question],
            ["click", f"{form} .submit"],
            ["wait", "100"],
        ]
    )
    assert browser.server.posts == [
        ("/ask", {"anchor_id": anchor, "text": question})
    ]

    run = tmp_path / "folded-browser-reply"
    run.mkdir()
    content_bytes = json.dumps(browser.data).encode()
    (run / "content.json").write_bytes(content_bytes)
    first = build_question_record(browser.server.posts[0][1])
    first["timestamp"] = "2026-07-25T20:00:00Z"
    queue_path = run / "questions.jsonl"
    queue_path.write_text(f"{json.dumps(first)}\n", encoding="utf-8")

    pending = merge_pending_followups(run)
    assert pending is not None
    pending_thread = next(
        thread
        for thread in _item(pending, anchor)["questions"]
        if thread["turns"][0]["text"] == question
    )
    pending_id = pending_thread["id"]
    assert pending_id.startswith("q-pending-")

    browser.data = pending
    browser.publish()
    browser.run("open", browser.url)
    reply_form = f'form.reply-box[data-parent-id="{pending_id}"]'
    browser.batch(
        [
            ["type", f"{reply_form} textarea", reply],
            ["click", f"{reply_form} .submit"],
            ["wait", "100"],
        ]
    )
    assert browser.server.posts[1] == (
        "/ask",
        {"anchor_id": anchor, "text": reply, "parent_id": pending_id},
    )

    second = build_question_record(browser.server.posts[1][1])
    second["timestamp"] = "2026-07-25T20:02:00Z"
    second["content_generation"] = hashlib.sha256(content_bytes).hexdigest()
    queue_path.write_text(
        f"{json.dumps(first)}\n{json.dumps(second)}\n",
        encoding="utf-8",
    )
    folded = json.loads(content_bytes)
    _item(folded, anchor).setdefault("questions", []).append(
        {
            "id": "q-final",
            "anchor": {"kind": "element", "path": anchor},
            "turns": [
                {
                    "author": "human",
                    "text": question,
                    "at": first["timestamp"],
                },
                {
                    "author": "agent",
                    "text": "Yes, once folded.",
                    "at": "2026-07-25T20:01:00Z",
                },
            ],
        }
    )
    (run / "content.json").write_text(json.dumps(folded), encoding="utf-8")

    merged = merge_pending_followups(run)

    assert merged is not None
    final_thread = next(
        thread
        for thread in _item(merged, anchor)["questions"]
        if thread["id"] == "q-final"
    )
    assert [turn["text"] for turn in final_thread["turns"]] == [
        question,
        "Yes, once folded.",
        reply,
    ]
    assert reply in render_content(merged)
    assert count_unanswered_questions(run) == 3
