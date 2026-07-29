"""Real-browser proof that a message survives the publish that follows it.

A question sent seconds before the daemon republishes used to lose its place
and its sign in the repaint that followed, because the publish threw the page
away. It no longer does: the new document is patched into the page the human
is reading. The sign is retired the moment those exact words appear on the
page. A delayed fold adds a diagnostic without opening a gap in that sign, and
the reader is left exactly where they were throughout.

What a tab does when it cannot make sense of the daemon at all lives next
door, in ``test_healing_browser``.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from browser_support import (
    AWAITING_THREAD,
    FIRST_ITEM,
    Browser,
    browser_session,
    landing_at,
)

ITEM = FIRST_ITEM
OLDER_AWAITING_THREAD = (
    "review-round-four/round-four-change/three-verdict-contract"
    "#q-unsupported-valid"
)

_WAITING_RAILS = """
    (() => {
      const marked = [
        ...document.querySelectorAll("[data-waiting]"),
      ].flatMap((row) => {
        const head = row.querySelector(":scope > .row-head");
        return head === null
          ? []
          : [{
            id: row.dataset.rowId,
            strength: row.dataset.waiting,
            color: getComputedStyle(head).borderLeftColor,
          }];
      });
      return {
        marked,
        chips: document.querySelectorAll(".chip-awaiting").length,
        labels: [...document.querySelectorAll(".row-head")].filter(
          (head) => head.textContent.includes("Awaiting answer"),
        ).length,
      };
    })()
    """

FOLD_MAP = {
    "current-update": "true",
    "current-update/what-changed": "false",
    "current-update/why-it-matters": "true",
    "review-round-four": "true",
    "review-round-four/round-four-change": "false",
    "review-round-four/round-four-next": "true",
}


def _note_state(row_id: str) -> str:
    """Return a script reading the waiting note one row is showing.

    Args:
        row_id: Identifier of the row that was written against.

    Returns:
        JavaScript returning what a human sees of the note and its sign.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const note = row?.querySelector(
        ':scope > .row-body > [data-pending="true"]',
      )
        ?? null;
      const stalled = row?.querySelector(":scope > .row-body > p.stalled")
        ?? null;
      return {{
        note: note === null ? null : note.textContent,
        stalled: note === null ? null : note.dataset.stalled,
        working: row === null
          ? 0
          : row.querySelectorAll(":scope > .row-body > p.working").length,
        text: stalled === null ? null : stalled.textContent,
      }};
    }})()
    """


def _thread_sign(row_id: str) -> str:
    """Return a script reading one conversation's own waiting sign.

    Args:
        row_id: Identifier of the conversation row.

    Returns:
        JavaScript returning the sign and the page's remaining notes.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      return {{
        present: row !== null,
        working: row === null
          ? 0
          : row.querySelectorAll(":scope > .row-body > p.working").length,
        notes: document.querySelectorAll('[data-pending="true"]').length,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
      }};
    }})()
    """


_PLACE = """
    (() => ({
      scroll: Math.round(window.scrollY),
      cursor:
        document.querySelector('[data-cursor="true"]')?.dataset.rowId ?? null,
    }))()
    """


def _set_fold(browser: Browser, row_id: str, open_state: str) -> None:
    """Set one disclosure through its real control."""
    script = (
        f'document.querySelector(\'[data-row-id="{row_id}"]\')'
        ".dataset.open"
    )
    if browser.evaluate(script) != open_state:
        browser.click_row(row_id)
    assert browser.evaluate(script) == open_state


def _fold_map(browser: Browser) -> dict[str, str]:
    """Read the arbitrary disclosures used by the live-publish proof."""
    return {
        row_id: str(
            browser.evaluate(
                f'document.querySelector(\'[data-row-id="{row_id}"]\')'
                ".dataset.open"
            )
        )
        for row_id in FOLD_MAP
    }


def _fold_into_content(
    browser: Browser,
    thread_id: str,
    text: str,
    at: str,
    anchor: str = ITEM,
) -> None:
    """Publish a queued question as the conversation the daemon folds it into.

    The id is deliberately nothing the page could have guessed: what the page
    recognizes is the queue line itself, the words and the instant, wherever
    they end up and whatever they end up called.

    Args:
        browser: The open browser, whose ``data`` is the served document.
        thread_id: Identifier the fold gives the conversation.
        text: What the human asked.
        at: Timestamp the daemon stamped the queue line with.
        anchor: Item path the conversation hangs from.
    """
    update_id, lane_id, item_id = anchor.split("/")
    item: dict[str, Any] = next(
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
            "anchor": {"kind": "element", "path": anchor},
            "turns": [{"author": "human", "text": text, "at": at}],
        }
    )


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_a_folded_older_update_names_the_waiting_work_accessibly(
    browser: Browser,
) -> None:
    """The quiet contained rail has a non-colour description."""
    described = browser.evaluate(
        """
        (() => {
          const row = document.querySelector(
            '[data-row-id="review-round-four"]',
          );
          const toggle = row.querySelector(":scope > .row-head .row-toggle");
          const description = document.getElementById(
            toggle.getAttribute("aria-describedby"),
          );
          return {
            open: row.dataset.open,
            waiting: row.dataset.waiting,
            description: description?.textContent ?? null,
          };
        })()
        """
    )

    assert described == {
        "open": "false",
        "waiting": "contained",
        "description": "Contains a conversation waiting for an agent answer.",
    }


def test_the_waiting_sign_clears_when_those_words_appear_on_the_page(
    browser: Browser,
) -> None:
    """Retire a sent question by its own queue line, not by any id.

    The daemon folds a queued question into the document under an id of its
    own choosing. The page matches on what the queue line says — the verbatim
    text and the timestamp — so the sign clears wherever those words land.
    """
    question = "Does the sign clear itself?"
    browser.compose_at(ITEM)
    browser.send(question)
    browser.read_until(landing_at(ITEM), lambda seen: seen["note"] is not None)
    before = browser.evaluate(_note_state(ITEM))
    stamp = browser.server.stamps[0]

    _fold_into_content(browser, "q-folded-under-another-name", question, stamp)
    browser.data["title"] = "Folded into the document"
    browser.publish()
    browser.wait_for_title("Folded into the document")
    folded = f"{ITEM}#q-folded-under-another-name"
    after = browser.read_until(
        _thread_sign(folded), lambda seen: seen["present"] is True
    )

    assert before["note"] is not None
    assert before["working"] == 1, before
    assert after["notes"] == 0, after
    # One sign, now the document's own, under the conversation it belongs to.
    assert after["working"] == 1, after
    # The human was never taken anywhere, so there is nowhere to put them
    # back: the cursor is on the row they were writing at, as it was before.
    assert after["cursor"] == ITEM, after


def test_a_delayed_question_keeps_the_sign_with_its_diagnostic(
    browser: Browser,
) -> None:
    """Add the truthful diagnostic without flapping the working sign."""
    question = "Where did this one go?"
    browser.compose_at(ITEM)
    browser.send(question)
    browser.read_until(landing_at(ITEM), lambda seen: seen["note"] is not None)

    browser.data["title"] = "Published without the question"
    browser.publish()
    browser.wait_for_title("Published without the question")
    carried = browser.read_until(
        _note_state(ITEM), lambda seen: seen["note"] is not None
    )
    stalled = browser.read_until(
        _note_state(ITEM), lambda seen: seen["stalled"] == "true", timeout=8
    )

    # The sign came through the reload with the page rather than being lost.
    assert carried["note"] is not None and question in carried["note"]
    assert stalled["stalled"] == "true", stalled
    assert stalled["working"] == 1, stalled
    assert stalled["text"] == "submitted — refresh if this persists", stalled


def test_a_publish_does_not_move_the_page_under_the_reader(
    browser: Browser,
) -> None:
    """Leave the reader exactly where they were, scroll and cursor alike.

    A publish used to hand the human a page scrolled somewhere else entirely,
    because it handed them a different page. It no longer does: the new
    conversation appears where it belongs and nothing else moves at all. This
    is deliberately measured deep in the document, where a reload's scroll
    restoration had the furthest to throw them.
    """
    deep = "review-round-four/round-four-next/manual-parity-review"
    question = "Does the page stay where it is?"
    browser.press("E")
    browser.run("wait", "400")
    browser.compose_at(deep)
    browser.send(question)
    browser.read_until(landing_at(deep), lambda seen: seen["note"] is not None)
    stamp = browser.server.stamps[0]
    # One key press hands the cursor back to the keyboard, so nothing sliding
    # under a stationary mouse can move it while this is being measured.
    browser.press("j")
    browser.run("wait", "400")
    before = browser.evaluate(_PLACE)

    _fold_into_content(browser, "q-deep-fold", question, stamp, anchor=deep)
    browser.data["title"] = "Published while reading at the bottom"
    browser.publish()
    browser.wait_for_title("Published while reading at the bottom")
    folded = f"{deep}#q-deep-fold"
    browser.wait_for_row(folded)
    after = browser.evaluate(_PLACE)

    assert before["cursor"] is not None, before
    assert after == before, (before, after)


def test_a_publish_preserves_the_readers_fold_map(browser: Browser) -> None:
    """Keep several independently chosen disclosures open and closed."""
    for row_id, open_state in FOLD_MAP.items():
        _set_fold(browser, row_id, open_state)
    before = _fold_map(browser)

    browser.data["title"] = "Published with a hand-picked fold map"
    browser.publish()
    browser.wait_for_title("Published with a hand-picked fold map")
    after = _fold_map(browser)

    assert before == FOLD_MAP
    assert after == before


def test_a_live_patch_preserves_unchanged_row_node_identity(
    browser: Browser,
) -> None:
    """Keep an unchanged row's exact DOM node through an unrelated patch."""
    stored = browser.evaluate(
        f"""
        (() => {{
          window.__unchangedRow = document.querySelector(
            '[data-row-id="{ITEM}"]',
          );
          return window.__unchangedRow !== null;
        }})()
        """
    )

    browser.data["title"] = "Published without changing the stored row"
    browser.publish()
    browser.wait_for_title("Published without changing the stored row")
    same_node = browser.evaluate(
        f"""
        (() => window.__unchangedRow?.isSameNode(
          document.querySelector('[data-row-id="{ITEM}"]'),
        ) ?? false)()
        """
    )

    assert stored is True
    assert same_node is True


