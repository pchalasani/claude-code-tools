"""Mechanical warnings about content that renders badly or misdates itself.

Every check here is decidable from the bytes: no check needs an opinion about
whether the writing is good. The verbs run these checks after every write and
``visual-brief render`` runs them too, so an agent sees the warning in the
same breath as the change that caused it.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from visual_brief.render.threads import is_legacy_pair, normalize_document
from visual_brief.schema import (
    CURRENT_STATE_ROOT,
    current_state_item_path,
    current_state_lane_path,
)
from visual_brief.server.counting_io import _contained_child
from visual_brief.writes.queue_view import (
    document_view,
    is_folded,
    parse_timestamp,
    queue_records,
)
from visual_brief.writes.runfiles import read_content, resolve_run

MAX_GLANCE_LENGTH = 200
MAX_INLINE_MARKERS = 2
EPOCH_LIMIT = datetime(1971, 1, 1, tzinfo=timezone.utc)

_NUMBERED_MARKER = re.compile(r"(?<![\w.])\d{1,3}[.)]\s")
_BULLET_MARKER = re.compile(r"(?m)^[ \t]*[-*•]\s")


def lint_document(
    data: Any,
    settled_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
    """Check one document for mechanical content faults.

    Args:
        data: A parsed content document, before or after normalization.
        settled_pairs: The anchor path and question text of every legacy
            pair whose own queue line still matches it. Those pairs are
            deliberately left as they are, so nothing is said about them.

    Returns:
        One message per fault, in document order.
    """
    warnings: list[str] = []
    if not isinstance(data, dict):
        return warnings
    state = data.get("current_state")
    if isinstance(state, dict) and isinstance(state.get("lanes"), list):
        warnings.extend(
            _lint_threads(
                state.get("questions"),
                CURRENT_STATE_ROOT,
                settled_pairs,
            )
        )
        for lane in state["lanes"]:
            warnings.extend(
                _lint_lane(lane, True, settled_pairs)
            )
    updates = data.get("updates")
    if not isinstance(updates, list):
        return warnings
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("id")
        lanes = update.get("lanes")
        if not isinstance(update_id, str) or not isinstance(lanes, list):
            continue
        for lane in lanes:
            warnings.extend(
                _lint_lane(lane, False, settled_pairs, update_id)
            )
    return warnings


def _lint_lane(
    lane: Any,
    current_state: bool,
    settled_pairs: frozenset[tuple[str, str]],
    update_id: str = "",
) -> list[str]:
    """Lint one lane using the anchor namespace it belongs to."""
    if not isinstance(lane, dict) or not isinstance(lane.get("id"), str):
        return []
    lane_id = lane["id"]
    lane_path = (
        current_state_lane_path(lane_id)
        if current_state
        else f"{update_id}/{lane_id}"
    )
    warnings = _lint_threads(
        lane.get("questions"),
        lane_path,
        settled_pairs,
    )
    items = lane.get("items")
    if not isinstance(items, list):
        return warnings
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        item_path = (
            current_state_item_path(item["id"])
            if current_state
            else f"{lane_path}/{item['id']}"
        )
        warnings.extend(_lint_item(item, item_path, settled_pairs))
    return warnings


def lint_run(run_dir: Path, data: Any) -> list[str]:
    """Check a document and the queue standing beside it.

    Args:
        run_dir: The run directory.
        data: The document as it now stands on disk.

    Returns:
        Every warning, document faults first.
    """
    return [
        *lint_document(data, _settled_legacy_pairs(run_dir, data)),
        *_lint_queue(run_dir, data),
    ]


def report_lint(run_dir: Path, data: Any) -> list[str]:
    """Print a run's warnings to stderr and return them.

    Args:
        run_dir: The run directory.
        data: The document as it now stands on disk.

    Returns:
        The warnings that were printed.
    """
    warnings = lint_run(run_dir, data)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return warnings


def lint_command(runs_root: Path, run_id: str | None, strict: bool) -> int:
    """Check one run and report what the checks found.

    Args:
        runs_root: Directory holding every run.
        run_id: Explicit run identifier, or ``None`` for the only run.
        strict: Whether any warning should fail the command.

    Returns:
        The process exit status: 2 under ``--strict`` with warnings, else 0.
    """
    _, run_dir = resolve_run(runs_root, run_id)
    warnings = report_lint(run_dir, read_content(run_dir))
    if not warnings:
        print("lint: clean")
        return 0
    subject = "warning" if len(warnings) == 1 else "warnings"
    print(f"lint: {len(warnings)} {subject}")
    return 2 if strict else 0


def _lint_item(
    item: dict[str, Any],
    path: str,
    settled_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
    """Check one item's prose fields and its conversations."""
    warnings: list[str] = []
    glance = item.get("glance")
    if isinstance(glance, str) and len(glance) > MAX_GLANCE_LENGTH:
        warnings.append(
            f"{path}: glance is {len(glance)} characters; it is a one-line "
            f"claim (at most {MAX_GLANCE_LENGTH}) — move the rest into "
            "explanation"
        )
    for field in ("glance", "explanation"):
        value = item.get(field)
        if isinstance(value, str) and _enumeration_markers(value) > (
            MAX_INLINE_MARKERS
        ):
            warnings.append(
                f"{path}: {field} holds an enumeration; N things are N items, "
                "N table rows or N forensic notes, not one crammed paragraph"
            )
    warnings.extend(_lint_threads(item.get("questions"), path, settled_pairs))
    return warnings


