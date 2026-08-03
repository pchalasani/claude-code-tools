"""Real-browser proof that the deepest layer answers the keyboard.

Raw evidence used to be a fold with its own private open state, which meant
the layer an item's claims rest on could only be opened with a mouse. It is a
row now, so the keys that reach everything else reach it: a jump label to go
there, the fold key to open it, and the whole-page commands to take it with
them.

Everything here is driven with real keys and read off the painted page.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from browser_support import Browser, browser_session

LANE = "current-update/what-i-verified"
ITEM = f"{LANE}/twelve-inputs"
# A row the page invents for itself opens its segment with '~', which no
# document identifier may hold, and a note is named for itself rather than
# for the slot it happens to occupy.
EVIDENCE = f"{ITEM}#~evidence"
NOTE = f"{EVIDENCE}#~~agreement-rule"
DEEPER = f"{NOTE}#~~direction-one"


def _open_item(browser: Browser) -> None:
    """Open the lane and then the item whose evidence this suite reads.

    Args:
        browser: The open browser.
    """
    browser.click_row(ITEM)
    browser.run("wait", "200")


def _jump_to(browser: Browser, row_id: str) -> None:
    """Arm the jump labels and type the one this row is wearing.

    Args:
        browser: The open browser.
        row_id: Identifier of the row to jump to.
    """
    browser.press("f")
    browser.run("wait", "150")
    label = browser.evaluate(
        f"""
        document.querySelector('[data-row-id="{row_id}"] .hint')
          ?.dataset.hint ?? null
        """
    )
    assert label, f"no jump label on {row_id!r}"
    for key in str(label):
        browser.press(key)
    browser.run("wait", "200")


def _row_state(row_id: str) -> str:
    """Return a script reading whether one row is present, open and current.

    Args:
        row_id: Identifier of the row to read.

    Returns:
        JavaScript returning what a human sees of that row.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      return {{
        present: row !== null,
        open: row?.dataset.open ?? null,
        cursor: row?.dataset.cursor ?? null,
        head: row?.querySelector(":scope > .row-head")?.textContent ?? null,
      }};
    }})()
    """


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_a_jump_label_and_the_fold_key_open_the_raw_evidence(
    browser: Browser,
) -> None:
    """Reach the evidence, and open it, without touching the mouse."""
    _open_item(browser)
    folded = browser.evaluate(_row_state(EVIDENCE))

    _jump_to(browser, EVIDENCE)
    landed = browser.evaluate(_row_state(EVIDENCE))
    browser.press(" ")
    browser.run("wait", "250")
    opened = browser.evaluate(_row_state(EVIDENCE))
    evidence = browser.evaluate(
        f"""
        document.querySelector('[data-row-id="{EVIDENCE}"] pre.evidence')
          ?.textContent ?? null
        """
    )

    assert folded["present"] is True, folded
    assert folded["open"] == "false", folded
    assert landed["cursor"] == "true", landed
    assert opened["open"] == "true", opened
    assert evidence is not None and evidence.strip() != ""


def test_the_keyboard_reaches_a_note_nested_inside_a_note(
    browser: Browser,
) -> None:
    """Walk down the nesting one fold at a time, all from the keyboard."""
    _open_item(browser)
    _jump_to(browser, EVIDENCE)
    browser.press(" ")
    browser.run("wait", "250")

    _jump_to(browser, NOTE)
    note = browser.evaluate(_row_state(NOTE))
    browser.press(" ")
    browser.run("wait", "250")
    deeper = browser.evaluate(_row_state(DEEPER))

    assert note["cursor"] == "true", note
    assert "Agreement rule" in (note["head"] or ""), note
    assert deeper["present"] is True, deeper
    assert deeper["open"] == "false", deeper


def test_expand_all_and_collapse_all_take_the_evidence_with_them(
    browser: Browser,
) -> None:
    """Make the whole-page commands mean the whole page."""
    browser.press("E")
    browser.run("wait", "400")
    opened = browser.evaluate(_row_state(DEEPER))

    browser.press("C")
    browser.run("wait", "400")
    collapsed = browser.evaluate(_row_state(EVIDENCE))

    assert opened["present"] is True, opened
    assert opened["open"] == "true", opened
    assert collapsed["present"] is False, collapsed


def test_the_toggle_names_the_one_body_it_opens(
    browser: Browser,
) -> None:
    """Give assistive technology one id reference, not a list of absences.

    ``aria-controls`` is a whitespace-separated list of id references, so a
    space anywhere in a row id would send a screen reader looking for several
    elements that do not exist instead of the body the toggle opens.
    """
    _open_item(browser)
    _jump_to(browser, EVIDENCE)
    browser.press(" ")
    browser.run("wait", "250")

    named = browser.evaluate(
        f"""
        (() => {{
          const toggle = document.querySelector(
            '[data-row-id="{EVIDENCE}"] > .row-head > .row-toggle',
          );
          const value = toggle?.getAttribute("aria-controls") ?? "";
          return {{
            value,
            spaced: value.includes(" "),
            found: value !== "" && document.getElementById(value) !== null,
          }};
        }})()
        """
    )

    assert named["spaced"] is False, named
    assert named["found"] is True, named


def test_the_masthead_still_counts_what_it_always_counted(
    browser: Browser,
) -> None:
    """Leave the page's own arithmetic alone: evidence is not an item."""
    counts = browser.evaluate(
        """
        [...document.querySelectorAll(".meta-count")].map(
          (count) => count.textContent,
        )
        """
    )

    assert counts[:3] == ["2 updates", "10 lanes", "15 items"], counts
