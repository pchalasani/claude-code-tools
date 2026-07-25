"""Tests for the visual brief renderer."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from visual_brief.render import render_content

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"


def _example() -> dict[str, Any]:
    """Load an independent copy of the bundled example document."""
    with EXAMPLE_PATH.open(encoding="utf-8") as source:
        value = json.load(source)
    assert isinstance(value, dict)
    return value


def _first_item(data: dict[str, Any]) -> dict[str, Any]:
    """Return the example's first item."""
    return data["updates"][0]["lanes"][0]["items"][0]


def test_bundled_example_renders_without_external_requests() -> None:
    """Render the example as a self-contained page."""
    rendered = render_content(_example())

    assert rendered.startswith("<!doctype html>")
    assert "<details" in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered


def test_question_text_is_escaped() -> None:
    """Treat question text as untrusted content."""
    data = _example()
    thread = _first_item(data)["questions"][0]
    thread["turns"][0]["text"] = "<script>alert(1)</script>"

    rendered = render_content(data)

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered


def test_legacy_pairs_convert_in_memory_with_stable_ids() -> None:
    """Render real iteration-1 data without rewriting or renumbering it."""
    data = _example()
    item = _first_item(data)
    item["questions"] = [
        {
            "question": "Legacy question?",
            "answer": "Legacy answer.",
            "asked_at": "2026-07-25T19:00:00Z",
        }
    ]
    original = copy.deepcopy(data)

    first = render_content(data)
    second = render_content(data)

    assert data == original
    assert "q-" in first
    assert first == second
    assert first.index("Legacy question?") < first.index("Legacy answer.")


def test_legacy_pair_without_answer_is_awaiting_and_opens_ancestors() -> None:
    """A legacy human-only question cannot hide in closed disclosures."""
    data = _example()
    item = _first_item(data)
    item["questions"] = [{"question": "Still waiting?", "answer": ""}]

    rendered = render_content(data)

    assert '<details class="thread" open data-awaiting>' in rendered
    assert '<details class="item" open>' in rendered
    assert 'class="lane" open' in rendered
    assert "Awaiting answer" in rendered


def test_thread_turns_render_oldest_first_with_reply_after_newest() -> None:
    """Keep chronological turns intact and put the reply below them."""
    data = _example()
    thread = _first_item(data)["questions"][0]
    thread["turns"].append(
        {
            "author": "human",
            "text": "Newest follow-up",
            "at": "2026-07-25T19:12:00Z",
        }
    )

    rendered = render_content(data)

    question_at = rendered.index(thread["turns"][0]["text"])
    answer_at = rendered.index(thread["turns"][1]["text"])
    follow_up_at = rendered.index("Newest follow-up")
    reply_at = rendered.index(f'id="reply-{thread["id"]}"')
    assert question_at < answer_at < follow_up_at < reply_at


def test_agent_newest_thread_stays_collapsed_by_default() -> None:
    """An answered thread does not force itself or its item open."""
    rendered = render_content(_example())
    first_thread = rendered.index('<details class="thread"')
    first_summary = rendered.index("<summary", first_thread)

    assert " open" not in rendered[first_thread:first_summary]


def test_unknown_anchor_kind_has_clear_validation_error() -> None:
    """Reject the reserved quote variant without a renderer crash."""
    data = _example()
    thread = _first_item(data)["questions"][0]
    thread["anchor"] = {
        "kind": "quote",
        "quote": "future schema",
        "nearest_id": "three-verdict-contract",
    }

    with pytest.raises(ValueError, match="unknown anchor kind 'quote'"):
        render_content(data)


def test_keyboard_controls_are_progressive_and_have_mouse_equivalents() -> None:
    """Expose every binding while retaining native details disclosures."""
    rendered = render_content(_example())

    for label in (
        "j · Next item",
        "k · Previous item",
        "J · Next lane",
        "K · Previous lane",
        "n · Awaiting",
        "/ · Search",
        "g · Top",
        "G · Bottom",
        "? · Keys",
        "Space",
        "a",
        "Escape",
    ):
        assert label in rendered
    assert "<details" in rendered
    assert "<summary" in rendered


def test_keyboard_script_protects_typing_and_restores_focus() -> None:
    """Keep bindings inert in editors and retain an ancestor focus fallback."""
    rendered = render_content(_example())

    assert 'target.matches("textarea,input,[contenteditable]")' in rendered
    assert 'event.target === searchInput' in rendered
    assert 'event.target.blur()' in rendered
    assert 'sessionStorage.setItem(' in rendered
    assert 'saved.split("/").slice(0, -1).join("/")' in rendered
    assert "openAncestors(element)" in rendered


def test_search_and_awaiting_navigation_are_safe() -> None:
    """Filter by text without HTML execution and tolerate no waiting thread."""
    rendered = render_content(_example())

    assert "item.textContent.toLocaleLowerCase()" in rendered
    assert "matchCount.textContent" in rendered
    assert "innerHTML" not in rendered
    assert "if (!threads.length) return;" in rendered
    assert "% threads.length" in rendered


def test_help_is_modal_mouse_reachable_and_disclosures_have_aria() -> None:
    """Render accessible overlay controls and disclosure relationships."""
    rendered = render_content(_example())

    assert '<dialog id="key-help"' in rendered
    assert 'data-action="help"' in rendered
    assert 'id="close-help"' in rendered
    assert "help.showModal()" in rendered
    assert 'event.key === "Escape"' in rendered
    assert 'aria-controls="item-body-' in rendered
    assert 'aria-controls="lane-body-' in rendered
    assert 'aria-expanded="' in rendered
    assert ":focus, .nav-focus" in rendered


def test_forensics_must_be_a_list_with_precise_path() -> None:
    """Name the item path when forensics has the known wrong shape."""
    data = _example()
    _first_item(data)["forensics"] = {"title": "wrong"}

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics "
        r"must be a list$",
    ):
        render_content(data)


def test_table_requires_caption_with_precise_path() -> None:
    """Name the table path when the known required field is absent."""
    data = _example()
    item = _first_item(data)
    item["tables"] = [{"columns": ["a"], "rows": [["b"]]}]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.tables\[0\]"
        r"\.caption must be non-empty text$",
    ):
        render_content(data)


def test_unknown_trust_chip_is_rejected_with_precise_path() -> None:
    """Reject an unknown trust chip at its item path."""
    data = copy.deepcopy(_example())
    _first_item(data)["trust"] = "probably"

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.trust "
        r"is not a recognized trust chip$",
    ):
        render_content(data)


def test_trust_chip_with_surrounding_whitespace_is_rejected() -> None:
    """Reject trust values that the renderer cannot index exactly."""
    data = copy.deepcopy(_example())
    _first_item(data)["trust"] = " verified-by-me "

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.trust "
        r"is not a recognized trust chip$",
    ):
        render_content(data)
