"""Append one direct briefing and retire legacy current state safely."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from visual_brief.render.validate import (
    validate_current_state,
    validate_publish_briefing,
)
from visual_brief.schema import (
    CURRENT_STATE_ROOT,
    LEGACY_ANCHOR_ALIASES_FIELD,
    Update,
    current_state_item_path,
    current_state_lane_path,
    legacy_anchor_aliases,
)
from visual_brief.server.counting import merge_pending_followups
from visual_brief.writes.legacy import (
    LegacyPairs,
    read_for_write,
    settle_legacy_pairs,
)
from visual_brief.writes.lint import report_lint
from visual_brief.writes.panels import _updates, append_update
from visual_brief.writes.runfiles import (
    CliError,
    resolve_run,
    save_document,
    write_transaction,
)

_BRIEFING_FIELDS = {"id", "timestamp", "headline", "summary", "lanes"}
_OLD_ENVELOPE_FIELDS = {"current_state", "changes"}


def publish_command(
    runs_root: Path,
    run_id: str | None,
    payload: Any,
) -> int:
    """Append one direct briefing, archiving legacy state on first use.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.
        payload: One briefing with id, timestamp, headline, summary, and lanes.

    Returns:
        The process exit status.

    Raises:
        CliError: If the briefing or resulting document is invalid, or its id
            already exists.
    """
    briefing = _validate_payload(payload)
    _, run_dir = resolve_run(runs_root, run_id)
    briefing_id = briefing["id"]

    with write_transaction(run_dir):
        document, legacy = read_for_write(run_dir)
        _require_available_id(document, briefing_id)
        merged_legacy = LegacyPairs()
        merged = merge_pending_followups(
            run_dir,
            merged_legacy.undated,
            merged_legacy.sources,
        )
        if merged is not None:
            document = merged
            legacy = merged_legacy
        migrated_threads = _archive_current_state(
            document,
            reserved_id=briefing_id,
        )
        append_update(document, briefing)
        settle_legacy_pairs(run_dir, document, legacy, migrated_threads)
        index_path = save_document(run_dir, document)
    print(f"publish: appended {briefing_id}; rendered {index_path}")
    report_lint(run_dir, document)
    return 0


def _validate_payload(payload: Any) -> Update:
    """Validate and copy one exact agent-authored briefing object."""
    if not isinstance(payload, dict):
        raise CliError("publish payload must be one briefing JSON object")
    if _OLD_ENVELOPE_FIELDS <= set(payload):
        raise CliError(
            "publish no longer accepts the current_state plus changes "
            "envelope; pass one briefing object directly with exactly id, "
            "timestamp, headline, summary, and lanes"
        )
    _reject_agent_questions(payload)
    _require_exact_fields(payload, _BRIEFING_FIELDS, "briefing")
    candidate = copy.deepcopy(payload)
    try:
        validate_publish_briefing(candidate)
    except ValueError as error:
        raise CliError(str(error)) from error
    return cast(Update, candidate)


def _reject_agent_questions(briefing: dict[str, Any]) -> None:
    """Reject conversations anywhere in an agent-authored briefing."""
    def walk(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if key == "questions":
                    raise CliError(
                        f"{child_location} is tool-owned; publish the "
                        "briefing without conversations"
                    )
                walk(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}[{index}]")

    walk(briefing, "briefing")


def _archive_current_state(
    document: dict[str, Any],
    *,
    reserved_id: str,
) -> set[str]:
    """Move a legacy current-state object into the older briefing ledger."""
    state = document.get("current_state")
    if not isinstance(state, dict):
        return set()
    try:
        validate_current_state(state)
    except ValueError as error:
        raise CliError(str(error)) from error
    archive_id = _archive_id(state, _taken_ids(document) | {reserved_id})
    if isinstance(state.get("lanes"), list):
        archive, thread_ids, aliases = _archive_structured_state(
            state,
            archive_id,
        )
        stored_aliases = legacy_anchor_aliases(document)
        stored_aliases.update(aliases)
        document[LEGACY_ANCHOR_ALIASES_FIELD] = stored_aliases
    else:
        archive = _archive_four_claim_state(state, archive_id)
        thread_ids = set()
    _updates(document).append(archive)
    del document["current_state"]
    return thread_ids


def _archive_structured_state(
    state: dict[str, Any],
    archive_id: str,
) -> tuple[dict[str, Any], set[str], dict[str, str]]:
    """Build an ordinary update while retaining all structured state data."""
    archive: dict[str, Any] = {
        "id": archive_id,
        "timestamp": copy.deepcopy(state.get("updated_at")),
        "headline": copy.deepcopy(state.get("headline")),
        "summary": copy.deepcopy(state.get("summary")),
        "lanes": copy.deepcopy(state.get("lanes")),
    }
    if "questions" in state:
        archive["questions"] = copy.deepcopy(state["questions"])
    aliases = {CURRENT_STATE_ROOT: archive_id}
    thread_ids = _rewrite_questions(archive, archive_id)
    lanes = archive.get("lanes")
    if not isinstance(lanes, list):
        return archive, thread_ids, aliases
    for lane in lanes:
        if not isinstance(lane, dict) or not isinstance(lane.get("id"), str):
            continue
        lane_path = f"{archive_id}/{lane['id']}"
        aliases[current_state_lane_path(lane["id"])] = lane_path
        thread_ids.update(_rewrite_questions(lane, lane_path))
        items = lane.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            item_path = f"{lane_path}/{item['id']}"
            aliases[current_state_item_path(item["id"])] = item_path
            thread_ids.update(
                _rewrite_questions(item, item_path)
            )
    return archive, thread_ids, aliases


def _archive_four_claim_state(
    state: dict[str, Any],
    archive_id: str,
) -> dict[str, Any]:
    """Preserve the shipped four claims in one small ordinary briefing."""
    claims = (
        ("goal", "Goal"),
        ("focus", "Working then"),
        ("blocker", "Blocker"),
        ("next", "Next step"),
    )
    items = [
        {
            "id": field,
            "glance": copy.deepcopy(state.get(field)),
            "explanation": f"This was the recorded {label.lower()} claim.",
            "trust": "reported-by-agent",
        }
        for field, label in claims
        if state.get(field) is not None
    ]
    return {
        "id": archive_id,
        "timestamp": copy.deepcopy(state.get("updated_at")),
        "headline": "Archived legacy current state",
        "summary": (
            "This briefing preserves the earlier four-part current-state "
            "record."
        ),
        "lanes": [
            {
                "id": "legacy-current-state",
                "name": "Legacy current state",
                "items": items,
            }
        ],
    }


def _rewrite_questions(owner: dict[str, Any], path: str) -> set[str]:
    """Rewrite one archived owner's thread anchors and return their ids."""
    migrated: set[str] = set()
    questions = owner.get("questions")
    if not isinstance(questions, list):
        return migrated
    for thread in questions:
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("id")
        if isinstance(thread_id, str):
            migrated.add(thread_id)
        anchor = thread.get("anchor")
        if isinstance(anchor, dict):
            anchor["path"] = path
    return migrated


def _archive_id(state: dict[str, Any], taken: set[str]) -> str:
    """Return a deterministic valid id that does not collide in this run."""
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="surrogatepass")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    base = f"archived-current-state-{digest}"
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _taken_ids(document: dict[str, Any]) -> set[str]:
    """Return every valid update id already stored in a document."""
    return {
        update["id"]
        for update in _updates(document)
        if isinstance(update, dict) and isinstance(update.get("id"), str)
    }


def _require_available_id(document: dict[str, Any], update_id: str) -> None:
    """Reject a briefing id that immutable history already owns."""
    if update_id in _taken_ids(document):
        raise CliError(
            f"update {update_id!r} already exists; updates are appended, "
            "never rewritten"
        )


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
