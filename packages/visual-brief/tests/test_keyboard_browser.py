"""Real-browser proof that the page, not the browser, owns the keyboard.

The cursor is application state rather than ``document.activeElement``, so the
two diverge the moment a mouse click parks the browser's focus on a control.
These press real keys in that state and assert the page's bindings still act
on the row the human can see the cursor on.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from browser_support import (
    AWAITING_THREAD,
    FIRST_ITEM,
    Browser,
    browser_session,
)


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


# The evidence fold, which is an ordinary row now: the control a keyboard
# reader tabs to is its head's toggle, exactly like every other row's.
EVIDENCE_FOLD = ".row-evidence > .row-head > .row-toggle"

_FOCUSED_FOLD = """
    (() => {
      const fold = document.activeElement;
      return {
        expanded: fold.getAttribute('aria-expanded'),
        control: fold.className,
        row: fold.closest('[data-row-id]')?.dataset.rowKind ?? null,
      };
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

_ARROW_SPY = """
    (() => {
      window.__arrows = [];
      document.addEventListener('keydown', (event) => {
        if (event.key.startsWith('Arrow')) {
          window.__arrows.push(event.defaultPrevented);
        }
      });
      return true;
    })()
    """

_CURSOR_IS_OPEN = """
    document.querySelector('[data-cursor="true"]').dataset.open
    """

_CHAT_BOX = """
    (() => {
      const box = document.querySelector(".composer");
      const cursor = document.querySelector('[data-cursor="true"]');
      const button = cursor?.querySelector(":scope > .row-head .chat-button");
      return {
        open: box !== null,
        label: box?.querySelector(".composer-label")?.textContent ?? "",
        affordance: button?.textContent ?? "",
      };
    })()
    """


def _painted_row_ids(browser: Browser) -> list[str]:
    """Return the identifiers of all rows in their current painted order."""
    rows = browser.evaluate(
        """
        [...document.querySelectorAll("[data-row-id]")].map(
          (row) => row.dataset.rowId,
        )
        """
    )
    return [str(row_id) for row_id in rows]


def _row_open(browser: Browser, row_id: str) -> str:
    """Return one painted row's open state."""
    return str(
        browser.evaluate(
            f'document.querySelector(\'[data-row-id="{row_id}"]\').dataset.open'
        )
    )


def test_the_shifted_keys_are_alive(browser: Browser) -> None:
    """Drive J, K, G and ? for real; all four were silently dead before."""
    browser.click_row(FIRST_ITEM)
    browser.press("J")
    browser.run("wait", "150")
    lane = browser.cursor_row()

    browser.press("K")
    browser.run("wait", "150")
    previous_lane = browser.cursor_row()

    browser.press("G")
    browser.run("wait", "150")
    bottom = browser.cursor_row()

    assert lane == "current-update/why-it-matters"
    assert previous_lane == "current-update/what-changed"
    assert bottom != previous_lane
    assert browser.evaluate(
        "document.querySelector('[role=\"dialog\"]') !== null"
    ) is False

    browser.press("?")
    browser.run("wait", "150")
    assert browser.evaluate(
        "document.querySelector('.help h2').textContent"
    ) == "Keyboard control"

    browser.press("Escape")
    browser.run("wait", "150")
    assert browser.evaluate(
        "document.querySelector('[role=\"dialog\"]') !== null"
    ) is False


def test_lowercase_keys_reach_their_own_targets(browser: Browser) -> None:
    """Keep g, n and space doing what the help overlay promises."""
    browser.press("G")
    browser.press("g")
    browser.run("wait", "150")
    assert browser.cursor_row() == "current-update"

    browser.press("n")
    browser.run("wait", "200")
    assert browser.cursor_row() == AWAITING_THREAD
    assert browser.evaluate(_CURSOR_IS_OPEN) == "true"

    browser.press(" ")
    browser.run("wait", "200")
    assert browser.evaluate(_CURSOR_IS_OPEN) == "false"


def test_writing_at_a_folded_update_types_into_the_composer(
    browser: Browser,
) -> None:
    """Land the caret in the box a human just asked for, on the first key.

    Folding an update and pressing ``a`` used to open an unfocused composer,
    so the sentence the human typed ran the navigation bindings instead of
    reaching the text box.
    """
    browser.press("g")
    browser.run("wait", "200")
    browser.press(" ")
    browser.run("wait", "300")
    assert browser.evaluate(_CURSOR_IS_OPEN) == "false"

    browser.press("a")
    browser.run("wait", "300")
    focused = browser.evaluate(
        """
        (() => {
          const box = document.querySelector('.composer textarea');
          return {
            present: box !== null,
            focused: box !== null && document.activeElement === box,
          };
        })()
        """
    )
    assert focused == {"present": True, "focused": True}

    before = browser.cursor_row()
    browser.run("keyboard", "type", "jkg")
    browser.run("wait", "200")

    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == "jkg"
    assert browser.cursor_row() == before


def test_space_belongs_to_the_page_not_to_a_focused_disclosure(
    browser: Browser,
) -> None:
    """Fold the cursor row even while a control holds the browser's focus.

    Standing aside for a focused control cost the page its own binding: the
    browser focuses a button when it is clicked, and the cursor is not the
    browser's focus, so Space aimed itself at whatever the mouse last touched.
    """
    browser.press("E")
    browser.run("scrollintoview", EVIDENCE_FOLD)
    browser.run("click", EVIDENCE_FOLD)
    browser.click_row(FIRST_ITEM)
    browser.run("focus", EVIDENCE_FOLD)
    before = browser.evaluate(_FOCUSED_FOLD)
    assert browser.cursor_row() == FIRST_ITEM

    browser.press(" ")
    browser.run("wait", "300")

    assert before == {
        "expanded": "false",
        "control": "row-toggle",
        "row": "evidence",
    }
    assert browser.evaluate(_FOCUSED_FOLD) == before
    assert browser.cursor_row() == FIRST_ITEM
    assert browser.evaluate(_CURSOR_IS_OPEN) == "true"


def test_enter_natively_opens_a_disclosure_tabbed_to(
    browser: Browser,
) -> None:
    """Keep a disclosure the human tabbed to operable from the keyboard.

    Enter belongs to the cursor elsewhere, but a focused row toggle remains a
    native button for a keyboard reader.
    """
    browser.press("E")
    browser.run("scrollintoview", EVIDENCE_FOLD)
    browser.run("click", EVIDENCE_FOLD)
    browser.run("focus", EVIDENCE_FOLD)
    browser.evaluate(_ENTER_SPY)
    before = browser.evaluate(_FOCUSED_FOLD)

    browser.press("Enter")
    browser.run("wait", "200")

    delivered = browser.evaluate("window.__enter")
    after = browser.evaluate(_FOCUSED_FOLD)
    assert delivered, "the browser delivered no Enter key press"
    assert not any(delivered), delivered
    assert before["expanded"] == "false"
    assert after["expanded"] == "true"


def test_enter_acts_at_the_cursor_not_an_invisible_focused_button(
    browser: Browser,
) -> None:
    """Do not press the masthead control after the cursor has moved away."""
    browser.click_row(FIRST_ITEM)
    rows = _painted_row_ids(browser)
    adjacent = rows[rows.index(FIRST_ITEM) + 1]
    browser.run(
        "focus",
        f'[data-row-id="{FIRST_ITEM}"] > .row-head > .row-toggle',
    )
    browser.press("j")
    browser.run("wait", "200")
    assert browser.cursor_row() == adjacent
    before = _row_open(browser, adjacent)
    browser.evaluate(_ENTER_SPY)

    browser.press("Enter")
    browser.run("wait", "250")

    assert browser.evaluate("window.__enter") == [True]
    assert browser.cursor_row() == adjacent
    assert _row_open(browser, adjacent) != before


def test_space_folds_the_cursor_row_after_the_mouse_moved_focus(
    browser: Browser,
) -> None:
    """Aim Space at the cursor, never at whatever the mouse last touched.

    Clicking a row head leaves Chrome's focus on that head. The cursor is not
    the browser's focus, so the two diverge the moment a key moves the cursor;
    Space has to follow the cursor, not the stranded focus.
    """
    browser.click_row(FIRST_ITEM)
    browser.run("wait", "200")
    rows = _painted_row_ids(browser)
    adjacent = rows[rows.index(FIRST_ITEM) + 1]
    assert browser.cursor_row() == FIRST_ITEM
    focused = browser.evaluate("document.activeElement.className")
    first_open = _row_open(browser, FIRST_ITEM)
    adjacent_open = _row_open(browser, adjacent)

    browser.press("j")
    browser.run("wait", "200")
    assert browser.cursor_row() == adjacent

    browser.press(" ")
    browser.run("wait", "300")

    assert focused == "row-toggle"
    assert browser.cursor_row() == adjacent
    assert _row_open(browser, FIRST_ITEM) == first_open
    assert _row_open(browser, adjacent) != adjacent_open


def test_the_arrow_keys_walk_the_page_with_the_pointer_resting_on_it(
    browser: Browser,
) -> None:
    """Move item by item on the real arrow keys, mouse parked on a row.

    Two things can make an arrow key look dead while ``j`` works. The browser
    can scroll instead, which happens the moment the page does not claim the
    key; and hover is selection, so a mouse resting on the page can put the
    cursor straight back where it was. This presses the real keys with a real
    pointer at rest and checks both: the page consumed every press, and the
    cursor walked and stayed walked.
    """
    rows = _painted_row_ids(browser)
    index = rows.index(FIRST_ITEM)
    adjacent = rows[index + 1]
    following = rows[index + 2]
    browser.run("scrollintoview", f'[data-row-id="{FIRST_ITEM}"]')
    browser.run("hover", f'[data-row-id="{FIRST_ITEM}"] > .row-head .row-toggle')
    browser.run("wait", "250")
    browser.evaluate(_ARROW_SPY)
    assert browser.cursor_row() == FIRST_ITEM

    browser.press("ArrowDown")
    browser.run("wait", "250")
    down = browser.cursor_row()

    browser.press("ArrowDown")
    browser.run("wait", "250")
    further = browser.cursor_row()

    browser.press("ArrowUp")
    browser.run("wait", "250")
    back = browser.cursor_row()

    claimed = browser.evaluate("window.__arrows")
    assert claimed == [True, True, True], claimed
    assert down == adjacent
    assert further == following
    assert back == adjacent


def test_the_chat_box_opens_on_c_and_still_on_the_old_a(
    browser: Browser,
) -> None:
    """Open the box the page now calls Chat from its documented key.

    ``a`` is kept as an undocumented alias because fingers remember it.
    """
    browser.click_row(FIRST_ITEM)
    browser.press("c")
    browser.run("wait", "300")
    on_c = browser.evaluate(_CHAT_BOX)

    browser.press("Escape")
    browser.run("wait", "200")
    closed = browser.evaluate("document.querySelector('.composer') !== null")

    browser.press("a")
    browser.run("wait", "300")
    on_a = browser.evaluate(_CHAT_BOX)

    assert on_c == {
        "open": True,
        "label": "Chat about this section",
        "affordance": "Chat",
    }
    assert closed is False
    assert on_a == on_c


def test_keys_are_inert_while_a_question_is_being_typed(
    browser: Browser,
) -> None:
    """Let the human write the letter j without losing their place."""
    browser.click_row(FIRST_ITEM)
    browser.press("a")
    browser.run("wait", "200")
    browser.run("type", ".composer textarea", "j and k and J")
    browser.run("wait", "150")

    assert browser.cursor_row() == FIRST_ITEM
    assert browser.evaluate(
        "document.querySelector('.composer textarea').value"
    ) == "j and k and J"

    browser.press("Escape")
    browser.run("wait", "150")
    assert browser.evaluate("document.querySelector('.composer') !== null")
    browser.press("Escape")
    browser.run("wait", "150")
    assert browser.evaluate("document.querySelector('.composer') !== null") is False

    rows = _painted_row_ids(browser)
    adjacent = rows[rows.index(FIRST_ITEM) + 1]
    browser.press("j")
    browser.run("wait", "150")
    assert browser.cursor_row() == adjacent
