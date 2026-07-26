"""Awaiting-answer accounting for saved threads and queued questions."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from visual_brief.render.threads import normalize_document
from visual_brief.server.counting_io import _contained_child, _read_json_object
from visual_brief.server.queue import MAX_QUESTION_LENGTH, MAX_QUEUE_RECORD_BYTES

FoldedKey = tuple[str | None, str, str, datetime | str | None]
ThreadState = tuple[str, bool]


def count_unanswered_questions(run_dir: Path) -> int:
    """Count awaiting threads in content plus queue records not yet folded in."""
    try:
        root = run_dir.expanduser().resolve()
    except (OSError, RuntimeError):
        return 0
    normalized, _, legacy_records = _merge_pending_content(root)
    states, _, _, _ = _collect_thread_state(normalized)
    awaiting = {
        thread_id
        for thread_id, (_, is_awaiting) in states.items()
        if is_awaiting
    }
    new_threads = 0
    for record in legacy_records:
        parent = record.get("parent_id")
        if parent is None:
            new_threads += 1
        elif (
            parent in states
            and states[parent][0] == record.get("anchor_id")
        ):
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
) -> FoldedKey | None:
    """Consume an exact folded record, matching only unknown timestamps loosely."""
    if folded[key]:
        folded[key] -= 1
        return key
    prefix = key[:3]
    for candidate, count in folded.items():
        timestamps_match_loosely = key[3] is None or candidate[3] is None
        if count and candidate[:3] == prefix and timestamps_match_loosely:
            folded[candidate] -= 1
            return candidate
    return None


def _discard_record_remainder(queue: Any) -> bool:
    """Discard a long record in bounded chunks and report completeness."""
    while chunk := queue.readline(MAX_QUEUE_RECORD_BYTES + 1):
        if chunk.endswith(b"\n"):
            return True
    return False


def _collect_thread_state(
    normalized: Any,
    legacy_unknown_ids: set[str] | None = None,
) -> tuple[
    dict[str, ThreadState],
    Counter[FoldedKey],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Collect thread state, folding keys, threads, and anchor owners."""
    if not isinstance(normalized, dict):
        return {}, Counter(), {}, {}
    updates = normalized.get("updates")
    if not isinstance(updates, list):
        return {}, Counter(), {}, {}

    states: dict[str, ThreadState] = {}
    folded: Counter[FoldedKey] = Counter()
    threads: dict[str, dict[str, Any]] = {}
    owners: dict[str, dict[str, Any]] = {}
    legacy_ids = legacy_unknown_ids or set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("id")
        lanes = update.get("lanes")
        if not isinstance(update_id, str) or not isinstance(lanes, list):
            continue
        for lane in lanes:
            _collect_lane(
                states, folded, threads, owners, update_id, lane, legacy_ids
            )
    return states, folded, threads, owners


