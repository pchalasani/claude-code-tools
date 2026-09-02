"""The name a forensic note answers to among its siblings.

A note is a row of the page, and its row id is built from this name and never
from the note's position in the list: a later publish that writes one more
note above it must not hand the note's identity to a neighbour, or a saved
cursor comes back pointing at the wrong evidence.

A note may declare the name as an ``id``. A note that declares none is known
by a slug of its title, marked with the ``~`` a declared id may not hold, so a
derived name and a declared one can never spell each other. The front end
derives the same name from the same fields, which is what makes checking it
here a check on the ids the page will actually paint.

Two siblings that answer to one name are refused rather than told apart by
their positions. Only the author can say which note is which, and saying so
costs one ``id``.
"""

from __future__ import annotations

import re

#: Mark worn by a name derived from a title rather than declared.
DERIVED_MARK = "~"

#: Longest name derived from a note's title.
NAME_LIMIT = 48

#: The name a note falls back to when nothing usable can be slugged.
FALLBACK_NAME = "note"

_UNSLUGGABLE = re.compile(r"[^a-z0-9]+")
_EDGE_DASHES = re.compile(r"^-+|-+$")


def derived_name(title: str) -> str:
    """Return the name a note that declares none is known by.

    Args:
        title: The note's title.

    Returns:
        A slug of the title, safe inside a row id.
    """
    slug = _UNSLUGGABLE.sub("-", title.lower())[:NAME_LIMIT]
    return _EDGE_DASHES.sub("", slug) or FALLBACK_NAME


def note_name(declared: str | None, title: str) -> str:
    """Return the name one note answers to among its siblings.

    Args:
        declared: The ``id`` the note declared, or None when it declared none.
        title: The note's title.

    Returns:
        The declared name as written, or a marked slug of the title.
    """
    if declared is not None:
        return declared
    return f"{DERIVED_MARK}{derived_name(title)}"


def require_distinct_note_names(names: list[str], location: str) -> None:
    """Refuse two sibling notes that answer to one name.

    Two siblings claiming one name would paint two rows the cursor cannot
    tell apart, and nothing but their positions could separate them — the
    identity-by-position this module exists to be rid of. So the collision is
    refused at publish time, where the author can settle it by declaring an
    ``id`` on each of the notes involved.

    Args:
        names: What each sibling note answers to, in document order.
        location: JSON path used in validation errors.

    Raises:
        ValueError: If two siblings answer to one name.
    """
    taken: set[str] = set()
    for name in names:
        if name in taken:
            if name.startswith(DERIVED_MARK):
                raise ValueError(
                    f"{location} notes whose titles read as one name must "
                    "declare unique ids"
                )
            raise ValueError(f"{location} note ids must be unique")
        taken.add(name)
