"""Tests for the visual brief renderer.

The renderer delivers the validated document as an embedded JSON blob next to
the inlined front-end bundle, so these tests assert what the page delivers:
the document's content, identity and ordering, and the fact that the page
reaches nothing outside itself.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from page_document import (
    embedded_document,
    find_thread,
    is_awaiting,
    iter_threads,
    thread_ids,
    turn_texts,
)
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


def _first_item_path(data: dict[str, Any]) -> str:
    """Return the anchor path of the example's first item."""
    update = data["updates"][0]
    lane = update["lanes"][0]
    return f'{update["id"]}/{lane["id"]}/{lane["items"][0]["id"]}'


def _threads_at(
    delivered: dict[str, Any],
    path: str,
) -> list[dict[str, Any]]:
    """Return the delivered threads anchored at one path."""
    return [
        thread
        for anchor, thread in iter_threads(delivered)
        if anchor == path
    ]


def test_bundled_example_renders_without_external_requests() -> None:
    """Render the example as a self-contained page."""
    rendered = render_content(_example())

    assert rendered.startswith("<!doctype html>")
    assert "http://" not in rendered
    assert "https://" not in rendered
    assert '<div id="visual-brief-root"></div>' in rendered
    assert rendered.count("<script") == 2
    assert rendered.count("<style>") == 1
    assert "<link" not in rendered.replace('<link rel="icon" href="data:,">', "")


def test_bundled_example_renders_the_bytes_that_were_built() -> None:
    """Carry no control character; the HTML parser rewrites one unasked."""
    rendered = render_content(_example())

    assert re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", rendered) is None


def test_page_delivers_the_validated_document_and_the_bundle() -> None:
    """Deliver the document as data and the interface as one inlined bundle."""
    data = _example()

    rendered = render_content(data)
    delivered = embedded_document(rendered)

    assert delivered["title"] == data["title"]
    assert delivered["summary"] == data["summary"]
    assert [update["id"] for update in delivered["updates"]] == [
        update["id"] for update in data["updates"]
    ]
    assert "VisualBrief" in rendered
    assert "visual-brief-root" in rendered


def test_question_text_is_escaped() -> None:
    """Treat question text as untrusted data that never becomes markup."""
    data = _example()
    thread = _first_item(data)["questions"][0]
    thread["turns"][0]["text"] = "<script>alert(1)</script>"

    rendered = render_content(data)

    assert "<script>alert(1)</script>" not in rendered
    assert "\\u003cscript\\u003ealert(1)" in rendered
    delivered = find_thread(embedded_document(rendered), thread["id"])
    assert delivered["turns"][0]["text"] == "<script>alert(1)</script>"


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
    assert first == second
    threads = _threads_at(embedded_document(first), _first_item_path(data))
    assert re.fullmatch(r"q-[0-9a-f]{12}", threads[0]["id"])
    assert turn_texts(threads[0]) == ["Legacy question?", "Legacy answer."]


def test_legacy_pair_without_answer_is_delivered_awaiting() -> None:
    """A legacy human-only question arrives marked as awaiting an answer."""
    data = _example()
    item = _first_item(data)
    item["questions"] = [{"question": "Still waiting?", "answer": ""}]

    delivered = embedded_document(render_content(data))

    threads = _threads_at(delivered, _first_item_path(data))
    assert len(threads) == 1
    assert turn_texts(threads[0]) == ["Still waiting?"]
    assert is_awaiting(threads[0])


