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


def _example() -> dict[str, Any]:
    """Load an independent copy of the bundled example document."""
    with EXAMPLE_PATH.open(encoding="utf-8") as source:
        value = json.load(source)
    assert isinstance(value, dict)
    return value


def _first_item(data: dict[str, Any]) -> dict[str, Any]:
    """Return the example's first item."""
    return data["updates"][0]["lanes"][0]["items"][0]


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
