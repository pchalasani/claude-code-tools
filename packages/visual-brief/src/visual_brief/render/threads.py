"""Thread normalization shared by rendering and run accounting."""

from __future__ import annotations

import hashlib
from typing import Any

LEGACY_TIMESTAMP = "1970-01-01T00:00:00Z"
UPDATES_ORDER_FIELD = "updates_order"
APPEND_ORDER = "append"


def normalize_document(
    data: Any,
    legacy_unknown_ids: set[str] | None = None,
    legacy_id_aliases: dict[str, str] | None = None,
    legacy_sources: dict[str, Any] | None = None,
) -> Any:
    """Return a deep copy with legacy question pairs converted to threads.

    Args:
        data: Parsed content that may contain iteration-1 question pairs.
        legacy_unknown_ids: Optional collector for converted pairs without an
            original ``asked_at`` timestamp.
        legacy_id_aliases: Optional mapping from prior generated IDs to their
            timestamp-stable replacements.
        legacy_sources: Optional mapping from each converted thread's id back
            to the pair it came from, so a caller that only reads the
            conversion can still write the original bytes back.

    Returns:
        An independent value whose recognized legacy pairs are threads.
    """
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    updates = data.get("updates")
    if not isinstance(updates, list):
        return normalized
    normalized_updates = _migrate_pinned_now(data, updates)
    normalized[UPDATES_ORDER_FIELD] = APPEND_ORDER
    normalized["updates"] = normalized_updates
    for update_index, update in enumerate(normalized_updates):
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
            _normalize_questions(
                normalized_lane,
                lane_path,
                legacy_unknown_ids,
                legacy_id_aliases,
                legacy_sources,
            )
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
                    legacy_unknown_ids,
                    legacy_id_aliases,
                    legacy_sources,
                )
    return normalized


def _migrate_pinned_now(
    document: dict[str, Any],
    updates: list[Any],
) -> list[Any]:
    """Move the former pinned panel into chronological append order.

    The old writer kept its rewritten ``now`` update in place while later
    history accumulated behind it. A run's initial ``created`` update may
    precede that panel. Moving ``now`` behind the existing history records its
    final timestamp in append chronology. The new writer persists
    ``updates_order: append``, which explicitly distinguishes a migrated
    document from an old pinned-panel document. A sole update remains in place;
    the marker is persisted before a writer appends its successor.

    Args:
        document: The complete saved document.
        updates: Updates in their saved order.

    Returns:
        A copied update list in append order.
    """
    copied = list(updates)
    if document.get(UPDATES_ORDER_FIELD) == APPEND_ORDER:
        return copied
    for index, update in enumerate(copied[:-1]):
        if isinstance(update, dict) and update.get("id") == "now":
            copied.append(copied.pop(index))
            break
    return copied


def thread_is_awaiting(thread: Any) -> bool:
    """Return whether a thread's newest valid turn is human-authored."""
    if not isinstance(thread, dict):
        return False
    turns = thread.get("turns")
    if not isinstance(turns, list) or not turns:
        return False
    newest = turns[-1]
    return isinstance(newest, dict) and newest.get("author") == "human"


