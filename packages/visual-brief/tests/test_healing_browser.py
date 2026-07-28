"""Real-browser proof that a tab left open never goes quietly stale.

A page that cannot reconcile itself with the daemon reloads — once, not in a
loop — and a daemon that is simply not there is left alone. Both are pinned
here, and so is the gap between them that this round's audit turned up: what
the tab remembered about giving up was only ITSELF, so a page carrying no
generation at all — one an older release rendered, or one served from a
byte-identical copy — gave up once and then read every later answer as the
same impasse. It stopped reloading for the life of the tab and went on
running whatever code it had been served, which is what a tab showing
withdrawn wording looks like from the outside.

What is remembered is now the standoff: this page, and the answer it could
not place. A different answer is a different situation, and worth one more
reload.
"""

from __future__ import annotations

import re
from typing import Iterator

import pytest

from browser_server import served_page
from browser_support import FIRST_ITEM, Browser, browser_session

GENERATION_META = re.compile(
    r'<meta name="visual-brief-render-version" content="[0-9a-f]{64}">'
)

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

def _without_its_generation(browser: Browser) -> str:
    """Render the current document as a page that names no generation.

    This is what an older release served: a page the daemon can still hash
    but the page itself cannot compare with anything.

    Args:
        browser: The open browser, whose ``data`` is the served document.

    Returns:
        The rendered page, with its generation stripped out.
    """
    return GENERATION_META.sub("", served_page(browser.data))


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


def test_a_page_it_cannot_compare_still_notices_the_next_publish(
    browser: Browser,
) -> None:
    """Close the way a tab could keep running withdrawn code for good.

    The tab is handed a page that names no generation — the shape an older
    release served — and heals out of it once, as it should. Then the agent
    publishes. The tab still cannot compare the two generations, but it can
    see that the answer is not the one it gave up on, and that is worth one
    more reload: the page it fetches is the page being served now, bundle
    and all.

    The daemon is made as old as the page it is serving: a release that
    rendered pages without a generation had no document endpoint either, so
    there is nothing here to patch from and a reload is the only way forward.
    """
    browser.server.document_status = 404
    browser.server.version_body = "a" * 64
    browser.server.html = _without_its_generation(browser)
    unreadable = browser.read_until(
        """
        document.querySelector('meta[name="visual-brief-render-version"]')
          === null
        """,
        lambda seen: seen is True,
        timeout=7,
    )
    # Let the healing reload happen and the tab settle into staying put.
    browser.run("wait", "1200")
    settled = browser.evaluate("document.title")

    browser.data["title"] = "Published after the tab gave up"
    browser.server.html = _without_its_generation(browser)
    browser.server.version_body = "b" * 64

    browser.wait_for_title("Published after the tab gave up")
    assert unreadable is True, "the page never came back without a generation"
    assert settled != "Published after the tab gave up"
    assert browser.evaluate(_READABLE)["rows"] > 0
