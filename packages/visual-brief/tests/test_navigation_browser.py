"""Real-browser proof of the ways a human gets around a long page.

Everything here is asserted as paint. A jump label that exists only in the
application's memory is not a label the human can type, an expand-all that
does not change what is on the screen has not expanded anything, and a key
that reaches a lane's chat has to open the same box its own button does.
"""

from __future__ import annotations

import json
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

_REVEAL_STATE = """
    (() => {
      const rows = [...document.querySelectorAll("[data-row-id]")];
      const button = document.querySelector(".meta-attention");
      return {
        painted: rows.map((row) => row.dataset.rowId),
        open: Object.fromEntries(
          rows.map((row) => [row.dataset.rowId, row.dataset.open]),
        ),
        threads: rows
          .filter((row) => row.dataset.rowKind === "thread")
          .map((row) => row.dataset.rowId),
        count: button?.dataset.attentionCount ?? null,
        pressed: button?.getAttribute("aria-pressed") ?? null,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
        query: document.querySelector("#brief-search")?.value ?? null,
        composer:
          document.querySelector(".composer")?.closest("[data-row-id]")
            ?.dataset.rowId ?? null,
        draft:
          document.querySelector(".composer textarea")?.value ?? null,
        overlay: document.querySelector('[role="search"]') === null
          ? "none" : "search",
        scroll: Math.round(window.scrollY),
      };
    })()
    """

_HUMAN_STATE_STORAGE = """
    (() => {
      const parts = ["chosen", "cursor", "drafts", "seen"];
      const keys = Object.keys(sessionStorage);
      return Object.fromEntries(parts.map((part) => {
        const key = keys.find(
          (candidate) =>
            candidate.startsWith("visual-brief-v2:")
            && candidate.endsWith(":" + part),
        );
        return [part, key === undefined ? null : sessionStorage.getItem(key)];
      }));
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
    """Bulk folds change choices without reconciling the human's cursor."""
    browser.press("E")
    browser.run("wait", "300")
    browser.press("g")
    browser.press("n")
    browser.run("wait", "250")
    opened = browser.evaluate(_FOLD_STATE)

    browser.press("C")
    browser.run("wait", "300")
    folded = browser.evaluate(_FOLD_STATE)

    browser.press("n")
    browser.run("wait", "300")
    revealed = browser.evaluate(_FOLD_STATE)

    browser.press("E")
    browser.run("wait", "300")
    reopened = browser.evaluate(_FOLD_STATE)

    assert opened["open"] == opened["rows"], opened
    assert opened["threads"] == 3, opened
    assert opened["cursor"] == AWAITING_THREAD, opened
    assert folded["openKinds"] == [], folded
    assert folded["open"] == 0, folded
    assert folded["threads"] == 0, folded
    assert folded["rows"] == 2, folded
    # The selected conversation is temporarily not painted. Collapse-all may
    # write fold choices, but it must not move the human-owned cursor.
    assert folded["cursor"] is None, folded
    assert revealed["cursor"] is not None, revealed
    assert revealed["threads"] > 0, revealed
    assert reopened["cursor"] == revealed["cursor"], reopened


def test_reveal_chats_then_restores_the_exact_fold_layout(
    browser: Browser,
) -> None:
    """Reveal in place, then restore every captured fold choice."""
    draft = "Keep this draft while conversations are revealed."
    browser.click_row(ANSWERED_THREAD)
    browser.compose_at(AWAITING_THREAD)
    browser.run("fill", ".composer textarea", draft)
    browser.press("Escape")
    browser.evaluate(
        "document.querySelector('.composer textarea')?.blur(); true"
    )
    browser.press("C")
    browser.run("wait", "300")
    browser.click_row("current-update")
    browser.press("/")
    browser.evaluate("document.querySelector('#brief-search')?.blur(); true")
    before = browser.evaluate(_REVEAL_STATE)
    storage_before = browser.evaluate(_HUMAN_STATE_STORAGE)

    browser.press("m")
    browser.run("wait", "300")
    after = browser.evaluate(_REVEAL_STATE)
    storage_after = browser.evaluate(_HUMAN_STATE_STORAGE)

    browser.press("m")
    browser.run("wait", "300")
    restored = browser.evaluate(_REVEAL_STATE)
    storage_restored = browser.evaluate(_HUMAN_STATE_STORAGE)

    threads = [AWAITING_THREAD, ANSWERED_THREAD, OTHER_AWAITING]
    reveal_ids = set()
    for thread in threads:
        current = thread
        while True:
            reveal_ids.add(current)
            split_at = max(current.rfind("#"), current.rfind("/"))
            if split_at <= 0:
                break
            current = current[:split_at]
    chosen_before = json.loads(storage_before["chosen"])
    chosen_after = json.loads(storage_after["chosen"])
    changed = {
        key
        for key in chosen_before.keys() | chosen_after.keys()
        if chosen_before.get(key) != chosen_after.get(key)
    }
    needed = {row for row in reveal_ids if chosen_before.get(row) is False}
    unrelated_before = set(before["open"]) - reveal_ids

    assert set(before["painted"]) <= set(after["painted"])
    assert after["threads"] == threads
    assert all(after["open"][thread] == "true" for thread in threads)
    assert all(
        after["open"][row] == before["open"][row]
        for row in unrelated_before
    )
    assert changed == needed
    assert all(chosen_after[row] is True for row in changed)
    assert storage_after["cursor"] == storage_before["cursor"]
    assert storage_after["drafts"] == storage_before["drafts"]
    assert storage_after["seen"] == storage_before["seen"]
    assert after["cursor"] == before["cursor"]
    assert after["query"] == before["query"]
    assert after["overlay"] == before["overlay"] == "search"
    assert after["scroll"] == before["scroll"]
    assert after["pressed"] == "true"
    assert after["count"] == "2"
    assert storage_restored == storage_before
    assert restored == before


