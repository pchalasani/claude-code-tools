"""Real runs on disk for the typed-write verbs.

Everything here builds actual files the way the daemon and the CLI build
them: real queue records through the daemon's own writer, a real rendered
page, real metadata. Nothing is stubbed, and nothing touches the runs under
``~/.claude``.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from visual_brief.render import render_content
from visual_brief.server.queue import append_record, build_question_record

RUN_ID = "write-run"
ANCHOR = "now/state/tests"
LANE_ANCHOR = "now/state"


def base_document() -> dict[str, Any]:
    """Return a small valid document carrying one Now panel."""
    return {
        "title": "Typed writes",
        "summary": "One run that the write verbs act on.",
        "updates": [
            {
                "id": "now",
                "timestamp": "2026-07-27 12:00 EDT",
                "headline": "Where things stand",
                "summary": "The page states the current position.",
                "lanes": [
                    {
                        "id": "state",
                        "name": "What works",
                        "items": [
                            {
                                "id": "tests",
                                "glance": "The suite runs end to end.",
                                "explanation": "Every test uses real files.",
                                "trust": "verified-by-me",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def make_run(
    runs_root: Path,
    run_id: str = RUN_ID,
    document: dict[str, Any] | None = None,
) -> Path:
    """Create one complete run directory on disk.

    Args:
        runs_root: Directory that will hold the run.
        run_id: Identifier of the run.
        document: Content to publish, defaulting to the base document.

    Returns:
        The run directory.
    """
    content = base_document() if document is None else document
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    write_content(run_dir, content)
    (run_dir / "questions.jsonl").write_bytes(b"")
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "label": "Typed writes",
                "created_at": "2026-07-27T12:00:00Z",
                "updated_at": "2026-07-27T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def write_content(run_dir: Path, document: dict[str, Any]) -> None:
    """Publish content and page the way a hand-written edit would.

    Args:
        run_dir: The run directory.
        document: The document to write.
    """
    (run_dir / "content.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "index.html").write_text(
        render_content(document) + "\n",
        encoding="utf-8",
    )


def read_content_file(run_dir: Path) -> dict[str, Any]:
    """Read one run's saved document.

    Args:
        run_dir: The run directory.

    Returns:
        The parsed document.
    """
    value = json.loads((run_dir / "content.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def queue_line(
    run_dir: Path,
    text: str,
    anchor_id: str = ANCHOR,
    parent_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Append one question to the queue through the daemon's own writer.

    Args:
        run_dir: The run directory.
        text: The human's text.
        anchor_id: Anchor path the question hangs from.
        parent_id: Thread the question replies to, if any.
        timestamp: Override for the record's arrival instant.

    Returns:
        The record as it was written.
    """
    record: dict[str, Any] = dict(
        build_question_record(
            {"anchor_id": anchor_id, "text": text, "parent_id": parent_id}
        )
    )
    if timestamp is not None:
        record["timestamp"] = timestamp
    append_record(run_dir, record, threading.Lock())
    return record


def threads_at(document: dict[str, Any], anchor: str) -> list[Any]:
    """Return the conversations hanging from one anchor path.

    Args:
        document: A saved document.
        anchor: An anchor path of the form ``update/lane`` or with an item.

    Returns:
        The owner's ``questions`` list, or an empty list.
    """
    owner = owner_at(document, anchor)
    questions = owner.get("questions") if owner is not None else None
    return questions if isinstance(questions, list) else []


def owner_at(document: dict[str, Any], anchor: str) -> dict[str, Any] | None:
    """Return the lane or item at one anchor path.

    Args:
        document: A saved document.
        anchor: An anchor path.

    Returns:
        The owner object, or ``None`` when the anchor is gone.
    """
    parts = anchor.split("/")
    for update in document.get("updates", []):
        if update.get("id") != parts[0]:
            continue
        for lane in update.get("lanes", []):
            if lane.get("id") != parts[1]:
                continue
            if len(parts) == 2:
                return lane
            for item in lane.get("items", []):
                if item.get("id") == parts[2]:
                    return item
    return None


def with_thread(anchor: str, thread: dict[str, Any]) -> dict[str, Any]:
    """Return the base document with one conversation planted at an anchor.

    Args:
        anchor: Anchor path of the lane or item to plant it on.
        thread: The conversation entry, in either the thread or pair shape.

    Returns:
        A fresh document carrying the entry.
    """
    document = copy.deepcopy(base_document())
    owner = owner_at(document, anchor)
    assert owner is not None
    owner["questions"] = [thread]
    return document
