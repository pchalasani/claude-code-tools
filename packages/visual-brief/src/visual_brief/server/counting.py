"""Awaiting-answer accounting for saved threads and queued questions."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from visual_brief.render.threads import normalize_document
from visual_brief.server.queue import MAX_QUEUE_RECORD_BYTES

FoldedKey = tuple[str | None, str, str]


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
    states: dict[str, bool],
    folded: Counter[FoldedKey],
) -> int:
    """Combine saved thread states with a bounded queue scan."""
    path = _contained_child(run_dir, "questions.jsonl")
    if path is None:
        return sum(states.values())
    try:
        with path.open("rb") as queue:
            return _count_queue_stream(queue, states, folded)
    except OSError:
        return sum(states.values())


def _count_queue_stream(
    queue: Any,
    states: dict[str, bool],
    folded: Counter[FoldedKey],
) -> int:
    """Count thread states while ignoring malformed queue records."""
    awaiting = {
        thread_id for thread_id, is_awaiting in states.items() if is_awaiting
    }
    new_threads = 0
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
        anchor = record.get("anchor_id")
        text = record.get("text")
        parent = record.get("parent_id")
        if not isinstance(anchor, str) or not isinstance(text, str):
            continue
        if parent is not None and not isinstance(parent, str):
            continue
        folded_key = (parent, anchor, text)
        if folded[folded_key]:
            folded[folded_key] -= 1
            continue
        if parent is None:
            new_threads += 1
        elif parent in states:
            awaiting.add(parent)
    return len(awaiting) + new_threads


def _discard_record_remainder(queue: Any) -> bool:
    """Discard a long record in bounded chunks and report completeness."""
    while chunk := queue.readline(MAX_QUEUE_RECORD_BYTES + 1):
        if chunk.endswith(b"\n"):
            return True
    return False


def _read_thread_state(
    run_dir: Path,
) -> tuple[dict[str, bool], Counter[FoldedKey]]:
    """Read saved thread states and queue-folding keys from content."""
    content = _read_json_object(run_dir, "content.json")
    if content is None:
        return {}, Counter()
    normalized = normalize_document(content)
    updates = normalized.get("updates")
    if not isinstance(updates, list):
        return {}, Counter()

    states: dict[str, bool] = {}
    folded: Counter[FoldedKey] = Counter()
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("id")
        lanes = update.get("lanes")
        if not isinstance(update_id, str) or not isinstance(lanes, list):
            continue
        for lane in lanes:
            _collect_lane(states, folded, update_id, lane)
    return states, folded


def _collect_lane(
    states: dict[str, bool],
    folded: Counter[FoldedKey],
    update_id: str,
    lane: Any,
) -> None:
    """Collect threads from a recognized lane and its items."""
    if not isinstance(lane, dict):
        return
    lane_id = lane.get("id")
    if not isinstance(lane_id, str):
        return
    lane_anchor = f"{update_id}/{lane_id}"
    _collect_threads(states, folded, lane.get("questions"), lane_anchor)
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
        _collect_threads(states, folded, item.get("questions"), anchor)


def _collect_threads(
    states: dict[str, bool],
    folded: Counter[FoldedKey],
    questions: Any,
    anchor: str,
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
        states[thread_id] = author == "human"
        _collect_human_turns(folded, thread_id, turns, anchor)


def _collect_human_turns(
    folded: Counter[FoldedKey],
    thread_id: str,
    turns: list[Any],
    anchor: str,
) -> None:
    """Collect queue identities for human turns already saved."""
    human_index = 0
    for turn in turns:
        if not isinstance(turn, dict) or turn.get("author") != "human":
            continue
        text = turn.get("text")
        if not isinstance(text, str):
            continue
        parent = None if human_index == 0 else thread_id
        folded[(parent, anchor, text)] += 1
        human_index += 1


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
