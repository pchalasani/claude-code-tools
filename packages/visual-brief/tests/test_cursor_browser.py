"""Real-browser proof that the cursor is visible, and that keys move it.

The previous keyboard layer passed a suite that asserted ``activeElement``
changed, which a human cannot see. These tests assert paint: the row the
application calls the cursor must differ from its neighbours by a measured
amount of colour and sit in comfortable reading position. What the individual
bindings do lives next door, in ``test_keyboard_browser``.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterator

import pytest

from browser_support import (
    FIRST_ITEM,
    SECOND_ITEM,
    Browser,
    browser_session,
)

_NUMBERS = re.compile(r"[-+]?\d*\.?\d+")


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def colour(value: str) -> tuple[float, float, float, float]:
    """Parse a computed CSS colour into channels and alpha.

    Browsers report a mixed colour as ``color(srgb r g b)`` with channels in
    the unit range and a plain colour as ``rgb(r, g, b)`` in the byte range;
    both have to become comparable numbers.

    Args:
        value: A computed colour string.

    Returns:
        Red, green and blue in 0-255, and alpha in 0-1.

    Raises:
        AssertionError: If the colour cannot be read.
    """
    numbers = [float(match) for match in _NUMBERS.findall(value)]
    assert len(numbers) >= 3, value
    red, green, blue = numbers[0], numbers[1], numbers[2]
    alpha = numbers[3] if len(numbers) > 3 else 1.0
    if value.startswith("color("):
        return red * 255, green * 255, blue * 255, alpha
    return red, green, blue, alpha


def distance(first: str, second: str) -> float:
    """Measure how far apart two computed colours are.

    Args:
        first: One computed colour.
        second: Another computed colour.

    Returns:
        The Euclidean distance between them in 0-255 channel space.
    """
    left = colour(first)
    right = colour(second)
    return math.dist(left[:3], right[:3])


def item_heads(browser: Browser) -> dict[str, Any]:
    """Read the painted style of the cursor row and of every other item.

    Args:
        browser: The open browser.

    Returns:
        The cursor's identity and paint, and the paint of the other items.
    """
    return browser.evaluate(
        """
        (() => {
          const paint = (row) => {
            const head = row.querySelector(":scope > .row-head");
            const style = getComputedStyle(head);
            return {
              id: row.dataset.rowId,
              background: style.backgroundColor,
              railColour: style.borderLeftColor,
              railWidth: style.borderLeftWidth,
            };
          };
          const marked = [...document.querySelectorAll('[data-cursor="true"]')];
          const others = [
            ...document.querySelectorAll(
              '[data-row-kind="item"][data-cursor="false"]',
            ),
          ];
          return {
            marked: marked.length,
            kind: marked[0].dataset.rowKind,
            cursor: paint(marked[0]),
            others: others.map(paint),
          };
        })()
        """
    )


def walk_content(
    browser: Browser,
    key: str,
    count: int,
) -> list[tuple[str, str]]:
    """Press one movement key and record each exact painted landing.

    Args:
        browser: The open browser.
        key: Real browser key to press.
        count: Number of rows to visit.

    Returns:
        Each cursor row identifier and its painted row kind.
    """
    landed: list[tuple[str, str]] = []
    for _ in range(count):
        browser.press(key)
        browser.run("wait", "180")
        landed.append(
            (
                browser.cursor_row(),
                browser.evaluate(
                    "document.querySelector('[data-cursor=\"true\"]')"
                    ".dataset.rowKind"
                ),
            )
        )
    return landed


def painted_rows(browser: Browser) -> list[tuple[str, str]]:
    """Return every currently painted row in browser order.

    Args:
        browser: The open browser.

    Returns:
        Each painted row identifier and kind, from top to bottom.
    """
    rows = browser.evaluate(
        """
        [...document.querySelectorAll("[data-row-id]")].map(
          (row) => [row.dataset.rowId, row.dataset.rowKind],
        )
        """
    )
    return [(str(row_id), str(kind)) for row_id, kind in rows]


def test_the_cursor_row_is_painted_unlike_every_other_item(
    browser: Browser,
) -> None:
    """Measure the contrast step a human is meant to see, in numbers."""
    browser.click_row(FIRST_ITEM)
    browser.press("j")
    browser.run("wait", "200")

    painted = item_heads(browser)

    assert painted["marked"] == 1
    assert painted["kind"] == "item"
    assert painted["cursor"]["id"] == SECOND_ITEM
    assert len(painted["others"]) >= 2
    rail = colour(painted["cursor"]["railColour"])
    assert rail[3] == 1.0, painted["cursor"]
    assert float(painted["cursor"]["railWidth"].removesuffix("px")) >= 3
    for other in painted["others"]:
        assert colour(other["railColour"]) != rail, other
        gap = distance(painted["cursor"]["background"], other["background"])
        assert gap >= 24, (gap, painted["cursor"], other)


def test_moving_the_cursor_repaints_it_rather_than_moving_focus(
    browser: Browser,
) -> None:
    """Prove the mark travels while the browser's own focus stays put."""
    cursor_before = browser.cursor_row()
    before = browser.evaluate("document.activeElement.tagName")

    browser.press("j")
    browser.press("j")
    browser.run("wait", "200")

    assert browser.cursor_row() != cursor_before
    assert browser.evaluate("document.activeElement.tagName") == before


