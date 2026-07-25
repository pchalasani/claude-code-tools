"""Thread normalization shared by rendering and run accounting."""

from __future__ import annotations

import hashlib
from typing import Any

LEGACY_TIMESTAMP = "1970-01-01T00:00:00Z"


def normalize_document(data: Any) -> Any:
    """Return a deep copy with legacy question pairs converted to threads.

    Args:
        data: Parsed content that may contain iteration-1 question pairs.

    Returns:
        An independent value whose recognized legacy pairs are threads.
    """
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    updates = data.get("updates")
    if not isinstance(updates, list):
        return normalized
    normalized_updates = list(updates)
    normalized["updates"] = normalized_updates
    for update_index, update in enumerate(updates):
        if not isinstance(update, dict):
            continue
        normalized_update = dict(update)
        normalized_updates[update_index] = normalized_update
        update_id = update.get("id")
        lanes = update.get("lanes")
        if not isinstance(update_id, str) or not isinstance(lanes, list):
            continue
        normalized_lanes = list(lanes)
        normalized_update["lanes"] = normalized_lanes
        for lane_index, lane in enumerate(lanes):
            if not isinstance(lane, dict):
                continue
            normalized_lane = dict(lane)
            normalized_lanes[lane_index] = normalized_lane
            lane_id = lane.get("id")
            if not isinstance(lane_id, str):
                continue
            lane_path = f"{update_id}/{lane_id}"
            _normalize_questions(normalized_lane, lane_path)
            items = lane.get("items")
            if not isinstance(items, list):
                continue
            normalized_items = list(items)
            normalized_lane["items"] = normalized_items
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                normalized_item = dict(item)
                normalized_items[item_index] = normalized_item
                item_id = item.get("id")
                if not isinstance(item_id, str):
                    continue
                _normalize_questions(
                    normalized_item,
                    f"{lane_path}/{item_id}",
                )
    return normalized


def thread_is_awaiting(thread: Any) -> bool:
    """Return whether a thread's newest valid turn is human-authored."""
    if not isinstance(thread, dict):
        return False
    turns = thread.get("turns")
    if not isinstance(turns, list) or not turns:
        return False
    newest = turns[-1]
    return isinstance(newest, dict) and newest.get("author") == "human"


def _normalize_questions(owner: dict[str, Any], path: str) -> None:
    """Convert recognized legacy entries on one lane or item."""
    questions = owner.get("questions")
    if not isinstance(questions, list):
        return
    occurrences: dict[str, int] = {}
    converted: list[Any] = []
    for entry in questions:
        if not _is_legacy_pair(entry):
            converted.append(entry)
            continue
        question = entry["question"]
        occurrence = occurrences.get(question, 0)
        occurrences[question] = occurrence + 1
        converted.append(_legacy_thread(entry, path, occurrence))
    owner["questions"] = converted


def _is_legacy_pair(value: Any) -> bool:
    """Return whether a value has the old question-pair shape."""
    return (
        isinstance(value, dict)
        and "question" in value
        and "turns" not in value
    )


def _legacy_thread(
    pair: dict[str, Any],
    path: str,
    occurrence: int,
) -> dict[str, Any]:
    """Convert one legacy pair without mutating its source object."""
    question = pair.get("question")
    timestamp = pair.get("asked_at")
    if not isinstance(timestamp, str) or not timestamp.strip():
        timestamp = LEGACY_TIMESTAMP
    turns = [{"author": "human", "text": question, "at": timestamp}]
    answer = pair.get("answer")
    if isinstance(answer, str) and answer.strip():
        turns.append({"author": "agent", "text": answer, "at": timestamp})
    identity = f"{path}\0{question}\0{occurrence}".encode(
        "utf-8",
        errors="surrogatepass",
    )
    digest = hashlib.sha256(identity).hexdigest()[:12]
    return {
        "id": f"q-{digest}",
        "anchor": {"kind": "element", "path": path},
        "turns": turns,
    }
