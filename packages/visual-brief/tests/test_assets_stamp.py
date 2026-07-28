"""The stamp that tells new content apart from new code.

An open page patches a newly published document into itself rather than
replacing itself, and that is only safe while the page and the daemon are
running the same front end. A generation change alone cannot say which
happened — publishing changes it, and so does reinstalling the tool — so every
page carries a second identity, derived from the bundle and from nothing else.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from visual_brief.render import render_content
from visual_brief.render.assets import (
    bundle_script,
    bundle_stamp,
    bundle_style,
    stamp_bundle,
)

EXAMPLE_PATH = Path(__file__).parents[1] / "example.json"
ASSETS_META = re.compile(
    r'<meta name="visual-brief-assets-version" content="([0-9a-f]{64})">'
)
GENERATION_META = re.compile(
    r'<meta name="visual-brief-render-version" content="([0-9a-f]{64})">'
)


def _example() -> dict[str, Any]:
    """Load the example document.

    Returns:
        The example brief.
    """
    value = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_every_page_says_which_front_end_it_is_running() -> None:
    """Carry the stamp beside the generation, in the page's own head."""
    page = render_content(_example())

    stamped = ASSETS_META.search(page)
    assert stamped is not None
    assert stamped.group(1) == bundle_stamp()


def test_the_stamp_is_the_bundle_and_nothing_else() -> None:
    """Derive it from the two artifacts a page inlines, and from those only."""
    assert bundle_stamp() == stamp_bundle(bundle_script(), bundle_style())


def test_a_different_front_end_is_a_different_stamp() -> None:
    """Change the stamp for any change to either artifact.

    This is what makes the page reload instead of patching: a document patched
    into a tab running an older bundle leaves that tab running the older
    bundle for the rest of its life.
    """
    base = stamp_bundle("const app = 1;", ".row { color: red }")

    assert stamp_bundle("const app = 2;", ".row { color: red }") != base
    assert stamp_bundle("const app = 1;", ".row { color: blue }") != base
    # Nor can the two be run together into one another's contents.
    assert stamp_bundle("ab", "c") != stamp_bundle("a", "bc")


def test_publishing_changes_the_generation_and_not_the_stamp() -> None:
    """Keep the stamp still while the document moves under it.

    A page that saw its bundle change on every publish would reload on every
    publish, which is the behaviour this whole arrangement replaces.
    """
    first = render_content(_example())
    changed = _example()
    changed["title"] = "Published again, with a different title"
    second = render_content(changed)

    first_stamp = ASSETS_META.search(first)
    second_stamp = ASSETS_META.search(second)
    first_generation = GENERATION_META.search(first)
    second_generation = GENERATION_META.search(second)
    assert first_stamp is not None and second_stamp is not None
    assert first_generation is not None and second_generation is not None
    assert first_stamp.group(1) == second_stamp.group(1)
    assert first_generation.group(1) != second_generation.group(1)
