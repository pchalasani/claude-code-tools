"""Real-browser checks that the delivered page works as one self-contained file.

These assert the delivery contract rather than the interface: the inlined
bundle executes, the embedded document reaches it intact, the page fetches
nothing but its own local channel, and a republished run takes the new
document in without replacing itself. The reading interface and its keyboard
surface are asserted separately once they exist.
"""

from __future__ import annotations

import json
from typing import Iterator

import pytest

from browser_support import FIRST_ITEM, Browser, browser_session
from page_document import embedded_document, is_awaiting, iter_threads
from visual_brief.render import render_content


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_bundle_executes_and_renders_the_delivered_document(
    browser: Browser,
) -> None:
    """Prove the inlined bundle ran and painted the delivered document."""
    mounted = browser.evaluate(
        """
        (() => {
          const root = document.getElementById("visual-brief-root");
          const app = root && root.querySelector("[data-mounted]");
          const count = (name) =>
            app.querySelector(`[data-count="${name}"] b`).textContent;
          return {
            mounted: Boolean(app),
            title: app && app.querySelector(".brief-title").textContent,
            items: count("items"),
            lanes: count("lanes"),
            awaiting: app.querySelector("[data-awaiting-count]")
              .dataset.awaitingCount,
            painted: document.querySelectorAll(
              '[data-row-kind="item"]',
            ).length,
          };
        })()
        """
    )

    expected = embedded_document(browser.server.html)
    current = expected.get("current_state", {})
    lanes = list(current.get("lanes", []))
    lanes.extend(
        lane for update in expected["updates"] for lane in update["lanes"]
    )
    total_items = sum(len(lane["items"]) for lane in lanes)
    assert mounted["mounted"] is True
    assert mounted["title"] == expected["title"]
    assert mounted["items"] == str(total_items)
    assert mounted["lanes"] == str(len(lanes))
    assert 0 < int(mounted["painted"]) <= total_items


def test_the_page_agrees_with_the_document_on_what_awaits_an_answer(
    browser: Browser,
) -> None:
    """Show the same outstanding count the run accounting works from."""
    shown = browser.evaluate(
        """
        document.querySelector("[data-awaiting-count]").dataset.awaitingCount
        """
    )

    delivered = embedded_document(browser.server.html)
    awaiting = [
        thread
        for _, thread in iter_threads(delivered)
        if is_awaiting(thread)
    ]
    assert int(shown) == len(awaiting) == 2


def test_page_makes_no_request_other_than_its_own_local_channel(
    browser: Browser,
) -> None:
    """Keep the page self-contained: no subresource ever leaves the file.

    Two requests are the page's own channel back to the daemon that served
    it: the generation it polls, and the document it fetches when that
    generation has moved on. Nothing else may ever be asked for.
    """
    browser.run("wait", "300")
    requested = browser.evaluate(
        """
        performance.getEntriesByType("resource").map((entry) => entry.name)
        """
    )

    own = {f"{browser.url}render-version", f"{browser.url}document"}
    off_page = [
        name
        for name in requested
        if not name.startswith("data:") and name not in own
    ]
    assert off_page == [], requested
    assert "http://" not in browser.server.html
    assert "https://" not in browser.server.html


def test_untrusted_question_text_stays_inert_data(browser: Browser) -> None:
    """Deliver hostile question text without ever executing it."""
    payload = '<img src="missing" onerror="window.injected=true">'
    thread = browser.data["updates"][1]["lanes"][1]["items"][0]["questions"][0]
    thread["turns"].append(
        {
            "author": "human",
            "text": payload,
            "at": "2026-07-25T21:00:00Z",
        }
    )
    browser.publish()
    browser.run("open", browser.url)
    browser.run("wait", "300")

    assert browser.evaluate(
        f"""
        (() => {{
          const blob = document.getElementById("visual-brief-document");
          const brief = JSON.parse(blob.textContent);
          const turns = brief.updates[1].lanes[1].items[0].questions[0].turns;
          return [
            turns[turns.length - 1].text === {json.dumps(payload)},
            document.querySelector("img") === null,
            Boolean(window.injected),
          ];
        }})()
        """
    ) == [True, True, False]


def test_first_version_poll_catches_up_a_page_that_lost_render_race(
    browser: Browser,
) -> None:
    """Catch up stale HTML when replacement wins before its first poll."""
    browser.data["title"] = "Race winner"
    browser.server.replacement_html = render_content(browser.data)

    browser.run("open", browser.url)
    marked = browser.mark()

    browser.wait_for_title("Race winner")
    # The page that lost the race is the page that caught up: it was never
    # thrown away and rebuilt.
    assert browser.marked() == marked


def test_a_publish_changes_the_page_without_navigating_away_from_it(
    browser: Browser,
) -> None:
    """The whole point: an open page takes the new content and stays open.

    The mark is written into the loaded document and belongs to it alone, so
    a reload — however brief, however invisible — takes it. Finding both the
    new content and the same mark is the proof that the human reading this
    page kept the page they were reading.
    """
    update_id, lane_id, _ = FIRST_ITEM.split("/")
    marked = browser.mark()
    before = browser.evaluate(
        f'document.querySelector(\'[data-row-id="{FIRST_ITEM}"]\') !== null'
    )

    lane = next(
        lane
        for update in browser.data["updates"]
        if update["id"] == update_id
        for lane in update["lanes"]
        if lane["id"] == lane_id
    )
    lane["items"].append(
        {
            "id": "arrived-without-a-reload",
            "glance": "This item arrived while the page stayed open",
            "explanation": "It was patched in.",
            "trust": "reported-by-agent",
        }
    )
    browser.data["title"] = "Published under an open page"
    browser.publish()
    browser.wait_for_title("Published under an open page")
    arrived_id = f"{update_id}/{lane_id}/arrived-without-a-reload"
    arrived = browser.read_until(
        f"""
        (() => {{
          const row = document.querySelector('[data-row-id="{arrived_id}"]');
          return {{
            painted: row !== null,
            glance: row?.querySelector(".glance")?.textContent ?? null,
            mark: window.__brief_mark ?? null,
          }};
        }})()
        """,
        lambda seen: seen["painted"] is True,
        timeout=7,
    )

    assert before is True
    assert arrived["painted"] is True, arrived
    assert arrived["glance"] == "This item arrived while the page stayed open"
    assert arrived["mark"] == marked, arrived
