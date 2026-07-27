"""Real-browser regressions for reverse-channel form submission.

A real double-click must send one question, not two, and the submit control
must say so by being disabled while the request is in flight.
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

import pytest

from browser_support import Browser, browser_session

ITEM = "current-update/what-changed/differential-reader-check"
REFUSED = "Could not send. Is the local server running?"


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_double_click_sends_one_question_while_request_is_in_flight(
    browser: Browser,
) -> None:
    """Keep a real double-click from creating duplicate queue requests."""
    browser.server.post_gate = threading.Event()
    try:
        browser.compose_at(ITEM)
        browser.run("fill", ".composer textarea", "Send this only once")
        browser.run("dblclick", ".composer .submit")
        deadline = time.monotonic() + 2
        while browser.server.post_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)

        assert browser.server.post_count == 1
        assert browser.evaluate(
            "document.querySelector('.composer .submit').disabled"
        )
    finally:
        browser.server.post_gate.set()

    browser.run("wait", "300")
    assert browser.server.posts == [
        ("/ask", {"anchor_id": ITEM, "text": "Send this only once"})
    ]
    assert browser.evaluate(
        """
        [
          document.querySelectorAll("p.pending").length,
          document.querySelector(".composer") === null,
          document.querySelector("p.pending").textContent.includes(
            "Send this only once",
          ),
        ]
        """
    ) == [1, True, True]


def test_escape_during_a_send_still_shows_the_question_landing(
    browser: Browser,
) -> None:
    """Show the sent note even when Escape folded its row mid-request."""
    browser.server.post_gate = threading.Event()
    try:
        browser.compose_at(ITEM)
        browser.run("fill", ".composer textarea", "Does this still confirm?")
        browser.run("click", ".composer .submit")
        deadline = time.monotonic() + 2
        while browser.server.post_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        browser.press("Escape")
        browser.run("wait", "200")
    finally:
        browser.server.post_gate.set()
    browser.run("wait", "600")

    assert browser.evaluate(
        f"""
        (() => {{
          const row = document.querySelector('[data-row-id="{ITEM}"]');
          const note = row.querySelector("p.pending");
          return [
            row.dataset.open,
            note === null ? "" : note.textContent.includes(
              "Does this still confirm?",
            ),
          ];
        }})()
        """
    ) == ["true", True]


def test_a_question_the_daemon_refuses_is_not_lost(browser: Browser) -> None:
    """Leave the human's text in place when the request does not land."""
    browser.server.refuse = True
    browser.compose_at(ITEM)
    browser.run("fill", ".composer textarea", "Does this survive a refusal?")
    browser.run("click", ".composer .submit")
    browser.run("wait", "600")

    assert browser.evaluate(
        """
        [
          document.querySelector(".composer textarea").value,
          document.querySelector(".composer .status").textContent,
          document.querySelectorAll("p.pending").length,
        ]
        """
    ) == ["Does this survive a refusal?", REFUSED, 0]