def test_the_masthead_offers_the_same_reveal_action_to_the_mouse(
    browser: Browser,
) -> None:
    """Put the human's conversations in place from the masthead control."""
    browser.press("C")
    browser.run("click", ".meta-attention")
    browser.run("wait", "300")

    shown = browser.evaluate(_REVEAL_STATE)

    assert shown["pressed"] == "true", shown
    assert shown["threads"] == [
        AWAITING_THREAD,
        ANSWERED_THREAD,
        OTHER_AWAITING,
    ], shown

    browser.run("click", ".meta-attention")
    browser.run("wait", "300")
    restored = browser.evaluate(_REVEAL_STATE)

    assert restored["pressed"] == "false", restored
    assert restored["threads"] == [], restored


def test_a_long_question_keeps_the_thread_controls_on_one_header_line(
    browser: Browser,
) -> None:
    """Clip only the header copy while keeping the full question available."""
    question = (
        "Can the differential reader preserve every nested parser verdict "
        "while keeping this deliberately long conversation question readable?"
    )
    thread = next(
        thread
        for update in browser.data["updates"]
        for lane in update["lanes"]
        for thread in lane.get("questions", [])
        if thread["id"] == "q-parser-parity"
    )
    thread["turns"][0]["text"] = question
    browser.data["title"] = "Long question header regression"
    browser.publish()
    browser.wait_for_title("Long question header regression")
    browser.press("m")

    shown = browser.evaluate(
        f"""
        (() => {{
          const row = document.querySelector(
            '[data-row-id="{ANSWERED_THREAD}"]',
          );
          const title = row?.querySelector(".thread-title");
          const count = row?.querySelector(".row-count");
          const chat = row?.querySelector(".chat-button");
          const toggle = row?.querySelector(".row-toggle");
          const style = title === null ? null : getComputedStyle(title);
          return {{
            question: title?.textContent ?? null,
            whiteSpace: style?.whiteSpace ?? null,
            overflow: style?.overflow ?? null,
            ellipsis: style?.textOverflow ?? null,
            titleHeight: title?.getBoundingClientRect().height ?? 0,
            lineHeight: style === null ? 0 : parseFloat(style.lineHeight),
            countVisible: (count?.getClientRects().length ?? 0) > 0,
            chatVisible: (chat?.getClientRects().length ?? 0) > 0,
            bodyHasQuestion:
              row?.querySelector(".row-body")?.textContent
                ?.includes({json.dumps(question)}) ?? false,
            accessible:
              toggle?.textContent?.includes({json.dumps(question)}) ?? false,
          }};
        }})()
        """
    )

    assert shown["question"] == question
    assert shown["whiteSpace"] == "nowrap"
    assert shown["overflow"] == "hidden"
    assert shown["ellipsis"] == "ellipsis"
    assert shown["titleHeight"] <= shown["lineHeight"] * 1.1
    assert shown["countVisible"] is True
    assert shown["chatVisible"] is True
    assert shown["bodyHasQuestion"] is True
    assert shown["accessible"] is True


def test_typing_a_label_jumps_straight_to_that_row(browser: Browser) -> None:
    """Label every painted row and go to the one whose label was typed."""
    browser.press("f")
    browser.run("wait", "300")
    labelled = browser.evaluate(_HINT_STATE)

    wanted = labelled["labels"][LANE]
    for key in wanted:
        browser.press(key)
        browser.run("wait", "120")
    browser.run("wait", "200")
    jumped = browser.evaluate(_HINT_STATE)

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
    before = browser.evaluate(_HINT_STATE)["cursor"]
    browser.press("f")
    browser.run("wait", "250")
    assert browser.evaluate(_HINT_STATE)["labels"] != {}

    browser.press("Escape")
    browser.run("wait", "250")
    left = browser.evaluate(_HINT_STATE)

    assert left["labels"] == {}
    assert left["cursor"] == before


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
    browser.click_row(FIRST_ITEM)
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
    assert named["reveal-chats"] == ("m", "Reveal / restore chats")
    assert named["hints"] == ("f", "Jump to a row")
    assert named["next-row"] == ("j", "Next row")
    assert named["next-awaiting"] == ("n", "Next open chat")
    # The key bar has to say that chatting is not an item-only affordance.
    assert named["compose"] == ("c", "Chat here")
    listed = {key: meaning for key, meaning in help_rows}
    assert "E / C" in listed
    assert listed["f"].startswith("Label every row")
    assert "conversation" in listed["c"]
    assert listed["m"] == (
        "Reveal your chats, then restore the prior fold layout"
    )
    assert listed["n"] == "Jump to your next open chat"
