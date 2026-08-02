"""Publish replaceable current state and one immutable update together."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

from visual_brief.render.validate import validate_current_state
from visual_brief.schema import (
    PublishState,
    StructuredCurrentState,
    current_state_item_path,
    current_state_lane_path,
)
from visual_brief.writes.legacy import read_for_write, settle_legacy_pairs
from visual_brief.writes.lint import report_lint
from visual_brief.writes.panels import _updates
from visual_brief.writes.queue_view import (
    document_view,
    is_folded,
    queue_records,
)
from visual_brief.writes.runfiles import (
    CliError,
    resolve_run,
    save_document,
    write_transaction,
)

_PAYLOAD_FIELDS = {"current_state", "changes"}
_INPUT_STATE_FIELDS = {"headline", "summary", "lanes"}


def publish_command(
    runs_root: Path,
    run_id: str | None,
    payload: Any,
) -> int:
    """Replace current state and append its matching dated update atomically.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.
        payload: Exact ``current_state`` and ``changes`` publish envelope.

    Returns:
        The process exit status.

    Raises:
        CliError: If the envelope, state, update, or resulting document is
            invalid, or the update id already exists.
    """
    state, changes = _validate_envelope(payload)
    _, run_dir = resolve_run(runs_root, run_id)
    update_id = changes.get("id")
    if not isinstance(update_id, str) or not update_id.strip():
        raise CliError("changes must carry a non-empty id")
    timestamp = changes.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise CliError("changes must carry a timestamp")
    stored_state: StructuredCurrentState = {
        "updated_at": timestamp,
        **copy.deepcopy(state),
    }

    with write_transaction(run_dir):
        document, legacy = read_for_write(run_dir)
        updates = _updates(document)
        if any(
            isinstance(existing, dict) and existing.get("id") == update_id
            for existing in updates
        ):
            raise CliError(
                f"update {update_id!r} already exists; updates are appended, "
                "never rewritten"
            )
        _carry_state_conversations(document.get("current_state"), stored_state)
        _reject_unfolded_owner_removal(
            run_dir,
            document,
            stored_state,
            legacy.undated,
        )
        try:
            validate_current_state(stored_state)
        except ValueError as error:
            raise CliError(str(error)) from error
        document["current_state"] = stored_state
        updates.append(changes)
        settle_legacy_pairs(run_dir, document, legacy)
        index_path = save_document(run_dir, document)
    print(f"publish: updated state and appended {update_id}; rendered {index_path}")
    report_lint(run_dir, document)
    return 0


def _validate_envelope(
    payload: Any,
) -> tuple[PublishState, dict[str, Any]]:
    """Validate and return the two exact publish-envelope objects."""
    if not isinstance(payload, dict):
        raise CliError("publish payload must be a JSON object")
    _require_exact_fields(payload, _PAYLOAD_FIELDS, "publish payload")
    state = payload.get("current_state")
    if not isinstance(state, dict):
        raise CliError("current_state must be an object")
    _reject_agent_questions(state)
    _require_exact_fields(state, _INPUT_STATE_FIELDS, "current_state")
    changes = payload.get("changes")
    if not isinstance(changes, dict):
        raise CliError("changes must be a JSON object")
    return cast(PublishState, copy.deepcopy(state)), changes


def _reject_agent_questions(state: dict[str, Any]) -> None:
    """Reject conversations in agent-authored replacement state."""
    def walk(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if key == "questions":
                    raise CliError(
                        f"{child_location} is tool-owned; publish "
                        "current_state without conversations"
                    )
                walk(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}[{index}]")

    walk(state, "current_state")


def _carry_state_conversations(
    previous: Any,
    replacement: StructuredCurrentState,
) -> None:
    """Carry chats by stable state identity or reject their removal."""
    old = _state_owners(previous)
    if old is None:
        return
    new = _state_owners(replacement)
    if new is None:
        return
    for kind, old_owners, new_owners in (
        ("lane", old[0], new[0]),
        ("item", old[1], new[1]),
    ):
        for owner_id, old_owner in old_owners.items():
            questions = _owned_questions(old_owner)
            if not questions:
                continue
            new_owner = new_owners.get(owner_id)
            if new_owner is None:
                raise CliError(
                    "publish would remove current-state "
                    f"{kind} {owner_id!r}, which owns conversations; "
                    f"keep the same {kind} id"
                )
            new_owner["questions"] = copy.deepcopy(questions)
    root_questions = _owned_questions(previous)
    if root_questions:
        replacement["questions"] = copy.deepcopy(root_questions)


def _reject_unfolded_owner_removal(
    run_dir: Path,
    document: dict[str, Any],
    replacement: StructuredCurrentState,
    legacy_unknown_ids: set[str],
) -> None:
    """Reject removing a state owner targeted by an unmatched queue line."""
    old = _state_owners(document.get("current_state"))
    new = _state_owners(replacement)
    if old is None or new is None:
        return
    removed: dict[str, tuple[str, str]] = {}
    for kind, old_owners, new_owners, owner_path in (
        ("lane", old[0], new[0], current_state_lane_path),
        ("item", old[1], new[1], current_state_item_path),
    ):
        for owner_id in old_owners.keys() - new_owners.keys():
            removed[owner_path(owner_id)] = (kind, owner_id)
    if not removed:
        return
    view = document_view(document, legacy_unknown_ids)
    for record in queue_records(run_dir):
        owner = removed.get(record.anchor_id)
        if owner is None or is_folded(record, view):
            continue
        if (
            record.parent_id is not None
            and view.thread_anchors.get(record.parent_id) != record.anchor_id
        ):
            continue
        kind, owner_id = owner
        raise CliError(
            "publish would remove current-state "
            f"{kind} {owner_id!r}, which has an unmatched queued question; "
            f"keep the same {kind} id"
        )


def _state_owners(
    state: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None:
    """Index structured state lanes and globally identified items."""
    if not isinstance(state, dict) or not isinstance(state.get("lanes"), list):
        return None
    lanes: dict[str, dict[str, Any]] = {}
    items: dict[str, dict[str, Any]] = {}
    for lane in state["lanes"]:
        if not isinstance(lane, dict) or not isinstance(lane.get("id"), str):
            continue
        lanes[lane["id"]] = lane
        lane_items = lane.get("items")
        if not isinstance(lane_items, list):
            continue
        for item in lane_items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                items[item["id"]] = item
    return lanes, items


def _owned_questions(owner: Any) -> list[Any]:
    """Return one owner's non-empty conversation list."""
    questions = owner.get("questions") if isinstance(owner, dict) else None
    return questions if isinstance(questions, list) and questions else []


def _require_exact_fields(
    value: dict[str, Any],
    expected: set[str],
    location: str,
) -> None:
    """Raise a CLI error unless an object carries exactly expected fields."""
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(str(field) for field in actual - expected)
    details: list[str] = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unexpected:
        details.append(f"unexpected {', '.join(unexpected)}")
    raise CliError(
        f"{location} must have exactly {', '.join(sorted(expected))}; "
        + "; ".join(details)
    )
