"""Real-browser proof of the ways a human gets around a long page.

Everything here is asserted as paint. A jump label that exists only in the
application's memory is not a label the human can type, an expand-all that
does not change what is on the screen has not expanded anything, and a key
that reaches a lane's chat has to open the same box its own button does.
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

LANE = "current-update/what-changed"
ANSWERED_THREAD = "current-update/what-i-verified#q-parser-parity"
OTHER_AWAITING = (
    "review-round-four/round-four-change/three-verdict-contract"
    "#q-unsupported-valid"
)

_FOLD_STATE = """
    (() => {
      const rows = [...document.querySelectorAll("[data-row-id]")];
      const open = rows.filter((row) => row.dataset.open === "true");
      return {
        rows: rows.length,
        open: open.length,
        openKinds: [...new Set(open.map((row) => row.dataset.rowKind))].sort(),
        threads: rows.filter(
          (row) => row.dataset.rowKind === "thread",
        ).length,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
      };
    })()
    """

_CHATS_STATE = """
    (() => {
      const rows = [...document.querySelectorAll("[data-row-id]")];
      const button = document.querySelector(".meta-chats");
      return {
        painted: rows.map((row) => row.dataset.rowId),
        threads: rows
          .filter((row) => row.dataset.rowKind === "thread")
          .map((row) => row.dataset.rowId),
        count: button?.dataset.chatsCount ?? null,
        pressed: button?.getAttribute("aria-pressed") ?? null,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
      };
    })()
    """

_HINT_STATE = """
    (() => {
      const rows = [...document.querySelectorAll("[data-row-id]")];
      const labels = {};
      for (const row of rows) {
        const hint = row.querySelector(":scope > .row-head > .hint");
        if (hint !== null) {
          labels[row.dataset.rowId] = hint.dataset.hint;
        }
      }
      const painted = rows.find(
        (row) => row.querySelector(":scope > .row-head > .hint") !== null,
      );
      const style = painted === undefined
        ? null
        : getComputedStyle(
            painted.querySelector(":scope > .row-head > .hint"),
          );
      return {
        rows: rows.length,
        labels,
        widths: [...new Set(Object.values(labels).map((one) => one.length))],
        background: style === null ? null : style.backgroundColor,
        colour: style === null ? null : style.color,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
      };
    })()
    """

_ORDINALS = """
    (() => {
      const marks = [...document.querySelectorAll("[data-row-id]")].map(
        (row) => [
          row.dataset.rowId,
          row.querySelector(":scope > .row-head > .ordinal")?.textContent
            ?? null,
        ],
      );
      return {
        numbered: marks.filter(([, mark]) => mark !== null),
        onFolded: marks.filter(
          ([id, mark]) =>
            mark !== null
            && document.querySelector(
              '[data-row-id="' + id + '"]',
            ).dataset.rowKind !== "item",
        ).length,
      };
    })()
    """

_CHAT_BOX = """
    (() => {
      const box = document.querySelector(".composer");
      return {
        open: box !== null,
        anchor: box?.dataset.anchorId ?? null,
        parent: box?.dataset.parentId ?? null,
        label: box?.querySelector(".composer-label")?.textContent ?? null,
        row: box?.closest("[data-row-id]")?.dataset.rowId ?? null,
      };
    })()
    """

_KEY_BAR = """
    [...document.querySelectorAll(".keybar .key-control")].map((control) => [
      control.dataset.action,
      control.querySelector("kbd").textContent,
      control.querySelector("span").textContent,
    ])
    """

_HELP_KEYS = """
    [...document.querySelectorAll(".help .help-row")].map((row) => [
      row.querySelector("dt kbd").textContent,
      row.querySelector("dd").textContent,
    ])
    """


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_one_key_opens_the_whole_page_and_another_folds_it_back(
    browser: Browser,
) -> None:
    """Open everything, then fold back to lanes, and watch the page do it."""
    browser.press("E")
    browser.run("wait", "300")
    opened = browser.evaluate(_FOLD_STATE)

    browser.press("C")
    browser.run("wait", "300")
    folded = browser.evaluate(_FOLD_STATE)

    assert opened["open"] == opened["rows"], opened
    assert opened["threads"] == 3, opened
    assert folded["openKinds"] == ["update"], folded
    assert folded["open"] == 2, folded
    assert folded["threads"] == 0, folded
    # Lanes stay on the page: a fold that hid them would leave the reader
    # holding two headlines and nowhere to go.
    assert folded["rows"] == 9, folded
    # And the cursor comes up with it rather than folding out of sight.
    assert folded["cursor"] == LANE, folded


def test_the_chats_view_finds_conversations_folding_took_away(
    browser: Browser,
) -> None:
    """Surface every conversation the human wrote in, answered or not."""
    browser.press("C")
    browser.run("wait", "300")
    assert browser.evaluate(_FOLD_STATE)["threads"] == 0

    browser.press("m")
    browser.run("wait", "300")
    inside = browser.evaluate(_CHATS_STATE)

    browser.press("j")
    browser.run("wait", "250")
    stepped = browser.cursor_row()

    browser.press("m")
    browser.run("wait", "300")
    outside = browser.evaluate(_CHATS_STATE)

    assert inside["threads"] == [
        AWAITING_THREAD,
        ANSWERED_THREAD,
        OTHER_AWAITING,
    ], inside
    assert inside["count"] == "3", inside
    assert inside["pressed"] == "true", inside
    # Nothing on the page that is not a conversation or the road to one.
    assert all(
        any(thread.startswith(row) for thread in inside["threads"])
        for row in inside["painted"]
    ), inside
    assert inside["cursor"] == AWAITING_THREAD, inside
    assert stepped == ANSWERED_THREAD
    assert outside["pressed"] == "false", outside
    assert len(outside["painted"]) > len(inside["painted"]), outside


def test_escape_leaves_the_chats_view(browser: Browser) -> None:
    """Give the view the exit every other surface on the page has."""
    browser.press("m")
    browser.run("wait", "300")
    assert browser.evaluate(_CHATS_STATE)["pressed"] == "true"

    browser.press("Escape")
    browser.run("wait", "300")

    assert browser.evaluate(_CHATS_STATE)["pressed"] == "false"


def test_the_masthead_offers_the_same_view_to_a_hand_on_the_mouse(
    browser: Browser,
) -> None:
    """Put the human's own conversations one click from the top of the page."""
    browser.run("click", ".meta-chats")
    browser.run("wait", "300")

    shown = browser.evaluate(_CHATS_STATE)

    assert shown["pressed"] == "true", shown
    assert shown["threads"] == [
        AWAITING_THREAD,
        ANSWERED_THREAD,
        OTHER_AWAITING,
    ], shown