def test_the_cursor_stays_in_comfortable_reading_position(
    browser: Browser,
) -> None:
    """Hold the cursor in the reading band, not merely off the edges.

    ``scroll-margin-block: 35vh`` puts a row the page scrolled down to at
    roughly two thirds of the window, so the text above it is the text the
    human just read. A cursor drifting towards either edge — which is what a
    lost or shrunken scroll margin looks like — has to redden this.
    """
    for _ in range(10):
        browser.press("j")
    browser.run("wait", "300")

    placement = browser.evaluate(
        """
        (() => {
          const head = document.querySelector(
            '[data-cursor="true"] > .row-head',
          );
          const box = head.getBoundingClientRect();
          return {
            top: box.top,
            bottom: box.bottom,
            height: window.innerHeight,
          };
        })()
        """
    )

    assert placement["top"] > placement["height"] * 0.2
    assert placement["bottom"] < placement["height"] * 0.75


def test_the_mouse_and_the_keyboard_share_one_cursor(browser: Browser) -> None:
    """Click a row and then keep moving from it with the keyboard."""
    rows = [row_id for row_id, _ in painted_rows(browser)]
    previous = rows[rows.index(SECOND_ITEM) - 1]
    browser.click_row(SECOND_ITEM)
    browser.run("wait", "200")
    clicked = browser.cursor_row()

    browser.press("k")
    browser.run("wait", "200")

    assert clicked == SECOND_ITEM
    assert browser.cursor_row() == previous


def test_plain_movement_walks_every_painted_row_in_both_directions(
    browser: Browser,
) -> None:
    """Walk headers, content, evidence, and chats without boundary dead ends."""
    browser.press("E")
    browser.run("wait", "300")
    painted = painted_rows(browser)
    assert {kind for _, kind in painted} >= {
        "update",
        "lane",
        "item",
        "thread",
        "evidence",
    }

    browser.press("g")
    assert (browser.cursor_row(), painted[0][1]) == painted[0]
    steps = len(painted) - 1
    assert walk_content(browser, "j", steps) == painted[1:]
    assert walk_content(browser, "k", steps) == list(reversed(painted[:-1]))
    assert walk_content(browser, "ArrowDown", steps) == painted[1:]
    assert walk_content(browser, "ArrowUp", steps) == list(
        reversed(painted[:-1])
    )


def test_the_cursor_stays_on_its_row_when_the_agent_publishes(
    browser: Browser,
) -> None:
    """Keep the human's place when the agent publishes new content."""
    browser.click_row(FIRST_ITEM)
    browser.press("J")
    browser.run("wait", "200")
    before = browser.cursor_row()

    browser.data["title"] = "Published while reading"
    browser.publish()
    browser.wait_for_title("Published while reading")
    browser.run("wait", "300")

    assert before == "current-update/why-it-matters"
    assert browser.cursor_row() == before


