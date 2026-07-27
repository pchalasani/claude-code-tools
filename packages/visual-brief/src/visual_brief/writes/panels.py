"""Publish the Now panel, and append dated history updates.

The Now panel is rewritten in place on every publish, which is exactly how a
conversation hanging from it gets lost. So the rewrite carries conversations
forward itself wherever their anchor still exists, and anything it cannot
carry is printed in full rather than dropped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from visual_brief.writes.legacy import read_for_write, settle_legacy_pairs
from visual_brief.writes.lint import report_lint
from visual_brief.writes.runfiles import (
    CliError,
    local_timestamp,
    resolve_run,
    save_document,
)

NOW_ID = "now"


def publish_now_command(runs_root: Path, run_id: str | None, panel: Any) -> int:
    """Replace the Now panel with a freshly written one.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.
        panel: The new Now update, as a JSON object.

    Returns:
        The process exit status.

    Raises:
        CliError: If the panel is not an object, or the resulting document
            would not validate.
    """
    _, run_dir = resolve_run(runs_root, run_id)
    if not isinstance(panel, dict):
        raise CliError("the Now panel must be a JSON object")
    panel = dict(panel)
    panel["id"] = NOW_ID
    stamp = panel.get("timestamp")
    if not isinstance(stamp, str) or not stamp.strip():
        panel["timestamp"] = local_timestamp()

    document, legacy = read_for_write(run_dir)
    updates = _updates(document)
    position = _now_position(updates)
    previous = updates[position] if position is not None else None
    carried, orphans = _carry_threads(previous, panel, updates)
    if position is None:
        updates.append(panel)
    else:
        updates[position] = panel
    settle_legacy_pairs(run_dir, document, legacy)
    index_path = save_document(run_dir, document)

    for path, thread in orphans:
        print(
            f"warning: a conversation from {path} could not be carried "
            "forward; that anchor is not in the new Now panel",
            file=sys.stderr,
        )
        print(json.dumps(thread, ensure_ascii=False, indent=2), file=sys.stderr)
    print(
        f"publish-now: carried {_conversations(len(carried))}, "
        f"{len(orphans)} not carried; rendered {index_path}"
    )
    report_lint(run_dir, document)
    return 0


def add_update_command(runs_root: Path, run_id: str | None, update: Any) -> int:
    """Append one dated update to the run's history.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.
        update: The update, as a JSON object.

    Returns:
        The process exit status.

    Raises:
        CliError: If the update is not a dated, uniquely identified object,
            or the resulting document would not validate.
    """
    _, run_dir = resolve_run(runs_root, run_id)
    if not isinstance(update, dict):
        raise CliError("an update must be a JSON object")
    update_id = update.get("id")
    if not isinstance(update_id, str) or not update_id.strip():
        raise CliError("the update must carry a non-empty id")
    if update_id == NOW_ID:
        raise CliError(
            f"id {NOW_ID!r} belongs to the Now panel; publish it with "
            "'visual-brief publish-now'"
        )
    stamp = update.get("timestamp")
    if not isinstance(stamp, str) or not stamp.strip():
        raise CliError("a dated update must carry a timestamp")

    document, legacy = read_for_write(run_dir)
    updates = _updates(document)
    if any(
        isinstance(existing, dict) and existing.get("id") == update_id
        for existing in updates
    ):
        raise CliError(
            f"update {update_id!r} already exists; history is appended, "
            "never rewritten"
        )
    updates.append(update)
    settle_legacy_pairs(run_dir, document, legacy)
    index_path = save_document(run_dir, document)
    print(f"add-update: appended {update_id}; rendered {index_path}")
    report_lint(run_dir, document)
    return 0


def _conversations(count: int) -> str:
    """Name a count of conversations."""
    return f"{count} conversation" if count == 1 else f"{count} conversations"


def _updates(document: Any) -> list[Any]:
    """Return the document's own updates list."""
    updates = document.get("updates") if isinstance(document, dict) else None
    if not isinstance(updates, list):
        raise CliError("content.json has no updates list")
    return updates


def _now_position(updates: list[Any]) -> int | None:
    """Return where the Now panel sits among the updates."""
    for index, update in enumerate(updates):
        if isinstance(update, dict) and update.get("id") == NOW_ID:
            return index
    return None


def _carry_threads(
    previous: Any,
    panel: dict[str, Any],
    updates: list[Any],
) -> tuple[list[tuple[str, Any]], list[tuple[str, Any]]]:
    """Move the old panel's conversations onto the anchors that survive."""
    if not isinstance(previous, dict):
        return [], []
    owners = _panel_owners(panel)
    taken = {
        thread_id
        for update in updates
        if update is not previous
        for _, thread_id in _thread_ids(update)
    }
    taken.update(thread_id for _, thread_id in _thread_ids(panel))
    carried: list[tuple[str, Any]] = []
    orphans: list[tuple[str, Any]] = []
    for path, thread in _panel_threads(previous):
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if isinstance(thread_id, str) and thread_id in taken:
            continue
        owner = owners.get(path)
        questions = owner.get("questions") if owner is not None else None
        if owner is None or not isinstance(thread_id, str):
            orphans.append((path, thread))
            continue
        if questions is None:
            questions = []
            owner["questions"] = questions
        if not isinstance(questions, list):
            orphans.append((path, thread))
            continue
        questions.append(thread)
        taken.add(thread_id)
        carried.append((path, thread))
    return carried, orphans


def _panel_owners(update: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map every anchor path in one update to the lane or item at it."""
    owners: dict[str, dict[str, Any]] = {}
    for path, owner in _panel_walk(update):
        owners[path] = owner
    return owners


def _panel_threads(update: dict[str, Any]) -> list[tuple[str, Any]]:
    """List every conversation in one update with its anchor path."""
    found: list[tuple[str, Any]] = []
    for path, owner in _panel_walk(update):
        questions = owner.get("questions")
        if isinstance(questions, list):
            found.extend((path, thread) for thread in questions)
    return found


def _thread_ids(update: Any) -> list[tuple[str, str]]:
    """List the anchor path and id of every identified conversation."""
    if not isinstance(update, dict):
        return []
    return [
        (path, thread["id"])
        for path, thread in _panel_threads(update)
        if isinstance(thread, dict) and isinstance(thread.get("id"), str)
    ]


def _panel_walk(update: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Walk one update's lanes and items in anchor-path order."""
    update_id = update.get("id")
    lanes = update.get("lanes")
    if not isinstance(update_id, str) or not isinstance(lanes, list):
        return []
    walked: list[tuple[str, dict[str, Any]]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lane_id = lane.get("id")
        if not isinstance(lane_id, str):
            continue
        lane_path = f"{update_id}/{lane_id}"
        walked.append((lane_path, lane))
        items = lane.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str):
                walked.append((f"{lane_path}/{item_id}", item))
    return walked
