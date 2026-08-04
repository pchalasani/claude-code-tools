"""Loopback-only multi-run HTTP daemon."""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from visual_brief import __version__
from visual_brief.server.dashboard import render_dashboard
from visual_brief.server.counting import reply_target_error
from visual_brief.server.queue import (
    MAX_QUEUE_RECORD_BYTES,
    append_record,
    build_question_record,
    build_signal_record,
)
from visual_brief.server.registry import discover_runs, resolve_run_path
from visual_brief.server.responses import JsonResponder
from visual_brief.server.routes import Route, route_request
from visual_brief.server.served_page import (
    read_file_generation,
    read_served_document,
    read_served_generation,
    read_served_page,
)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = MAX_QUEUE_RECORD_BYTES
CONTENT_CHANGED = "Content changed while validating reply"

class VisualBriefServer(ThreadingHTTPServer):
    """HTTP server carrying the shared runs root."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runs_root: Path) -> None:
        """Initialize a loopback-only server at an address and runs root."""
        host, port = address
        if host != HOST:
            raise ValueError(f"visual-brief must bind to {HOST}")
        try:
            self.runs_root = runs_root.expanduser().resolve()
            self.runs_root.mkdir(parents=True, exist_ok=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(
                f"could not prepare runs root {runs_root}: {error}"
            ) from error
        self.queue_lock = threading.RLock()
        super().__init__((host, port), VisualBriefHandler)

class VisualBriefHandler(JsonResponder):
    """Serve all registered briefs and accept inert questions."""

    server: VisualBriefServer

    def do_GET(self) -> None:
        """Handle readable endpoints."""
        self._dispatch(send_body=True)

    def do_HEAD(self) -> None:
        """Handle HEAD for readable endpoints."""
        self._dispatch(send_body=False)

    def do_POST(self) -> None:
        """Handle the run-scoped question and signal endpoints."""
        route = self._route()
        if route.endpoint not in {"ask", "signal"} or route.run_id is None:
            self._json_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        run_dir = self._existing_run(route.run_id)
        if run_dir is None:
            return
        if route.endpoint == "ask":
            self._record_question(run_dir)
        else:
            self._record_signal(run_dir)

    def _dispatch(self, *, send_body: bool) -> None:
        route = self._route()
        if route.endpoint == "health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "service": "visual-brief",
                    "version": __version__,
                    "runs_root_id": runs_root_id(self.server.runs_root),
                },
                send_body=send_body,
            )
            return
        if route.endpoint == "dashboard":
            html = render_dashboard(
                discover_runs(self.server.runs_root),
                self.server.server_address[1],
            ).encode("utf-8")
            self._send(
                HTTPStatus.OK,
                html,
                "text/html; charset=utf-8",
                send_body=send_body,
            )
            return
        readable = {"run", "version", "render_version", "document"}
        if route.run_id is None or route.endpoint not in readable:
            self._json_error(
                HTTPStatus.NOT_FOUND,
                "Not found",
                send_body=send_body,
            )
            return
        run_dir = self._existing_run(route.run_id, send_body=send_body)
        if run_dir is None:
            return
        if route.endpoint == "run":
            self._serve_file(run_dir, send_body=send_body)
        elif route.endpoint == "version":
            self._serve_version(run_dir, send_body=send_body)
        elif route.endpoint == "document":
            self._serve_document(run_dir, send_body=send_body)
        else:
            self._serve_render_version(run_dir, send_body=send_body)

    def _route(self) -> Route:
        """Parse the current request."""
        path = urlsplit(self.path).path
        return route_request(self.headers.get("Host"), path)

    def _existing_run(
        self,
        run_id: str,
        *,
        send_body: bool = True,
    ) -> Path | None:
        """Resolve a run and send 404 when it does not exist."""
        try:
            run_dir = resolve_run_path(self.server.runs_root, run_id)
        except ValueError:
            self._json_error(
                HTTPStatus.NOT_FOUND,
                "Unknown run",
                send_body=send_body,
            )
            return None
        if not run_dir.is_dir():
            self._json_error(
                HTTPStatus.NOT_FOUND,
                "Unknown run",
                send_body=send_body,
            )
            return None
        return run_dir

    def _serve_file(self, run_dir: Path, *, send_body: bool) -> None:
        """Serve the rendered run page."""
        body = read_served_page(run_dir)
        if body is None:
            self._json_error(
                HTTPStatus.NOT_FOUND,
                "Rendered page is unavailable",
                send_body=send_body,
            )
            return
        self._send(
            HTTPStatus.OK,
            body,
            "text/html; charset=utf-8",
            send_body=send_body,
        )

    def _serve_document(self, run_dir: Path, *, send_body: bool) -> None:
        """Serve a run's document, generation and bundle stamp together.

        All three come out of one read of the page ``/`` would serve right
        now, so an open page can never patch a document that belongs to a
        generation the daemon is not serving.
        """
        payload = read_served_document(run_dir)
        if payload is None:
            self._json_error(
                HTTPStatus.NOT_FOUND,
                "Rendered page is unavailable",
                send_body=send_body,
            )
            return
        self._send_json(HTTPStatus.OK, payload, send_body=send_body)

    def _serve_version(self, run_dir: Path, *, send_body: bool) -> None:
        """Serve the SHA-256 hash of a run's source content."""
        self._serve_file_hash(
            run_dir,
            "content.json",
            "Content is unavailable",
            send_body=send_body,
        )

    def _serve_render_version(self, run_dir: Path, *, send_body: bool) -> None:
        """Serve the SHA-256 hash of a run's rendered page."""
        self._serve_file_hash(
            run_dir,
            None,
            "Rendered page is unavailable",
            send_body=send_body,
        )

    def _serve_file_hash(
        self,
        run_dir: Path,
        name: str | None,
        unavailable: str,
        *,
        send_body: bool,
    ) -> None:
        """Serve a contained run file's SHA-256 hash."""
        version = (
            read_served_generation(run_dir)
            if name is None
            else read_file_generation(run_dir, name)
        )
        if version is None:
            self._json_error(
                HTTPStatus.NOT_FOUND,
                unavailable,
                send_body=send_body,
            )
            return
        self._send(
            HTTPStatus.OK,
            version,
            "text/plain; charset=ascii",
            send_body=send_body,
        )

    def _record_question(self, run_dir: Path) -> None:
        """Validate and append one question JSON record."""
        data = self._read_json_object()
        if data is None:
            return
        try:
            record = build_question_record(data)
        except ValueError as error:
            self._json_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        generation = read_file_generation(run_dir, "content.json")
        if record["parent_id"] is not None and generation is not None:
            record["content_generation"] = generation.decode("ascii")
        target_error = reply_target_error(
            run_dir,
            record["parent_id"],
            record["anchor_id"] or "",
        )
        if target_error is not None:
            self._json_error(HTTPStatus.CONFLICT, target_error)
            return
        if record["parent_id"] is not None:
            with self.server.queue_lock:
                if read_file_generation(run_dir, "content.json") != generation:
                    self._json_error(HTTPStatus.CONFLICT, CONTENT_CHANGED)
                    return
                self._append_queue_record(run_dir, record)
            return
        self._append_queue_record(run_dir, record)

    def _record_signal(self, run_dir: Path) -> None:
        """Validate and append one agent-authored suggested reply."""
        data = self._read_json_object()
        if data is None:
            return
        try:
            record = build_signal_record(data)
        except ValueError as error:
            self._json_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        self._append_queue_record(run_dir, record)

    def _append_queue_record(
        self,
        run_dir: Path,
        record: dict[str, str | None],
    ) -> None:
        """Append one JSON record to a run's reverse-channel queue."""
        try:
            append_record(run_dir, record, self.server.queue_lock)
        except OSError:
            self._json_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Could not append to question queue",
            )
            return
        # The queued timestamp is reported back because it is half of the
        # identity the page recognizes its own message by once the daemon has
        # folded it into the document: those words, stamped at that instant.
        queued = {"status": "queued"}
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            queued["timestamp"] = timestamp
        self._send_json(HTTPStatus.ACCEPTED, queued)

    def _read_json_object(self) -> dict[str, Any] | None:
        """Read a bounded JSON object from the request body."""
        if self.headers.get_content_type() != "application/json":
            self._json_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type must be application/json",
            )
            return None
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if not 1 <= length <= MAX_BODY_BYTES:
            self._json_error(
                HTTPStatus.BAD_REQUEST,
                f"JSON body must be 1 to {MAX_BODY_BYTES} bytes",
            )
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_error(HTTPStatus.BAD_REQUEST, "Body must be valid JSON")
            return None
        if not isinstance(data, dict):
            self._json_error(
                HTTPStatus.BAD_REQUEST,
                "JSON body must be an object",
            )
            return None
        return data

def runs_root_id(runs_root: Path) -> str:
    """Return a stable identity for a normalized runs root."""
    normalized = str(runs_root.expanduser().resolve()).encode(
        sys.getfilesystemencoding(),
        errors="surrogateescape",
    )
    return hashlib.sha256(normalized).hexdigest()

def create_server(runs_root: Path, port: int = DEFAULT_PORT) -> VisualBriefServer:
    """Create a daemon bound to loopback.

    Args:
        runs_root: Directory containing all visual brief runs.
        port: TCP port, or zero for an ephemeral port.

    Returns:
        The initialized server.
    """
    if not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    return VisualBriefServer((HOST, port), runs_root)

def serve(runs_root: Path, port: int = DEFAULT_PORT) -> None:
    """Serve briefs until interrupted."""
    server = create_server(runs_root, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
