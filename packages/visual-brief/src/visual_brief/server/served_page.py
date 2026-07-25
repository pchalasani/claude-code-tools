"""Build the exact run page and generation served by the daemon."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from visual_brief.render import render_content
from visual_brief.server.counting import merge_pending_followups

_GENERATION = re.compile(
    br'<meta name="visual-brief-render-version" content="([0-9a-f]{64})">'
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


def read_served_generation(run_dir: Path) -> bytes | None:
    """Return the generation for the exact page a fresh GET would serve."""
    page = read_served_page(run_dir)
    return page_generation(page) if page is not None else None


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
