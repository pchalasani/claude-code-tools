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
from typing import Any, Iterator

from visual_brief.server.counting import (
    _collect_thread_state,
    _parse_timestamp,
    _question_records,
    _timestamp_key,
)
from visual_brief.server.counting_io import _contained_child
from visual_brief.server.queue import MAX_QUESTION_LENGTH
from visual_brief.schema import (
    CURRENT_STATE_ROOT,
    current_state_item_path,
    current_state_lane_path,
)


@dataclass(frozen=True, slots=True)
class QueueRecord:
    """One well-formed question line from a run's queue."""

    anchor_id: str
    text: str
    timestamp: str | None
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class DocumentView:
    """What a saved document says about its threads and anchors.

    ``human_turns`` maps each saved human turn's parent, anchor, and text to
    the instants it was recorded at. An undated legacy pair contributes
    ``None``, which stands for "the saved instant is unknown" — the same
    thing the accounting means by it.
    """

    thread_anchors: dict[str, str]
    threads: dict[str, dict[str, Any]]
    anchors: dict[str, dict[str, Any]]
    human_turns: dict[
        tuple[str | None, str, str],
        frozenset[str | None],
    ]


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


def document_view(
    document: Any,
    legacy_unknown_ids: set[str] | None = None,
) -> DocumentView:
    """Summarize a document's threads, anchors and saved human turns.

    Args:
        document: A normalized visual brief document.
        legacy_unknown_ids: Ids of threads the normalization built from a
            legacy pair that carried no ``asked_at``. Passing them is what
            makes a fold agree with the awaiting badge: their saved instant
            is unknown, not 1970.

    Returns:
        The thread anchors, thread objects, anchor owners and the identity of
        every human turn already saved.
    """
    states, folded, threads, owners = _collect_thread_state(
        document, legacy_unknown_ids
    )
    human_turns: dict[tuple[str | None, str, str], set[str | None]] = {}
    for parent, anchor, text, timestamp in folded:
        human_turns.setdefault((parent, anchor, text), set()).add(
            _timestamp_text(timestamp)
        )
    return DocumentView(
        thread_anchors={
            thread_id: anchor for thread_id, (anchor, _) in states.items()
        },
        threads=threads,
        anchors=owners,
        human_turns={key: frozenset(value) for key, value in human_turns.items()},
    )


def question_lists(document: Any) -> Iterator[tuple[str, list[Any]]]:
    """Yield every conversation list in a document, with its anchor path.

    Args:
        document: A document, normalized or exactly as it was read.

    Yields:
        The anchor path of each lane and item that carries conversations,
        paired with the list itself, so a caller can read the entries or
        replace them in place.
    """
    if not isinstance(document, dict):
        return
    state = document.get("current_state")
    if isinstance(state, dict) and isinstance(state.get("lanes"), list):
        yield from _owner_questions(state, CURRENT_STATE_ROOT)
        for lane in state["lanes"]:
            if not isinstance(lane, dict):
                continue
            lane_id = lane.get("id")
            if not isinstance(lane_id, str):
                continue
            yield from _owner_questions(
                lane,
                current_state_lane_path(lane_id),
            )
            items = lane.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if isinstance(item_id, str):
                    yield from _owner_questions(
                        item,
                        current_state_item_path(item_id),
                    )
    updates = document.get("updates")
    if not isinstance(updates, list):
        return
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("id")
        lanes = update.get("lanes")
        if not isinstance(update_id, str) or not isinstance(lanes, list):
            continue
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            lane_id = lane.get("id")
            if not isinstance(lane_id, str):
                continue
            lane_path = f"{update_id}/{lane_id}"
            yield from _owner_questions(lane, lane_path)
            items = lane.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if isinstance(item_id, str):
                    yield from _owner_questions(item, f"{lane_path}/{item_id}")


def record_identity(
    record: QueueRecord,
) -> tuple[str | None, str, str, str | None]:
    """Return the parent, anchor, text and instant identifying a queue line.

    Args:
        record: One queue record.

    Returns:
        The identity a saved human turn must match to count as folded, with
        ``None`` for an instant the line does not carry.
    """
    return (
        record.parent_id,
        record.anchor_id,
        record.text,
        _timestamp_text(record.timestamp),
    )


def is_folded(record: QueueRecord, view: DocumentView) -> bool:
    """Report whether one queue line is already in the document.

    Args:
        record: One queue record.
        view: A summary of the saved document.

    Returns:
        True when a saved human turn carries the same parent, anchor, and
        text, and an instant that matches. An unknown instant on either side
        matches any instant, which is the rule the awaiting badge counts by:
        a pair converted from an undated legacy entry is still the queue line
        it came from.
    """
    parent, anchor, text, instant = record_identity(record)
    saved = view.human_turns.get((parent, anchor, text))
    if saved is None:
        return False
    return instant is None or None in saved or instant in saved


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
    text = text.strip()
    if not text or len(text) > MAX_QUESTION_LENGTH:
        return None
    if parent is not None and not isinstance(parent, str):
        return None
    return QueueRecord(
        anchor_id=anchor,
        text=text,
        timestamp=timestamp if isinstance(timestamp, str) else None,
        parent_id=parent,
    )


def _owner_questions(
    owner: dict[str, Any],
    path: str,
) -> Iterator[tuple[str, list[Any]]]:
    """Yield one lane's or item's conversation list when it has one."""
    questions = owner.get("questions")
    if isinstance(questions, list):
        yield path, questions


def _timestamp_text(value: Any) -> str | None:
    """Return a comparable text form, or ``None`` for an unknown instant."""
    key = _timestamp_key(value) if isinstance(value, str) else value
    return None if key is None else str(key)
