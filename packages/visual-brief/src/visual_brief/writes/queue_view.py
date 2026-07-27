"""One shared reading of a run's queue against its saved document.

Folding and linting both need the same two facts: what the queue holds, and
whether a given queue line is already present in the document. Both are
answered here on top of the accounting module the daemon already uses, so a
line is judged folded by exactly the rule the awaiting badge counts by.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from visual_brief.server.counting import (
    _collect_thread_state,
    _parse_timestamp,
    _question_records,
    _timestamp_key,
)
from visual_brief.server.counting_io import _contained_child


@dataclass(frozen=True, slots=True)
class QueueRecord:
    """One well-formed question line from a run's queue."""

    anchor_id: str
    text: str
    timestamp: str | None
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class DocumentView:
    """What a saved document says about its threads and anchors."""

    thread_anchors: dict[str, str]
    threads: dict[str, dict[str, Any]]
    anchors: dict[str, dict[str, Any]]
    human_turns: frozenset[tuple[str, str, str]]


def queue_records(run_dir: Path) -> list[QueueRecord]:
    """Read every well-formed question line from a run's queue.

    Args:
        run_dir: The run directory.

    Returns:
        The queue's question records in arrival order. Unreadable queues and
        malformed lines yield nothing rather than an error, matching how the
        daemon reads the same file.
    """
    path = _contained_child(run_dir, "questions.jsonl")
    if path is None:
        return []
    records: list[QueueRecord] = []
    try:
        with path.open("rb") as queue:
            for record in _question_records(queue):
                parsed = _parse_record(record)
                if parsed is not None:
                    records.append(parsed)
    except OSError:
        return []
    return records


def document_view(document: Any) -> DocumentView:
    """Summarize a document's threads, anchors and saved human turns.

    Args:
        document: A normalized visual brief document.

    Returns:
        The thread anchors, thread objects, anchor owners and the identity of
        every human turn already saved.
    """
    states, folded, threads, owners = _collect_thread_state(document)
    human_turns = frozenset(
        (anchor, text, _timestamp_text(timestamp))
        for _, anchor, text, timestamp in folded
    )
    return DocumentView(
        thread_anchors={
            thread_id: anchor for thread_id, (anchor, _) in states.items()
        },
        threads=threads,
        anchors=owners,
        human_turns=human_turns,
    )


def record_identity(record: QueueRecord) -> tuple[str, str, str]:
    """Return the anchor, text and instant that identify a queue line.

    Args:
        record: One queue record.

    Returns:
        The identity a saved human turn must match to count as folded.
    """
    return (record.anchor_id, record.text, _timestamp_text(record.timestamp))


def is_folded(record: QueueRecord, view: DocumentView) -> bool:
    """Report whether one queue line is already in the document.

    Args:
        record: One queue record.
        view: A summary of the saved document.

    Returns:
        True when a saved human turn carries the same anchor, text and
        instant.
    """
    return record_identity(record) in view.human_turns


def parse_timestamp(value: Any) -> datetime | None:
    """Parse one timezone-aware ISO 8601 timestamp.

    Args:
        value: A candidate timestamp.

    Returns:
        The instant, or ``None`` when the value is absent, unparseable or
        carries no timezone.
    """
    return _parse_timestamp(value)


def _parse_record(record: dict[str, Any]) -> QueueRecord | None:
    """Return a usable question record, or none for a malformed line."""
    anchor = record.get("anchor_id")
    text = record.get("text")
    parent = record.get("parent_id")
    timestamp = record.get("timestamp")
    if not isinstance(anchor, str) or not isinstance(text, str):
        return None
    if not text.strip():
        return None
    if parent is not None and not isinstance(parent, str):
        return None
    return QueueRecord(
        anchor_id=anchor,
        text=text.strip(),
        timestamp=timestamp if isinstance(timestamp, str) else None,
        parent_id=parent,
    )


def _timestamp_text(value: Any) -> str:
    """Return a comparable text form of a normalized timestamp key."""
    return str(_timestamp_key(value) if isinstance(value, str) else value)
