"""Build the exact run page, generation and document served by the daemon.

Everything an open page is told about itself is read out of one page. The page
is not ``index.html`` on disk — valid pending follow-ups are merged and the
document is re-rendered — so a second source of truth written at publish time
would omit those follow-ups and drift from the page under every race. One
read, three answers taken from it: the generation, the bundle stamp and the
embedded document cannot then disagree with what ``/`` is serving.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from visual_brief.render import render_content
from visual_brief.server.counting import merge_pending_followups

_GENERATION = re.compile(
    br'<meta name="visual-brief-render-version" content="([0-9a-f]{64})">'
)
_ASSETS = re.compile(
    br'<meta name="visual-brief-assets-version" content="([0-9a-f]{64})">'
)
_DOCUMENT = re.compile(
    br'<script type="application/json" id="visual-brief-document">'
    br"(.*?)</script>",
    re.DOTALL,
)


def read_served_page(run_dir: Path) -> bytes | None:
    """Read a run page, merging valid pending follow-ups when present."""
    path = _contained_child(run_dir, "index.html")
    if path is None:
        return None
    try:
        saved = path.read_bytes()
    except OSError:
        return None
    pending = merge_pending_followups(run_dir)
    if pending is None:
        return saved
    try:
        return render_content(pending).encode("utf-8")
    except ValueError:
        return saved


def page_generation(page: bytes) -> bytes:
    """Return an embedded generation, or a legacy page hash."""
    match = _GENERATION.search(page)
    if match is not None:
        return match.group(1)
    return hashlib.sha256(page).hexdigest().encode("ascii")


def page_assets(page: bytes) -> bytes:
    """Return the front-end bundle stamp a page carries.

    Args:
        page: A rendered page.

    Returns:
        The stamp, or empty bytes for a page rendered before stamps existed.
    """
    match = _ASSETS.search(page)
    return match.group(1) if match is not None else b""


def page_document(page: bytes) -> dict[str, Any] | None:
    """Return the document blob a page embeds.

    Args:
        page: A rendered page.

    Returns:
        The embedded document, or ``None`` when the page carries none that
        parses as an object.
    """
    match = _DOCUMENT.search(page)
    if match is None:
        return None
    try:
        value = json.loads(match.group(1))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def page_payload(page: bytes) -> dict[str, Any] | None:
    """Return everything an open page is told about one rendered page.

    Args:
        page: A rendered page.

    Returns:
        Its generation, bundle stamp and embedded document, or ``None`` when
        the page carries no readable document.
    """
    document = page_document(page)
    if document is None:
        return None
    return {
        "generation": page_generation(page).decode("ascii"),
        "assets": page_assets(page).decode("ascii"),
        "document": document,
    }


def read_served_generation(run_dir: Path) -> bytes | None:
    """Return the generation for the exact page a fresh GET would serve."""
    page = read_served_page(run_dir)
    return page_generation(page) if page is not None else None


def read_served_document(run_dir: Path) -> dict[str, Any] | None:
    """Return the payload for the exact page a fresh GET would serve."""
    page = read_served_page(run_dir)
    return page_payload(page) if page is not None else None


def read_file_generation(run_dir: Path, name: str) -> bytes | None:
    """Return the SHA-256 generation of one contained run file."""
    path = _contained_child(run_dir, name)
    if path is None:
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(content).hexdigest().encode("ascii")


def _contained_child(run_dir: Path, name: str) -> Path | None:
    """Resolve one run child only when it remains contained."""
    try:
        root = run_dir.resolve()
        child = (root / name).resolve()
    except (OSError, RuntimeError):
        return None
    if child == root or not child.is_relative_to(root):
        return None
    return child
