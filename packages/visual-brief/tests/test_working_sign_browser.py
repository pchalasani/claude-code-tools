"""Real-browser proof that the waiting sign is continuous.

A human who asks a question watches one thing: whether anything is happening.
The page has three ways of knowing — a request in the air, a message the
daemon has taken but the document has not caught up with, and a conversation
the document itself says is unanswered — and they used to paint three signs
that stood one another down. Standing down is how a sign disappears at a
reload boundary, which is what was reported from live use.

So there is one sign, asked once of all three, and its words are never the
part that moves.
"""

from __future__ import annotations

import threading
import time
from typing import Iterator

import pytest

from browser_support import Browser, browser_session, landing_at

ITEM = "current-update/what-changed/differential-reader-check"


def _working_at(row_id: str) -> str:
    """Return a script reading the waiting sign one row paints for itself.

    Rows nest, so the sign is looked for among the row's own body children:
    a conversation's sign belongs to the conversation, not to the item it
    hangs from. What moves is the mark beside the words; the words are read
    separately, because a sign whose words come and go is a sign that
    disappears.

    Args:
        row_id: Identifier of the row to read.

    Returns:
        JavaScript returning what a human sees of that row's waiting sign.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const signs = row === null
        ? []
        : [...row.querySelectorAll(":scope > .row-body > p.working")];
      const words = signs[0]?.querySelector(".working-text") ?? null;
      const mark = signs[0]?.querySelector(".working-mark") ?? null;
      const motion = mark === null ? null : getComputedStyle(mark);
      return {{
        count: signs.length,
        text: words === null ? "" : words.textContent,
        moving: motion !== null
          && motion.animationName !== "none"
          && motion.animationIterationCount === "infinite"
          && motion.animationDuration !== "0s",
        awaiting: document.querySelectorAll(".chip-awaiting").length > 0,
      }};
    }})()
    """


def _still_at(row_id: str) -> str:
    """Return a script reading the sign's words and the mark beside them.

    Args:
        row_id: Identifier of the row to read.

    Returns:
        JavaScript returning the preference in force, the painted words and
        whether anything about them is moving or see-through.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const sign = row === null
        ? null
        : row.querySelector(":scope > .row-body > p.working");
      const words = sign?.querySelector(".working-text") ?? null;
      const mark = sign?.querySelector(".working-mark") ?? null;
      if (words === null || mark === null) {{
        return null;
      }}
      const style = getComputedStyle(words);
      const motion = getComputedStyle(mark);
      return {{
        reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
        text: words.textContent,
        animation: style.animationName,
        markAnimation: motion.animationName,
        markOpacity: motion.opacity,
        fill: style.getPropertyValue("-webkit-text-fill-color"),
        color: style.getPropertyValue("color"),
      }};
    }})()
    """


def _fold_question_into_content(
    browser: Browser, thread_id: str, text: str, at: str
) -> None:
    """Publish a sent question as the awaiting conversation it becomes.

    This is what the daemon does on its next publish: a queued question the
    agent has not answered yet is folded into the served content as a
    conversation whose newest turn is the human's, carrying the queue line's
    own timestamp.

    Args:
        browser: The open browser, whose ``data`` is the served document.
        thread_id: Identifier to give the folded conversation.
        text: What the human asked.
        at: Timestamp the daemon stamped the queue line with.
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
            "turns": [{"author": "human", "text": text, "at": at}],
        }
    )


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


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
    """Keep the same words readable, and stop the motion, when it is unwelcome.

    Chrome's real preference is turned on for this, and the result is read off
    the elements rather than off the stylesheet. The words are checked in both
    states: they are never the part that moves, so nothing about them may
    change when motion is switched off.
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
        painted = browser.evaluate(_still_at(ITEM))
        with browser.reduced_motion():
            still = browser.evaluate(_still_at(ITEM))
    finally:
        browser.server.post_gate.set()

    assert moving["moving"] is True
    # The words themselves: one solid colour, at every instant, either way.
    # They used to be painted through a travelling gradient, which left them
    # transparent for most of every cycle and fainter still at the moment a
    # reload restarted it — the sign a human watched disappear.
    for reading in (painted, still):
        assert reading is not None
        assert reading["text"] == "agent is working"
        assert reading["animation"] == "none"
        assert reading["fill"] == reading["color"]
        assert reading["fill"] not in ("transparent", "rgba(0, 0, 0, 0)")
    assert painted["markAnimation"] != "none"
    assert still["reduced"] is True
    assert still["markAnimation"] == "none"
    assert still["markOpacity"] == "1"


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

    _fold_question_into_content(
        browser, thread_id, question, browser.server.stamps[0]
    )
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


def test_the_arriving_chips_stand_beside_the_sign_rather_than_replacing_it(
    browser: Browser,
) -> None:
    """Let both be true at once, because both are.

    The reload a send causes brings the awaiting chips up all the way to the
    top of the page. That is news about the question; the sign is news about
    the agent, and it is the more informative of the two. The human reported
    watching the second disappear as the first arrived.
    """
    question = "Do the chips push the sign off the page?"
    thread_id = "q-chips-and-sign"
    browser.compose_at(ITEM)
    browser.send(question)
    browser.read_until(landing_at(ITEM), lambda seen: seen["note"] is not None)

    _fold_question_into_content(
        browser, thread_id, question, browser.server.stamps[0]
    )
    browser.data["title"] = "Published with the question folded in"
    browser.publish()
    browser.wait_for_title("Published with the question folded in")
    folded = f"{ITEM}#{thread_id}"
    sign = browser.read_until(
        _working_at(folded), lambda seen: seen["count"] == 1
    )
    chips = browser.evaluate(
        f"""
        (() => {{
          const rows = [...document.querySelectorAll("[data-row-id]")];
          return rows
            .filter((row) =>
              row.querySelector(":scope > .row-head .chip-awaiting") !== null,
            )
            .map((row) => row.dataset.rowId);
        }})()
        """
    )

    assert sign["count"] == 1, sign
    assert sign["text"] == "agent is working", sign
    # Every level above the conversation now says it is waiting, and the
    # conversation still says what is happening about it.
    assert folded in chips, chips
    assert ITEM in chips, chips
    assert "current-update/what-changed" in chips, chips
