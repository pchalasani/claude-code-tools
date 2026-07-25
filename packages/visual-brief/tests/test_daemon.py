"""Integration tests for the loopback visual brief daemon."""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
from pathlib import Path
from typing import Iterator

import pytest

from visual_brief.server.daemon import HOST, VisualBriefServer, create_server
from visual_brief.server.registry import count_unanswered_questions


@pytest.fixture
def live_server(
    tmp_path: Path,
) -> Iterator[tuple[VisualBriefServer, Path]]:
    """Run a real daemon on an ephemeral loopback port."""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "demo-run"
    run_dir.mkdir(parents=True)
    (run_dir / "index.html").write_text(
        "<!doctype html><title>demo</title>",
        encoding="utf-8",
    )
    (run_dir / "content.json").write_text(
        '{"title":"content"}\n',
        encoding="utf-8",
    )
    server = create_server(runs_root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, run_dir
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(
    server: VisualBriefServer,
    method: str,
    path: str,
    *,
    host: str = "localhost",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Make one HTTP request to a live daemon."""
    port = server.server_address[1]
    connection = http.client.HTTPConnection(HOST, port, timeout=2)
    request_headers = {"Host": host}
    if headers:
        request_headers.update(headers)
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    connection.close()
    return status, payload


def test_daemon_binds_only_to_loopback(tmp_path: Path) -> None:
    """The public server factory always binds to 127.0.0.1."""
    server = create_server(tmp_path / "runs", 0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
    with pytest.raises(ValueError, match="must bind"):
        VisualBriefServer(("0.0.0.0", 0), tmp_path / "other")


def test_health_responds(live_server: tuple[VisualBriefServer, Path]) -> None:
    """The health endpoint identifies the service."""
    server, run_dir = live_server
    status, body = request(server, "GET", "/health")
    assert status == 200
    health = json.loads(body)
    assert health["service"] == "visual-brief"
    assert health["runs_root_id"]
    assert health["runs_root_id"] != str(run_dir.parent)


def test_url_forms_serve_identical_run(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """Subdomain and path fallback forms return the same document."""
    server, _ = live_server
    first = request(server, "GET", "/", host="demo-run.localhost")
    second = request(server, "GET", "/r/demo-run/")
    assert first == second
    assert first[0] == 200


def test_bare_host_serves_dashboard(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """Bare localhost serves the run dashboard."""
    server, _ = live_server
    status, body = request(server, "GET", "/")
    assert status == 200
    assert b"demo-run" in body


def test_version_hashes_content_json(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """Run versions derive from source content, not rendered HTML."""
    server, run_dir = live_server
    content = (run_dir / "content.json").read_bytes()
    status, body = request(server, "GET", "/r/demo-run/version")
    assert status == 200
    assert body.decode("ascii") == hashlib.sha256(content).hexdigest()


def test_render_version_changes_only_with_rendered_page(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """The browser-facing version tracks the atomically rendered artifact."""
    server, run_dir = live_server
    path = "/r/demo-run/render-version"
    status, original = request(server, "GET", path)
    assert status == 200

    (run_dir / "content.json").write_text('{"title":"edited"}', encoding="utf-8")
    assert request(server, "GET", path) == (200, original)

    rendered = b"<!doctype html><title>new render</title>"
    (run_dir / "index.html").write_bytes(rendered)
    status, changed = request(server, "GET", path)
    assert status == 200
    assert changed.decode("ascii") == hashlib.sha256(rendered).hexdigest()
    assert changed != original


def test_unknown_run_is_404(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """Unknown runs return 404 rather than failing the handler."""
    server, _ = live_server
    status, _ = request(server, "GET", "/r/missing-run/")
    assert status == 404


@pytest.mark.parametrize(
    ("path", "filename"),
    [
        ("/r/demo-run/", "index.html"),
        ("/r/demo-run/version", "content.json"),
        ("/r/demo-run/render-version", "index.html"),
    ],
)
def test_self_referential_run_file_is_unavailable(
    live_server: tuple[VisualBriefServer, Path],
    path: str,
    filename: str,
) -> None:
    """A run-file symlink loop returns unavailable instead of disconnecting."""
    server, run_dir = live_server
    run_file = run_dir / filename
    run_file.unlink()
    run_file.symlink_to(filename)

    status, body = request(server, "GET", path)

    assert status == 404
    assert b"unavailable" in body


def test_post_ask_appends_one_inert_line(
    live_server: tuple[VisualBriefServer, Path],
    tmp_path: Path,
) -> None:
    """Question text is stored as data exactly once and never executed."""
    server, run_dir = live_server
    marker = tmp_path / "must-not-exist"
    question = f"$(touch {marker})"
    sentinel = '{"type":"signal","anchor_id":"old","signal":"skip"}\n'
    (run_dir / "questions.jsonl").write_text(sentinel, encoding="utf-8")
    payload = json.dumps(
        {"anchor_id": "update/lane/item", "text": question}
    ).encode("utf-8")
    status, _ = request(
        server,
        "POST",
        "/r/demo-run/ask",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    assert status == 202
    lines = (run_dir / "questions.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 2
    assert lines[0] == sentinel.rstrip("\n")
    assert json.loads(lines[1])["text"] == question
    assert not marker.exists()


def test_post_ask_records_optional_parent_id(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """Distinguish a follow-up from a new queued thread."""
    server, run_dir = live_server
    example = Path(__file__).parents[1] / "example.json"
    (run_dir / "content.json").write_bytes(example.read_bytes())
    payload = {
        "anchor_id": "current-update/why-it-matters/repair-loop-routing",
        "text": "Follow-up?",
        "parent_id": "q-malformed-unsupported",
    }

    status, _ = request(
        server,
        "POST",
        "/r/demo-run/ask",
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    assert status == 202
    record = json.loads(
        (run_dir / "questions.jsonl").read_text(encoding="utf-8")
    )
    assert record["anchor_id"] == payload["anchor_id"]
    assert record["text"] == payload["text"]
    assert record["parent_id"] == payload["parent_id"]
    assert record["type"] == "question"
    assert "timestamp" in record


def test_post_new_question_records_null_parent_id(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """Keep the added queue field even when a new thread has no parent."""
    server, run_dir = live_server
    payload = {"anchor_id": "update/lane", "text": "New thread?"}

    status, _ = request(
        server,
        "POST",
        "/r/demo-run/ask",
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    assert status == 202
    record = json.loads(
        (run_dir / "questions.jsonl").read_text(encoding="utf-8")
    )
    assert "parent_id" in record
    assert record["parent_id"] is None


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/r/demo-run/ask",
            {"anchor_id": "update/lane/item", "text": "Question?"},
        ),
        (
            "/r/demo-run/signal",
            {"anchor_id": "update/lane/item", "signal": "go-deeper"},
        ),
    ],
)
def test_self_referential_queue_is_unavailable(
    live_server: tuple[VisualBriefServer, Path],
    path: str,
    payload: dict[str, str],
) -> None:
    """A queue symlink loop returns 500 instead of disconnecting."""
    server, run_dir = live_server
    (run_dir / "questions.jsonl").symlink_to("questions.jsonl")

    status, body = request(
        server,
        "POST",
        path,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    assert status == 500
    assert json.loads(body) == {
        "error": "Could not append to question queue"
    }


def test_maximum_accepted_question_is_visible_to_registry(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """A large accepted UTF-8 question remains visible as unanswered."""
    server, run_dir = live_server
    question = "😀" * 16_363
    payload = json.dumps(
        {"anchor_id": "update/lane/item", "text": question},
        ensure_ascii=False,
    ).encode("utf-8")
    assert len(payload) <= 64 * 1024

    status, _ = request(
        server,
        "POST",
        "/r/demo-run/ask",
        body=payload,
        headers={"Content-Type": "application/json"},
    )

    assert status == 202
    assert count_unanswered_questions(run_dir) == 1


@pytest.mark.parametrize(
    ("host", "path"),
    [
        ("demo-run.localhost", "/signal"),
        ("localhost", "/r/demo-run/signal"),
    ],
)
def test_post_signal_accepts_fixed_vocabulary_in_both_forms(
    live_server: tuple[VisualBriefServer, Path],
    host: str,
    path: str,
) -> None:
    """Feedback buttons queue inert signal records in either URL form."""
    server, run_dir = live_server
    payload = json.dumps(
        {"anchor_id": "update/lane/item", "signal": "go-deeper"}
    ).encode("utf-8")

    status, _ = request(
        server,
        "POST",
        path,
        host=host,
        body=payload,
        headers={"Content-Type": "application/json"},
    )

    assert status == 202
    record = json.loads(
        (run_dir / "questions.jsonl").read_text(encoding="utf-8")
    )
    assert record["type"] == "signal"
    assert record["signal"] == "go-deeper"


def test_post_signal_rejects_unknown_value(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """Only feedback values rendered by the client are accepted."""
    server, run_dir = live_server
    payload = json.dumps(
        {"anchor_id": "update/lane/item", "signal": "execute-this"}
    ).encode("utf-8")

    status, _ = request(
        server,
        "POST",
        "/r/demo-run/signal",
        body=payload,
        headers={"Content-Type": "application/json"},
    )

    assert status == 400
    assert not (run_dir / "questions.jsonl").exists()


def test_dashboard_survives_boundary_timestamp(
    live_server: tuple[VisualBriefServer, Path],
) -> None:
    """A timestamp that underflows in UTC cannot crash the dashboard."""
    server, run_dir = live_server
    metadata = {
        "run_id": "demo-run",
        "label": "Boundary run",
        "updated_at": "0001-01-01T00:00:00+23:59",
    }
    (run_dir / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")

    status, body = request(server, "GET", "/")

    assert status == 200
    assert b"Boundary run" in body
