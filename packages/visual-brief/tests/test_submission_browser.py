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


def _working_at(row_id: str) -> str:
    """Return a script reading the waiting sign one row paints for itself.

    Rows nest, so the sign is looked for among the row's own body children:
    a conversation's sign belongs to the conversation, not to the item it
    hangs from.

    Args:
        row_id: Identifier of the row to read.

    Returns:
        JavaScript returning what a human sees of that row's waiting sign.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const marks = row === null
        ? []
        : [...row.querySelectorAll(":scope > .row-body > p.working")];
      const first = marks[0]?.querySelector(".working-text") ?? null;
      const style = first === null ? null : getComputedStyle(first);
      return {{
        count: marks.length,
        text: first === null ? "" : first.textContent,
        moving: style !== null
          && style.animationName !== "none"
          && style.animationIterationCount === "infinite"
          && style.animationDuration !== "0s",
        awaiting: document.querySelectorAll(".chip-awaiting").length > 0,
      }};
    }})()
    """


def _still_at(row_id: str) -> str:
    """Return a script reading the same sign as a still, coloured label.

    Args:
        row_id: Identifier of the row to read.

    Returns:
        JavaScript returning the preference in force and the painted words.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const mark = row === null
        ? null
        : row.querySelector(":scope > .row-body > p.working .working-text");
      if (mark === null) {{
        return null;
      }}
      const style = getComputedStyle(mark);
      return {{
        reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
        text: mark.textContent,
        animation: style.animationName,
        fill: style.getPropertyValue("-webkit-text-fill-color"),
        color: style.getPropertyValue("color"),
      }};
    }})()
    """


def _fold_question_into_content(browser: Browser, thread_id: str, text: str) -> None:
    """Publish a sent question as the awaiting conversation it becomes.

    This is what the daemon does on its next publish: a queued question the
    agent has not answered yet is folded into the served content as a
    conversation whose newest turn is the human's.

    Args:
        browser: The open browser, whose ``data`` is the served document.
        thread_id: Identifier to give the folded conversation.
        text: What the human asked.
    """
    update_id, lane_id, item_id = ITEM.split("/")
    item = next(
        item
        for update in browser.data["updates"]
        if update["id"] == update_id
        for lane in update["lanes"]
        if lane["id"] == lane_id
        for item in lane["items"]
        if item["id"] == item_id
    )
    item.setdefault("questions", []).append(
        {
            "id": thread_id,
            "anchor": {"kind": "element", "path": ITEM},
            "turns": [
                {
                    "author": "human",
                    "text": text,
                    "at": "2026-07-25T20:05:00Z",
                }
            ],
        }
    )


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


def test_the_page_says_the_agent_is_working_until_the_answer_lands(
    browser: Browser,
) -> None:
    """Move something where the answer will appear, from send until arrival."""
    browser.server.post_gate = threading.Event()
    try:
        browser.compose_at(ITEM)
        browser.run("fill", ".composer textarea", "Is anything happening?")
        browser.run("click", ".composer .submit")
        deadline = time.monotonic() + 2
        while browser.server.post_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        in_flight = browser.read_until(
            _working_at(ITEM), lambda seen: seen["count"] == 1, timeout=3
        )
    finally:
        browser.server.post_gate.set()
    browser.read_until(landing_at(ITEM), lambda seen: seen["note"] is not None)

    landed = browser.evaluate(_working_at(ITEM))

    expected = {
        "count": 1,
        "text": "agent is working",
        "moving": True,
        "awaiting": True,
    }
    assert in_flight == expected
    assert landed == expected


def test_the_working_sign_stands_still_where_motion_is_unwelcome(
    browser: Browser,
) -> None:
    """Keep the same words readable, and still, when motion is unwelcome.

    The shimmer paints through transparent text, so switching the animation
    off is not enough on its own: the fill has to come back or the human is
    left with a blank line where the reassurance should be. Chrome's real
    preference is turned on for this, and the result is read off the element
    rather than off the stylesheet.
    """
    browser.server.post_gate = threading.Event()
    try:
        browser.compose_at(ITEM)
        browser.run("fill", ".composer textarea", "Is anything happening?")
        browser.run("click", ".composer .submit")
        deadline = time.monotonic() + 2
        while browser.server.post_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        moving = browser.read_until(
            _working_at(ITEM), lambda seen: seen["count"] == 1, timeout=3
        )
        with browser.reduced_motion():
            still = browser.evaluate(_still_at(ITEM))
    finally:
        browser.server.post_gate.set()

    assert moving["moving"] is True
    assert still is not None
    assert still["reduced"] is True
    assert still["text"] == "agent is working"
    assert still["animation"] == "none"
    assert still["fill"] == still["color"]
    assert still["fill"] not in ("transparent", "rgba(0, 0, 0, 0)")


def test_the_working_sign_outlives_a_publish_that_carries_no_answer(
    browser: Browser,
) -> None:
    """Keep the reassurance up when a republish beats the answer to the page.

    The agent republishes constantly, and every publish reloads the page from
    scratch. The sign has to be a fact about the conversation rather than
    about the page load that sent the question, or the first unrelated update
    wipes the reassurance out for the whole remaining wait.
    """
    question = "Will this outlast a republish?"
    thread_id = "q-pending-republish"
    browser.compose_at(ITEM)
    browser.send(question)
    browser.read_until(landing_at(ITEM), lambda seen: seen["note"] is not None)
    before = browser.evaluate(_working_at(ITEM))

    _fold_question_into_content(browser, thread_id, question)
    browser.data["title"] = "Republished with no answer yet"
    browser.publish()
    browser.wait_for_title("Republished with no answer yet")
    folded = browser.read_until(
        _working_at(f"{ITEM}#{thread_id}"), lambda seen: seen["count"] == 1
    )

    assert before["count"] == 1
    assert folded == {
        "count": 1,
        "text": "agent is working",
        "moving": True,
        "awaiting": True,
    }
    assert browser.evaluate(_working_at(ITEM))["count"] == 0


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
