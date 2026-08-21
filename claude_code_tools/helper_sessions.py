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
    """Mark the exact newly created fork reported by the headless client.

    Args:
        snapshot: Transcript paths captured before the fork started.
        session_id: Session ID reported by the headless client.

    Returns:
        The marked transcript, or None when the reported target is unavailable.
    """
    if not session_id or Path(session_id).name != session_id:
        return None
    new_paths = _session_files(snapshot.directory) - snapshot.paths
    candidate = (snapshot.directory / f"{session_id}.jsonl").absolute()
    if candidate not in new_paths:
        return None

    from claude_code_tools.session_utils import mark_session_as_helper

    return candidate if mark_session_as_helper(candidate) else None


def run_and_mark_helper_fork(
    source_session_file: Path,
    operation: Callable[[], tuple[ResultT, str | None]],
) -> ResultT:
    """Run a headless fork operation and mark its transcript afterward.

    Marking runs whenever the operation returns an exact session ID, including
    nonzero client results. Raised exceptions and missing IDs safely leave every
    transcript untouched.

    Args:
        source_session_file: Transcript passed to the headless fork client.
        operation: Blocking operation returning its result and fork session ID.

    Returns:
        The operation's original result.
    """
    snapshot = snapshot_session_files(source_session_file)
    outcome: tuple[ResultT, str | None] | None = None
    try:
        outcome = operation()
        return outcome[0]
    finally:
        if outcome is not None:
            mark_new_helper_session(snapshot, session_id=outcome[1])