def _normalize_questions(
    owner: dict[str, Any],
    path: str,
    legacy_unknown_ids: set[str] | None,
    legacy_id_aliases: dict[str, str] | None,
    legacy_sources: dict[str, Any] | None = None,
) -> None:
    """Convert recognized legacy entries on one lane or item."""
    questions = owner.get("questions")
    if not isinstance(questions, list):
        return
    prior_occurrences: dict[str, int] = {}
    timestamp_occurrences: dict[tuple[str, str], int] = {}
    prior_timestamp_occurrences: dict[tuple[str, str], int] = {}
    undated_remaining: dict[str, int] = {}
    for entry in questions:
        if not is_legacy_pair(entry):
            continue
        asked_at = entry.get("asked_at")
        if not isinstance(asked_at, str) or not asked_at.strip():
            question = entry["question"]
            undated_remaining[question] = undated_remaining.get(question, 0) + 1
    converted: list[Any] = []
    for entry in questions:
        if not is_legacy_pair(entry):
            converted.append(entry)
            continue
        question = entry["question"]
        asked_at = entry.get("asked_at")
        has_timestamp = isinstance(asked_at, str) and bool(asked_at.strip())
        timestamp = (
            asked_at.strip()
            if has_timestamp
            else LEGACY_TIMESTAMP
        )
        timestamp_identity = (question, timestamp)
        timestamp_occurrence = timestamp_occurrences.get(timestamp_identity, 0)
        prior_timestamp_occurrence = prior_timestamp_occurrences.get(
            timestamp_identity, 0
        )
        prior_timestamp_occurrences[timestamp_identity] = (
            prior_timestamp_occurrence + 1
        )
        if not has_timestamp:
            undated_remaining[question] -= 1
            occurrence = undated_remaining[question]
        else:
            timestamp_occurrences[timestamp_identity] = timestamp_occurrence + 1
            occurrence = timestamp_occurrence
        thread = _legacy_thread(entry, path, occurrence)
        prior_occurrence = prior_occurrences.get(question, 0)
        prior_occurrences[question] = prior_occurrence + 1
        if legacy_id_aliases is not None:
            prior_identity = f"{path}\0{question}\0{prior_occurrence}".encode(
                "utf-8", errors="surrogatepass"
            )
            prior_digest = hashlib.sha256(prior_identity).hexdigest()[:12]
            legacy_id_aliases[f"q-{prior_digest}"] = thread["id"]
            timestamp_identity_bytes = (
                f"{path}\0{question}\0{timestamp}\0"
                f"{prior_timestamp_occurrence}"
            ).encode("utf-8", errors="surrogatepass")
            timestamp_digest = hashlib.sha256(
                timestamp_identity_bytes
            ).hexdigest()[:12]
            legacy_id_aliases[f"q-{timestamp_digest}"] = thread["id"]
        converted.append(thread)
        if legacy_sources is not None:
            legacy_sources[thread["id"]] = entry
        if (
            legacy_unknown_ids is not None
            and (not isinstance(asked_at, str) or not asked_at.strip())
        ):
            legacy_unknown_ids.add(thread["id"])
    owner["questions"] = converted


def is_legacy_pair(value: Any) -> bool:
    """Return whether a value has the old question-pair shape.

    Args:
        value: A candidate entry from a ``questions`` list.

    Returns:
        True for the iteration-1 ``{question, answer}`` shape.
    """
    return (
        isinstance(value, dict)
        and "question" in value
        and isinstance(value.get("question"), str)
        and "turns" not in value
    )


def legacy_pair_turns(pair: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the turns one legacy pair converts to.

    Args:
        pair: An iteration-1 ``{question, answer}`` entry.

    Returns:
        The converted turns. A caller holding a converted thread can compare
        against these to tell an untouched conversion from an edited one.
    """
    timestamp, _ = _legacy_timestamp(pair)
    turns = [
        {"author": "human", "text": pair.get("question"), "at": timestamp}
    ]
    answer = pair.get("answer")
    if isinstance(answer, str) and answer.strip():
        turns.append({"author": "agent", "text": answer, "at": timestamp})
    return turns


def _legacy_timestamp(pair: dict[str, Any]) -> tuple[str, bool]:
    """Return a pair's conversion instant, and whether the pair carried it."""
    timestamp = pair.get("asked_at")
    if isinstance(timestamp, str) and timestamp.strip():
        return timestamp.strip(), True
    return LEGACY_TIMESTAMP, False


def _legacy_thread(
    pair: dict[str, Any],
    path: str,
    occurrence: int,
) -> dict[str, Any]:
    """Convert one legacy pair without mutating its source object."""
    question = pair.get("question")
    timestamp, has_timestamp = _legacy_timestamp(pair)
    turns = legacy_pair_turns(pair)
    if not has_timestamp:
        identity_version = "\0undated-v2"
    elif timestamp == LEGACY_TIMESTAMP:
        identity_version = "\0dated-v2"
    else:
        identity_version = ""
    identity = (
        f"{path}\0{question}\0{timestamp}{identity_version}\0{occurrence}"
    ).encode(
        "utf-8",
        errors="surrogatepass",
    )
    digest = hashlib.sha256(identity).hexdigest()[:12]
    return {
        "id": f"q-{digest}",
        "anchor": {"kind": "element", "path": path},
        "turns": turns,
    }
