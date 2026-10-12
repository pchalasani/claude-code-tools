"""Shared lexical normalization for user-supplied transfer path mappings."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from typing import Any


def normalize_path_mappings(
    mappings: Mapping[str, str] | Sequence[Any] | None,
) -> dict[str, str]:
    """Coalesce equivalent absolute endpoints and reject ambiguous destinations."""
    pairs = mappings.items() if isinstance(mappings, Mapping) else mappings or []
    result: dict[str, str] = {}
    for pair in pairs:
        if isinstance(pair, Mapping):
            old, new = pair["source"], pair["destination"]
        else:
            old, new = pair
        if not all(
            isinstance(path, str) and path.startswith("/") for path in (old, new)
        ):
            raise ValueError("--map requires two absolute paths")
        old, new = ("/" + posixpath.normpath(path).lstrip("/") for path in (old, new))
        if old in result and result[old] != new:
            raise ValueError("Conflicting destinations for the same --map source")
        result[old] = new
    return result
