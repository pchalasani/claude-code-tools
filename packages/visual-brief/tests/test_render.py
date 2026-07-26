"""Tests for the visual brief renderer."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from visual_brief.render import render_content
from visual_brief.render.threads import normalize_document

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


def test_legacy_pair_with_missing_answer_is_awaiting() -> None:
    """Treat an omitted legacy answer the same as an unanswered pair."""
    data = _example()
    _first_item(data)["questions"] = [{"question": "No answer field?"}]

    rendered = render_content(data)

    assert "No answer field?" in rendered
    assert "Awaiting answer" in rendered


def test_malformed_legacy_question_raises_validation_error() -> None:
    """Reject non-text legacy questions through the renderer contract."""
    data = _example()
    _first_item(data)["questions"] = [{"question": []}]

    with pytest.raises(ValueError, match=r"questions\[0\]\.id"):
        render_content(data)


def test_multiple_legacy_pairs_on_one_item_get_distinct_stable_ids() -> None:
    """Convert every pair on an item without collisions or reordering."""
    data = _example()
    _first_item(data)["questions"] = [
        {"question": "Repeated?", "answer": "First answer."},
        {"question": "Repeated?", "answer": "Second answer."},
        {"question": "Third?", "answer": ""},
    ]

    first = render_content(data)
    second = render_content(data)
    thread_ids = re.findall(
        r'data-focus-id="[^"]+#(q-[0-9a-f]{12})"',
        first,
    )

    assert first == second
    assert len(thread_ids) == 3
    assert len(set(thread_ids)) == 3
    assert first.index("First answer.") < first.index("Second answer.")
    assert first.index("Second answer.") < first.index("Third?")


def test_undated_legacy_ids_survive_insertion_of_same_text_pair() -> None:
    """Keep real legacy pair identities stable after same-text insertion."""
    data = _example()
    questions = [
        {
            "question": "Repeated?",
            "answer": "First answer.",
        },
        {
            "question": "Repeated?",
            "answer": "Second answer.",
        },
    ]
    _first_item(data)["questions"] = questions
    before = normalize_document(data)
    before_threads = _first_item(before)["questions"]
    ids_by_answer = {
        thread["turns"][1]["text"]: thread["id"] for thread in before_threads
    }

    questions.insert(
        0,
        {
            "question": "Repeated?",
            "answer": "Inserted answer.",
        },
    )
    after = normalize_document(data)
    after_by_answer = {
        thread["turns"][1]["text"]: thread
        for thread in _first_item(after)["questions"]
    }

    assert after_by_answer["First answer."]["id"] == ids_by_answer[
        "First answer."
    ]
    assert after_by_answer["Second answer."]["id"] == ids_by_answer[
        "Second answer."
    ]
    assert after_by_answer["Inserted answer."]["id"] not in set(
        ids_by_answer.values()
    )


def test_undated_legacy_id_survives_answer_update() -> None:
    """Keep an undated legacy identity when its answer is filled."""
    data = _example()
    pair = {"question": "Still waiting?", "answer": ""}
    _first_item(data)["questions"] = [pair]
    before = normalize_document(data)
    before_id = _first_item(before)["questions"][0]["id"]

    pair["answer"] = "Now answered."
    after = normalize_document(data)

    assert _first_item(after)["questions"][0]["id"] == before_id


def test_undated_and_epoch_dated_legacy_ids_do_not_collide() -> None:
    """Keep undated identity separate from an explicit epoch timestamp."""
    data = _example()
    _first_item(data)["questions"] = [
        {"question": "Repeated?", "answer": "Undated."},
        {
            "question": "Repeated?",
            "answer": "Explicit epoch.",
            "asked_at": "1970-01-01T00:00:00Z",
        },
    ]

    normalized = normalize_document(data)
    threads = _first_item(normalized)["questions"]

    assert threads[0]["id"] != threads[1]["id"]
    assert render_content(data).startswith("<!doctype html>")


def test_lane_legacy_pairs_convert_alongside_new_threads() -> None:
    """Support lane pairs and mixed legacy/new thread collections."""
    data = _example()
    lane = data["updates"][0]["lanes"][0]
    new_thread = copy.deepcopy(_first_item(data)["questions"][0])
    new_thread["id"] = "q-existing"
    new_thread["anchor"]["path"] = (
        f'{data["updates"][0]["id"]}/{lane["id"]}'
    )
    lane["questions"] = [
        {"question": "Legacy lane?", "answer": "Lane answer."},
        new_thread,
    ]

    rendered = render_content(data)

    assert "Legacy lane?" in rendered
    assert "Lane answer." in rendered
    assert 'data-focus-id="review-round-four/round-four-change#q-existing"' in (
        rendered
    )


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


def test_out_of_order_thread_turns_are_rejected() -> None:
    """Reject stored turns whose timestamps are not oldest first."""
    data = _example()
    thread = _first_item(data)["questions"][0]
    thread["turns"] = [
        {
            "author": "human",
            "text": "Newer question",
            "at": "2026-07-25T20:00:00Z",
        },
        {
            "author": "agent",
            "text": "Older answer",
            "at": "2026-07-25T19:00:00Z",
        },
    ]

    with pytest.raises(ValueError, match="chronological"):
        render_content(data)


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
    assert "else leaveTextBox(event.target)" in rendered
    assert 'form.closest("details.thread")' in rendered
    assert "focusElement(owner || nav(" in rendered
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
    assert all(
        "aria-expanded" not in summary
        for summary in re.findall(r"<summary[^>]*>", rendered)
    )
    assert ":focus, .nav-focus" in rendered


def test_update_summaries_have_stable_focus_identity() -> None:
    """Allow focus restoration to stop at a surviving update."""
    rendered = render_content(_example())

    assert '<summary data-focus-id="current-update"' in rendered
    assert '<summary data-focus-id="review-round-four"' in rendered


def test_unused_deeply_nested_field_does_not_break_rendering() -> None:
    """Ignore an unknown field without recursively copying its contents."""
    data = _example()
    nested: list[Any] = []
    for _ in range(1_500):
        nested = [nested]
    data["unused"] = nested

    assert render_content(data).startswith("<!doctype html>")
