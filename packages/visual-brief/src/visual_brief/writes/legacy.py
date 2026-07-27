"""Keep old ``{question, answer}`` pairs exactly as a verb found them.

A verb normalizes the saved document because the converted thread shape is
the only one it can reason about — but that conversion is not the verb's
change, and writing it back is a second, silent one. A pair without an
``asked_at`` converts to a thread dated at the 1970 epoch, and once that date
is on disk the accounting can no longer recognize the pair's queue line as
already folded: the question returns as a phantom duplicate, which is the
failure these verbs exist to kill.

So every pair a verb did not touch is written back verbatim, and the rare
pair a verb had to convert — because it appended a turn to it — adopts the
instant of its own queue line rather than the epoch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from visual_brief.render.threads import (
    LEGACY_TIMESTAMP,
    legacy_pair_turns,
    normalize_document,
)
from visual_brief.writes.queue_view import (
    QueueRecord,
    parse_timestamp,
    question_lists,
    queue_records,
)
from visual_brief.writes.runfiles import read_content


@dataclass(slots=True)
class LegacyPairs:
    """The legacy pairs one normalization rewrote, and how it dated them."""

    sources: dict[str, Any] = field(default_factory=dict)
    undated: set[str] = field(default_factory=set)


def normalize_for_write(data: Any) -> tuple[Any, LegacyPairs]:
    """Normalize a document for a verb, remembering the pairs it rewrote.

    Args:
        data: The document as it stands on disk.

    Returns:
        The normalized document and the pairs behind its converted threads.
    """
    legacy = LegacyPairs()
    document = normalize_document(data, legacy.undated, None, legacy.sources)
    return document, legacy


def read_for_write(run_dir: Path) -> tuple[Any, LegacyPairs]:
    """Read one run's document and normalize it for a verb.

    Args:
        run_dir: The run directory.

    Returns:
        The normalized document and the pairs behind its converted threads.

    Raises:
        CliError: If the content file cannot be read or is not an object.
    """
    return normalize_for_write(read_content(run_dir))


def settle_legacy_pairs(
    run_dir: Path,
    document: Any,
    legacy: LegacyPairs,
) -> None:
    """Undo every conversion the verb did not need, in place.

    Args:
        run_dir: The run directory, whose queue dates a converted pair.
        document: The normalized document the verb has finished changing.
        legacy: The pairs the normalization rewrote.
    """
    if not legacy.sources:
        return
    records: list[QueueRecord] | None = None
    claimed: set[int] = set()
    for _, questions in question_lists(document):
        for index, entry in enumerate(questions):
            thread_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(thread_id, str):
                continue
            pair = legacy.sources.get(thread_id)
            if pair is None:
                continue
            if entry.get("turns") == legacy_pair_turns(pair):
                questions[index] = pair
                continue
            if thread_id not in legacy.undated:
                continue
            if records is None:
                records = queue_records(run_dir)
            _adopt_queue_instant(entry, records, claimed)


def _adopt_queue_instant(
    thread: dict[str, Any],
    records: list[QueueRecord],
    claimed: set[int],
) -> None:
    """Date a converted undated pair from the queue line that asked it."""
    turns = thread.get("turns")
    if not isinstance(turns, list) or not turns:
        return
    opening = turns[0]
    if not isinstance(opening, dict) or opening.get("at") != LEGACY_TIMESTAMP:
        return
    anchor = thread.get("anchor")
    path = anchor.get("path") if isinstance(anchor, dict) else None
    for position, record in enumerate(records):
        if position in claimed or record.parent_id is not None:
            continue
        if record.anchor_id != path or record.text != opening.get("text"):
            continue
        if parse_timestamp(record.timestamp) is None:
            continue
        claimed.add(position)
        for turn in turns:
            if isinstance(turn, dict) and turn.get("at") == LEGACY_TIMESTAMP:
                turn["at"] = record.timestamp
        return
