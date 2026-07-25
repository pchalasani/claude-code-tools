"""Integration regressions for queued visual-brief follow-ups."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from visual_brief.cli import list_command
from visual_brief.render import render_content
from visual_brief.server.daemon import HOST, VisualBriefServer, create_server
from visual_brief.server.registry import count_unanswered_questions

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"
RUN_ID = "pending-run"
THREAD_ID = "q-malformed-unsupported"
ANCHOR_ID = "current-update/why-it-matters/repair-loop-routing"


def _example() -> dict[str, object]:
    """Load the example document."""
    value = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _make_run(root: Path) -> Path:
    """Create a rendered run with one saved answered thread."""
    run = root / RUN_ID
    run.mkdir(parents=True)
    content = _example()
    (run / "content.json").write_text(
        json.dumps(content),
        encoding="utf-8",
    )
    (run / "index.html").write_text(
        render_content(content),
        encoding="utf-8",
    )
    (run / "questions.jsonl").write_bytes(b"")
    metadata = {
        "run_id": RUN_ID,
        "label": "Pending follow-up",
        "created_at": "2026-07-25T19:00:00Z",
        "updated_at": "2026-07-25T19:00:00Z",
    }
    (run / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    return run


@contextmanager
def _running_server(root: Path) -> Iterator[VisualBriefServer]:
    """Run a real daemon on an ephemeral loopback port."""
    server = create_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(
    server: VisualBriefServer,
    method: str,
    path: str,
    payload: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Send one request to the real test daemon."""
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    connection = http.client.HTTPConnection(
        HOST,
        server.server_address[1],
        timeout=2,
    )
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    response_body = response.read()
    status = response.status
    connection.close()
    return status, response_body


@pytest.mark.parametrize(
    ("parent_id", "anchor_id"),
    [
        ("q-does-not-exist", ANCHOR_ID),
        (THREAD_ID, "current-update/what-changed"),
    ],
)
def test_post_rejects_stale_or_cross_anchor_reply_without_queue_change(
    tmp_path: Path,
    parent_id: str,
    anchor_id: str,
) -> None:
    """Reject an invalid reply target and preserve the queue byte-for-byte."""
    root = tmp_path / "runs"
    run = _make_run(root)
    queue = run / "questions.jsonl"
    queue.write_bytes(b'{"type":"signal","signal":"skip"}\n')
    before = queue.read_bytes()
    with _running_server(root) as server:
        status, body = _request(
            server,
            "POST",
            f"/r/{RUN_ID}/ask",
            {
                "anchor_id": anchor_id,
                "text": "Reply from a stale page",
                "parent_id": parent_id,
            },
        )

    assert status == 409
    assert b"Reply parent" in body
    assert queue.read_bytes() == before


def test_pending_follow_up_agrees_across_page_dashboard_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expose one queued reply consistently in every fresh status view."""
    root = tmp_path / "runs"
    run = _make_run(root)
    question = "<b>Still unsafe?</b>"
    with _running_server(root) as server:
        status, _ = _request(
            server,
            "POST",
            f"/r/{RUN_ID}/ask",
            {
                "anchor_id": ANCHOR_ID,
                "text": question,
                "parent_id": THREAD_ID,
            },
        )
        assert status == 202
        dashboard_status, dashboard = _request(server, "GET", "/")
        page_status, page = _request(server, "GET", f"/r/{RUN_ID}/")
        version_status, generation = _request(
            server,
            "GET",
            f"/r/{RUN_ID}/render-version",
        )

    assert count_unanswered_questions(run) == 1
    assert list_command(root) == 0
    assert "unanswered: 1" in capsys.readouterr().out
    assert dashboard_status == 200
    assert b"waiting on you \xc2\xb7 1 question" in dashboard
    assert page_status == 200
    assert b"&lt;b&gt;Still unsafe?&lt;/b&gt;" in page
    assert b'<details class="thread" open data-awaiting>' in page
    assert b'<details class="item" open>' in page
    assert b'class="lane" open' in page
    assert version_status == 200
    assert generation in page


def test_identical_later_follow_up_is_not_consumed_as_folded(
    tmp_path: Path,
) -> None:
    """Use timestamps to distinguish repeated human text on one thread."""
    root = tmp_path / "runs"
    run = _make_run(root)
    content = json.loads((run / "content.json").read_text(encoding="utf-8"))
    thread = content["updates"][1]["lanes"][1]["items"][0]["questions"][0]
    thread["turns"].extend(
        [
            {
                "author": "human",
                "text": "Still?",
                "at": "2026-07-25T20:00:00Z",
            },
            {
                "author": "agent",
                "text": "Yes.",
                "at": "2026-07-25T20:01:00Z",
            },
        ]
    )
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    records = [
        {
            "timestamp": "2026-07-25T20:00:00Z",
            "type": "question",
            "anchor_id": ANCHOR_ID,
            "text": "Still?",
            "parent_id": THREAD_ID,
        },
        {
            "timestamp": "2026-07-25T20:02:00Z",
            "type": "question",
            "anchor_id": ANCHOR_ID,
            "text": "Still?",
            "parent_id": THREAD_ID,
        },
    ]
    (run / "questions.jsonl").write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )

    assert count_unanswered_questions(run) == 1


def test_cross_anchor_follow_up_does_not_change_saved_thread_state(
    tmp_path: Path,
) -> None:
    """Ignore a historical reply whose parent belongs to another anchor."""
    root = tmp_path / "runs"
    run = _make_run(root)
    record = {
        "timestamp": "2026-07-25T20:00:00Z",
        "type": "question",
        "anchor_id": "current-update/what-changed",
        "text": "Wrong owner",
        "parent_id": THREAD_ID,
    }
    (run / "questions.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )

    assert count_unanswered_questions(run) == 0
