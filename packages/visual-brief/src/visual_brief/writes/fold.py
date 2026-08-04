"""Fold pending queue lines into the saved document.

The merge itself is the daemon's own pending-follow-up merge, so a folded
line lands exactly where the served page already showed it, carrying the
queue line's text and timestamp unchanged. Nothing here paraphrases, retimes
or invents an anchor: this module decides only what to say about the result.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from visual_brief.server.counting import merge_pending_followups
from visual_brief.writes.legacy import (
    LegacyPairs,
    normalize_for_write,
    settle_legacy_pairs,
)
from visual_brief.writes.lint import report_lint
from visual_brief.writes.queue_view import (
    DocumentView,
    QueueRecord,
    document_view,
    is_folded,
    parse_timestamp,
    queue_records,
)
from visual_brief.writes.runfiles import (
    read_content,
    resolve_run,
    save_document,
    write_transaction,
)


@dataclass(frozen=True, slots=True)
class FoldedTurn:
    """One human turn that the fold added to the document."""

    thread_id: str
    anchor: str
    text: str
    started_thread: bool


def fold_command(runs_root: Path, run_id: str | None) -> int:
    """Fold every foldable queue line into the run's content document.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.

    Returns:
        The process exit status.

    Raises:
        CliError: If the run is unknown, unreadable, or the folded document
            would not validate.
    """
    _, run_dir = resolve_run(runs_root, run_id)
    with write_transaction(run_dir):
        saved = read_content(run_dir)
        before, before_legacy = normalize_for_write(saved)
        merged_legacy = LegacyPairs()
        merged = merge_pending_followups(
            run_dir, merged_legacy.undated, merged_legacy.sources
        )
        document = before if merged is None else merged
        undated = (
            before_legacy.undated
            if merged is None
            else merged_legacy.undated
        )
        folded = _describe_folded(before, document)
        left = _describe_unfolded(run_dir, document, undated)
        index_path = None
        if merged is not None:
            settle_legacy_pairs(run_dir, merged, merged_legacy)
            index_path = save_document(run_dir, merged)
            saved = merged

    for entry in folded:
        opening = (
            f"folded {entry.thread_id}"
            if entry.started_thread
            else f"folded a reply into {entry.thread_id}"
        )
        print(f"{opening} at {entry.anchor}")
        _print_block(entry.text, sys.stdout)
    for record, reason in left:
        print(f"warning: left in the queue — {reason}", file=sys.stderr)
        _print_block(record.text, sys.stderr)

    print(_summary(folded, left, index_path))
    report_lint(run_dir, saved)
    return 0


def _summary(
    folded: list[FoldedTurn],
    left: list[tuple[QueueRecord, str]],
    index_path: Path | None,
) -> str:
    """Build the one-line result of a fold."""
    threads = sum(1 for entry in folded if entry.started_thread)
    replies = len(folded) - threads
    parts = [f"folded {len(folded)} ({threads} new, {replies} replies)"]
    if left:
        parts.append(f"{len(left)} left in the queue")
    if index_path is not None:
        parts.append(f"rendered {index_path}")
    return "fold: " + "; ".join(parts)


def _describe_folded(before: Any, after: Any) -> list[FoldedTurn]:
    """List the human turns present after the fold but not before."""
    old = document_view(before)
    new = document_view(after)
    added: list[FoldedTurn] = []
    for thread_id, thread in new.threads.items():
        anchor = new.thread_anchors.get(thread_id, "")
        previous = old.threads.get(thread_id)
        if previous is None:
            added.extend(_describe_new_thread(thread_id, anchor, thread))
            continue
        seen = Counter(_turn_key(turn) for turn in _turns(previous))
        for turn in _turns(thread):
            key = _turn_key(turn)
            if seen[key]:
                seen[key] -= 1
                continue
            if turn.get("author") != "human":
                continue
            text = turn.get("text")
            added.append(
                FoldedTurn(
                    thread_id,
                    anchor,
                    text if isinstance(text, str) else "",
                    False,
                )
            )
    return added


def _describe_new_thread(
    thread_id: str,
    anchor: str,
    thread: dict[str, Any],
) -> list[FoldedTurn]:
    """Describe every human turn a thread this fold created arrived with.

    A brand-new thread can arrive with more than the question that opened
    it. A human who asks on the served page and then replies to their own
    still-pending question leaves both lines in the queue, and the merge
    resolves the reply against the thread it has just created. Reporting
    only the opening turn would fold that reply into the page and never say
    it, which is exactly the text the agent is meant to answer next.

    Args:
        thread_id: Id of the thread the fold created.
        anchor: Anchor path the thread hangs from.
        thread: The thread as the merge built it.

    Returns:
        One entry per human turn, the first marked as starting the thread.
    """
    said: list[FoldedTurn] = []
    for turn in _turns(thread):
        if turn.get("author") != "human":
            continue
        text = turn.get("text")
        opens_the_thread = not said
        said.append(
            FoldedTurn(
                thread_id,
                anchor,
                text if isinstance(text, str) else "",
                opens_the_thread,
            )
        )
    if not said:
        said.append(FoldedTurn(thread_id, anchor, _first_text(thread), True))
    return said


def _describe_unfolded(
    run_dir: Path,
    document: Any,
    legacy_unknown_ids: set[str],
) -> list[tuple[QueueRecord, str]]:
    """List the queue lines the fold could not place, and why."""
    view = document_view(document, legacy_unknown_ids)
    return [
        (record, _unfolded_reason(record, view))
        for record in queue_records(run_dir, document)
        if not is_folded(record, view)
    ]


def _unfolded_reason(record: QueueRecord, view: DocumentView) -> str:
    """Explain why one queue line stayed in the queue."""
    if record.parent_id is not None:
        anchor = view.thread_anchors.get(record.parent_id)
        if anchor is None:
            return (
                f"the reply names thread {record.parent_id!r}, which is not "
                "in the page"
            )
        if anchor != record.anchor_id:
            return (
                f"the reply names thread {record.parent_id!r}, which hangs "
                f"from {anchor!r}, not from {record.anchor_id!r}"
            )
    elif record.anchor_id not in view.anchors:
        return (
            f"anchor {record.anchor_id!r} is no longer in the page; it was "
            "not invented anywhere else"
        )
    if parse_timestamp(record.timestamp) is None:
        return "the queue line carries no usable timestamp"
    return f"the queue line could not be placed at {record.anchor_id!r}"


def _print_block(text: str, stream: Any) -> None:
    """Print one queue text indented under its heading."""
    for line in text.splitlines() or [""]:
        print(f"    {line}", file=stream)


def _turns(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one thread's well-formed turns."""
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return []
    return [turn for turn in turns if isinstance(turn, dict)]


def _turn_key(turn: dict[str, Any]) -> tuple[str, str, str]:
    """Return the identity a turn keeps across a fold."""
    return (
        str(turn.get("author")),
        str(turn.get("text")),
        str(turn.get("at")),
    )


def _first_text(thread: dict[str, Any]) -> str:
    """Return the text that opens a thread."""
    turns = _turns(thread)
    if not turns:
        return ""
    text = turns[0].get("text")
    return text if isinstance(text, str) else ""
