"""Real-browser interaction tests for visual brief keyboard behavior."""

import json
from typing import Iterator

import pytest

from browser_support import Browser, browser_session
from visual_brief.render import render_content

KEYS = ["j", "k", "J", "K", " ", "a", "n", "/", "g", "G", "?"]


@pytest.fixture
def browser() -> Iterator[Browser]:
    """Serve a brief and open it in an isolated real browser."""
    with browser_session() as driver:
        yield driver


def test_typing_contexts_keep_every_binding_inert(browser: Browser) -> None:
    """Dispatch every binding in editors and type real text into each."""
    results = browser.evaluate(
        f"""
        (() => {{
          const form = document.querySelector(".question-box:not(.reply-box)");
          form.classList.add("open");
          document.querySelector(
            '.key-control[data-action="search"]'
          ).click();
          const editable = document.createElement("div");
          editable.id = "test-editable";
          editable.contentEditable = "true";
          editable.tabIndex = 0;
          document.body.append(editable);
          const targets = [
            form.querySelector("textarea"),
            document.querySelector("#page-search"),
            editable,
          ];
          const keys = {json.dumps(KEYS + ["Escape"])};
          return targets.map((target) => keys.map((key) => {{
            target.focus();
            const event = new KeyboardEvent(
              "keydown",
              {{key, bubbles: true, cancelable: true}},
            );
            target.dispatchEvent(event);
            return {{
              key,
              prevented: event.defaultPrevented,
              active: document.activeElement === target,
            }};
          }}));
        }})()
        """
    )
    for target_results in results:
        assert all(
            not result["prevented"] and result["active"]
            for result in target_results[:-1]
        )
        assert target_results[-1] == {
            "key": "Escape",
            "prevented": True,
            "active": False,
        }

    browser.batch(
        [
            ["type", ".question-box:not(.reply-box) textarea", "again"],
            ["type", "#test-editable", "again"],
            [
                "eval",
                "document.querySelector("
                "'.key-control[data-action=\"search\"]'"
                ").click()",
            ],
            ["type", "#page-search", "again"],
        ]
    )
    assert browser.evaluate(
        """
        [
          document.querySelector(
            ".question-box:not(.reply-box) textarea"
          ).value,
          document.querySelector("#page-search").value,
          document.querySelector("#test-editable").textContent,
        ]
        """
    ) == ["again", "again", "again"]


@pytest.mark.parametrize(
    "selector",
    [
        ".ask-button",
        ".signal",
        ".question-box:not(.reply-box) .submit",
        '.key-control[data-action="next-lane"]',
    ],
)
def test_space_keeps_native_button_operation(
    browser: Browser,
    selector: str,
) -> None:
    """Do not suppress Space activation on an ordinary button control."""
    browser.evaluate(
        f"""
        (() => {{
          document.querySelector(
            ".question-box:not(.reply-box)"
          ).classList.add("open");
          const original = document.querySelector({json.dumps(selector)});
          const button = original.cloneNode(true);
          original.replaceWith(button);
          button.dataset.clicks = "0";
          button.addEventListener("click", (event) => {{
            button.dataset.clicks = String(Number(button.dataset.clicks) + 1);
            event.preventDefault();
          }});
        }})()
        """
    )
    browser.batch([["focus", selector], ["press", "Space"]])

    assert browser.evaluate(
        f"document.querySelector({json.dumps(selector)}).dataset.clicks"
    ) == "1"


def test_space_keeps_native_button_and_disclosure_operation(
    browser: Browser,
) -> None:
    """Keep search-close activation and native disclosure toggling."""
    browser.evaluate(
        """
        document.querySelector(
          '.key-control[data-action="search"]'
        ).click()
        """
    )
    browser.batch([["focus", "#close-search"], ["press", "Space"]])
    assert browser.evaluate("document.querySelector('#search-panel').hidden")

    summary = 'summary[data-focus-id="current-update/what-changed"]'
    before = browser.evaluate(
        f"document.querySelector({json.dumps(summary)}).parentElement.open"
    )
    browser.batch([["focus", summary], ["press", "Space"]])
    assert browser.evaluate(
        f"""
        (() => {{
          const summary = document.querySelector({json.dumps(summary)});
          return [
            summary.parentElement.open,
            summary.getAttribute("aria-expanded"),
          ];
        }})()
        """
    ) == [
        not before,
        str(not before).lower(),
    ]


def test_non_space_binding_still_works_from_button(browser: Browser) -> None:
    """Keep page shortcuts active when a non-typing control has focus."""
    browser.run("focus", ".signal")
    browser.press("/")

    assert browser.evaluate(
        """
        [
          document.querySelector("#search-panel").hidden,
          document.activeElement.id,
        ]
        """
    ) == [False, "page-search"]


