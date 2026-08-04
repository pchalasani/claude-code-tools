"""Validate and append inert reverse-channel records."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from visual_brief import MAX_THREAD_ID_LENGTH
from visual_brief.schema import MAX_SUGGESTION_LABEL_LENGTH

MAX_ANCHOR_LENGTH = 200
MAX_QUESTION_LENGTH = 20_000
MAX_QUEUE_RECORD_BYTES = 128 * 1024
LEGACY_SIGNALS = frozenset(
    {"too-dense", "show-evidence", "go-deeper", "skip"}
)


def build_question_record(
    data: dict[str, Any],
) -> dict[str, str | None]:
    """Build a validated question record."""
    parent_id = data.get("parent_id")
    if parent_id is not None:
        if not isinstance(parent_id, str) or not parent_id.strip():
            raise ValueError("Field 'parent_id' must be non-empty text")
        parent_id = parent_id.strip()
        if len(parent_id) > MAX_THREAD_ID_LENGTH:
            raise ValueError(
                f"Field 'parent_id' must be at most "
                f"{MAX_THREAD_ID_LENGTH} characters"
            )
    return {
        "timestamp": _timestamp(),
        "type": "question",
        "anchor_id": _required_text(
            data,
            "anchor_id",
            MAX_ANCHOR_LENGTH,
        ),
        "text": _required_text(data, "text", MAX_QUESTION_LENGTH),
        "parent_id": parent_id,
    }


def build_signal_record(data: dict[str, Any]) -> dict[str, str | None]:
    """Build one validated agent-authored suggested-reply record."""
    anchor = _required_text(data, "anchor_id", MAX_ANCHOR_LENGTH)
    if "label" in data or "text" in data:
        return {
            "timestamp": _timestamp(),
            "type": "question",
            "anchor_id": anchor,
            "label": _required_text(
                data,
                "label",
                MAX_SUGGESTION_LABEL_LENGTH,
            ),
            "text": _required_text(data, "text", MAX_QUESTION_LENGTH),
            "parent_id": None,
        }
    signal = data.get("signal")
    if not isinstance(signal, str) or signal not in LEGACY_SIGNALS:
        raise ValueError("Field 'signal' must be a supported feedback signal")
    return {
        "timestamp": _timestamp(),
        "type": "signal",
        "anchor_id": anchor,
        "signal": signal,
    }


def append_record(
    run_dir: Path,
    record: dict[str, str | None],
    lock: threading.Lock,
) -> None:
    """Append one JSON record durably to a contained run queue."""
    encoded = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_QUEUE_RECORD_BYTES:
        raise ValueError("Queue record is too large")
    queue_path = _contained_queue(run_dir)
    if queue_path is None:
        raise OSError("question queue is unavailable")
    flags = (
        os.O_APPEND
        | os.O_CREAT
        | os.O_WRONLY
        | getattr(os, "O_NOFOLLOW", 0)
    )
    with lock:
        descriptor = os.open(queue_path, flags, 0o600)
        try:
            view = memoryview(encoded)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _required_text(
    data: dict[str, Any],
    field: str,
    maximum: int,
) -> str:
    """Return one validated, stripped text field."""
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Field '{field}' must be non-empty text")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(
            f"Field '{field}' must be at most {maximum} characters"
        )
    return value


def _contained_queue(run_dir: Path) -> Path | None:
    """Resolve the queue only when its final path stays in the run."""
    try:
        root = run_dir.resolve()
        path = (root / "questions.jsonl").resolve()
    except (OSError, RuntimeError):
        return None
    if path == root or not path.is_relative_to(root):
        return None
    return path


def _timestamp() -> str:
    """Return an RFC 3339 UTC timestamp."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
