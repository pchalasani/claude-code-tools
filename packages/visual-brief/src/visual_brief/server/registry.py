"""Discover visual-brief runs and summarize their state."""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from visual_brief.server.queue import MAX_QUEUE_RECORD_BYTES


RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
_EPOCH = datetime.fromtimestamp(0, timezone.utc)
_RUN_FILES = ("content.json", "index.html", "questions.jsonl", "meta.json")


@dataclass(frozen=True, slots=True)
class RunInfo:
    """Dashboard-facing information about one run."""

    run_id: str
    label: str
    cwd: str | None
    repo: str | None
    branch: str | None
    created_at: str | None
    updated_at: str | None
    activity_at: datetime
    unanswered_count: int
    degraded: bool = False


def is_valid_run_id(run_id: str) -> bool:
    """Return whether a value exactly matches the run-id contract."""
    return (
        isinstance(run_id, str)
        and RUN_ID_PATTERN.fullmatch(run_id) is not None
    )


def validate_run_id(run_id: str) -> str:
    """Validate and return a run identifier.

    Args:
        run_id: Untrusted identifier from a command, host, or URL.

    Returns:
        The validated identifier.

    Raises:
        ValueError: If the identifier does not match the run-id contract.
    """
    if not is_valid_run_id(run_id):
        raise ValueError(
            "run id must match "
            "^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$"
        )
    return run_id


def resolve_run_path(runs_root: Path, run_id: str) -> Path:
    """Resolve a validated run path and enforce containment.

    Args:
        runs_root: Directory containing all runs.
        run_id: Untrusted run identifier.

    Returns:
        The resolved path for the run.

    Raises:
        ValueError: If the id is invalid or the path escapes the runs root.
    """
    validated = validate_run_id(run_id)
    try:
        root = runs_root.expanduser().resolve()
        lexical_candidate = root / validated
        try:
            candidate_mode = lexical_candidate.lstat().st_mode
        except FileNotFoundError:
            candidate_mode = None
        if candidate_mode is not None:
            if not any(entry.name == validated for entry in root.iterdir()):
                raise ValueError(
                    f"run path name must exactly match run id: {run_id}"
                )
            if stat.S_ISLNK(candidate_mode):
                raise ValueError(f"run path may not be a symlink: {run_id}")
        candidate = lexical_candidate.resolve()
    except (OSError, RuntimeError) as error:
        raise ValueError(f"cannot resolve run path: {run_id}") from error
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError(f"run path escapes runs root: {run_id}")
    return candidate


def discover_runs(runs_root: Path) -> list[RunInfo]:
    """Scan a runs directory without failing on damaged entries.

    Args:
        runs_root: Directory containing run subdirectories.

    Returns:
        Valid, contained runs ordered by newest activity first.
    """
    try:
        root = runs_root.expanduser().resolve()
        entries = list(root.iterdir())
    except (OSError, RuntimeError):
        return []

    runs: list[RunInfo] = []
    for entry in entries:
        try:
            run_id = validate_run_id(entry.name)
            run_path = resolve_run_path(root, run_id)
            if not run_path.is_dir():
                continue
            runs.append(_read_run(run_path, run_id))
        except (OSError, ValueError):
            continue
    return sorted(
        runs,
        key=lambda run: (run.activity_at, run.run_id),
        reverse=True,
    )


def count_unanswered_questions(run_dir: Path) -> int:
    """Count queued questions without matching answers.

    Matching is intentionally centralized here for iteration 1. A queued
    question is answered only when content contains a non-empty answer at the
    same anchor and with the same question text.

    Args:
        run_dir: One resolved run directory.

    Returns:
        The number of valid question records lacking an answer.
    """
    try:
        root = run_dir.expanduser().resolve()
    except (OSError, RuntimeError):
        return 0
    return _count_queued_questions(root, _read_answered_questions(root))


