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
from visual_brief.render.note_names import derived_name

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


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("Agreement rule", "agreement-rule"),
        ("  log!  ", "log"),
        ("!!!", "note"),
        ("x" * 60, "x" * 48),
    ],
)
def test_the_name_a_title_reads_as(title: str, name: str) -> None:
    """Derive the name the page derives, character for character.

    Refusing a collision is only worth anything if the two sides agree on
    which titles collide, so the rule checked here is the rule the front end
    slugs titles by: lowercase, runs of anything else become one dash, cut at
    the limit, no dash on either end, and a fallback when nothing is left.
    ``Agreement rule`` is the note the browser suite reads off the real page.
    """
    assert derived_name(title) == name


def test_two_siblings_named_by_one_title_are_refused() -> None:
    """Refuse the collision the page cannot settle without using positions.

    Named by their titles, both notes answer to one name. Anything the page
    could do to tell them apart — numbering them, taking the first — reads
    their positions, and a publish that writes a third ``Log`` above them
    would hand each note the identity of its neighbour.
    """
    data = _example()
    _first_item(data)["forensics"] = [
        {"title": "Log", "body": "First."},
        {"title": "Log", "body": "Second."},
    ]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics notes whose "
        r"titles read as one name must declare unique ids$",
    ):
        render_content(data)


def test_two_titles_that_slug_to_one_name_are_refused() -> None:
    """Compare the names the page derives, not the titles as written."""
    data = _example()
    _first_item(data)["forensics"] = [
        {"title": "Log", "body": "First."},
        {"title": "  log!  ", "body": "Second."},
    ]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics notes whose "
        r"titles read as one name must declare unique ids$",
    ):
        render_content(data)


def test_nested_siblings_named_by_one_title_are_refused() -> None:
    """Apply the rule to the notes hanging under a note, with its path."""
    data = _example()
    _first_item(data)["forensics"] = [
        {
            "title": "A note",
            "body": "Body.",
            "children": [
                {"title": "Log", "body": "First."},
                {"title": "Log", "body": "Second."},
            ],
        }
    ]

    with pytest.raises(
        ValueError,
        match=r"^updates\[0\]\.lanes\[0\]\.items\[0\]\.forensics\[0\]\.children"
        r" notes whose titles read as one name must declare unique ids$",
    ):
        render_content(data)


def test_declared_ids_settle_a_title_collision() -> None:
    """Let the author say which note is which, and publish it unchanged."""
    data = _example()
    _first_item(data)["forensics"] = [
        {"id": "first-log", "title": "Log", "body": "First."},
        {"id": "second-log", "title": "Log", "body": "Second."},
    ]

    delivered = embedded_document(render_content(data))

    item = delivered["updates"][0]["lanes"][0]["items"][0]
    assert [note["id"] for note in item["forensics"]] == [
        "first-log",
        "second-log",
    ]


def test_a_title_may_read_like_the_id_a_sibling_declares() -> None:
    """Keep the two namespaces apart: a derived name wears a ``~``.

    The note named by its title answers to ``~log``, which no declared id can
    spell, so the sibling calling itself ``log`` takes nothing from it.
    """
    data = _example()
    _first_item(data)["forensics"] = [
        {"title": "Log", "body": "First."},
        {"id": "log", "title": "The other one", "body": "Second."},
    ]

    delivered = embedded_document(render_content(data))

    item = delivered["updates"][0]["lanes"][0]["items"][0]
    assert [note.get("id") for note in item["forensics"]] == [None, "log"]


def test_two_notes_in_different_lists_may_share_a_title() -> None:
    """A name only has to belong to one note among the siblings it sits with."""
    data = _example()
    _first_item(data)["forensics"] = [
        {
            "title": "First note",
            "body": "Body.",
            "children": [{"title": "Log", "body": "First."}],
        },
        {
            "title": "Second note",
            "body": "Body.",
            "children": [{"title": "Log", "body": "Second."}],
        },
    ]

    delivered = embedded_document(render_content(data))

    item = delivered["updates"][0]["lanes"][0]["items"][0]
    assert [note["children"][0]["title"] for note in item["forensics"]] == [
        "Log",
        "Log",
    ]


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
