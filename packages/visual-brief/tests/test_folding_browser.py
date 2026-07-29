"""Real-browser regression coverage for queue-backed thread folding.

These drive composition through the page: they open the composer at a pending
thread, send a reply, and check that the reply survives once the pending
parent folds into saved content.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
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
    browser.compose_at(anchor)
    browser.send(question)
    browser.run("wait", "300")
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
    browser.wait_for_row(f"{anchor}#{pending_id}")
    browser.compose_at(f"{anchor}#{pending_id}")
    browser.send(reply)
    browser.run("wait", "300")
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


def test_identical_pending_threads_keep_their_own_replies_when_prepended(
    browser: Browser,
    tmp_path: Path,
) -> None:
    """Keep accepted replies with their pending threads after a legacy fold."""
    anchor = "current-update/what-changed/differential-reader-check"
    question = "Which identical conversation is this?"
    replies = ["Reply to the first thread.", "Reply to the second thread."]
    answers = ["Answer to the first thread.", "Answer to the second thread."]
    for index in range(2):
        browser.compose_at(anchor)
        browser.send(question)
        browser.run("wait", "500")
        assert browser.server.post_count == index + 1

    run = tmp_path / "identical-folded-browser-replies"
    run.mkdir()
    content_bytes = json.dumps(browser.data).encode()
    content_generation = hashlib.sha256(content_bytes).hexdigest()
    (run / "content.json").write_bytes(content_bytes)
    questions = [
        build_question_record(payload)
        for _, payload in browser.server.posts
    ]
    for index, record in enumerate(questions):
        record["timestamp"] = f"2026-07-25T20:0{index}:00Z"
    queue_path = run / "questions.jsonl"
    queue_path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in questions),
        encoding="utf-8",
    )

    pending = merge_pending_followups(run)
    assert pending is not None
    pending_threads = [
        thread
        for thread in _item(pending, anchor)["questions"]
        if thread["turns"][0]["text"] == question
    ]
    assert len(pending_threads) == 2

    browser.data = pending
    browser.publish()
    for thread in pending_threads:
        browser.wait_for_row(f"{anchor}#{thread['id']}")
    for thread, reply in zip(pending_threads, replies):
        browser.compose_at(f"{anchor}#{thread['id']}")
        browser.send(reply)
        browser.run("wait", "300")

    reply_records = [
        build_question_record(payload)
        for _, payload in browser.server.posts[2:]
    ]
    for index, record in enumerate(reply_records, start=2):
        record["timestamp"] = f"2026-07-25T20:0{index}:00Z"
        record["content_generation"] = content_generation
    queue_path.write_text(
        "".join(
            f"{json.dumps(record)}\n"
            for record in [*questions, *reply_records]
        ),
        encoding="utf-8",
    )

    folded = json.loads(content_bytes)
    existing = _item(folded, anchor).setdefault("questions", [])
    existing[:0] = [
        {"question": question, "answer": answers[1]},
        {"question": question, "answer": answers[0]},
    ]
    (run / "content.json").write_text(json.dumps(folded), encoding="utf-8")

    merged = merge_pending_followups(run)

    assert merged is not None
    folded_threads = [
        thread
        for thread in _item(merged, anchor)["questions"]
        if thread["turns"][0]["text"] == question
    ]
    conversations = {
        thread["turns"][1]["text"]: [
            turn["text"] for turn in thread["turns"]
        ]
        for thread in folded_threads
    }
    assert conversations[answers[0]] == [question, answers[0], replies[0]]
    assert conversations[answers[1]] == [question, answers[1], replies[1]]


def test_an_appended_update_arrives_open_at_the_top_with_its_age(
    browser: Browser,
) -> None:
    """Paint a live publish as a dated update rather than pinned state."""
    arrived = datetime.now(timezone.utc) - timedelta(minutes=4)
    timestamp = arrived.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    browser.data["updates"].append(
        {
            "id": "just-published",
            "timestamp": timestamp,
            "headline": "A live update",
            "summary": "Appended while the reader stays on the page.",
            "lanes": [],
        }
    )

    browser.publish()
    browser.wait_for_row("just-published")
    state = browser.evaluate(
        """
        (() => {
          const updates = [
            ...document.querySelectorAll('[data-row-kind="update"]'),
          ];
          const latest = updates[0];
          return {
            order: updates.map((row) => row.dataset.rowId),
            open: latest?.dataset.open ?? null,
            timestamp:
              latest?.querySelector(".update-time")?.textContent ?? null,
            age: latest?.querySelector(".update-age")?.textContent ?? null,
            divider: document.querySelector(".earlier-heading") !== null,
            nowMark: document.querySelector(".now-mark") !== null,
          };
        })()
        """
    )

    assert state["order"][0] == "just-published", state
    assert state["open"] == "true", state
    assert state["timestamp"] == timestamp, state
    assert state["age"] in {"4 minutes ago", "5 minutes ago"}, state
    assert state["divider"] is False, state
    assert state["nowMark"] is False, state