def test_typing_a_label_jumps_straight_to_that_row(browser: Browser) -> None:
    """Label every painted row and go to the one whose label was typed."""
    before = browser.cursor_row()
    browser.press("f")
    browser.run("wait", "300")
    labelled = browser.evaluate(_HINT_STATE)

    wanted = labelled["labels"][LANE]
    for key in wanted:
        browser.press(key)
        browser.run("wait", "120")
    browser.run("wait", "200")
    jumped = browser.evaluate(_HINT_STATE)

    assert before == FIRST_ITEM
    assert len(labelled["labels"]) == labelled["rows"], labelled
    # One length for the whole page, so no label is a prefix of another and
    # no typed label has to wait to see whether more is coming.
    assert len(labelled["widths"]) == 1, labelled
    # Painted state, not a tooltip: the label is drawn, in the page's colours.
    assert labelled["background"] not in (None, "rgba(0, 0, 0, 0)"), labelled
    assert jumped["labels"] == {}, jumped
    assert jumped["cursor"] == LANE, jumped


def test_escape_takes_the_labels_away_without_moving(browser: Browser) -> None:
    """Let a human change their mind about jumping."""
    browser.press("f")
    browser.run("wait", "250")
    assert browser.evaluate(_HINT_STATE)["labels"] != {}

    browser.press("Escape")
    browser.run("wait", "250")
    left = browser.evaluate(_HINT_STATE)

    assert left["labels"] == {}
    assert left["cursor"] == FIRST_ITEM


def test_the_page_numbers_what_it_is_showing_so_it_can_be_cited(
    browser: Browser,
) -> None:
    """Number the visible items, and only the visible items."""
    browser.press("E")
    browser.run("wait", "300")
    opened = browser.evaluate(_ORDINALS)

    browser.press("C")
    browser.run("wait", "300")
    folded = browser.evaluate(_ORDINALS)

    numbers = [mark for _, mark in opened["numbered"]]
    assert numbers == [str(one) for one in range(1, len(numbers) + 1)]
    assert len(numbers) == 12, opened
    assert opened["onFolded"] == 0, opened
    # Folded content carries no number, which is what makes a number readable
    # off the screen at all.
    assert folded["numbered"] == [], folded


def test_a_lane_chat_opens_from_the_keyboard_as_it_does_from_its_button(
    browser: Browser,
) -> None:
    """Reach with keys every granularity the mouse can chat at.

    The rule this enforces: whatever the mouse can start a conversation
    against, ``J``/``K`` onto that row and ``c`` starts the same one.
    """
    browser.press("J")
    browser.run("wait", "250")
    assert browser.cursor_row() == "current-update/why-it-matters"

    browser.press("K")
    browser.run("wait", "250")
    assert browser.cursor_row() == LANE

    browser.press("c")
    browser.run("wait", "300")
    from_keys = browser.evaluate(_CHAT_BOX)

    browser.press("Escape")
    browser.run("wait", "250")
    browser.compose_at(LANE)
    browser.run("wait", "300")
    from_mouse = browser.evaluate(_CHAT_BOX)

    assert from_keys == {
        "open": True,
        "anchor": LANE,
        "parent": None,
        "label": "Chat about this section",
        "row": LANE,
    }
    assert from_mouse == from_keys


def test_a_conversation_chat_opens_from_the_keyboard_too(
    browser: Browser,
) -> None:
    """Continue a conversation from the keys, parent thread and all."""
    browser.press("n")
    browser.run("wait", "300")
    assert browser.cursor_row() == AWAITING_THREAD

    browser.press("c")
    browser.run("wait", "300")

    assert browser.evaluate(_CHAT_BOX) == {
        "open": True,
        "anchor": "current-update/why-it-matters/repair-loop-routing",
        "parent": "q-malformed-unsupported",
        "label": "Chat in this conversation",
        "row": AWAITING_THREAD,
    }


def test_the_page_says_which_keys_do_the_new_things(browser: Browser) -> None:
    """Name every new key where the human looks for keys."""
    bar = browser.evaluate(_KEY_BAR)

    browser.press("?")
    browser.run("wait", "250")
    help_rows = browser.evaluate(_HELP_KEYS)

    named = {action: (key, label) for action, key, label in bar}
    assert named["expand-all"] == ("E", "Expand all")
    assert named["collapse-all"] == ("C", "Collapse all")
    assert named["chats"] == ("m", "My chats")
    assert named["hints"] == ("f", "Jump to a row")
    # The key bar has to say that chatting is not an item-only affordance.
    assert named["compose"] == ("c", "Chat here")
    listed = {key: meaning for key, meaning in help_rows}
    assert "E / C" in listed
    assert listed["f"].startswith("Label every row")
    assert "conversation" in listed["c"]
    assert listed["m"] == "Show every conversation you have written in"
