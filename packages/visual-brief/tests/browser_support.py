"""Real-browser support for visual brief interaction tests.

The driver a suite drives: real keys, real clicks, and readings taken off the
painted page. The document it drives lives next door in ``browser_server``,
whose row identifiers are re-exported here so a suite has one import.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import pytest

from browser_server import (
    AWAITING_THREAD,
    FIRST_ITEM,
    SECOND_ITEM,
    PageServer,
    browser_data,
    served_page,
    serving,
)
from cdp import emulated_media, press_enter

__all__ = [
    "AWAITING_THREAD",
    "FIRST_ITEM",
    "SECOND_ITEM",
    "Browser",
    "browser_session",
    "landing_at",
]


def landing_at(row_id: str) -> str:
    """Return a script reading where a message sent from one row lands.

    Every field is guarded, so a page that has not painted the note yet says
    what it does show — whether the box is still open, what is written in it,
    whether the row folded, and what the status line reports — instead of
    throwing and taking the diagnosis with it.

    Args:
        row_id: Identifier of the row that was written against.

    Returns:
        JavaScript returning what a human sees of one send.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const box = document.querySelector(".composer");
      return {{
        composer: box !== null,
        open: row?.dataset.open ?? null,
        notes: document.querySelectorAll('[data-pending="true"]').length,
        note: row?.querySelector('[data-pending="true"]')?.textContent ?? null,
        typed: box?.querySelector("textarea")?.value ?? null,
        status: box?.querySelector(".status")?.textContent ?? null,
      }};
    }})()
    """


@dataclass
class Browser:
    """Small real-browser driver around the installed agent-browser CLI."""

    executable: str
    session: str
    server: PageServer
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

    def read_until(
        self,
        script: str,
        painted: Callable[[Any], bool],
        timeout: float = 5.0,
    ) -> Any:
        """Read the page over and over until it paints what a test waits for.

        Reading once after a fixed wait loses the race on a loaded machine,
        and the reading is handed back whether or not it ever became the
        awaited one, so the caller's own assertion reports what the page
        actually showed rather than failing inside the browser.

        Args:
            script: JavaScript returning what a human sees. It has to read
                defensively: an unpainted page must read as a value.
            painted: Decides whether one reading is the awaited one.
            timeout: Seconds to keep reading before handing back what is
                there.

        Returns:
            The first awaited reading, or the last one taken before the
            deadline passed.
        """
        deadline = time.monotonic() + timeout
        while True:
            seen = self.evaluate(script)
            if painted(seen) or time.monotonic() >= deadline:
                return seen
            time.sleep(0.1)

    def press(self, key: str) -> None:
        """Press a real browser key."""
        if key == "Enter":
            press_enter(self.run("get", "cdp-url").strip(), self.url)
            return
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
        opened = self.evaluate(
            f"""
            document.querySelector(
              '[data-row-id="{row_id}"] > .row-body > .composer',
            ) !== null
            """
        )
        if not opened:
            # Chrome attached over CDP can occasionally acknowledge a pointer
            # command without activating its target. HTMLElement.click() runs
            # the browser's activation behavior and the same handler.
            self.evaluate(
                f"document.querySelector({json.dumps(selector)})?.click(); true"
            )

    def send(self, text: str) -> None:
        """Write and submit one message in the open composer.

        Args:
            text: What to write.
        """
        self.run("fill", ".composer textarea", text)
        self.submit()

    def submit(self, times: int = 1) -> None:
        """Activate the open composer's submit control.

        Args:
            times: Number of immediate activation attempts.
        """
        command = "dblclick" if times == 2 else "click"
        self.run(command, ".composer .submit")
        activated = self.evaluate(
            """
            (() => {
              const box = document.querySelector(".composer");
              const button = box?.querySelector(".submit");
              const status = box?.querySelector(".status");
              return box === null
                || button?.disabled === true
                || (status?.textContent ?? "") !== "";
            })()
            """
        )
        if activated:
            return
        # Keep attached-CDP runs meaningful when the driver drops pointer
        # activation: HTMLElement.click() runs the browser's activation
        # behavior and the same submit handler as a physical click.
        for _ in range(times):
            self.evaluate(
                "document.querySelector('.composer .submit')?.click(); true"
            )

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
        """Serve the current data as the page the daemon would now serve."""
        self.server.html = served_page(self.data)

    def mark(self) -> str:
        """Write a mark into the page that only a navigation could remove.

        Anything on ``window`` belongs to one loaded document and nothing
        else: a reload, however brief, takes it. That is what makes it a
        proof — the page either kept living through the publish or it did not.

        Returns:
            The mark that was written.
        """
        token = uuid.uuid4().hex
        self.evaluate(f"window.__brief_mark = {json.dumps(token)}; true")
        return token

    def marked(self) -> str | None:
        """Read the mark the page is still carrying.

        Returns:
            The mark, or None when the page has been replaced since.
        """
        read = self.evaluate("window.__brief_mark ?? null")
        return None if read is None else str(read)

    def wait_for_row(self, row_id: str) -> None:
        """Wait for the page to take in a publish and paint one row.

        Publishing is enough on its own: the page notices the new generation,
        fetches the new document and patches it in. Asking the browser to
        navigate as well would throw away the very page under test.

        Args:
            row_id: Identifier of the row the new content must carry.
        """
        script = (
            "document.querySelector("
            f'\'[data-row-id="{row_id}"]\') !== null'
        )
        painted = self.read_until(script, lambda seen: seen is True, timeout=7)
        assert painted is True, f"the page never painted {row_id!r}"

    def wait_for_title(self, title: str) -> None:
        """Wait for a publish to reach the open page's own title."""
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
            if os.environ.get("AGENT_BROWSER_CDP"):
                driver.run("tab", "new", driver.url)
            else:
                driver.run("open", driver.url)
            driver.run("wait", "300")
            return
        except (AssertionError, subprocess.TimeoutExpired) as error:
            failure = str(error)
            try:
                _close_browser(driver)
            except (AssertionError, subprocess.TimeoutExpired):
                pass
            time.sleep(0.5 * attempt)
    pytest.fail(
        f"the browser did not start after {STARTUP_ATTEMPTS} attempts: {failure}"
    )


def _close_browser(driver: Browser) -> None:
    """Close only the tab created inside an externally managed Chrome."""
    if os.environ.get("AGENT_BROWSER_CDP"):
        driver.run("tab", "close")
    else:
        driver.run("close")


@contextmanager
def browser_session() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    executable = shutil.which("agent-browser")
    if executable is None:
        pytest.fail(
            "agent-browser is required by the visual-brief verification suite; "
            "install it before running the documented pytest command"
        )
    data = browser_data()
    with serving(data) as server:
        name = f"visual-brief-{uuid.uuid4().hex}"
        driver = Browser(executable, name, server, data)
        try:
            _start_browser(driver)
            yield driver
        finally:
            try:
                _close_browser(driver)
            except (AssertionError, subprocess.TimeoutExpired):
                pass