def test_search_filters_without_writing_the_cursor_or_query(
    browser: Browser,
) -> None:
    """Hide a selected row temporarily and restore the exact human state."""
    browser.click_row(SECOND_ITEM)
    browser.run("wait", "200")
    before = browser.cursor_row()

    browser.press("/")
    browser.run("wait", "200")
    browser.run("type", "#brief-search", "Cedar CLI 4.11.2")
    browser.run("wait", "300")

    filtered = browser.evaluate(
        """
        (() => ({
          items: [...document.querySelectorAll('[data-row-kind="item"]')].map(
            (row) => row.dataset.rowId,
          ),
          cursor: document.querySelector('[data-cursor="true"]')
            ?.dataset.rowId ?? null,
          query: document.querySelector("#brief-search")?.value ?? null,
        }))()
        """
    )
    assert FIRST_ITEM in filtered["items"]
    assert SECOND_ITEM not in filtered["items"]
    # The selected item is filtered out, so the visible fallback is the
    # latest briefing rather than an unmarked page.
    assert filtered["cursor"] == "current-update"
    assert filtered["query"] == "Cedar CLI 4.11.2"

    browser.press("Escape")
    browser.run("wait", "300")
    restored = browser.evaluate(
        """
        (() => ({
          items: document.querySelectorAll('[data-row-kind="item"]').length,
          cursor: document.querySelector('[data-cursor="true"]')
            ?.dataset.rowId ?? null,
          query: document.querySelector("#brief-search")?.value ?? null,
        }))()
        """
    )
    assert restored["items"] > 1
    assert restored["cursor"] == before
    assert restored["query"] is None


def test_the_map_cannot_send_the_cursor_off_the_filtered_page(
    browser: Browser,
) -> None:
    """Keep exactly one row marked when a click reaches a hidden lane.

    The updates log offers every lane whatever the search is showing, so a
    click can name a row the filter removed. The cursor has to end up on a row
    that is on the page: here the search gives way and the lane is marked.
    """
    lane = "review-round-four/round-four-next"
    browser.click_row(SECOND_ITEM)
    browser.press("/")
    browser.run("wait", "200")
    browser.run("type", "#brief-search", "Cedar CLI 4.11.2")
    browser.run("wait", "300")
    assert browser.cursor_row() == "current-update"

    browser.run("click", '[aria-label="Open briefing ledger"]')
    browser.run("wait", "200")
    geometry = browser.evaluate(
        """
        (() => {
          const drawer = document.querySelector(".map").getBoundingClientRect();
          const stream = document.querySelector(".stream").getBoundingClientRect();
          const search = document.querySelector(".search").getBoundingClientRect();
          return { drawerRight: drawer.right, streamLeft: stream.left,
            searchLeft: search.left, desktop: innerWidth > 64 * 16 };
        })()
        """
    )
    if geometry["desktop"]:
        assert geometry["drawerRight"] <= geometry["streamLeft"], geometry
        assert geometry["searchLeft"] >= geometry["drawerRight"], geometry
    browser.run("scrollintoview", f'[data-map-lane="{lane}"]')
    browser.run("click", f'[data-map-lane="{lane}"]')
    browser.run("wait", "300")

    selected = browser.evaluate(
        f"""
        (() => ({{
          cursor: document.querySelector('[data-cursor="true"]')
            ?.dataset.rowId ?? null,
          lane: document.querySelector('[data-row-id="{lane}"]') !== null,
          update: document.querySelector(
            '[data-row-id="review-round-four"]',
          )?.dataset.open ?? null,
          query: document.querySelector("#brief-search")?.value ?? null,
        }}))()
        """
    )
    assert selected["cursor"] == lane, selected
    assert browser.evaluate(
        f"""
        [
          document.querySelector('[data-row-id="{lane}"]') !== null,
          document.querySelector("#brief-search").value,
        ]
        """
    ) == [True, ""]
