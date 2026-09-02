"""Append one agent turn to a saved conversation.

The turn's shape and its timestamp are not the agent's to choose: the author
is ``agent``, the instant is the real clock, and the turn goes on the end of
an existing thread. That removes every way the old hand-written path produced
``{question, answer}`` pairs dated 1970.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from visual_brief.server.counting import merge_pending_followups
from visual_brief.writes.legacy import read_for_write, settle_legacy_pairs
from visual_brief.writes.lint import report_lint
from visual_brief.writes.queue_view import document_view, parse_timestamp
from visual_brief.writes.runfiles import (
    CliError,
    resolve_run,
    save_document,
    utc_timestamp,
    write_transaction,
)


def answer_command(
    runs_root: Path,
    run_id: str | None,
    thread_id: str,
    text: str,
) -> int:
    """Answer one conversation on the page.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.
        thread_id: Id of the thread to append to.
        text: The answer, complete, as it should appear on the page.

    Returns:
        The process exit status.

    Raises:
        CliError: If the thread does not exist, the text is empty, or the
            resulting document would not validate.
    """
    _, run_dir = resolve_run(runs_root, run_id)
    answer = text.strip()
    if not answer:
        raise CliError("the answer text must not be empty")
    with write_transaction(run_dir):
        document, legacy = read_for_write(run_dir)
        view = document_view(document, legacy.undated)
        thread = view.threads.get(thread_id)
        if thread is None:
            raise CliError(_unknown_thread(run_dir, thread_id))
        at = utc_timestamp(milliseconds=True)
        _require_clock_after(thread, at, thread_id)
        thread["turns"].append({"author": "agent", "text": answer, "at": at})
        settle_legacy_pairs(run_dir, document, legacy)
        index_path = save_document(run_dir, document)
        anchor = view.thread_anchors.get(thread_id, "")
    print(f"answer: appended to {thread_id} at {anchor}; rendered {index_path}")
    report_lint(run_dir, document)
    return 0


def _unknown_thread(run_dir: Path, thread_id: str) -> str:
    """Explain a missing thread, naming the fold when the queue holds it."""
    merged = merge_pending_followups(run_dir)
    if merged is not None and thread_id in document_view(merged).threads:
        return (
            f"thread {thread_id!r} is still only in the queue; run "
            "'visual-brief fold' first"
        )
    return f"unknown thread: {thread_id}"


def _require_clock_after(
    thread: dict[str, Any],
    at: str,
    thread_id: str,
) -> None:
    """Refuse to write a turn the validator would call out of order."""
    now = parse_timestamp(at)
    newest = _newest_instant(thread)
    if now is None or newest is None or newest <= now:
        return
    raise CliError(
        f"thread {thread_id!r} already holds a turn dated {newest.isoformat()},"
        f" which is later than the current clock ({at}); the answer would be"
        " out of order"
    )


def _newest_instant(thread: dict[str, Any]) -> datetime | None:
    """Return the newest instant already recorded on a thread."""
    instants = [
        instant
        for turn in thread.get("turns", [])
        if isinstance(turn, dict)
        and (instant := parse_timestamp(turn.get("at"))) is not None
    ]
    return max(instants, default=None)
