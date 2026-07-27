"""Real-browser proof that a tab left open looks after itself.

Two failures reported from live use are pinned here. A tab open across an
upgrade of the daemon met a ``render-version`` answer it could not read, said
nothing, and sat showing a working sign until it was reloaded by hand. And a
question sent seconds before the daemon republished lost its place and its
sign in the repaint that followed.

So the page reloads itself out of a conversation it cannot follow — once, not
in a loop — keeps a transient outage readable, carries the waiting sign across
the reload, retires it the moment those exact words appear on the page, and
stops promising progress when they never do.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from browser_support import FIRST_ITEM, Browser, browser_session, landing_at

ITEM = FIRST_ITEM

_MARK = "window.__watched = true"
_STILL_MARKED = "(() => window.__watched === true)()"

_READABLE = """
    (() => {
      const rows = [...document.querySelectorAll("[data-row-id]")];
      return {
        rows: rows.length,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
      };
    })()
    """

_CHIP_MOTION = """
    (() => {
      const arriving = {};
      for (const chip of document.querySelectorAll(".chip-awaiting")) {
        const head = chip.closest(".row-head");
        const row = chip.closest("[data-row-id]");
        if (head === null || row === null) {
          continue;
        }
        const style = getComputedStyle(chip);
        arriving[row.dataset.rowKind] = {
          name: style.animationName,
          delay: style.animationDelay,
        };
      }
      return arriving;
    })()
    """


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
      const note = row?.querySelector(":scope > .row-body > p.pending")
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
        notes: document.querySelectorAll("p.pending").length,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
      }};
    }})()
    """


def _placement(row_id: str) -> str:
    """Return a script reading where one row sits in the window.

    Args:
        row_id: Identifier of the row to measure.

    Returns:
        JavaScript returning the row's position on the screen.
    """
    return f"""
    (() => {{
      const head = document.querySelector(
        '[data-row-id="{row_id}"] > .row-head',
      );
      if (head === null) {{
        return {{ present: false }};
      }}
      const box = head.getBoundingClientRect();
      return {{
        present: true,
        top: box.top,
        bottom: box.bottom,
        height: window.innerHeight,
        cursor:
          document.querySelector('[data-cursor="true"]')?.dataset.rowId
          ?? null,
      }};
    }})()
    """


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


def test_a_page_the_daemon_stopped_speaking_to_reloads_itself_once(
    browser: Browser,
) -> None:
    """Replace a stranded page, and then leave it alone.

    An answer the client cannot read means the two ends have drifted apart,
    and the page is the end that can be replaced. But a page that comes back
    exactly as unintelligible must stay readable instead of reloading forever.
    """
    browser.evaluate(_MARK)
    browser.server.version_body = "visual-brief 2: generation eyJ2IjoyfQ"

    healed = browser.read_until(_STILL_MARKED, lambda seen: seen is False)

    browser.evaluate(_MARK)
    browser.run("wait", "1500")
    settled = browser.evaluate(_STILL_MARKED)
    readable = browser.evaluate(_READABLE)

    assert healed is False, "the stranded page never reloaded itself"
    assert settled is True, "the page reloaded itself in a loop"
    assert readable["rows"] > 0
    assert readable["cursor"] == FIRST_ITEM


def test_a_daemon_that_stops_answering_does_not_reload_anything(
    browser: Browser,
) -> None:
    """Keep a page readable while the local server is simply not there.

    Not being answered is different from being answered incomprehensibly: a
    saved page opened without its daemon has to stay put and stay readable.
    """
    browser.evaluate(_MARK)
    browser.server.version_status = 404

    browser.run("wait", "1500")

    assert browser.evaluate(_STILL_MARKED) is True
    assert browser.evaluate(_READABLE)["rows"] > 0


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
    assert before["stalled"] == "false"
    assert after["notes"] == 0, after
    # One sign, now the document's own, under the conversation it belongs to.
    assert after["working"] == 1, after
    # And the human is put back where they were writing, not at the top.
    assert after["cursor"] == folded, after


def test_a_question_that_never_appears_stops_promising_progress(
    browser: Browser,
) -> None:
    """Degrade to a plain statement rather than spinning forever."""
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
    assert stalled["working"] == 0, stalled
    assert stalled["text"] == "submitted — refresh if this persists", stalled


def test_the_reload_after_a_send_comes_back_where_the_human_was_writing(
    browser: Browser,
) -> None:
    """Return the reader to their conversation, in reading position.

    The reload a send causes used to hand the human a page scrolled somewhere
    else entirely. The page they get back opens on the conversation they just
    wrote in, and puts it where it can be read.
    """
    deep = "review-round-four/round-four-next/manual-parity-review"
    question = "Does the page come back to this conversation?"
    browser.press("E")
    browser.run("wait", "400")
    browser.compose_at(deep)
    browser.send(question)
    browser.read_until(landing_at(deep), lambda seen: seen["note"] is not None)
    stamp = browser.server.stamps[0]

    _fold_into_content(browser, "q-deep-fold", question, stamp, anchor=deep)
    # Whatever the browser would restore on its own is now the top of the
    # page, so anything the conversation gets is the page's own doing.
    browser.evaluate("window.scrollTo(0, 0); true")
    browser.data["title"] = "Published while writing at the bottom"
    browser.publish()
    browser.wait_for_title("Published while writing at the bottom")
    folded = f"{deep}#q-deep-fold"
    placed = browser.read_until(
        _placement(folded), lambda seen: seen.get("cursor") == folded
    )

    assert placed["present"] is True, placed
    assert placed["cursor"] == folded, placed
    assert placed["top"] >= 0, placed
    assert placed["bottom"] <= placed["height"], placed


def test_the_awaiting_chips_arrive_in_order_rather_than_all_at_once(
    browser: Browser,
) -> None:
    """Let the news spread from the conversation outwards, gently.

    After a send the page reloads and every level above the conversation
    acquires an awaiting chip. Arriving in one frame reads as the whole page
    changing; arriving in order reads as one thing happening, which is what
    did happen.
    """
    arriving = browser.evaluate(_CHIP_MOTION)

    with browser.reduced_motion():
        still = browser.evaluate(_CHIP_MOTION)

    assert {kind: mark["delay"] for kind, mark in arriving.items()} == {
        "thread": "0s",
        "item": "0.07s",
        "lane": "0.14s",
        "update": "0.21s",
    }, arriving
    assert {mark["name"] for mark in arriving.values()} == {"chip-arrive"}
    assert {mark["name"] for mark in still.values()} == {"none"}, still
