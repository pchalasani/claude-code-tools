"""Real-browser regressions for reverse-channel form submission.

A real double-click must send one question, not two, and the submit control
must say so by being disabled while the request is in flight.
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

import pytest

from browser_support import SECOND_ITEM, Browser, browser_session, landing_at

ITEM = "current-update/what-changed/differential-reader-check"
ANSWERED_THREAD = "current-update/what-i-verified#q-parser-parity"
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
        browser.submit(times=2)
        deadline = time.monotonic() + 2
        while browser.server.post_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)

        assert browser.server.post_count == 1
        assert browser.evaluate(
            """
            document.querySelector(".composer") === null
              && document.querySelectorAll('[data-pending="true"]').length === 1
            """
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
          document.querySelectorAll('[data-pending="true"]').length,
          document.querySelector(".composer") === null,
          document.querySelector('[data-pending="true"]').textContent.includes(
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
        browser.submit()
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
          const note = row.querySelector('[data-pending="true"]');
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
    written = "First line"
    with_paragraph = f"{written}\n"
    submitted = f"{with_paragraph}second line"
    browser.compose_at(ITEM)
    browser.run("fill", ".composer textarea", written)
    browser.evaluate(_ENTER_SPY)

    browser.press("Enter")
    browser.run("wait", "400")
    delivered = browser.evaluate("window.__enter")
    after_enter = browser.evaluate(landing_at(ITEM))
    unsent = list(browser.server.posts)

    browser.run("type", ".composer textarea", "second line")
    consumed = browser.evaluate(_SEND_CHORD)
    landed = browser.read_until(
        landing_at(ITEM), lambda seen: seen["note"] is not None
    )

    assert delivered == [False]
    assert after_enter["composer"] is True, after_enter
    assert after_enter["typed"] == with_paragraph, after_enter
    assert after_enter["notes"] == 0, after_enter
    assert unsent == []
    assert consumed is True
    assert browser.server.posts == [
        ("/ask", {"anchor_id": ITEM, "text": submitted})
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
    browser.submit()
    browser.run("wait", "600")

    assert browser.evaluate(
        """
        [
          document.querySelector(".composer textarea").value,
          document.querySelector(".composer .status").textContent,
          document.querySelectorAll('[data-pending="true"]').length,
        ]
        """
    ) == ["Does this survive a refusal?", REFUSED, 0]

    browser.run("reload")
    browser.compose_at(ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == "Does this survive a refusal?"


def test_repeated_follow_up_stays_visible_through_reload(
    browser: Browser,
) -> None:
    """Keep repeated words pending under a seen answered conversation."""
    repeated = "Does twelve clean cases prove full parser parity?"
    browser.compose_at(ANSWERED_THREAD)
    browser.send(repeated)
    landed = browser.read_until(
        landing_at(ANSWERED_THREAD),
        lambda seen: seen["note"] is not None,
    )

    browser.run("reload")
    restored = browser.read_until(
        landing_at(ANSWERED_THREAD),
        lambda seen: seen["note"] is not None,
    )

    assert repeated in (landed["note"] or ""), landed
    assert restored["open"] == "true", restored
    assert restored["notes"] == 1, restored
    assert repeated in (restored["note"] or ""), restored


def test_each_draft_survives_navigation_publish_collapse_and_reload(
    browser: Browser,
) -> None:
    """Keep both rows' words through every ordinary reader action."""
    first = "First row draft"
    second = "Second row draft"
    browser.compose_at(ITEM)
    browser.run("fill", ".composer textarea", first)
    browser.compose_at(SECOND_ITEM)
    browser.run("fill", ".composer textarea", second)
    browser.compose_at(ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == first

    browser.data["title"] = "Published around an unfinished message"
    browser.publish()
    browser.wait_for_title("Published around an unfinished message")
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == first

    browser.click_row(ITEM)
    browser.press("C")
    assert browser.evaluate(
        "document.querySelector('.composer') === null"
    )
    browser.press("E")
    browser.compose_at(ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == first

    browser.run("reload")
    browser.wait_for_title("Published around an unfinished message")
    browser.compose_at(SECOND_ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == second


def test_removed_row_draft_survives_if_its_id_returns(
    browser: Browser,
) -> None:
    """A publish closes the absent box without discarding human-owned words."""
    removed = "Draft for the old version of this row"
    surviving = "Draft for the row that remains"
    browser.compose_at(ITEM)
    browser.run("fill", ".composer textarea", removed)
    browser.compose_at(SECOND_ITEM)
    browser.run("fill", ".composer textarea", surviving)
    browser.compose_at(ITEM)

    lane = browser.data["updates"][1]["lanes"][0]
    old_item = lane["items"].pop(0)
    browser.data["title"] = "The drafted row was removed"
    browser.publish()
    browser.wait_for_title("The drafted row was removed")
    assert browser.evaluate("document.querySelector('.composer') === null")

    lane["items"].insert(0, old_item)
    browser.data["title"] = "The row id returned"
    browser.publish()
    browser.wait_for_title("The row id returned")

    browser.compose_at(ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == removed
    browser.compose_at(SECOND_ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == surviving


def test_storage_failure_keeps_a_warning_with_the_draft(
    browser: Browser,
) -> None:
    """Never imply reload survival after the browser refuses draft storage."""
    browser.evaluate(
        """
        (() => {
          Storage.prototype.setItem = () => {
            throw new DOMException("Storage disabled", "SecurityError");
          };
          return true;
        })()
        """
    )
    browser.compose_at(ITEM)
    browser.run("fill", ".composer textarea", "Words only held in memory")
    warning = browser.evaluate(
        "document.querySelector('.draft-warning')?.textContent ?? null"
    )

    browser.compose_at(SECOND_ITEM)
    persisted = browser.evaluate(
        "document.querySelector('.draft-warning')?.textContent ?? null"
    )

    assert warning == (
        "Draft storage is unavailable. Reloading will lose this text."
    )
    assert persisted == warning


def test_discard_needs_two_escapes_or_the_explicit_control(
    browser: Browser,
) -> None:
    """Keep words after one Escape and erase them only after confirmation."""
    browser.compose_at(ITEM)
    browser.run("fill", ".composer textarea", "Human-owned words")

    browser.press("Escape")
    first = browser.evaluate(
        """
        [
          document.querySelector(".composer") !== null,
          document.querySelector(".composer textarea").value,
          document.querySelector(".composer .status").textContent,
        ]
        """
    )
    assert first[0:2] == [True, "Human-owned words"], first
    assert "again" in first[2], first

    browser.press("Escape")
    assert browser.evaluate("document.querySelector('.composer') === null")
    browser.compose_at(ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == ""

    browser.run("fill", ".composer textarea", "Discard with the control")
    browser.run("click", ".composer .quiet")
    if browser.evaluate("document.querySelector('.composer') !== null"):
        browser.evaluate(
            "document.querySelector('.composer .quiet')?.click(); true"
        )
    browser.compose_at(ITEM)
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == ""
