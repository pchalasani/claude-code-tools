"""Public rendering API for visual briefs."""

from __future__ import annotations

from typing import Any

from .page import render_page
from .threads import normalize_document
from .validate import validate_document

__all__ = ["render_content", "render_document"]


def render_content(data: Any) -> str:
    """Validate and render a visual brief document.

    Args:
        data: Parsed visual brief JSON.

    Returns:
        A self-contained HTML document.

    Raises:
        ValueError: If the content does not match the visual brief schema.
    """
    normalized = normalize_document(data)
    return render_page(validate_document(normalized))


def render_document(data: Any) -> str:
    """Render content using the prototype's original API name."""
    return render_content(data)
