"""Real-browser regressions for chat collection and submit presentation."""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from browser_support import AWAITING_THREAD, Browser, browser_session, landing_at

ITEM = AWAITING_THREAD.split("#", maxsplit=1)[0]
SEARCH_MARKER = "repro-sign-only"


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def _items(data: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return every item with the update and lane ids that contain it."""
    return [
        (update["id"], lane["id"], item)
        for update in data["updates"]
        for lane in update["lanes"]
        for item in lane["items"]
    ]


def _threads(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every lane-level and item-level conversation."""
    found: list[dict[str, Any]] = []
    for update in data["updates"]:
        for lane in update["lanes"]:
            found.extend(lane.get("questions", []))
            for item in lane["items"]:
                found.extend(item.get("questions", []))
    return found


def _target_thread(data: dict[str, Any]) -> dict[str, Any]:
    """Return the existing conversation anchored at the reproduction item."""
    update_id, lane_id, item_id = ITEM.split("/")
    item = next(
        item
        for update, lane, item in _items(data)
        if update == update_id
        and lane == lane_id
        and item["id"] == item_id
    )
    return item["questions"][0]


def _make_twenty_chats(data: dict[str, Any]) -> None:
    """Grow the two-update document to twenty human conversations."""
    candidates = [
        (update, lane, item)
        for update, lane, item in _items(data)
        if f"{update}/{lane}/{item['id']}" != ITEM
    ]
    missing = 20 - len(_threads(data))
    assert missing > 0
    for index in range(missing):
        update_id, lane_id, item = candidates[index % len(candidates)]
        anchor = f"{update_id}/{lane_id}/{item['id']}"
        item.setdefault("questions", []).append(
            {
                "id": f"q-chat-repro-{index}",
                "anchor": {"kind": "element", "path": anchor},
                "turns": [
                    {
                        "author": "human",
                        "text": f"Reproduction question {index}",
                        "at": f"2026-07-25T18:{index:02d}:00Z",
                    },
                    {
                        "author": "agent",
                        "text": f"Reproduction answer {index}",
                        "at": f"2026-07-25T18:{index:02d}:30Z",
                    },
                ],
            }
        )
    assert len(_threads(data)) == 20


def _pending_presentation(row_id: str) -> str:
    """Return a script reading the human words and working sign at one row."""
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      const turn = row?.querySelector(
        ":scope > .row-body > .turn-human",
      ) ?? null;
      return {{
        className: turn?.className ?? null,
        author: turn?.querySelector(".turn-author")?.textContent ?? null,
        text: turn?.querySelector(".turn-text")?.textContent ?? null,
        chip: row?.querySelectorAll(
          ':scope > .row-body > p.pending',
        ).length ?? 0,
        working: row?.querySelectorAll(
          ":scope > .row-body > p.working",
        ).length ?? 0,
      }};
    }})()
    """


def test_my_chats_ignores_a_search_left_active_through_a_live_answer(
    browser: Browser,
) -> None:
    """Show all twenty chats after searching, sending, and live publishing."""
    _make_twenty_chats(browser.data)
    target = _target_thread(browser.data)
    target["turns"][-1]["text"] = "The working sign is ready."
    for update, lane, item in _items(browser.data):
        if f"{update}/{lane}/{item['id']}" == ITEM:
            item["glance"] = f"The working sign {SEARCH_MARKER}"
    browser.data["title"] = "Twenty conversations across two updates"
    browser.publish()
    browser.wait_for_title("Twenty conversations across two updates")

    thread_row = f"{ITEM}#{target['id']}"
    browser.press("/")
    browser.run("fill", "#brief-search", SEARCH_MARKER)
    browser.compose_at(thread_row)
    question = "Does my-chats still find everything after this answer?"
    browser.send(question)
    browser.read_until(
        landing_at(thread_row),
        lambda seen: seen["note"] is not None,
    )

    target["turns"].append(
        {
            "author": "human",
            "text": question,
            "at": browser.server.stamps[0],
        }
    )
    browser.data["title"] = "The follow-up arrived by live publish"
    browser.publish()
    browser.wait_for_title("The follow-up arrived by live publish")
    target["turns"].append(
        {
            "author": "agent",
            "text": "Yes. The answer has landed.",
            "at": "2026-07-25T21:00:02Z",
        }
    )
    browser.data["title"] = "The live answer landed"
    browser.publish()
    browser.wait_for_title("The live answer landed")

    browser.press("m")
    painted = browser.evaluate(
        """
        (() => ({
          badge: document.querySelector(".meta-chats")?.dataset.chatsCount,
          threads: document.querySelectorAll(
            '[data-row-kind="thread"]',
          ).length,
          updates: document.querySelectorAll(
            '[data-row-kind="update"]',
          ).length,
          query: document.querySelector("#brief-search")?.value ?? null,
        }))()
        """
    )

    assert painted == {
        "badge": "20",
        "threads": 20,
        "updates": 2,
        "query": SEARCH_MARKER,
    }


def test_a_conversation_patched_after_my_chats_open_is_painted(
    browser: Browser,
) -> None:
    """Paint a newly collected conversation without reopening the view."""
    browser.press("m")
    before = len(_threads(browser.data))
    update_id, lane_id, item = next(
        (update, lane, item)
        for update, lane, item in _items(browser.data)
        if not item.get("questions")
    )
    anchor = f"{update_id}/{lane_id}/{item['id']}"
    thread_row = f"{anchor}#q-patched-chat"
    item.setdefault("questions", []).append(
        {
            "id": "q-patched-chat",
            "anchor": {"kind": "element", "path": anchor},
            "turns": [
                {
                    "author": "human",
                    "text": "Did this conversation reach the open view?",
                    "at": "2026-07-29T15:00:00Z",
                },
                {
                    "author": "agent",
                    "text": "Yes. It arrived without reopening My chats.",
                    "at": "2026-07-29T15:01:00Z",
                },
            ],
        }
    )
    browser.data["title"] = "A new conversation reached My chats"
    browser.publish()
    browser.wait_for_title("A new conversation reached My chats")

    painted = browser.read_until(
        f"""
        (() => ({{
          badge: document.querySelector(".meta-chats")?.dataset.chatsCount,
          pressed: document.querySelector(".meta-chats")
            ?.getAttribute("aria-pressed"),
          thread: document.querySelector(
            '[data-row-id="{thread_row}"]',
          ) !== null,
        }}))()
        """,
        lambda seen: seen["thread"] is True,
    )

    assert painted == {
        "badge": str(before + 1),
        "pressed": "true",
        "thread": True,
    }


def test_submit_keeps_one_human_turn_until_the_answer_replaces_working(
    browser: Browser,
) -> None:
    """Keep the submitted words in normal human-turn styling throughout."""
    question = "Can this stay visually steady?"
    answer = "Yes. Only the answer is new."
    thread_id = "q-steady-submit"
    folded = f"{ITEM}#{thread_id}"
    browser.compose_at(ITEM)
    browser.send(question)
    browser.read_until(landing_at(ITEM), lambda seen: seen["note"] is not None)
    pending = browser.evaluate(_pending_presentation(ITEM))

    item = next(
        item
        for update, lane, item in _items(browser.data)
        if f"{update}/{lane}/{item['id']}" == ITEM
    )
    thread = {
        "id": thread_id,
        "anchor": {"kind": "element", "path": ITEM},
        "turns": [
            {
                "author": "human",
                "text": question,
                "at": browser.server.stamps[0],
            }
        ],
    }
    item.setdefault("questions", []).append(thread)
    browser.data["title"] = "The question folded into the live page"
    browser.publish()
    browser.wait_for_title("The question folded into the live page")
    folded_in = browser.read_until(
        _pending_presentation(folded),
        lambda seen: seen["working"] == 1,
    )

    thread["turns"].append(
        {
            "author": "agent",
            "text": answer,
            "at": "2026-07-25T21:00:02Z",
        }
    )
    browser.data["title"] = "The steady answer landed"
    browser.publish()
    browser.wait_for_title("The steady answer landed")
    answered = browser.read_until(
        f"""
        (() => {{
          const row = document.querySelector('[data-row-id="{folded}"]');
          return {{
            answer: row?.querySelector(".turn-agent .turn-text")
              ?.textContent ?? null,
            working: row?.querySelectorAll(
              ":scope > .row-body > p.working",
            ).length ?? 0,
          }};
        }})()
        """,
        lambda seen: seen["answer"] == answer,
    )

    steady = {
        "className": "turn turn-human",
        "author": "human",
        "text": question,
        "chip": 0,
        "working": 1,
    }
    assert pending == steady
    assert folded_in == steady
    assert answered == {"answer": answer, "working": 0}
