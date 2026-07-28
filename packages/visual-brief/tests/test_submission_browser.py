"""Real-browser regressions for reverse-channel form submission.

A real double-click must send one question, not two, and the submit control
must say so by being disabled while the request is in flight.
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

import pytest

from browser_support import Browser, browser_session, landing_at

ITEM = "current-update/what-changed/differential-reader-check"
REFUSED = "Could not send. Is the local server running?"

# The send chord carries a modifier, and the installed agent-browser reports
# every chord as an empty key with no modifier set, so the chord itself has to
# be delivered to the box as a DOM key press. Everything around it — the plain
# Enter that must make a paragraph instead, and the painted result of sending —
# is driven with real keys and read off the page.
_SEND_CHORD = """
    (() => {
      const box = document.querySelector(".composer textarea");
      const apple = /mac|iphone|ipad|ipod/i.test(
        navigator.platform || navigator.userAgent,
      );
      const press = new KeyboardEvent("keydown", {
        key: "Enter",
        bubbles: true,
        cancelable: true,
        metaKey: apple,
        ctrlKey: !apple,
      });
      box.dispatchEvent(press);
      return press.defaultPrevented;
    })()
    """

_ENTER_SPY = """
    (() => {
      window.__enter = [];
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          window.__enter.push(event.defaultPrevented);
        }
      });
      return true;
    })()
    """


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


def test_plain_enter_stays_in_the_box_and_the_chord_sends(
    browser: Browser,
) -> None:
    """Leave Enter to the text box, and give sending its own chord.

    A question worth asking is often more than one line, so Enter has to keep
    belonging to the box. The real key press proves the page does not claim it;
    the chord then sends everything written, newline and all.
    """
    written = "First line\nsecond line"
    browser.compose_at(ITEM)
    browser.run("fill", ".composer textarea", written)
    browser.evaluate(_ENTER_SPY)

    # Enter leaving the box alone is proved by nothing happening, and nothing
    # is not something to wait for: this one moment is read after a settling
    # wait. Everything the send does paint is polled for instead.
    browser.press("Enter")
    browser.run("wait", "400")
    delivered = browser.evaluate("window.__enter")
    after_enter = browser.evaluate(landing_at(ITEM))
    unsent = list(browser.server.posts)

    consumed = browser.evaluate(_SEND_CHORD)
    landed = browser.read_until(
        landing_at(ITEM), lambda seen: seen["note"] is not None
    )

    assert delivered == [False]
    assert after_enter["composer"] is True, after_enter
    assert after_enter["typed"] == written, after_enter
    assert after_enter["notes"] == 0, after_enter
    assert unsent == []
    assert consumed is True
    assert browser.server.posts == [
        ("/ask", {"anchor_id": ITEM, "text": written})
    ]
    assert landed["composer"] is False, landed
    assert landed["open"] == "true", landed
    assert landed["notes"] == 1, landed
    assert "second line" in (landed["note"] or ""), landed


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