def test_waiting_paints_one_direct_rail_and_quiet_container_rails(
    browser: Browser,
) -> None:
    """Put the alarm on its conversation and containment on ancestors."""
    browser.press("E")
    browser.run("wait", "200")
    waiting = browser.evaluate(_WAITING_RAILS)
    marked = waiting["marked"]
    direct_ids = [
        row["id"] for row in marked if row["strength"] == "direct"
    ]
    contained_ids = {
        "current-update",
        "current-update/why-it-matters",
        "current-update/why-it-matters/repair-loop-routing",
        "review-round-four",
        "review-round-four/round-four-change",
        "review-round-four/round-four-change/three-verdict-contract",
    }
    by_id = {row["id"]: row for row in marked}

    assert sorted(direct_ids) == sorted(
        [AWAITING_THREAD, OLDER_AWAITING_THREAD]
    ), waiting
    assert set(by_id) == contained_ids | set(direct_ids), waiting
    assert all(
        by_id[row_id]["strength"] == "contained"
        for row_id in contained_ids
    ), waiting
    direct_colors = {by_id[row_id]["color"] for row_id in direct_ids}
    contained_colors = {
        by_id[row_id]["color"] for row_id in contained_ids
    }
    assert direct_colors.isdisjoint(contained_colors), waiting
    assert len(contained_colors) == 1, waiting
    assert waiting["chips"] == 0, waiting
    assert waiting["labels"] == 0, waiting
