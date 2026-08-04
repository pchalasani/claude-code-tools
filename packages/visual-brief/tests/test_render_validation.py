"""Focused validation tests for the visual brief renderer."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from visual_brief import MAX_THREAD_ID_LENGTH
from visual_brief.render import render_content

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"
STATE_FOR_VALIDATION = {
    "headline": "The detailed state contract is active",
    "summary": "The current position has addressable lanes and items.",
    "lanes": [],
}


def _example() -> dict[str, Any]:
    """Load an independent copy of the bundled example document."""
    with EXAMPLE_PATH.open(encoding="utf-8") as source:
        value = json.load(source)
    assert isinstance(value, dict)
    return value


def _first_item(data: dict[str, Any]) -> dict[str, Any]:
    """Return the example's first item."""
    return data["updates"][0]["lanes"][0]["items"][0]


def test_stored_current_state_requires_its_complete_exact_shape() -> None:
    """Stored state accepts only schema fields and tool-owned questions."""
    data = _example()
    data["current_state"] = {
        "updated_at": "2026-08-01T12:00:00Z",
        **STATE_FOR_VALIDATION,
        "extra": "This field does not belong in stored state.",
    }

    with pytest.raises(ValueError, match="must have exactly these fields"):
        render_content(data)


@pytest.mark.parametrize(
    "text",
    [
        "Ship v2",
        "Build/test/ship",
        "Plan -> build",
        "First line\nSecond line",
    ],
)
def test_legacy_four_claim_current_state_remains_permissive(text: str) -> None:
    """The legacy shape keeps its original non-empty text contract."""
    data = _example()
    data["current_state"] = {
        "updated_at": "2026-08-01T12:00:00Z",
        "goal": text,
        "focus": text,
        "blocker": text,
        "next": text,
    }

    assert render_content(data).startswith("<!doctype html>")


def test_legacy_four_claim_current_state_allows_no_blocker() -> None:
    """The legacy blocker retains its nullable contract."""
    data = _example()
    data["current_state"] = {
        "updated_at": "2026-08-01T12:00:00Z",
        "goal": "Ship v2",
        "focus": "Ship v2",
        "blocker": None,
        "next": "Ship v2",
    }

    assert render_content(data).startswith("<!doctype html>")


@pytest.mark.parametrize("invalid", ["", "   ", 2, None])
def test_legacy_four_claim_current_state_still_requires_text(
    invalid: Any,
) -> None:
    """Required legacy claims remain non-empty strings."""
    data = _example()
    data["current_state"] = {
        "updated_at": "2026-08-01T12:00:00Z",
        "goal": invalid,
        "focus": "Ship v2",
        "blocker": None,
        "next": "Ship v2",
    }

    with pytest.raises(ValueError, match="current_state.goal"):
        render_content(data)


def test_legacy_four_claim_current_state_still_requires_exact_shape() -> None:
    """Legacy read compatibility does not admit extra fields."""
    data = _example()
    data["current_state"] = {
        "updated_at": "2026-08-01T12:00:00Z",
        "goal": "Ship v2",
        "focus": "Ship v2",
        "blocker": None,
        "next": "Ship v2",
        "extra": "Not part of the legacy shape",
    }

    with pytest.raises(ValueError, match="must have exactly these fields"):
        render_content(data)


def test_thread_identifier_surrounding_whitespace_is_rejected() -> None:
    """Keep saved IDs identical to stripped queue parent IDs."""
    data = _example()
    _first_item(data)["questions"][0]["id"] = " q-spaced "

    with pytest.raises(
        ValueError,
        match=r"questions\[0\]\.id must not contain whitespace",
    ):
        render_content(data)


def test_thread_identifier_over_reply_limit_is_rejected() -> None:
    """Reject a saved thread ID that no rendered reply can submit."""
    data = _example()
    _first_item(data)["questions"][0]["id"] = (
        "q" * (MAX_THREAD_ID_LENGTH + 1)
    )

    with pytest.raises(
        ValueError,
        match=rf"questions\[0\]\.id must be at most {MAX_THREAD_ID_LENGTH}",
    ):
        render_content(data)