def _read_run(run_path: Path, run_id: str) -> RunInfo:
    """Read one run, degrading missing or malformed metadata."""
    metadata = _read_json_object(run_path, "meta.json")
    degraded = metadata is None
    if metadata is None:
        metadata = {}

    metadata_id = _optional_text(metadata.get("run_id"))
    label = _optional_text(metadata.get("label"))
    if metadata_id != run_id or label is None:
        degraded = True
    if label is None:
        label = f"{run_id} (metadata unavailable)"

    updated_at = _optional_text(metadata.get("updated_at"))
    if metadata.get("updated_at") is not None and _parse_timestamp(updated_at) is None:
        degraded = True
    activity = _activity_time(run_path, updated_at)
    return RunInfo(
        run_id=run_id,
        label=label,
        cwd=_optional_text(metadata.get("cwd")),
        repo=_optional_text(metadata.get("repo")),
        branch=_optional_text(metadata.get("branch")),
        created_at=_optional_text(metadata.get("created_at")),
        updated_at=updated_at,
        activity_at=activity,
        unanswered_count=count_unanswered_questions(run_path),
        degraded=degraded,
    )


def _count_queued_questions(
    run_dir: Path,
    answered: set[tuple[str, str]],
) -> int:
    """Count valid unanswered queue records using bounded memory."""
    path = _contained_child(run_dir, "questions.jsonl")
    if path is None:
        return 0
    try:
        with path.open("rb") as queue:
            return _count_queue_stream(queue, answered)
    except OSError:
        return 0


def _count_queue_stream(
    queue: Any,
    answered: set[tuple[str, str]],
) -> int:
    """Count valid records from a binary JSONL stream."""
    count = 0
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
        if (
            isinstance(anchor, str)
            and isinstance(text, str)
            and (anchor, text) not in answered
        ):
            count += 1
    return count


def _discard_record_remainder(queue: Any) -> bool:
    """Discard a long record in bounded chunks and report completeness."""
    while chunk := queue.readline(MAX_QUEUE_RECORD_BYTES + 1):
        if chunk.endswith(b"\n"):
            return True
    return False


def _read_answered_questions(run_dir: Path) -> set[tuple[str, str]]:
    """Read anchor-and-text keys for answered questions in content."""
    content = _read_json_object(run_dir, "content.json")
    if content is None:
        return set()
    updates = content.get("updates")
    if not isinstance(updates, list):
        return set()

    answered: set[tuple[str, str]] = set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("id")
        lanes = update.get("lanes")
        if not isinstance(update_id, str) or not isinstance(lanes, list):
            continue
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            lane_id = lane.get("id")
            if not isinstance(lane_id, str):
                continue
            lane_anchor = f"{update_id}/{lane_id}"
            _collect_answers(answered, lane.get("questions"), lane_anchor)
            items = lane.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if not isinstance(item_id, str):
                    continue
                anchor = f"{lane_anchor}/{item_id}"
                _collect_answers(answered, item.get("questions"), anchor)
    return answered


def _collect_answers(
    destination: set[tuple[str, str]],
    questions: Any,
    anchor: str,
) -> None:
    """Add well-formed answered question keys to a set."""
    if not isinstance(questions, list):
        return
    for question in questions:
        if not isinstance(question, dict):
            continue
        text = question.get("question")
        answer = question.get("answer")
        if (
            isinstance(text, str)
            and isinstance(answer, str)
            and answer.strip()
        ):
            destination.add((anchor, text))


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


def _activity_time(run_dir: Path, updated_at: str | None) -> datetime:
    """Return the newest trustworthy metadata or filesystem timestamp."""
    candidates: list[datetime] = []
    parsed = _parse_timestamp(updated_at)
    if parsed is not None:
        candidates.append(parsed)
    try:
        candidates.append(
            datetime.fromtimestamp(run_dir.stat().st_mtime, timezone.utc)
        )
    except OSError:
        pass
    for name in _RUN_FILES:
        path = _contained_child(run_dir, name)
        if path is None:
            continue
        try:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        candidates.append(timestamp)
    return max(candidates, default=_EPOCH)


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp as an aware UTC datetime."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    """Return stripped text or none for a missing/malformed value."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