def _collect_lane(
    states: dict[str, ThreadState],
    folded: Counter[FoldedKey],
    threads: dict[str, dict[str, Any]],
    owners: dict[str, dict[str, Any]],
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
    owners[lane_anchor] = lane
    _collect_threads(
        states, folded, threads, lane.get("questions"), lane_anchor,
        legacy_unknown_ids
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
        owners[anchor] = item
        _collect_threads(
            states, folded, threads, item.get("questions"), anchor,
            legacy_unknown_ids
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
            folded, thread_id, turns, anchor, thread_id in legacy_unknown_ids
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
        timestamp = _timestamp_key(turn.get("at"))
        if legacy_timestamp_unknown and turn_index == 0:
            timestamp = None
        parent = None if turn_index == 0 else thread_id
        folded[(parent, anchor, text, timestamp)] += 1


def reply_target_error(
    run_dir: Path,
    parent_id: str | None,
    anchor_id: str,
) -> str | None:
    """Return a clear error when a follow-up target is stale or mismatched."""
    if parent_id is None:
        return None
    normalized, _, _ = _merge_pending_content(run_dir)
    states, _, _, _ = _collect_thread_state(normalized)
    state = states.get(parent_id)
    saved_anchor = state[0] if state is not None else None
    if saved_anchor is None:
        return f"Reply parent {parent_id!r} does not exist"
    if saved_anchor != anchor_id:
        return f"Reply parent {parent_id!r} does not belong to this anchor"
    return None


def merge_pending_followups(run_dir: Path) -> dict[str, Any] | None:
    """Return content with valid, unfolded follow-ups added in memory."""
    normalized, changed, _ = _merge_pending_content(run_dir)
    return normalized if changed else None


def _merge_pending_content(
    run_dir: Path,
) -> tuple[dict[str, Any] | None, bool, list[dict[str, Any]]]:
    """Merge timestamped queue records and retain undated legacy records."""
    content, content_generation = _read_json_object(run_dir, "content.json")
    if content is None:
        return None, False, []
    legacy_unknown_ids: set[str] = set()
    aliases: dict[str, str] = {}
    normalized = normalize_document(content, legacy_unknown_ids, aliases)
    states, folded, threads, owners = _collect_thread_state(
        normalized, legacy_unknown_ids
    )
    folded_parents: dict[FoldedKey, list[str]] = {}
    for thread_id, thread in threads.items():
        first = thread["turns"][0]
        if not isinstance(first, dict) or first.get("author") != "human":
            continue
        text = first.get("text")
        if not isinstance(text, str):
            continue
        timestamp = _timestamp_key(first.get("at"))
        if thread_id in legacy_unknown_ids:
            timestamp = None
        key = (None, states[thread_id][0], text, timestamp)
        folded_parents.setdefault(key, []).append(thread_id)
    if not isinstance(normalized, dict):
        return None, False, []
    path = _contained_child(run_dir, "questions.jsonl")
    if path is None:
        return normalized, False, []
    changed = False
    pending: dict[str, list[dict[str, str]]] = {}
    pending_aliases: dict[str, str] = {}
    legacy_records: list[dict[str, Any]] = []
    occurrences: Counter[tuple[str, str, str]] = Counter()
    try:
        with path.open("rb") as queue:
            for record in _question_records(queue):
                parent = record.get("parent_id")
                anchor = record.get("anchor_id")
                text = record.get("text")
                timestamp = record.get("timestamp")
                if (
                    not isinstance(anchor, str)
                    or not isinstance(text, str)
                    or not text.strip()
                    or len(text.strip()) > MAX_QUESTION_LENGTH
                    or (
                        parent is not None
                        and not isinstance(parent, str)
                    )
                ):
                    continue
                text = text.strip()
                if isinstance(parent, str) and parent not in states:
                    if record.get("content_generation") == content_generation:
                        parent = aliases.get(parent, parent)
                    parent = pending_aliases.get(parent, parent)
                    record = {**record, "parent_id": parent}
                timestamp_key = _timestamp_key(timestamp)
                identity = (anchor, text, str(timestamp))
                occurrence = occurrences[identity]
                occurrences[identity] += 1
                key = (parent, anchor, text, timestamp_key)
                pending_id = None
                if parent is None:
                    encoded = (
                        f"{anchor}\0{text}\0{timestamp}\0{occurrence}"
                    ).encode("utf-8", errors="surrogatepass")
                    digest = hashlib.sha256(encoded).hexdigest()[:12]
                    pending_id = f"q-pending-{digest}"
                folded_key = _consume_folded(folded, key)
                if folded_key is not None:
                    targets = folded_parents.get(folded_key)
                    if pending_id is not None and targets:
                        index = -1 if folded_key[3] is None else 0
                        pending_aliases[pending_id] = targets.pop(index)
                    continue
                if not isinstance(timestamp, str) or not isinstance(
                    timestamp_key, datetime
                ):
                    legacy_records.append(record)
                    continue
                state = states.get(parent) if isinstance(parent, str) else None
                thread = threads.get(parent) if isinstance(parent, str) else None
                if parent is not None and (
                    state is None or state[0] != anchor or thread is None
                ):
                    continue
                if parent is None:
                    owner = owners.get(anchor)
                    if owner is None:
                        legacy_records.append(record)
                        continue
                    questions = owner.get("questions")
                    if questions is None:
                        questions = []
                        owner["questions"] = questions
                    if not isinstance(questions, list):
                        continue
                    thread_id = pending_id
                    thread = {
                        "id": thread_id,
                        "anchor": {"kind": "element", "path": anchor},
                        "turns": [{"author": "human", "text": text, "at": timestamp}],
                    }
                    questions.append(thread)
                    states[thread_id] = (anchor, True)
                    threads[thread_id] = thread
                    changed = True
                    continue
                pending.setdefault(parent, []).append(
                    {"author": "human", "text": text, "at": timestamp}
                )
    except OSError:
        return normalized, False, []
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
    return normalized, changed, legacy_records


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


def _timestamp_key(value: Any) -> datetime | str | None:
    """Normalize valid timestamps to instants while preserving bad text."""
    if not isinstance(value, str):
        return None
    return _parse_timestamp(value) or value
