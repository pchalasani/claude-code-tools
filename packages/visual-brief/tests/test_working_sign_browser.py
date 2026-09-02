"""Prove the working sign lasts from submission through the agent answer."""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator

import pytest

from browser_support import (
    AWAITING_THREAD,
    Browser,
    browser_session,
    landing_at,
)
from cdp import DevToolsPage, page_socket_url

ITEM = "current-update/what-changed/differential-reader-check"
OLDER_UPDATE = "review-round-four"


def _working_at(row_id: str) -> str:
    """Read the waiting sign one row paints among its own body children."""
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
        awaiting:
          document.querySelectorAll('[data-waiting="direct"]').length > 0,
      }};
    }})()
    """


def _motion_at(row_id: str) -> str:
    """Return a script reading both working-sign animations.

    Args:
        row_id: Identifier of the row to read.

    Returns:
        JavaScript returning the preference in force and the computed motion
        and paint state for the words and mark.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const sign = row === null
        ? null
        : row.querySelector(":scope > .row-body > p.working");
      const words = sign?.querySelector(".working-text") ?? null;
      const waveWords = sign?.querySelector(".working-text-wave") ?? null;
      const mark = sign?.querySelector(".working-mark") ?? null;
      if (words === null || waveWords === null || mark === null) {{
        return null;
      }}
      const style = getComputedStyle(words);
      const wave = getComputedStyle(waveWords);
      const motion = getComputedStyle(mark);
      return {{
        reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
        text: words.textContent,
        textAnimation: wave.animationName,
        textIteration: wave.animationIterationCount,
        textPosition: wave.backgroundPosition,
        textBackground: wave.backgroundImage,
        waveDisplay: wave.display,
        waveHidden: waveWords.getAttribute("aria-hidden"),
        waveFill: wave.getPropertyValue("-webkit-text-fill-color"),
        markAnimation: motion.animationName,
        markIteration: motion.animationIterationCount,
        markOpacity: motion.opacity,
        fill: style.getPropertyValue("-webkit-text-fill-color"),
        color: style.getPropertyValue("color"),
      }};
    }})()
    """


def _fold_question_into_content(
    browser: Browser, thread_id: str, text: str, at: str
) -> dict[str, Any]:
    """Add a sent question as the awaiting conversation it becomes."""
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
    thread = {
        "id": thread_id,
        "anchor": {"kind": "element", "path": ITEM},
        "turns": [{"author": "human", "text": text, "at": at}],
    }
    item.setdefault("questions", []).append(thread)
    return thread


def _accessible_toggle(browser: Browser, row_id: str) -> dict[str, Any]:
    """Read one disclosure from Chrome's accessibility tree.

    Args:
        browser: The open real browser.
        row_id: Identifier of the row whose disclosure to inspect.

    Returns:
        Chrome's accessible node for the row's disclosure button.
    """
    selector = f'[data-row-id="{row_id}"] > .row-head > .row-toggle'
    page = DevToolsPage(
        page_socket_url(browser.run("get", "cdp-url").strip(), browser.url)
    )
    try:
        evaluated = page.call(
            "Runtime.evaluate",
            {"expression": f"document.querySelector({selector!r})"},
        )
        object_id = evaluated["result"].get("objectId")
        assert isinstance(object_id, str), evaluated
        described = page.call("DOM.describeNode", {"objectId": object_id})
        backend_id = described["node"]["backendNodeId"]
        tree = page.call(
            "Accessibility.getPartialAXTree",
            {"backendNodeId": backend_id, "fetchRelatives": False},
        )
        nodes = tree["nodes"]
        assert len(nodes) == 1, tree
        return dict(nodes[0])
    finally:
        page.close()


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_the_page_says_the_agent_is_working_until_the_answer_lands(
    browser: Browser,
) -> None:
    """Move something where the answer will appear, from send until arrival."""
    question = "Is anything happening?"
    answer = "Yes. The answer has landed."
    thread_id = "q-complete-working-lifecycle"
    folded = f"{ITEM}#{thread_id}"
    browser.server.post_gate = threading.Event()
    try:
        browser.compose_at(ITEM)
        browser.run("fill", ".composer textarea", question)
        browser.submit()
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
    thread = _fold_question_into_content(
        browser, thread_id, question, browser.server.stamps[0]
    )
    browser.data["title"] = "Question awaiting its answer"
    browser.publish()
    browser.wait_for_title("Question awaiting its answer")
    awaiting = browser.read_until(
        _working_at(folded), lambda seen: seen["count"] == 1
    )

    thread["turns"].append(
        {"author": "agent", "text": answer, "at": "2026-07-25T21:00:02Z"}
    )
    browser.data["title"] = "Agent answer landed"
    browser.publish()
    browser.wait_for_title("Agent answer landed")
    answered = browser.read_until(
        f"""
        (() => {{
          const row = document.querySelector('[data-row-id="{folded}"]');
          return {{
            answer: row?.querySelector(".turn-agent .turn-text")?.textContent,
            working: row?.querySelectorAll(
              ":scope > .row-body > p.working",
            ).length ?? 0,
          }};
        }})()
        """,
        lambda seen: seen["answer"] == answer,
    )

    expected = {
        "count": 1,
        "text": "agent is working",
        "moving": True,
        "awaiting": True,
    }
    assert in_flight == expected
    assert landed == expected
    assert awaiting == expected
    assert answered == {"answer": answer, "working": 0}


def test_the_working_sign_moves_only_where_motion_is_welcome(
    browser: Browser,
) -> None:
    """Wave the legible words and pulse the dot unless motion is unwelcome.

    Chrome's real preference is turned on for this, and the result is read off
    the elements rather than off the stylesheet. Every colour in the moving
    text paint is opaque, so the words remain visible throughout the sweep.
    """
    browser.server.post_gate = threading.Event()
    try:
        browser.compose_at(ITEM)
        browser.run("fill", ".composer textarea", "Is anything happening?")
        browser.submit()
        deadline = time.monotonic() + 2
        while browser.server.post_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        moving = browser.read_until(
            _working_at(ITEM), lambda seen: seen["count"] == 1, timeout=3
        )
        painted = browser.evaluate(_motion_at(ITEM))
        painted_later = browser.read_until(
            _motion_at(ITEM),
            lambda seen: seen["textPosition"] != painted["textPosition"],
        )
        with browser.reduced_motion():
            still = browser.evaluate(_motion_at(ITEM))
    finally:
        browser.server.post_gate.set()

    assert moving["moving"] is True
    for reading in (painted, painted_later, still):
        assert reading is not None
        assert reading["text"] == "agent is working"

    assert painted["textAnimation"] == "agent-working-wave"
    assert painted["textIteration"] == "infinite"
    assert painted["textPosition"] != painted_later["textPosition"]
    assert painted["textBackground"].startswith("linear-gradient(")
    assert "rgba(0, 0, 0, 0)" not in painted["textBackground"]
    assert "transparent" not in painted["textBackground"]
    assert painted["waveDisplay"] == "block"
    assert painted["waveHidden"] == "true"
    assert painted["waveFill"] in ("transparent", "rgba(0, 0, 0, 0)")
    assert painted["fill"] == painted["color"]
    assert painted["fill"] not in ("transparent", "rgba(0, 0, 0, 0)")
    assert painted["markAnimation"] == "agent-working-pulse"
    assert painted["markIteration"] == "infinite"

    assert still["reduced"] is True
    assert still["textAnimation"] == "none"
    assert still["textBackground"] == "none"
    assert still["waveDisplay"] == "none"
    assert still["waveHidden"] == "true"
    assert still["fill"] == still["color"]
    assert still["fill"] not in ("transparent", "rgba(0, 0, 0, 0)")
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


def test_the_waiting_rail_stands_beside_the_working_sign(
    browser: Browser,
) -> None:
    """Keep one direct mark while its containers quietly carry the fact."""
    question = "Does the rail leave the sign in place?"
    thread_id = "q-rail-and-sign"
    browser.compose_at(ITEM)
    browser.send(question)
    browser.read_until(landing_at(ITEM), lambda seen: seen["note"] is not None)
    pending_rails = browser.evaluate(
        """
        Object.fromEntries(
          [...document.querySelectorAll("[data-waiting]")]
            .map((row) => [row.dataset.rowId, row.dataset.waiting]),
        )
        """
    )
    assert pending_rails[ITEM] == "direct", pending_rails
    assert pending_rails["current-update/what-changed"] == "contained"
    assert pending_rails["current-update"] == "contained"

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
    rails = browser.evaluate(
        """
        (() => {
          const rows = [...document.querySelectorAll("[data-row-id]")];
          return Object.fromEntries(
            rows
              .filter((row) => row.dataset.waiting !== undefined)
              .map((row) => [row.dataset.rowId, row.dataset.waiting]),
          );
        })()
        """
    )

    assert sign["count"] == 1, sign
    assert sign["text"] == "agent is working", sign
    assert rails[folded] == "direct", rails
    assert rails[ITEM] == "contained", rails
    assert rails["current-update/what-changed"] == "contained", rails


def test_folded_older_update_exposes_contained_waiting_accessibly(
    browser: Browser,
) -> None:
    """Name a hidden descendant's wait without repeating its direct alarm."""
    older_open = browser.evaluate(
        f'document.querySelector(\'[data-row-id="{OLDER_UPDATE}"]\')'
        ".dataset.open"
    )
    if older_open == "true":
        browser.click_row(OLDER_UPDATE)
    assert browser.evaluate(
        f'document.querySelector(\'[data-row-id="{OLDER_UPDATE}"]\')'
        ".dataset.open"
    ) == "false"

    contained = _accessible_toggle(browser, OLDER_UPDATE)
    direct = _accessible_toggle(browser, AWAITING_THREAD)

    assert contained["role"]["value"] == "button", contained
    assert contained["description"]["value"] == (
        "Contains a conversation waiting for an agent answer."
    ), contained
    assert direct["description"]["value"] == (
        "Waiting for an agent answer."
    ), direct


def test_newest_conversation_is_painted_first(browser: Browser) -> None:
    """Put the most recently appended conversation first in a real page."""
    _fold_question_into_content(
        browser,
        "q-older",
        "This question arrived first.",
        "2026-07-25T15:00:00Z",
    )
    _fold_question_into_content(
        browser,
        "q-newer",
        "This question arrived second.",
        "2026-07-25T16:00:00Z",
    )
    browser.data["title"] = "Two conversations in append order"
    browser.publish()
    browser.wait_for_title("Two conversations in append order")
    browser.click_row(ITEM)

    painted = browser.evaluate(
        f"""
        [...document.querySelectorAll(
          '[data-row-id="{ITEM}"] > .row-body > .row-thread',
        )].map((row) => row.dataset.rowId)
        """
    )

    assert painted == [f"{ITEM}#q-newer", f"{ITEM}#q-older"]
