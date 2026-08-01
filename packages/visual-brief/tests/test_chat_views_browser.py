"""Real-browser regressions for chat collection and submit presentation."""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from browser_support import AWAITING_THREAD, Browser, browser_session, landing_at

ITEM = AWAITING_THREAD.split("#", maxsplit=1)[0]
ANSWERED_THREAD = "current-update/what-i-verified#q-parser-parity"
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


def _record_submit_frames(question: str, answer: str, folded: str) -> str:
    """Return a script recording each painted submit-lifecycle frame."""
    return f"""
    (() => {{
      const question = {json.dumps(question)};
      const answer = {json.dumps(answer)};
      const rowIds = [{json.dumps(ITEM)}, {json.dumps(folded)}];
      const frames = [];
      let started = false;
      const presentation = (element) => {{
        if (element === undefined) return null;
        const style = getComputedStyle(element);
        return {{
          classes: [...element.classList],
          color: style.color,
          background: style.background,
          fontStyle: style.fontStyle,
          fontWeight: style.fontWeight,
          opacity: style.opacity,
          border: style.border,
        }};
      }};
      const readFrame = () => {{
        const humanTurns = [...document.querySelectorAll(".turn-human")]
          .filter(
            (turn) => turn.querySelector(".turn-text")?.textContent === question,
          );
        const humanRows = humanTurns.map(
          (turn) => turn.closest("[data-row-id]")?.dataset.rowId ?? null,
        );
        const workingSigns = [...document.querySelectorAll("[data-row-id]")]
          .filter((row) => rowIds.includes(row.dataset.rowId ?? ""))
          .flatMap(
            (row) => [...row.querySelectorAll(
              ":scope > .row-body > p.working",
            )],
          );
        const answers = [...document.querySelectorAll(
          ".turn-agent .turn-text",
        )].filter((turn) => turn.textContent === answer).length;
        if (started || humanTurns.length > 0 || workingSigns.length > 0) {{
          started = true;
          frames.push({{
            human: humanTurns.length,
            humanRows,
            humanPresentation: presentation(humanTurns[0]),
            working: workingSigns.length,
            workingPresentation: presentation(workingSigns[0]),
            answers,
          }});
        }}
        if (answers === 0) {{
          requestAnimationFrame(readFrame);
        }}
      }};
      window["__briefSubmitFrames"] = frames;
      requestAnimationFrame(readFrame);
      return true;
    }})()
    """


def _submit_frames() -> str:
    """Return a script reading the submit-lifecycle frame recording."""
    return 'window["__briefSubmitFrames"] ?? []'


def test_reveal_keeps_search_and_counts_only_attention_needed(
    browser: Browser,
) -> None:
    """Reveal folds while excluding one visited answer from the badge."""
    _make_twenty_chats(browser.data)
    target = _target_thread(browser.data)
    target["turns"][-1]["text"] = "The working sign is ready."
    for update, lane, item in _items(browser.data):
        if f"{update}/{lane}/{item['id']}" == ITEM:
            item["glance"] = f"The working sign {SEARCH_MARKER}"
    browser.data["title"] = "Twenty conversations across two updates"
    browser.publish()
    browser.wait_for_title("Twenty conversations across two updates")

    browser.click_row(ANSWERED_THREAD)
    browser.run("wait", "250")
    thread_row = f"{ITEM}#{target['id']}"
    browser.compose_at(thread_row)
    question = "Does reveal still find everything after this answer?"
    browser.send(question)
    browser.read_until(
        landing_at(thread_row),
        lambda seen: seen["note"] is not None,
    )
    browser.press("/")
    browser.run("fill", "#brief-search", SEARCH_MARKER)

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

    browser.evaluate("document.activeElement?.blur()")
    browser.press("m")
    painted = browser.evaluate(
        """
        (() => ({
          badge: document.querySelector(".meta-attention")
            ?.dataset.attentionCount,
          pressed: document.querySelector(".meta-attention")
            ?.getAttribute("aria-pressed") ?? null,
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

    assert painted["badge"] == "19"
    assert painted["pressed"] == "true"
    assert painted["query"] == SEARCH_MARKER
    browser.press("Escape")
    browser.run("wait", "250")
    assert browser.evaluate(
        "document.querySelectorAll('[data-row-kind=\"thread\"]').length"
    ) == 20


def test_a_later_conversation_uses_normal_fold_defaults(
    browser: Browser,
) -> None:
    """Do not turn an earlier reveal into a persistent mode."""
    browser.click_row(ANSWERED_THREAD)
    browser.run("wait", "250")
    outstanding_before = int(
        browser.evaluate(
            "document.querySelector('.meta-attention')?.dataset.attentionCount"
        )
    )
    browser.press("C")
    browser.press("m")
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
                    "text": "Does an old reveal remain active?",
                    "at": "2026-07-29T15:00:00Z",
                },
                {
                    "author": "agent",
                    "text": "No. Normal fold defaults still govern this.",
                    "at": "2026-07-29T15:01:00Z",
                },
            ],
        }
    )
    browser.data["title"] = "A later conversation used normal folds"
    browser.publish()
    browser.wait_for_title("A later conversation used normal folds")

    painted = browser.read_until(
        f"""
        (() => ({{
          badge: document.querySelector(".meta-attention")
            ?.dataset.attentionCount,
          pressed: document.querySelector(".meta-attention")
            ?.getAttribute("aria-pressed"),
          thread: document.querySelector(
            '[data-row-id="{thread_row}"]',
          ) !== null,
          chosen: Object.values(sessionStorage)
            .map((value) => {{
              try {{ return JSON.parse(value); }} catch {{ return null; }}
            }})
            .find((value) =>
              value !== null && typeof value === "object"
              && Object.prototype.hasOwnProperty.call(value, "{anchor}"),
            ) ?? {{}},
        }}))()
        """,
        lambda seen: seen["badge"] == str(outstanding_before + 1),
    )

    assert painted["badge"] == str(outstanding_before + 1)
    assert painted["pressed"] == "true"
    assert painted["thread"] is False
    assert painted["chosen"][anchor] is False
    assert thread_row not in painted["chosen"]


def test_submit_keeps_one_human_turn_until_the_answer_replaces_working(
    browser: Browser,
) -> None:
    """Keep the submitted words in normal human-turn styling throughout."""
    question = "Can this stay visually steady?"
    answer = "Yes. Only the answer is new."
    thread_id = "q-steady-submit"
    folded = f"{ITEM}#{thread_id}"
    browser.compose_at(ITEM)
    browser.evaluate(_record_submit_frames(question, answer, folded))
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
    frames = browser.read_until(
        _submit_frames(),
        lambda seen: bool(seen) and seen[-1]["answers"] == 1,
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
    assert frames
    assert all(
        frame["human"] == 1
        and frame["working"] == 1
        and frame["answers"] == 0
        for frame in frames[:-1]
    ), frames
    assert {
        key: frames[-1][key]
        for key in ("human", "humanRows", "working", "answers")
    } == {
        "human": 1,
        "humanRows": [folded],
        "working": 0,
        "answers": 1,
    }
    assert frames[-1]["workingPresentation"] is None
    assert {frame["humanRows"][0] for frame in frames} == {ITEM, folded}
    assert {
        json.dumps(frame["humanPresentation"], sort_keys=True)
        for frame in frames
    } == {json.dumps(frames[0]["humanPresentation"], sort_keys=True)}
    assert {
        json.dumps(frame["workingPresentation"], sort_keys=True)
        for frame in frames[:-1]
    } == {json.dumps(frames[0]["workingPresentation"], sort_keys=True)}
