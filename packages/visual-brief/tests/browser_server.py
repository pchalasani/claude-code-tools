"""The fake daemon the real-browser suites read their page from.

One rendered document served over a real socket, with the few switches those
suites need: what ``render-version`` answers, whether a queue request is held
open or refused, and what the next request serves instead. It lives here so
the driver next door can be a driver and nothing else.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from visual_brief.render import render_content
from visual_brief.render.page import POLL_INTERVAL_MS
from visual_brief.server.served_page import page_generation, page_payload

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"

# The served page carries its own poll interval, so a suite that has to watch
# the page notice something does not have to wait five real seconds for it.
FAST_POLL_MS = 250

# Rows of the served example page that the interaction suites navigate by.
FIRST_ITEM = "current-update/what-changed/differential-reader-check"
SECOND_ITEM = "current-update/what-changed/four-cold-review-defects"
AWAITING_THREAD = (
    "current-update/why-it-matters/repair-loop-routing#q-malformed-unsupported"
)


def served_page(data: dict[str, Any]) -> str:
    """Render one page that checks itself often enough to be watched.

    Args:
        data: The document to render.

    Returns:
        The rendered page, asking to be checked every few hundred
        milliseconds instead of every few seconds.
    """
    return render_content(data).replace(
        f'content="{POLL_INTERVAL_MS}"',
        f'content="{FAST_POLL_MS}"',
        1,
    )


def browser_data() -> dict[str, Any]:
    """Return example data with two awaiting threads."""
    data = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    threads = [
        data["updates"][0]["lanes"][0]["items"][0]["questions"][0],
        data["updates"][1]["lanes"][1]["items"][0]["questions"][0],
    ]
    for index, thread in enumerate(threads):
        thread["turns"].append(
            {
                "author": "human",
                "text": f"Awaiting follow-up {index}",
                "at": f"2026-07-25T20:0{index}:00Z",
            }
        )
    return data


class PageHandler(BaseHTTPRequestHandler):
    """Serve the current rendered page and reload version."""

    server: "PageServer"

    def do_GET(self) -> None:
        """Serve one test resource."""
        if self.path.endswith("/render-version"):
            if self.server.version_status != 200:
                self.send_response(self.server.version_status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            spoken = self.server.version_body
            body = (
                spoken.encode()
                if spoken is not None
                else page_generation(self.server.html.encode())
            )
            content_type = "text/plain"
        elif self.path.endswith("/document"):
            # The real daemon derives all three fields from one read of the
            # page it would serve, and so does this: an open page must never
            # be handed a document that belongs to something else.
            if self.server.document_status != 200:
                self.send_response(self.server.document_status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload = page_payload(self.server.html.encode())
            if payload is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps(payload).encode()
            content_type = "application/json"
        else:
            html = self.server.html
            if self.server.replacement_html is not None:
                self.server.html = self.server.replacement_html
                self.server.replacement_html = None
            body = html.encode()
            content_type = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        """Accept local form and signal requests made during interaction tests."""
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.posts.append((self.path, json.loads(body)))
        self.server.post_count += 1
        if self.server.post_gate is not None:
            self.server.post_gate.wait(timeout=5)
        if self.server.refuse:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # The real daemon reports the instant it stamped the queue line, and
        # the page recognizes its own message by those words at that instant.
        stamp = f"2026-07-25T21:00:{self.server.post_count:02d}Z"
        self.server.stamps.append(stamp)
        queued = json.dumps({"status": "queued", "timestamp": stamp}).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(queued)))
        self.end_headers()
        self.wfile.write(queued)

    def log_message(self, format: str, *args: object) -> None:
        """Keep browser-test requests out of pytest output."""


class PageServer(ThreadingHTTPServer):
    """HTTP server with mutable rendered content."""

    html: str
    replacement_html: str | None = None
    posts: list[tuple[str, dict[str, Any]]]
    stamps: list[str]
    post_count: int
    post_gate: threading.Event | None
    refuse: bool = False
    version_body: str | None = None
    version_status: int = 200
    document_status: int = 200


@contextmanager
def serving(data: dict[str, Any]) -> Iterator[PageServer]:
    """Serve one document on a local socket for the length of the block.

    Args:
        data: The document to render and serve.

    Yields:
        The running server, with every switch at its resting position.
    """
    server = PageServer(("127.0.0.1", 0), PageHandler)
    server.html = served_page(data)
    server.posts = []
    server.stamps = []
    server.post_count = 0
    server.post_gate = None
    server.refuse = False
    server.version_body = None
    server.version_status = 200
    server.document_status = 200
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
