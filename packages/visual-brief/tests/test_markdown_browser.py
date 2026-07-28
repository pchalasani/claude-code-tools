"""Real-browser proof that written markup never becomes markup.

Agents write markdown by habit, so the page reads it. The text it reads is
untrusted from every direction: the human's own words come back through the
daemon, and the agent's words are only as careful as the agent. A jsdom
assertion that no image element exists is worth something; a real browser is
worth more, because in a real browser an ``onerror`` that ever ran would run
here.

So the three named hazards are planted in the served document — an image tag
with a handler, a ``javascript:`` link, and a fenced block full of markup —
in an item's explanation, in an agent's turn and in a human's turn, and the
page is asked what it made of them.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from browser_support import Browser, browser_session

ITEM = "current-update/what-changed/differential-reader-check"
THREAD_ID = "q-hostile"
THREAD = f"{ITEM}#{THREAD_ID}"

IMAGE = "<img src=x onerror=window.__ran = true>"
HOSTILE_LINK = "[click](javascript:window.__ran = true)"
SAFE_LINK = "[the spec](https:example.test/spec)"

_HARM = """
    (() => ({
      ran: window.__ran === true,
      images: document.querySelectorAll("img").length,
      scripts: document.querySelectorAll("script:not([type])").length,
      handlers: document.querySelectorAll("[onerror], [onload]").length,
      hrefs: [...document.querySelectorAll("a")].map(
        (link) => link.getAttribute("href"),
      ),
    }))()
    """


def _read(row_id: str, inner: str = ":scope > .row-body") -> str:
    """Return a script reading how one row painted its prose.

    Rows nest, so the part of the row to read is named: an item's body holds
    its conversations too, and their prose belongs to them.

    Args:
        row_id: Identifier of the row to read.
        inner: Selector, relative to the row, of the prose to read.

    Returns:
        JavaScript returning the marks the row made of its markdown.
    """
    return f"""
    (() => {{
      const row = document.querySelector('[data-row-id="{row_id}"]');
      if (row === null) {{
        return null;
      }}
      const body = row.querySelector("{inner}");
      if (body === null) {{
        return null;
      }}
      return {{
        strong: [...body.querySelectorAll("strong")].map(
          (one) => one.textContent,
        ),
        code: [...body.querySelectorAll("code.md-code")].map(
          (one) => one.textContent,
        ),
        fenced: [...body.querySelectorAll("pre.md-code-block")].map(
          (one) => one.textContent,
        ),
        bullets: [...body.querySelectorAll("ul.md-list li")].map(
          (one) => one.textContent,
        ),
        text: body.textContent,
      }};
    }})()
    """


def _plant(browser: Browser) -> None:
    """Write hostile, marked-up prose into the served document.

    Args:
        browser: The open browser, whose ``data`` is the served document.
    """
    update_id, lane_id, item_id = ITEM.split("/")
    item: dict[str, Any] = next(
        item
        for update in browser.data["updates"]
        if update["id"] == update_id
        for lane in update["lanes"]
        if lane["id"] == lane_id
        for item in lane["items"]
        if item["id"] == item_id
    )
    item["explanation"] = (
        f"A **checked** claim about `read_served_page`.\n\n"
        f"- one\n- two\n\n{IMAGE}\n\n{HOSTILE_LINK}\n\n{SAFE_LINK}"
    )
    item["questions"] = [
        {
            "id": THREAD_ID,
            "anchor": {"kind": "element", "path": ITEM},
            "turns": [
                {
                    "author": "human",
                    "text": f"Is `alpha` checked? {IMAGE}",
                    "at": "2026-07-25T20:30:00Z",
                },
                {
                    "author": "agent",
                    "text": f"Yes — **checked**:\n\n```\n{IMAGE}\n```",
                    "at": "2026-07-25T20:31:00Z",
                },
            ],
        }
    ]


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief carrying hostile prose, in an isolated real browser."""
    with browser_session() as driver:
        _plant(driver)
        driver.data["title"] = "Published with hostile prose"
        driver.publish()
        driver.wait_for_title("Published with hostile prose")
        driver.press("E")
        driver.run("wait", "400")
        yield driver


def test_none_of_it_runs_or_becomes_an_element(browser: Browser) -> None:
    """The whole point: markup written as text stays text, in a real browser."""
    harm = browser.evaluate(_HARM)

    assert harm["ran"] is False, harm
    assert harm["images"] == 0, harm
    assert harm["handlers"] == 0, harm
    assert all(
        href is not None and not href.lower().startswith("javascript")
        for href in harm["hrefs"]
    ), harm


def test_the_item_reads_its_explanation_as_prose(browser: Browser) -> None:
    """Emphasis, code and lists, from the same text that carried the attack."""
    painted = browser.read_until(
        _read(ITEM, ":scope > .row-body > .explanation"),
        lambda seen: seen is not None and len(seen["strong"]) > 0,
    )

    assert painted["strong"] == ["checked"], painted
    assert painted["code"] == ["read_served_page"], painted
    assert painted["bullets"] == ["one", "two"], painted
    # The characters the author wrote are all still there — as characters.
    assert IMAGE in painted["text"], painted
    assert HOSTILE_LINK in painted["text"], painted


def test_a_link_on_the_allowlist_is_the_only_kind_that_becomes_one(
    browser: Browser,
) -> None:
    """One link, and it is the one whose scheme the page allows."""
    links = browser.evaluate(
        f"""
        (() => {{
          const body = document.querySelector(
            '[data-row-id="{ITEM}"] > .row-body > .explanation',
          );
          return [...body.querySelectorAll("a.md-link")].map((one) => ({{
            href: one.getAttribute("href"),
            text: one.textContent,
            rel: one.getAttribute("rel"),
          }}));
        }})()
        """
    )

    assert links == [
        {
            "href": "https:example.test/spec",
            "text": "the spec",
            "rel": "noreferrer noopener",
        }
    ], links


def test_both_authors_turns_are_read_the_same_way(browser: Browser) -> None:
    """One renderer for both, because one path is easier to keep safe."""
    painted = browser.read_until(
        _read(THREAD), lambda seen: seen is not None and len(seen["code"]) > 0
    )

    assert painted["code"] == ["alpha"], painted
    assert painted["strong"] == ["checked"], painted
    # Inside a fence the markup is code, and code is text.
    assert painted["fenced"] == [IMAGE], painted
