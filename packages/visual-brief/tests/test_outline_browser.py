"""Real-browser proof that the painted page and the cursor's list agree.

The outline the keyboard walks and the tree the browser paints are one list.
Nothing enforces that at runtime, so it is asserted here the only way that
means anything: by arming the jump labels, which are handed out in the
outline's order, and reading them back off the painted page in the browser's
order. If the two ever disagreed, a label would send the cursor somewhere the
human was not looking.

The same suite pins where a lane's own conversations belong, which is the
change that made the two orders easy to get wrong.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from browser_support import Browser, browser_session

# The keys jump labels are built from, in the order they are handed out.
HINT_KEYS = "asdfghjkl"

LANE = "current-update/what-i-verified"
LANE_CHAT = f"{LANE}#q-parser-parity"
FIRST_ITEM_OF_LANE = f"{LANE}/twelve-inputs"

_PAINTED = """
    (() => {
      const rows = [...document.querySelectorAll("[data-row-id]")];
      return rows.map((row) => ({
        id: row.dataset.rowId,
        kind: row.dataset.rowKind,
        hint:
          row.querySelector(":scope > .row-head > .hint")?.dataset.hint
          ?? null,
      }));
    })()
    """


def _label_order(label: str) -> list[int]:
    """Return one jump label as the position it was handed out at.

    Args:
        label: A painted jump label.

    Returns:
        The label's keys as their positions in the hint alphabet, which sort
        in the order the labels were handed out.
    """
    return [HINT_KEYS.index(key) for key in label]


def _head_top(browser: Browser, row_id: str) -> float:
    """Return how far down the window one row's head sits.

    Args:
        browser: The open browser.
        row_id: Identifier of the row to measure.

    Returns:
        The head's distance from the top of the document.
    """
    top = browser.evaluate(
        f"""
        (() => {{
          const head = document.querySelector(
            '[data-row-id="{row_id}"] > .row-head',
          );
          return head === null
            ? null
            : head.getBoundingClientRect().top + window.scrollY;
        }})()
        """
    )
    assert top is not None, f"the page never painted {row_id!r}"
    return float(top)


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_the_cursors_list_is_painted_in_exactly_its_own_order(
    browser: Browser,
) -> None:
    """Prove the one invariant every other navigation rests on.

    Labels are handed out in the outline's order, so reading them off the
    page in the browser's order and finding them ascending is the same as
    saying the two lists agree — for every row on an entirely opened page,
    conversations and evidence included.
    """
    browser.press("E")
    browser.run("wait", "300")
    browser.press("f")
    browser.run("wait", "200")

    painted: list[dict[str, Any]] = browser.evaluate(_PAINTED)

    assert len(painted) > 20, painted
    labels = [row["hint"] for row in painted]
    assert all(label is not None for label in labels), painted
    orders = [_label_order(str(label)) for label in labels]
    assert orders == sorted(orders), [row["id"] for row in painted]
    # And every kind of row is in there, so this is not a proof about updates.
    assert {row["kind"] for row in painted} >= {
        "update",
        "lane",
        "item",
        "thread",
        "evidence",
    }


def test_a_lanes_own_conversation_is_painted_under_its_head(
    browser: Browser,
) -> None:
    """Put the conversation next to the thing it is about.

    It used to be painted past every item in the lane, which on a long lane
    is nowhere near the header it belongs to.
    """
    browser.click_row(LANE)
    browser.run("wait", "200")

    lane_head = _head_top(browser, LANE)
    conversation = _head_top(browser, LANE_CHAT)
    first_item = _head_top(browser, FIRST_ITEM_OF_LANE)

    assert lane_head < conversation < first_item


def test_the_lane_chat_box_opens_where_the_lane_is(browser: Browser) -> None:
    """Open the box under the header the human clicked, not past the lane."""
    browser.click_row(LANE)
    browser.run("wait", "200")
    browser.compose_at(LANE)
    browser.run("wait", "200")

    placement = browser.evaluate(
        f"""
        (() => {{
          const body = document.querySelector(
            '[data-row-id="{LANE}"] > .row-body',
          );
          const children = [...(body?.children ?? [])];
          return {{
            box: children.findIndex((one) => one.matches("form.composer")),
            item: children.findIndex(
              (one) => one.dataset.rowId === "{FIRST_ITEM_OF_LANE}",
            ),
          }};
        }})()
        """
    )

    assert placement["box"] >= 0, placement
    assert placement["box"] < placement["item"], placement
