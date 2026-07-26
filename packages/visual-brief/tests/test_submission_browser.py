"""Real-browser regressions for reverse-channel form submission."""

from __future__ import annotations

import threading
import time
from typing import Iterator

import pytest

from browser_support import Browser, browser_session


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
        browser.run("click", ".ask-button")
        browser.run(
            "fill",
            ".question-box:not(.reply-box) textarea",
            "Send this only once",
        )
        browser.run(
            "dblclick",
            ".question-box:not(.reply-box) .submit",
        )
        deadline = time.monotonic() + 2
        while browser.server.post_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)

        assert browser.server.post_count == 1
        assert browser.evaluate(
            "document.querySelector("
            "'.question-box:not(.reply-box) .submit'"
            ").disabled"
        )
    finally:
        browser.server.post_gate.set()

    browser.run("wait", "300")
    assert browser.server.posts == [
        (
            "/ask",
            {
                "anchor_id": (
                    "current-update/what-changed/differential-reader-check"
                ),
                "text": "Send this only once",
            },
        )
    ]
    assert browser.evaluate(
        """
        [
          document.querySelectorAll("p.pending").length,
          document.querySelector(
            ".question-box:not(.reply-box) .submit"
          ).disabled,
        ]
        """
    ) == [1, False]
