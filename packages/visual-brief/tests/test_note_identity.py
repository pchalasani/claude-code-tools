"""A forensic note is named for itself, not for the slot it sits in.

Each note under an item is a row of the page, and its row id is built from the
name the note answers to. That name has to belong to the note: if it were the
note's position, a later publish that writes one more note above it would hand
its identity to a neighbour, and a saved cursor would come back pointing at
the wrong evidence. So a note may declare its own ``id``, the renderer
delivers that id to the page, and two siblings may not claim one name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from page_document import embedded_document
from visual_brief.render import render_content

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"


def _example() -> dict[str, Any]:
    """Load an independent copy of the bundled example document.

    Returns:
        The parsed example document.
    """
    with EXAMPLE_PATH.open(encoding="utf-8") as source:
        value = json.load(source)
    assert isinstance(value, dict)
    return value


def _first_item(data: dict[str, Any]) -> dict[str, Any]:
    """Return the example's first item.

    Args:
        data: A document to read.

    Returns:
        The first item of the first lane of the first update.
    """
    return data["updates"][0]["lanes"][0]["items"][0]


def test_a_declared_note_id_is_delivered_to_the_page() -> None:
    """Deliver the name a note claims, because its row id is built from it.

    Dropped here, the note would fall back to being named by its title, and
    rewording that title would move a row out from under the reader's cursor.
    """
    data = _example()
    _first_item(data)["forensics"] = [
        {"id": "reference-run", "title": "The reference run", "body": "Ran."}
    ]

    delivered = embedded_document(render_content(data))

    item = delivered["updates"][0]["lanes"][0]["items"][0]
    assert item["forensics"] == [
        {"id": "reference-run", "title": "The reference run", "body": "Ran."}
    ]


def test_a_note_without_an_id_is_delivered_unchanged() -> None:
    """Invent nothing: a note that names itself by its title stays as it is."""
    data = _example()
    _first_item(data)["forensics"] = [
        {"title": "The reference run", "body": "Ran."}
    ]

    delivered = embedded_document(render_content(data))

    item = delivered["updates"][0]["lanes"][0]["items"][0]
    assert item["forensics"] == [{"title": "The reference run", "body": "Ran."}]


def test_a_forensic_note_id_must_be_a_usable_identifier() -> None:
    """Name the note path when its declared id cannot become a row id."""
    data = _example()
    _first_item(data)["forensics"] = [
        {"id": "note one", "title": "A note", "body": "Body."}
    ]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics\[0\]\.id "
        r"must not contain whitespace",
    ):
        render_content(data)


def test_two_sibling_notes_may_not_claim_one_id() -> None:
    """Refuse a collision that would paint two rows under one identity."""
    data = _example()
    _first_item(data)["forensics"] = [
        {"id": "same", "title": "One", "body": "First."},
        {"id": "same", "title": "Two", "body": "Second."},
    ]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics "
        r"note ids must be unique$",
    ):
        render_content(data)


def test_two_nested_notes_may_not_claim_one_id() -> None:
    """Apply the same rule to the notes hanging under a note."""
    data = _example()
    _first_item(data)["forensics"] = [
        {
            "title": "A note",
            "body": "Body.",
            "children": [
                {"id": "same", "title": "One", "body": "First."},
                {"id": "same", "title": "Two", "body": "Second."},
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics\[0\]"
        r"\.children note ids must be unique$",
    ):
        render_content(data)


def test_the_same_id_under_two_different_notes_is_allowed() -> None:
    """A name only has to be unique among the siblings that share a row."""
    data = _example()
    _first_item(data)["forensics"] = [
        {
            "title": "First note",
            "body": "Body.",
            "children": [{"id": "detail", "title": "One", "body": "First."}],
        },
        {
            "title": "Second note",
            "body": "Body.",
            "children": [{"id": "detail", "title": "Two", "body": "Second."}],
        },
    ]

    delivered = embedded_document(render_content(data))

    item = delivered["updates"][0]["lanes"][0]["items"][0]
    assert [note["children"][0]["id"] for note in item["forensics"]] == [
        "detail",
        "detail",
    ]
