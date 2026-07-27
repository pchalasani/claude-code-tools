"""Real-browser support for visual brief interaction tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from cdp import emulated_media
from visual_brief.render import render_content
from visual_brief.server.served_page import page_generation

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"

# Rows of the served example page that the interaction suites navigate by.
FIRST_ITEM = "current-update/what-changed/differential-reader-check"
SECOND_ITEM = "current-update/what-changed/four-cold-review-defects"
AWAITING_THREAD = (
    "current-update/why-it-matters/repair-loop-routing#q-malformed-unsupported"
)


def _browser_data() -> dict[str, Any]:
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


class _Handler(BaseHTTPRequestHandler):
    """Serve the current rendered page and reload version."""

    server: "_TestServer"

    def do_GET(self) -> None:
        """Serve one test resource."""
        if self.path.endswith("/render-version"):
            body = page_generation(self.server.html.encode())
            content_type = "text/plain"
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
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Keep browser-test requests out of pytest output."""


class _TestServer(ThreadingHTTPServer):
    """HTTP server with mutable rendered content."""

    html: str
    replacement_html: str | None = None
    posts: list[tuple[str, dict[str, Any]]]
    post_count: int
    post_gate: threading.Event | None
    refuse: bool = False


@dataclass
class Browser:
    """Small real-browser driver around the installed agent-browser CLI."""

    executable: str
    session: str
    server: _TestServer
    data: dict[str, Any]

    @property
    def url(self) -> str:
        """Return the local page URL."""
        return f"http://127.0.0.1:{self.server.server_port}/"

    def run(self, *arguments: str, input_text: str | None = None) -> str:
        """Run one browser command and return stdout."""
        completed = subprocess.run(
            [self.executable, "--session", self.session, *arguments],
            input=input_text,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        return completed.stdout

    def evaluate(self, script: str) -> Any:
        """Evaluate JavaScript in the page and return its result."""
        output = self.run("--json", "eval", "--stdin", input_text=script)
        result = json.loads(output)
        assert result["success"], result
        return result["data"]["result"]

    def press(self, key: str) -> None:
        """Press a real browser key."""
        self.run("press", key)

    def click_row(self, row_id: str) -> None:
        """Scroll one row into view and click its head, the way a human does.

        Args:
            row_id: Identifier of the row to click.
        """
        selector = f'[data-row-id="{row_id}"] .row-toggle'
        self.run("scrollintoview", selector)
        self.run("click", selector)

    def compose_at(self, row_id: str) -> None:
        """Open the chat box at one row through its own affordance.

        Args:
            row_id: Identifier of the row to write against.
        """
        selector = f'[data-row-id="{row_id}"] > .row-head .chat-button'
        self.run("scrollintoview", selector)
        self.run("click", selector)

    def send(self, text: str) -> None:
        """Write and submit one message in the open composer.

        Args:
            text: What to write.
        """
        self.run("fill", ".composer textarea", text)
        self.run("click", ".composer .submit")

    def cursor_row(self) -> str:
        """Return the row the page paints as the cursor.

        Returns:
            The cursor's row identifier.
        """
        marked = self.evaluate(
            """
            [...document.querySelectorAll('[data-cursor="true"]')].map(
              (row) => row.dataset.rowId,
            )
            """
        )
        assert len(marked) == 1, marked
        return str(marked[0])

    @contextmanager
    def reduced_motion(self) -> Iterator[None]:
        """Turn Chrome's real motion preference on for the body of the block.

        The browser CLI's own ``set media reduced-motion`` is a silent no-op —
        after it, the page still matches ``no-preference`` — so the preference
        is set over the DevTools protocol the same browser exposes. Chrome
        forgets it when that connection drops, which is why the page has to be
        read inside the block.

        Yields:
            Nothing; the page matches ``(prefers-reduced-motion: reduce)``
            until the block ends.
        """
        cdp_url = self.run("get", "cdp-url").strip()
        with emulated_media(
            cdp_url,
            self.url,
            {"prefers-reduced-motion": "reduce"},
        ):
            # One paint at the new preference before anything is measured.
            self.run("wait", "150")
            yield

    def publish(self) -> None:
        """Publish current data under a new self-reload version."""
        self.server.html = render_content(self.data)

    def wait_for_title(self, title: str) -> None:
        """Wait for self-reload to display one document title."""
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            actual = self.evaluate("document.title")
            if actual == title:
                return
            time.sleep(0.25)
        pytest.fail(f"title did not become {title!r}; got {actual!r}")


STARTUP_ATTEMPTS = 3


def _start_browser(driver: Browser) -> None:
    """Open the served page, retrying only a browser that failed to start.

    Chrome occasionally fails to come up on a loaded machine. That is worth
    one more attempt; a failing assertion never is, so nothing past startup is
    retried, and a browser that will not start still fails the suite loudly.

    Args:
        driver: The browser driver to start.
    """
    failure = ""
    for attempt in range(1, STARTUP_ATTEMPTS + 1):
        try:
            driver.run("open", driver.url)
            driver.run("wait", "300")
            return
        except (AssertionError, subprocess.TimeoutExpired) as error:
            failure = str(error)
            try:
                driver.run("close")
            except (AssertionError, subprocess.TimeoutExpired):
                pass
            time.sleep(0.5 * attempt)
    pytest.fail(
        f"the browser did not start after {STARTUP_ATTEMPTS} attempts: {failure}"
    )


@contextmanager
def browser_session() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    executable = shutil.which("agent-browser")
    if executable is None:
        pytest.fail(
            "agent-browser is required by the visual-brief verification suite; "
            "install it before running the documented pytest command"
        )
    data = _browser_data()
    server = _TestServer(("127.0.0.1", 0), _Handler)
    server.html = render_content(data)
    server.posts = []
    server.post_count = 0
    server.post_gate = None
    server.refuse = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    driver = Browser(executable, f"visual-brief-{uuid.uuid4().hex}", server, data)
    try:
        _start_browser(driver)
        yield driver
    finally:
        try:
            driver.run("close")
        except (AssertionError, subprocess.TimeoutExpired):
            pass
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