def _lint_threads(
    questions: Any,
    path: str,
    settled_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> list[str]:
    """Check the conversations hanging from one lane or item.

    A legacy pair is worth naming only while converting it is still safe.
    The one conversion that never misdates a pair carries the instant from
    the pair's own queue line, so a pair that line still matches is left
    alone on purpose and says nothing here.
    """
    if not isinstance(questions, list):
        return []
    warnings: list[str] = []
    for entry in questions:
        if is_legacy_pair(entry):
            if (path, entry.get("question")) in settled_pairs:
                continue
            warnings.append(
                f"{path}: a legacy {{question, answer}} pair; write a thread "
                "of turns carrying the instant the question was asked at — "
                "take it from the pair's own queue line, never from the "
                "clock and never the 1970 epoch an undated pair lands at"
            )
            continue
        if not isinstance(entry, dict):
            continue
        thread_id = entry.get("id")
        turns = entry.get("turns")
        if not isinstance(thread_id, str) or not isinstance(turns, list):
            continue
        warnings.extend(_lint_turns(turns, f"{path}#{thread_id}"))
    return warnings


def _lint_turns(turns: list[Any], path: str) -> list[str]:
    """Check one conversation's turn text and turn timestamps.

    The enumeration check is about the shape of what the agent wrote, so it
    only reads agent turns. A human turn is the human's own words, copied out
    of the queue byte for byte; telling the agent to split it up would be
    telling it to paraphrase the human, which is exactly what folding exists
    to prevent. The epoch check reads every turn, because an epoch date is
    always the agent's doing.
    """
    warnings: list[str] = []
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            continue
        text = turn.get("text")
        if (
            turn.get("author") == "agent"
            and isinstance(text, str)
            and _enumeration_markers(text) > MAX_INLINE_MARKERS
        ):
            warnings.append(
                f"{path}: turn {index + 1} holds an enumeration; a turn is "
                "one flowing thought, so split the list into items or notes"
            )
        at = parse_timestamp(turn.get("at"))
        if at is not None and at < EPOCH_LIMIT:
            warnings.append(
                f"{path}: turn {index + 1} is dated {turn.get('at')}, at the "
                "1970 epoch; carry the real timestamp of the turn"
            )
    return warnings


def _settled_legacy_pairs(
    run_dir: Path,
    data: Any,
) -> frozenset[tuple[str, str]]:
    """Name the legacy pairs whose own queue line still matches them.

    Such a pair is the one shape a verb deliberately preserves: rewriting
    it moves its instant off the line it was asked on, the accounting stops
    seeing that line as folded, and the question returns as a phantom
    duplicate. Telling the agent to convert it would ask for exactly that.

    Args:
        run_dir: The run directory.
        data: The document as it now stands on disk.

    Returns:
        The anchor path and question text of every matched pair.
    """
    records = queue_records(run_dir)
    if not records:
        return frozenset()
    legacy_unknown_ids: set[str] = set()
    sources: dict[str, Any] = {}
    document = normalize_document(data, legacy_unknown_ids, None, sources)
    if not sources:
        return frozenset()
    view = document_view(document, legacy_unknown_ids)
    pairs = {
        (view.thread_anchors[thread_id], pair["question"])
        for thread_id, pair in sources.items()
        if thread_id in view.thread_anchors
        and isinstance(pair.get("question"), str)
    }
    settled: set[tuple[str, str]] = set()
    for record in records:
        if record.parent_id is not None:
            continue
        asked = (record.anchor_id, record.text)
        if asked in pairs and is_folded(record, view):
            settled.add(asked)
    return frozenset(settled)


def _lint_queue(run_dir: Path, data: Any) -> list[str]:
    """Report queue lines that outlived the newest content write."""
    records = queue_records(run_dir)
    if not records:
        return []
    legacy_unknown_ids: set[str] = set()
    view = document_view(
        normalize_document(data, legacy_unknown_ids), legacy_unknown_ids
    )
    written_at = _content_written_at(run_dir)
    if written_at is None:
        return []
    stale = 0
    for record in records:
        if is_folded(record, view):
            continue
        arrived = parse_timestamp(record.timestamp)
        if arrived is not None and arrived < written_at:
            stale += 1
    if stale == 0:
        return []
    if stale == 1:
        subject = "1 queued question arrived before the newest content write "
        subject += "and is"
    else:
        subject = f"{stale} queued questions arrived before the newest "
        subject += "content write and are"
    return [f"{subject} still not in the page; run `visual-brief fold`"]


def _content_written_at(run_dir: Path) -> datetime | None:
    """Return when the content document was last written."""
    path = _contained_child(run_dir, "content.json")
    if path is None:
        return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _enumeration_markers(text: str) -> int:
    """Count list markers crammed into one prose field.

    Numbered markers count wherever they appear, because ``1.``…``2.``…``3.``
    inside a paragraph is the shape that renders as a jumbled wall. A dash
    counts only at the start of a line: inside a sentence a dash is ordinary
    punctuation, and a check that fires on every write must not cry wolf.

    Args:
        text: One prose field or turn.

    Returns:
        How many list markers the text carries.
    """
    return len(_NUMBERED_MARKER.findall(text)) + len(
        _BULLET_MARKER.findall(text)
    )