@pytest.mark.parametrize("kind", ["update", "lane", "item", "thread"])
def test_focus_identifiers_reject_thread_separator(kind: str) -> None:
    """Keep element and thread focus identities unambiguous."""
    data = _example()
    update = data["updates"][0]
    lane = update["lanes"][0]
    item = lane["items"][0]
    targets = {
        "update": update,
        "lane": lane,
        "item": item,
        "thread": item["questions"][0],
    }
    targets[kind]["id"] = "c#q"

    with pytest.raises(ValueError, match=r"must not contain .*'#'"):
        render_content(data)


def test_a_thread_identifier_rejects_the_reserved_evidence_separator() -> None:
    """Keep a document from spelling a row the page invents for itself.

    A thread hangs off its row with ``#``, which is where the page names an
    item's evidence ``#~evidence``, so a thread id holding ``~`` could name a
    row that already belongs to something else.
    """
    data = _example()
    _first_item(data)["questions"][0]["id"] = "~evidence"

    with pytest.raises(ValueError, match=r"must not contain '~'"):
        render_content(data)


@pytest.mark.parametrize("kind", ["update", "lane", "item"])
def test_an_element_identifier_may_still_hold_a_tilde(kind: str) -> None:
    """Reserve ``~`` only where an invented segment can appear.

    Update, lane and item ids are joined with ``/`` and no invented segment
    follows a slash, so a document that has always rendered keeps rendering.
    """
    data = _example()
    update = data["updates"][0]
    lane = update["lanes"][0]
    item = lane["items"][0]
    targets = {"update": update, "lane": lane, "item": item}
    targets[kind]["id"] = f'linux~arm-{targets[kind]["id"]}'
    for thread in item.get("questions", []):
        thread["anchor"]["path"] = (
            f'{update["id"]}/{lane["id"]}/{item["id"]}'
        )

    assert render_content(data).startswith("<!doctype html>")


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


def test_a_note_child_must_be_a_note_with_precise_path() -> None:
    """Raw evidence sits beside a note, never inside its children."""
    data = _example()
    _first_item(data)["forensics"] = [
        {"title": "A note", "body": "Body.", "children": ["exit status 0"]}
    ]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics\[0\]"
        r"\.children\[0\] must be an object$",
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


def test_item_accepts_zero_to_three_authored_suggestions() -> None:
    """An item may carry a small ordered set of useful reply shortcuts."""
    data = _example()
    _first_item(data)["suggestions"] = [
        {"label": "Short", "message": "Say the full useful thing."},
        {"label": "Another", "message": "Send a different full message."},
    ]

    assert render_content(data).startswith("<!doctype html>")


def test_item_rejects_more_than_three_suggestions() -> None:
    """Keep the numeric reply strip deliberately small."""
    data = _example()
    _first_item(data)["suggestions"] = [
        {"label": f"Choice {index}", "message": f"Message {index}."}
        for index in range(4)
    ]

    with pytest.raises(ValueError, match="must contain at most 3 replies"):
        render_content(data)


@pytest.mark.parametrize("field", ["label", "message"])
def test_item_suggestion_requires_both_exact_fields(field: str) -> None:
    """A shortcut always explains both what is shown and what gets sent."""
    data = _example()
    suggestion = {"label": "Useful", "message": "Do the useful thing."}
    suggestion.pop(field)
    _first_item(data)["suggestions"] = [suggestion]

    with pytest.raises(ValueError, match="must have exactly these fields"):
        render_content(data)


def test_item_suggestion_labels_are_unique() -> None:
    """Do not present two visually indistinguishable choices."""
    data = _example()
    _first_item(data)["suggestions"] = [
        {"label": "Show proof", "message": "Show the direct output."},
        {"label": "show proof", "message": "Show the test result."},
    ]

    with pytest.raises(ValueError, match="labels must be unique"):
        render_content(data)
