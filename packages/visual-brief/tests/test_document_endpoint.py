"""What the daemon tells an open page about the document it is serving.

An open page no longer replaces itself when the agent publishes: it fetches
the new document as data and patches it in. That only holds together if what
this endpoint answers and what ``/`` serves are the same thing, always — one
read of one page, with the generation, bundle, physical run identity and
document taken out of it. These pin that, including the case a second source
of truth would get wrong: a queued follow-up the daemon merges in memory and
never writes to disk.
"""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from page_document import embedded_document
from visual_brief.render import render_content
from visual_brief.server.daemon import HOST, VisualBriefServer, create_server

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"
RUN_ID = "document-run"
ANCHOR_ID = "current-update/why-it-matters/repair-loop-routing"
THREAD_ID = "q-malformed-unsupported"


def _example() -> dict[str, Any]:
    """Load the example document.

    Returns:
        The example brief.
    """
    value = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _make_run(root: Path) -> Path:
    """Create one rendered run the daemon can serve.

    Args:
        root: Runs root to create it under.

    Returns:
        The run directory.
    """
    run = root / RUN_ID
    run.mkdir(parents=True)
    content = _example()
    (run / "content.json").write_text(json.dumps(content), encoding="utf-8")
    (run / "index.html").write_text(render_content(content), encoding="utf-8")
    (run / "questions.jsonl").write_bytes(b"")
    (run / "meta.json").write_text(
        json.dumps({"instance_id": "a" * 32}),
        encoding="utf-8",
    )
    return run


@contextmanager
def _running_server(root: Path) -> Iterator[VisualBriefServer]:
    """Run a real daemon on an ephemeral loopback port.

    Args:
        root: Runs root to serve.

    Yields:
        The running server.
    """
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
    """Send one request to the running daemon.

    Args:
        server: The running daemon.
        method: HTTP method.
        path: Request path.
        payload: JSON body, when there is one.

    Returns:
        The status and the response body.
    """
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


@pytest.fixture
def served(tmp_path: Path) -> Iterator[VisualBriefServer]:
    """Serve one rendered run from a real daemon.

    Yields:
        The running server.
    """
    root = tmp_path / "runs"
    _make_run(root)
    with _running_server(root) as server:
        yield server


def test_the_document_and_the_page_are_one_read_of_one_page(
    served: VisualBriefServer,
) -> None:
    """Answer the same generation and the same document as ``/`` does.

    The client compares this payload's generation with what
    ``/render-version`` said, so under one state the two have to be the same
    value; and it shows the document to a human who could open the page
    beside it, so it has to be the document that page carries.
    """
    status, payload = _request(served, "GET", f"/r/{RUN_ID}/document")
    _, generation = _request(served, "GET", f"/r/{RUN_ID}/render-version")
    _, page = _request(served, "GET", f"/r/{RUN_ID}/")

    assert status == 200
    answer = json.loads(payload)
    assert answer["generation"] == generation.decode("ascii")
    assert answer["document"] == embedded_document(page)
    instance_meta = (
        f'<meta name="visual-brief-run-instance" '
        f'content="{answer["instance"]}">'
    ).encode()
    assert instance_meta in page


def test_the_document_carries_the_bundle_the_page_is_running(
    served: VisualBriefServer,
) -> None:
    """Say which front-end built the page this document came out of.

    A page that patched a document into a different bundle's code would go on
    running that code for good, so the stamp travels with the document.
    """
    _, payload = _request(served, "GET", f"/r/{RUN_ID}/document")
    _, page = _request(served, "GET", f"/r/{RUN_ID}/")

    stamp = json.loads(payload)["assets"]
    assert (
        f'<meta name="visual-brief-assets-version" content="{stamp}">'
        in page.decode("utf-8")
    )


def test_the_document_shows_a_follow_up_that_is_only_in_the_queue(
    served: VisualBriefServer,
) -> None:
    """Answer from the served page, not from ``index.html`` on disk.

    A queued follow-up is merged and re-rendered on every request and is never
    written to the file. A document written at publish time would omit it, and
    the open page would then be showing something the daemon is not serving.
    """
    question = "Does the queue reach the document endpoint?"
    status, _ = _request(
        served,
        "POST",
        f"/r/{RUN_ID}/ask",
        {"anchor_id": ANCHOR_ID, "text": question, "parent_id": THREAD_ID},
    )
    assert status == 202

    _, payload = _request(served, "GET", f"/r/{RUN_ID}/document")
    _, page = _request(served, "GET", f"/r/{RUN_ID}/")

    answer = json.loads(payload)
    assert question in json.dumps(answer["document"])
    assert answer["document"] == embedded_document(page)
    assert question not in (
        Path(served.runs_root / RUN_ID / "index.html").read_text(
            encoding="utf-8"
        )
    )


def test_both_address_forms_answer_the_same_document(
    served: VisualBriefServer,
) -> None:
    """Reach the endpoint the two ways every run endpoint is reachable."""
    _, by_path = _request(served, "GET", f"/r/{RUN_ID}/document")
    connection = http.client.HTTPConnection(
        HOST,
        served.server_address[1],
        timeout=2,
    )
    connection.request("GET", "/document", headers={"Host": f"{RUN_ID}.localhost"})
    response = connection.getresponse()
    by_host = response.read()
    status = response.status
    connection.close()

    assert status == 200
    assert json.loads(by_host) == json.loads(by_path)


def test_an_unknown_run_has_no_document(served: VisualBriefServer) -> None:
    """Answer 404 for a run that is not there, as its neighbours do."""
    status, body = _request(served, "GET", "/r/no-such-run/document")

    assert status == 404
    assert json.loads(body) == {"error": "Unknown run"}


def test_a_run_with_no_page_has_no_document(tmp_path: Path) -> None:
    """Answer 404 in the shape the other page endpoints answer it in."""
    root = tmp_path / "runs"
    (root / "empty-run").mkdir(parents=True)
    with _running_server(root) as server:
        status, body = _request(server, "GET", "/r/empty-run/document")
        _, page_body = _request(server, "GET", "/r/empty-run/render-version")

    assert status == 404
    assert json.loads(body) == {"error": "Rendered page is unavailable"}
    assert json.loads(page_body) == json.loads(body)


def test_head_is_supported_like_its_neighbours(
    served: VisualBriefServer,
) -> None:
    """Answer HEAD with the headers and none of the body."""
    connection = http.client.HTTPConnection(
        HOST,
        served.server_address[1],
        timeout=2,
    )
    connection.request("HEAD", f"/r/{RUN_ID}/document")
    response = connection.getresponse()
    body = response.read()
    status = response.status
    content_type = response.getheader("Content-Type")
    length = response.getheader("Content-Length")
    connection.close()

    assert status == 200
    assert body == b""
    assert content_type == "application/json; charset=utf-8"
    assert length is not None and int(length) > 0
