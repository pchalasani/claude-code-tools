"""Build the exact run page, generation and document served by the daemon.

Everything an open page is told about itself is read out of one page. The page
is not ``index.html`` on disk — valid pending follow-ups are merged and the
document is re-rendered — so a second source of truth written at publish time
would omit those follow-ups and drift from the page under every race. One read
gives the generation, bundle, physical run identity and document, so they
cannot disagree with what ``/`` is serving.
"""

from __future__ import annotations

import hashlib
import json
import os
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
_HEAD_END = b"</head>"
_RUN_INSTANCE = re.compile(
    br'<meta name="visual-brief-run-instance" content="([0-9a-f]{64})">'
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
        page = saved
    else:
        try:
            page = render_content(pending).encode("utf-8")
        except ValueError:
            page = saved
    return _with_run_instance(page, run_dir)


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
        Its generation, bundle stamp, physical run identity and document, or
        ``None`` when the page carries no readable document.
    """
    document = page_document(page)
    if document is None:
        return None
    return {
        "generation": page_generation(page).decode("ascii"),
        "assets": page_assets(page).decode("ascii"),
        "instance": page_instance(page),
        "document": document,
    }


def page_instance(page: bytes) -> str:
    """Return the physical run identity carried by a served page."""
    match = _RUN_INSTANCE.search(page)
    return match.group(1).decode("ascii") if match is not None else ""


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


def _with_run_instance(page: bytes, run_dir: Path) -> bytes:
    """Namespace a served page with its runs root and creation identity."""
    if _RUN_INSTANCE.search(page) is not None:
        return page
    instance = _run_instance(run_dir)
    head = page.find(_HEAD_END)
    if instance is None or head < 0:
        return page
    tag = (
        b'<meta name="visual-brief-run-instance" content="'
        + instance.encode("ascii")
        + b'">'
    )
    injected = page[:head] + tag + page[head:]
    generation = _GENERATION.search(injected)
    if generation is None:
        return injected
    template = (
        injected[: generation.start(1)]
        + (b"0" * 64)
        + injected[generation.end(1) :]
    )
    digest = hashlib.sha256(template).hexdigest().encode("ascii")
    return (
        template[: generation.start(1)]
        + digest
        + template[generation.end(1) :]
    )


def _run_instance(run_dir: Path) -> str | None:
    """Hash the normalized runs root together with the run creation identity."""
    meta_path = _contained_child(run_dir, "meta.json")
    if meta_path is None:
        return None
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        root = run_dir.resolve().parent
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    identity = metadata.get("instance_id", metadata.get("created_at"))
    if not isinstance(identity, str) or not identity:
        return None
    material = os.fsencode(root) + b"\0" + identity.encode("utf-8")
    return hashlib.sha256(material).hexdigest()
