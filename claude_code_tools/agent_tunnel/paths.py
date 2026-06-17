"""Filesystem layout for the attachment round-trip.

Two per-thread directories back the feature:

- **Inbound uploads** live OUTSIDE any repo, under the tunnel's state dir
  (``<state>/uploads/<thread>/``). They are exposed to a fork via ``--add-dir``
  so the read-only ``Read`` tool can open them; keeping them out of the project
  dir means a colleague's upload never dirties the owner's working tree.
- **Outbound deliverables** live INSIDE the project, in a per-thread subdir of
  the outbox (``<project>/.agent-tunnel-out/<thread>/``). A ``.gitignore``
  holding ``*`` is dropped at the outbox root so deliverables stay invisible to
  ``git status`` even while they exist. The bot diffs this dir before/after a
  turn (see :func:`snapshot_dir`/:func:`changed_files`) to learn what the agent
  produced — which catches ``Write``-tool files and ``Bash``-generated ones
  (e.g. a pandoc PDF) alike.

Everything here is pure and stdlib-only so it is trivially unit-testable
without Discord or a live ``claude``.
"""

from __future__ import annotations

import re
from pathlib import Path

OUTBOX_DIRNAME = ".agent-tunnel-out"
UPLOADS_DIRNAME = "uploads"


def safe_key(thread_key: str) -> str:
    """Filesystem-safe slug for a thread key (e.g. ``th:123`` -> ``th-123``)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", thread_key).strip("-")
    return slug or "thread"


def uploads_dir_for(state_dir: Path, thread_key: str) -> Path:
    """Per-thread inbound-attachment dir under the tunnel state dir."""
    return Path(state_dir) / UPLOADS_DIRNAME / safe_key(thread_key)


def outbox_dir_for(project_dir: Path, thread_key: str) -> Path:
    """Per-thread outbound-deliverable dir inside the project's outbox."""
    return Path(project_dir) / OUTBOX_DIRNAME / safe_key(thread_key)


def ensure_outbox(project_dir: Path, thread_key: str) -> Path:
    """Create the per-thread outbox dir and return it.

    Also drops a ``.gitignore`` containing ``*`` at the outbox root the first
    time, so the whole ``.agent-tunnel-out`` tree (deliverables and the
    ignore file itself) never shows up in the owner's ``git status``.
    """
    root = Path(project_dir) / OUTBOX_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    thread_dir = outbox_dir_for(project_dir, thread_key)
    thread_dir.mkdir(parents=True, exist_ok=True)
    return thread_dir


def snapshot_dir(directory: Path) -> dict[str, tuple[int, int]]:
    """Map each file under `directory` (recursively) to a change signature.

    The signature is ``(st_size, st_mtime_ns)`` — higher fidelity than a
    second-resolution mtime, so a rewrite that preserves or coarsely rounds
    the mtime (``cp -p``, FAT/network volumes, a rapid regenerate) still
    registers as a change. Returns an empty dict if the directory does not
    exist. Keys are POSIX paths relative to `directory` so the snapshot
    survives being compared against the same dir later.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return {}
    snap: dict[str, tuple[int, int]] = {}
    for path in directory.rglob("*"):
        if path.is_file():
            st = path.stat()
            snap[path.relative_to(directory).as_posix()] = (
                st.st_size,
                st.st_mtime_ns,
            )
    return snap


def changed_files(
    directory: Path, snapshot: dict[str, tuple[int, int]]
) -> list[Path]:
    """Files under `directory` that are new or changed since `snapshot`.

    A file counts as changed when it was absent from the snapshot or its
    ``(st_size, st_mtime_ns)`` signature differs in any way (not merely a
    later mtime). Returned paths are absolute and sorted for stable ordering.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    changed: list[Path] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        prior = snapshot.get(rel)
        st = path.stat()
        if prior is None or (st.st_size, st.st_mtime_ns) != prior:
            changed.append(path)
    return sorted(changed)


def attachment_preamble(paths: list[Path], question: str) -> str:
    """Prepend uploaded-file paths to a question for the fork to read.

    The fork's ``Read`` tool handles PDFs, images, and text natively, so we
    simply point it at the absolute paths. When the colleague sent files with
    no text, a default instruction stands in for the question.

    Args:
        paths: Absolute paths of the downloaded attachments.
        question: The colleague's message text (may be empty).

    Returns:
        The question with a file-list preamble, unchanged if `paths` is empty.
    """
    if not paths:
        return question
    listing = "\n".join(f"- {p}" for p in paths)
    noun = "file" if len(paths) == 1 else "files"
    header = (
        f"A teammate attached the following {noun}; read them with the Read "
        f"tool before answering:\n{listing}"
    )
    body = question.strip() or (
        f"Please review the attached {noun} and summarize the key points."
    )
    return f"{header}\n\n{body}"
