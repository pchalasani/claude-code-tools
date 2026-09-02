"""Contained file access for awaiting-answer accounting."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from visual_brief.server.queue import MAX_QUEUE_RECORD_BYTES


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


def _discard_record_remainder(queue: Any) -> bool:
    """Discard a long record in bounded chunks and report completeness."""
    while chunk := queue.readline(MAX_QUEUE_RECORD_BYTES + 1):
        if chunk.endswith(b"\n"):
            return True
    return False


def _read_json_object(
    run_dir: Path,
    name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read a contained JSON object and its generation."""
    path = _contained_child(run_dir, name)
    if path is None:
        return None, None
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    generation = hashlib.sha256(encoded).hexdigest()
    return (value, generation) if isinstance(value, dict) else (None, None)


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