def test_legacy_pair_with_missing_answer_is_awaiting() -> None:
    """Treat an omitted legacy answer the same as an unanswered pair."""
    data = _example()
    _first_item(data)["questions"] = [{"question": "No answer field?"}]

    delivered = embedded_document(render_content(data))

    threads = _threads_at(delivered, _first_item_path(data))
    assert turn_texts(threads[0]) == ["No answer field?"]
    assert is_awaiting(threads[0])


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
    delivered = embedded_document(first)
    threads = _threads_at(delivered, _first_item_path(data))
    ids = [thread["id"] for thread in threads]

    assert first == second
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert set(ids) <= set(thread_ids(delivered))
    assert all(re.fullmatch(r"q-[0-9a-f]{12}", value) for value in ids)
    assert [turn_texts(thread) for thread in threads] == [
        ["Repeated?", "First answer."],
        ["Repeated?", "Second answer."],
        ["Third?"],
    ]


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
    lane_path = f'{data["updates"][0]["id"]}/{lane["id"]}'
    new_thread["anchor"]["path"] = lane_path
    lane["questions"] = [
        {"question": "Legacy lane?", "answer": "Lane answer."},
        new_thread,
    ]

    delivered = embedded_document(render_content(data))

    lane_threads = [
        (path, thread)
        for path, thread in iter_threads(delivered)
        if path == lane_path
    ]
    assert turn_texts(lane_threads[0][1]) == ["Legacy lane?", "Lane answer."]
    assert lane_threads[1][1]["id"] == "q-existing"
    assert lane_threads[1][1]["anchor"] == {
        "kind": "element",
        "path": "review-round-four/round-four-change",
    }


def test_thread_turns_are_delivered_oldest_first() -> None:
    """Keep chronological turns intact in the delivered document."""
    data = _example()
    thread = _first_item(data)["questions"][0]
    thread["turns"].append(
        {
            "author": "human",
            "text": "Newest follow-up",
            "at": "2026-07-25T19:12:00Z",
        }
    )

    delivered = find_thread(
        embedded_document(render_content(data)),
        thread["id"],
    )

    assert turn_texts(delivered) == [
        thread["turns"][0]["text"],
        thread["turns"][1]["text"],
        "Newest follow-up",
    ]
    assert is_awaiting(delivered)


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


def test_answered_thread_is_not_delivered_as_awaiting() -> None:
    """An answered thread does not arrive asking for attention."""
    delivered = embedded_document(render_content(_example()))

    thread = next(iter_threads(delivered))[1]

    assert thread["turns"][-1]["author"] == "agent"
    assert not is_awaiting(thread)


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


def test_update_identities_are_delivered_for_cursor_restoration() -> None:
    """Deliver stable update ids so a cursor can return to a survivor."""
    delivered = embedded_document(render_content(_example()))

    assert [update["id"] for update in delivered["updates"]] == [
        "review-round-four",
        "current-update",
    ]


def test_unused_deeply_nested_field_does_not_break_rendering() -> None:
    """Ignore an unknown field without recursively copying its contents."""
    data = _example()
    nested: list[Any] = []
    for _ in range(1_500):
        nested = [nested]
    data["unused"] = nested

    rendered = render_content(data)

    assert rendered.startswith("<!doctype html>")
    assert "unused" not in embedded_document(rendered)


def test_suggested_replies_reach_the_browser_as_inert_data() -> None:
    """Project both authored fields the Solid view needs for each shortcut."""
    data = _example()
    item = data["updates"][0]["lanes"][0]["items"][0]
    item["suggestions"] = [
        {
            "label": "Show proof",
            "message": "Show me the concrete evidence behind this claim.",
        }
    ]

    delivered = embedded_document(render_content(data))

    assert delivered["updates"][0]["lanes"][0]["items"][0][
        "suggestions"
    ] == item["suggestions"]


def test_suggested_replies_are_trimmed_before_reaching_the_browser() -> None:
    """Keep the browser's reply identity identical to the queued question."""
    data = _example()
    item = data["updates"][0]["lanes"][0]["items"][0]
    item["suggestions"] = [
        {
            "label": "  Show proof  ",
            "message": "  Show me the concrete evidence.  ",
        }
    ]

    delivered = embedded_document(render_content(data))

    assert delivered["updates"][0]["lanes"][0]["items"][0][
        "suggestions"
    ] == [
        {
            "label": "Show proof",
            "message": "Show me the concrete evidence.",
        }
    ]
