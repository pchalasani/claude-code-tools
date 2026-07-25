"""Awaiting-answer accounting for saved threads and queued questions."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from visual_brief.render.threads import normalize_document
from visual_brief.server.queue import MAX_QUEUE_RECORD_BYTES

FoldedKey = tuple[str | None, str, str, str | None]
ThreadState = tuple[str, bool]


def count_unanswered_questions(run_dir: Path) -> int:
    """Count awaiting threads in content plus queue records not yet folded in.

    Args:
        run_dir: One resolved run directory.

    Returns:
        The number of distinct threads awaiting an agent answer.
    """
    try:
        root = run_dir.expanduser().resolve()
    except (OSError, RuntimeError):
        return 0
    states, folded = _read_thread_state(root)
    return _count_queued_questions(root, states, folded)


def _count_queued_questions(
    run_dir: Path,
    states: dict[str, ThreadState],
    folded: Counter[FoldedKey],
) -> int:
    """Combine saved thread states with a bounded queue scan."""
    path = _contained_child(run_dir, "questions.jsonl")
    if path is None:
        return sum(state[1] for state in states.values())
    try:
        with path.open("rb") as queue:
            return _count_queue_stream(queue, states, folded)
    except OSError:
        return sum(state[1] for state in states.values())


def _count_queue_stream(
    queue: Any,
    states: dict[str, ThreadState],
    folded: Counter[FoldedKey],
) -> int:
    """Count thread states while ignoring malformed queue records."""
    awaiting = {
        thread_id
        for thread_id, (_, is_awaiting) in states.items()
        if is_awaiting
    }
    new_threads = 0
    for record in _question_records(queue):
        anchor = record.get("anchor_id")
        text = record.get("text")
        parent = record.get("parent_id")
        timestamp = record.get("timestamp")
        if not isinstance(anchor, str) or not isinstance(text, str):
            continue
        if parent is not None and not isinstance(parent, str):
            continue
        if timestamp is not None and not isinstance(timestamp, str):
            continue
        folded_key = (parent, anchor, text, timestamp)
        if _consume_folded(folded, folded_key):
            continue
        if parent is None:
            new_threads += 1
        elif parent in states and states[parent][0] == anchor:
            awaiting.add(parent)
    return len(awaiting) + new_threads


def _question_records(queue: Any) -> Iterator[dict[str, Any]]:
    """Yield bounded, decoded question records from a queue stream."""
    while line := queue.readline(MAX_QUEUE_RECORD_BYTES + 1):
        complete = line.endswith(b"\n")
        oversized = len(line) > MAX_QUEUE_RECORD_BYTES
        if oversized and not complete:
            complete = _discard_record_remainder(queue)
        if oversized or not complete:
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type", "question") != "question":
            continue
        yield record


def _consume_folded(
    folded: Counter[FoldedKey],
    key: FoldedKey,
) -> bool:
    """Consume an exact folded record, matching only unknown timestamps loosely."""
    if folded[key]:
        folded[key] -= 1
        return True
    prefix = key[:3]
    for candidate, count in folded.items():
        timestamps_match_loosely = key[3] is None or candidate[3] is None
        if count and candidate[:3] == prefix and timestamps_match_loosely:
            folded[candidate] -= 1
            return True
    return False


def _discard_record_remainder(queue: Any) -> bool:
    """Discard a long record in bounded chunks and report completeness."""
    while chunk := queue.readline(MAX_QUEUE_RECORD_BYTES + 1):
        if chunk.endswith(b"\n"):
            return True
    return False


def _read_thread_state(
    run_dir: Path,
) -> tuple[dict[str, ThreadState], Counter[FoldedKey]]:
    """Read saved thread states and queue-folding keys from content."""
    content = _read_json_object(run_dir, "content.json")
    if content is None:
        return {}, Counter()
    legacy_unknown_ids: set[str] = set()
    normalized = normalize_document(content, legacy_unknown_ids)
    states, folded, _ = _collect_thread_state(normalized, legacy_unknown_ids)
    return states, folded


def _collect_thread_state(
    normalized: Any,
    legacy_unknown_ids: set[str] | None = None,
) -> tuple[
    dict[str, ThreadState],
    Counter[FoldedKey],
    dict[str, dict[str, Any]],
]:
    """Collect saved thread states, folding keys, and thread objects."""
    if not isinstance(normalized, dict):
        return {}, Counter(), {}
    updates = normalized.get("updates")
    if not isinstance(updates, list):
        return {}, Counter(), {}

    states: dict[str, ThreadState] = {}
    folded: Counter[FoldedKey] = Counter()
    threads: dict[str, dict[str, Any]] = {}
    legacy_ids = legacy_unknown_ids or set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("id")
        lanes = update.get("lanes")
        if not isinstance(update_id, str) or not isinstance(lanes, list):
            continue
        for lane in lanes:
            _collect_lane(states, folded, threads, update_id, lane, legacy_ids)
    return states, folded, threads


def _collect_lane(
    states: dict[str, ThreadState],
    folded: Counter[FoldedKey],
    threads: dict[str, dict[str, Any]],
    update_id: str,
    lane: Any,
    legacy_unknown_ids: set[str],
) -> None:
    """Collect threads from a recognized lane and its items."""
    if not isinstance(lane, dict):
        return
    lane_id = lane.get("id")
    if not isinstance(lane_id, str):
        return
    lane_anchor = f"{update_id}/{lane_id}"
    _collect_threads(
        states,
        folded,
        threads,
        lane.get("questions"),
        lane_anchor,
        legacy_unknown_ids,
    )
    items = lane.get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        anchor = f"{lane_anchor}/{item_id}"
        _collect_threads(
            states,
            folded,
            threads,
            item.get("questions"),
            anchor,
            legacy_unknown_ids,
        )


def _collect_threads(
    states: dict[str, ThreadState],
    folded: Counter[FoldedKey],
    threads: dict[str, dict[str, Any]],
    questions: Any,
    anchor: str,
    legacy_unknown_ids: set[str],
) -> None:
    """Collect valid newest authors and human turns from one owner."""
    if not isinstance(questions, list):
        return
    for thread in questions:
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("id")
        turns = thread.get("turns")
        if (
            not isinstance(thread_id, str)
            or not isinstance(turns, list)
            or not turns
        ):
            continue
        newest = turns[-1]
        if not isinstance(newest, dict):
            continue
        author = newest.get("author")
        if author not in {"human", "agent"}:
            continue
        states[thread_id] = (anchor, author == "human")
        threads[thread_id] = thread
        _collect_human_turns(
            folded,
            thread_id,
            turns,
            anchor,
            thread_id in legacy_unknown_ids,
        )


def _collect_human_turns(
    folded: Counter[FoldedKey],
    thread_id: str,
    turns: list[Any],
    anchor: str,
    legacy_timestamp_unknown: bool,
) -> None:
    """Collect queue identities for human turns already saved."""
    for turn_index, turn in enumerate(turns):
        if not isinstance(turn, dict) or turn.get("author") != "human":
            continue
        text = turn.get("text")
        if not isinstance(text, str):
            continue
        timestamp = turn.get("at")
        if not isinstance(timestamp, str):
            timestamp = None
        if legacy_timestamp_unknown and turn_index == 0:
            timestamp = None
        parent = None if turn_index == 0 else thread_id
        folded[(parent, anchor, text, timestamp)] += 1


def thread_anchor_for_reply(run_dir: Path, parent_id: str) -> str | None:
    """Return the saved anchor for a reply parent, if it exists."""
    states, _ = _read_thread_state(run_dir)
    state = states.get(parent_id)
    return state[0] if state is not None else None


def reply_target_error(
    run_dir: Path,
    parent_id: str | None,
    anchor_id: str,
) -> str | None:
    """Return a clear error when a follow-up target is stale or mismatched."""
    if parent_id is None:
        return None
    saved_anchor = thread_anchor_for_reply(run_dir, parent_id)
    if saved_anchor is None:
        return f"Reply parent {parent_id!r} does not exist"
    if saved_anchor != anchor_id:
        return f"Reply parent {parent_id!r} does not belong to this anchor"
    return None


def merge_pending_followups(run_dir: Path) -> dict[str, Any] | None:
    """Return content with valid, unfolded follow-ups added in memory."""
    content = _read_json_object(run_dir, "content.json")
    if content is None:
        return None
    legacy_unknown_ids: set[str] = set()
    normalized = normalize_document(content, legacy_unknown_ids)
    states, folded, threads = _collect_thread_state(
        normalized, legacy_unknown_ids
    )
    path = _contained_child(run_dir, "questions.jsonl")
    if path is None:
        return None
    changed = False
    pending: dict[str, list[dict[str, str]]] = {}
    try:
        with path.open("rb") as queue:
            for record in _question_records(queue):
                parent = record.get("parent_id")
                anchor = record.get("anchor_id")
                text = record.get("text")
                timestamp = record.get("timestamp")
                if (
                    not isinstance(parent, str)
                    or not isinstance(anchor, str)
                    or not isinstance(text, str)
                    or not isinstance(timestamp, str)
                ):
                    continue
                key = (parent, anchor, text, timestamp)
                if _consume_folded(folded, key):
                    continue
                state = states.get(parent)
                thread = threads.get(parent)
                if state is None or state[0] != anchor or thread is None:
                    continue
                if _parse_timestamp(timestamp) is None:
                    continue
                pending.setdefault(parent, []).append(
                    {"author": "human", "text": text, "at": timestamp}
                )
    except OSError:
        return None
    for parent, pending_turns in pending.items():
        thread = threads[parent]
        turns = thread.get("turns")
        if not isinstance(turns, list):
            continue
        combined = [*turns, *pending_turns]
        if any(_turn_timestamp(turn) is None for turn in combined):
            continue
        combined.sort(key=_turn_timestamp)
        thread["turns"] = combined
        changed = True
    if not changed or not isinstance(normalized, dict):
        return None
    return normalized


def _turn_timestamp(turn: Any) -> datetime | None:
    """Return one valid timezone-aware turn timestamp."""
    if not isinstance(turn, dict):
        return None
    return _parse_timestamp(turn.get("at"))


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse one timezone-aware ISO 8601 timestamp."""
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None else None


def _read_json_object(run_dir: Path, name: str) -> dict[str, Any] | None:
    """Read a contained JSON object, returning none on any damage."""
    path = _contained_child(run_dir, name)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _contained_child(run_dir: Path, name: str) -> Path | None:
    """Resolve a named run file only when it stays within the run."""
    try:
        root = run_dir.resolve()
        child = (root / name).resolve()
    except (OSError, RuntimeError):
        return None
    if child == root or not child.is_relative_to(root):
        return None
    return child