def test_navigation_search_help_and_ask_behaviors(browser: Browser) -> None:
    """Exercise navigation, wrapping, filtering, modal focus, and ask."""
    browser.run(
        "focus",
        'summary[data-focus-id='
        '"current-update/what-changed/differential-reader-check"]',
    )
    browser.press("J")
    assert browser.evaluate("document.activeElement.dataset.focusId") == (
        "current-update/why-it-matters"
    )
    browser.run(
        "focus",
        'summary[data-focus-id='
        '"current-update/why-it-matters/repair-loop-routing"]',
    )
    browser.press("K")
    assert browser.evaluate("document.activeElement.dataset.focusId") == (
        "current-update/what-changed"
    )

    browser.evaluate(
        """
        document.querySelector(
          'summary[data-focus-id="current-update/what-changed"]'
        ).parentElement.open = false
        """
    )
    browser.press("j")
    assert browser.evaluate(
        """
        [
          document.activeElement.dataset.navKind,
          document.activeElement.closest("details.lane").open,
        ]
        """
    ) == ["item", True]

    item_id = "current-update/what-changed/differential-reader-check"
    browser.run("focus", f'summary[data-focus-id="{item_id}"]')
    browser.press("a")
    browser.run("focus", f'summary[data-focus-id="{item_id}"]')
    browser.press("a")
    assert browser.evaluate(
        """
        [
          document.activeElement.tagName,
          document.activeElement.closest("form").classList.contains("open"),
        ]
        """
    ) == ["TEXTAREA", True]

    awaiting_ids = browser.evaluate(
        """
        [...document.querySelectorAll(
          "details.thread[data-awaiting] > summary"
        )].map((summary) => summary.dataset.focusId)
        """
    )
    for expected in awaiting_ids + awaiting_ids[:1]:
        browser.run("focus", 'summary[data-focus-id="current-update"]')
        browser.press("n")
        assert browser.evaluate(
            "document.activeElement.dataset.focusId"
        ) == expected
    browser.evaluate(
        """
        document.querySelectorAll("details.thread[data-awaiting]").forEach(
          (thread) => thread.removeAttribute("data-awaiting")
        )
        """
    )
    focus_before = browser.evaluate("document.activeElement.dataset.focusId")
    browser.press("n")
    assert browser.evaluate("document.activeElement.dataset.focusId") == (
        focus_before
    )

    browser.run("focus", 'summary[data-focus-id="current-update"]')
    browser.press("/")
    payload = (
        '<img id="search-injection" src="missing" '
        'onerror="window.searchInjectionExecuted=true">'
    )
    browser.evaluate("window.searchInjectionExecuted = false")
    browser.run("type", "#page-search", payload)
    assert browser.evaluate(
        """
        [
          document.querySelector("#match-count").textContent,
          [...document.querySelectorAll(".item-shell:not([hidden])")].length,
          document.querySelector("#search-injection") === null,
          window.searchInjectionExecuted,
          document.querySelector("#page-search").value,
        ]
        """
    ) == ["0 matches", 0, True, False, payload]
    browser.press("Escape")
    assert browser.evaluate(
        """
        [
          document.querySelector("#search-panel").hidden,
          [...document.querySelectorAll(".item-shell:not([hidden])")].length,
        ]
        """
    )[0] is True

    browser.press("?")
    assert browser.evaluate(
        "document.querySelector('#key-help').open"
    ) is True
    browser.press("Tab")
    assert browser.evaluate("document.activeElement.id") == "close-help"
    browser.press("Escape")
    assert browser.evaluate(
        "document.querySelector('#key-help').open"
    ) is False


def test_self_reload_restores_item_then_surviving_ancestors(
    browser: Browser,
) -> None:
    """Restore focus after self-reload, including lane and update fallback."""
    item_id = "current-update/why-it-matters/repair-loop-routing"
    lane_id = "current-update/why-it-matters"
    current = browser.data["updates"][1]
    lane = current["lanes"][1]
    item = lane["items"][0]
    thread_id = f'{item_id}#{item["questions"][0]["id"]}'
    browser.run("focus", f'summary[data-focus-id="{thread_id}"]')
    item["questions"] = []
    browser.publish()
    browser.wait_for_focus(item_id)

    browser.run("focus", f'summary[data-focus-id="{item_id}"]')
    assert browser.evaluate(
        """
        (() => {
          const style = getComputedStyle(document.activeElement);
          return [
            style.outlineStyle,
            Number.parseFloat(style.outlineWidth),
          ];
        })()
        """
    ) == ["solid", 3]
    browser.publish()
    browser.wait_for_focus(item_id)
    assert browser.evaluate(
        """
        (() => {
          const style = getComputedStyle(document.activeElement);
          return [
            style.outlineStyle,
            Number.parseFloat(style.outlineWidth),
          ];
        })()
        """
    ) == ["solid", 3]

    lane["items"] = []
    browser.publish()
    browser.wait_for_focus(lane_id)

    current["lanes"].remove(lane)
    browser.publish()
    browser.wait_for_focus("current-update")


def test_first_version_poll_reloads_a_page_that_lost_render_race(
    browser: Browser,
) -> None:
    """Reload stale HTML when replacement wins before its first poll."""
    browser.data["title"] = "Race winner"
    browser.server.replacement_html = render_content(browser.data)

    browser.run("open", browser.url)

    browser.wait_for_title("Race winner")


def test_native_disclosure_works_without_javascript(browser: Browser) -> None:
    """Keep native details usable without stale authored ARIA state."""
    browser.run("open", f"{browser.url}no-script")
    selector = 'summary[data-focus-id="current-update/what-changed"]'
    before = browser.evaluate(
        f"document.querySelector({json.dumps(selector)}).parentElement.open"
    )
    assert browser.evaluate("document.querySelectorAll('script').length") == 0
    browser.run("focus", selector)
    browser.press("Space")
    browser.run("wait", "100")
    assert browser.evaluate(
        f"""
        (() => {{
          const summary = document.querySelector({json.dumps(selector)});
          return [
            summary.parentElement.open,
            summary.hasAttribute("aria-expanded"),
          ];
        }})()
        """
    ) == [not before, False]
