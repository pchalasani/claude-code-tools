"""Regression coverage for daemon question-acceptance concurrency."""

from __future__ import annotations

import http.client
import json
import linecache
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from visual_brief import MAX_THREAD_ID_LENGTH
from visual_brief.render import render_content
from visual_brief.server.daemon import HOST, VisualBriefServer, create_server
from visual_brief.server.registry import count_unanswered_questions
from visual_brief.server.served_page import read_served_page

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"
RUN_ID = "race-run"
THREAD_ID = "q-malformed-unsupported"
ANCHOR_ID = "current-update/why-it-matters/repair-loop-routing"


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


def _request(server: VisualBriefServer) -> tuple[int, bytes]:
    """Post one reply to the real daemon."""
    payload = json.dumps(
        {
            "anchor_id": ANCHOR_ID,
            "text": "Racing reply",
            "parent_id": THREAD_ID,
        }
    ).encode()
    connection = http.client.HTTPConnection(
        HOST,
        server.server_address[1],
        timeout=5,
    )
    connection.request(
        "POST",
        f"/r/{RUN_ID}/ask",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def _wait_until_handler_reaches(source_lines: set[str]) -> None:
    """Wait until a request handler reaches one of the source lines."""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        for frame in sys._current_frames().values():
            source = linecache.getline(
                frame.f_code.co_filename,
                frame.f_lineno,
            ).strip()
            if any(line in source for line in source_lines):
                return
        time.sleep(0.001)
    raise AssertionError("request handler did not reach synchronization point")


def test_longest_rendered_thread_id_accepts_reply_through_daemon(
    tmp_path: Path,
) -> None:
    """Keep the rendered reply-id boundary accepted by the daemon."""
    root = tmp_path / "runs"
    run = root / RUN_ID
    run.mkdir(parents=True)
    content = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    item = content["updates"][0]["lanes"][0]["items"][0]
    thread_id = "q" * MAX_THREAD_ID_LENGTH
    item["questions"][0]["id"] = thread_id
    anchor = (
        f'{content["updates"][0]["id"]}/'
        f'{content["updates"][0]["lanes"][0]["id"]}/{item["id"]}'
    )
    rendered = render_content(content)
    assert f'data-parent-id="{thread_id}"' in rendered
    (run / "content.json").write_text(
        json.dumps(content),
        encoding="utf-8",
    )
    (run / "index.html").write_text(rendered, encoding="utf-8")
    payload = json.dumps(
        {
            "anchor_id": anchor,
            "text": "Boundary reply",
            "parent_id": thread_id,
        }
    ).encode()

    with _running_server(root) as server:
        connection = http.client.HTTPConnection(
            HOST,
            server.server_address[1],
            timeout=5,
        )
        connection.request(
            "POST",
            f"/r/{RUN_ID}/ask",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 202
        response.read()
        connection.close()

    record = json.loads(
        (run / "questions.jsonl").read_text(encoding="utf-8")
    )
    assert record["parent_id"] == thread_id


def test_post_rejects_reply_when_content_changes_before_append(
    tmp_path: Path,
) -> None:
    """Reject a reply whose validated content generation is replaced."""
    root = tmp_path / "runs"
    run = root / RUN_ID
    run.mkdir(parents=True)
    content = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    (run / "index.html").write_text(
        render_content(content),
        encoding="utf-8",
    )
    (run / "questions.jsonl").write_bytes(b"")

    results: list[tuple[int, bytes]] = []
    with _running_server(root) as server:
        server.queue_lock.acquire()
        worker = threading.Thread(target=lambda: results.append(_request(server)))
        worker.start()
        try:
            _wait_until_handler_reaches(
                {"with self.server.queue_lock:", "with lock:"}
            )
            content_path = run / "content.json"
            item = content["updates"][1]["lanes"][1]["items"][0]
            item["questions"] = []
            replacement = run / "replacement.json"
            replacement.write_text(json.dumps(content), encoding="utf-8")
            replacement.replace(content_path)
        finally:
            server.queue_lock.release()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert results[0][0] == 409
    assert b"Content changed" in results[0][1]
    assert (run / "questions.jsonl").read_bytes() == b""


def test_post_drops_orphaned_reply_when_content_changes_during_append(
    tmp_path: Path,
) -> None:
    """Do not promote an accepted raced reply after its parent disappears."""
    root = tmp_path / "runs"
    run = root / RUN_ID
    run.mkdir(parents=True)
    content = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    content_path = run / "content.json"
    content_path.write_text(json.dumps(content), encoding="utf-8")
    (run / "index.html").write_text(
        render_content(content),
        encoding="utf-8",
    )
    queue_path = run / "questions.jsonl"
    queue_path.write_bytes(b"")

    results: list[tuple[int, bytes]] = []
    with _running_server(root) as server:
        server.queue_lock.acquire()
        worker = threading.Thread(target=lambda: results.append(_request(server)))
        worker.start()
        try:
            _wait_until_handler_reaches(
                {"with self.server.queue_lock:", "with lock:"}
            )
            queue_path.unlink()
            os.mkfifo(queue_path)
        finally:
            server.queue_lock.release()
        _wait_until_handler_reaches({"os.open(queue_path"})
        item = content["updates"][1]["lanes"][1]["items"][0]
        item["questions"] = []
        replacement = run / "replacement.json"
        replacement.write_text(json.dumps(content), encoding="utf-8")
        replacement.replace(content_path)
        with queue_path.open("rb") as queue:
            queued = queue.readline()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert results[0][0] == 202
    record = json.loads(queued)
    assert record["content_generation"]
    queue_path.unlink()
    queue_path.write_bytes(queued)
    page = read_served_page(run)
    assert page and b"Racing reply" not in page
    assert count_unanswered_questions(run) == 0
