"""Discovery and classification of programmatic helper sessions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar


ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class SessionFileSnapshot:
    """Transcript paths present before a helper fork starts."""

    directory: Path
    paths: frozenset[Path]


def _session_files(directory: Path) -> frozenset[Path]:
    """Return regular JSONL transcripts directly beneath a directory.

    Args:
        directory: Claude project transcript directory.

    Returns:
        Absolute paths to regular, non-symlink JSONL files.
    """
    try:
        return frozenset(
            path.absolute()
            for path in directory.glob("*.jsonl")
            if path.is_file() and not path.is_symlink()
        )
    except OSError:
        return frozenset()


def snapshot_session_files(source_session_file: Path) -> SessionFileSnapshot:
    """Capture sibling transcripts before launching a headless fork.

    Args:
        source_session_file: Transcript being forked.

    Returns:
        The source directory and its current regular JSONL files.
    """
    directory = source_session_file.expanduser().absolute().parent
    return SessionFileSnapshot(directory, _session_files(directory))


def is_helper_session(session_file: Path) -> bool:
    """Return whether a transcript carries explicit helper metadata.

    Args:
        session_file: Claude transcript to inspect.

    Returns:
        True only when the first JSON record has ``sessionType=helper``.
    """
    try:
        with session_file.open(encoding="utf-8") as handle:
            record = json.loads(handle.readline())
    except (OSError, UnicodeError, ValueError, RecursionError):
        return False
    return isinstance(record, dict) and record.get("sessionType") == "helper"


def mark_new_helper_session(
    snapshot: SessionFileSnapshot,
    session_id: str | None = None,
) -> Path | None:
    """Mark a newly created fork without guessing across concurrent files.

    Args:
        snapshot: Transcript paths captured before the fork started.
        session_id: Session ID reported by the headless client, when available.

    Returns:
        The marked transcript, or None when no unique safe target exists.
    """
    new_paths = _session_files(snapshot.directory) - snapshot.paths
    candidate: Path | None = None
    if session_id:
        if Path(session_id).name != session_id:
            return None
        reported = (snapshot.directory / f"{session_id}.jsonl").absolute()
        if reported in new_paths:
            candidate = reported
    elif len(new_paths) == 1:
        candidate = next(iter(new_paths))
    if candidate is None:
        return None

    from claude_code_tools.session_utils import mark_session_as_helper

    return candidate if mark_session_as_helper(candidate) else None


def run_and_mark_helper_fork(
    source_session_file: Path,
    operation: Callable[[], ResultT],
) -> ResultT:
    """Run a headless fork operation and mark its transcript afterward.

    Marking runs for successful results, nonzero client results, and raised
    exceptions. When concurrent session creation makes the fork ambiguous,
    :func:`mark_new_helper_session` safely leaves every transcript untouched.

    Args:
        source_session_file: Transcript passed to the headless fork client.
        operation: Blocking operation that creates and runs the fork.

    Returns:
        The operation's original result.
    """
    snapshot = snapshot_session_files(source_session_file)
    try:
        return operation()
    finally:
        mark_new_helper_session(snapshot)
