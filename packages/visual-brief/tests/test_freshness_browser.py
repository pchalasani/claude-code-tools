"""Real-browser proof that an answer the human was waiting for is seen.

The page reloads itself the moment the agent publishes, and rows hold
themselves open only while they are *awaiting*. That made the one conversation
the human was waiting on the one conversation that folded shut the instant its
answer arrived. These drive the whole moment through a real self-reload.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from browser_support import AWAITING_THREAD, Browser, browser_session

ANCHOR, THREAD_ID = AWAITING_THREAD.split("#")
UPDATE_ID, LANE_ID, ITEM_ID = ANCHOR.split("/")

_ROW_STATE = f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{AWAITING_THREAD}"]');
      const open = (id) => document.querySelector(
        '[data-row-id="' + id + '"]',
      )?.dataset.open ?? null;
      return {{
        present: row !== null,
        open: row === null ? null : row.dataset.open,
        fresh: row === null ? null : row.dataset.fresh,
        chip: row === null
          ? null
          : (row.querySelector(".chip-new")?.textContent ?? ""),
        item: open("{ANCHOR}"),
        lane: open("{UPDATE_ID}/{LANE_ID}"),
        update: open("{UPDATE_ID}"),
      }};
    }})()
    """


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def _thread(data: dict[str, Any]) -> dict[str, Any]:
    """Return the conversation the suite answers.

    Args:
        data: The document being served.

    Returns:
        The thread carrying the awaiting question.
    """
    return next(
        thread
        for update in data["updates"]
        if update["id"] == UPDATE_ID
        for lane in update["lanes"]
        if lane["id"] == LANE_ID
        for item in lane["items"]
        if item["id"] == ITEM_ID
        for thread in item["questions"]
        if thread["id"] == THREAD_ID
    )


def _answer(browser: Browser, text: str, at: str) -> None:
    """Have the agent answer the awaiting conversation.

    Args:
        browser: The open browser.
        text: What the agent says.
        at: When it said it.
    """
    _thread(browser.data)["turns"].append(
        {"author": "agent", "text": text, "at": at}
    )


def test_an_answer_that_landed_while_away_opens_itself_and_is_marked(
    browser: Browser,
) -> None:
    """Show the answer, and say it is new, after a real self-reload."""
    browser.press("g")
    browser.run("wait", "250")
    assert browser.cursor_row() == UPDATE_ID
    before = browser.evaluate(_ROW_STATE)

    _answer(browser, "Malformed and unsupported now differ.", "2026-07-25T21:00:00Z")
    browser.data["title"] = "The answer landed"
    browser.publish()
    browser.wait_for_title("The answer landed")
    browser.run("wait", "400")

    after = browser.evaluate(_ROW_STATE)
    assert before["fresh"] == "false"
    assert after["present"] is True
    assert after["open"] == "true"
    assert after["fresh"] == "true"
    assert "New answer" in after["chip"]
    assert [after["item"], after["lane"], after["update"]] == [
        "true",
        "true",
        "true",
    ]


def test_the_new_mark_survives_another_publish_and_clears_on_a_visit(
    browser: Browser,
) -> None:
    """Clear the mark by going to the answer, never by waiting it out."""
    browser.press("g")
    browser.run("wait", "250")
    _answer(browser, "Malformed and unsupported now differ.", "2026-07-25T21:00:00Z")
    browser.data["title"] = "The answer landed"
    browser.publish()
    browser.wait_for_title("The answer landed")
    browser.run("wait", "400")
    assert browser.evaluate(_ROW_STATE)["fresh"] == "true"

    browser.data["summary"] = "Republished without touching the conversation."
    browser.data["title"] = "Published again"
    browser.publish()
    browser.wait_for_title("Published again")
    browser.run("wait", "400")
    still_marked = browser.evaluate(_ROW_STATE)

    browser.click_row(AWAITING_THREAD)
    browser.run("wait", "400")
    visited = browser.evaluate(_ROW_STATE)

    assert still_marked["fresh"] == "true"
    assert still_marked["open"] == "true"
    assert visited["fresh"] == "false"
    assert visited["chip"] == ""
