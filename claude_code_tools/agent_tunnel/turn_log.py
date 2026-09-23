"""An append-only log of HTTP turns, and the reader behind ``GET /turns``.

The daemon's thread store keeps only a trimmed recap of each conversation,
so it cannot answer "what was asked about this page last month". When
``[http] turn_log`` names a file, every answered HTTP turn is appended to it
as one JSON line: when, which handle and thread, the question, the answer,
and whatever flat ``metadata`` the caller attached (a docs site sends the
page and heading). The sender is never written. The fork is still told who
is asking and may repeat it in its answer, so a caller that shows the log to
other people sends a pseudonym as the sender rather than a name or email.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger("agent_tunnel.turn_log")

META_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
MAX_META_KEYS = 20
MAX_META_VALUE = 4000
MAX_LIMIT = 500


def validate_metadata(value: Any) -> tuple[dict[str, str], str]:
    """A caller's ``metadata`` as a flat string map, or why it is not one.

    Args:
        value: The request's ``metadata`` field; absent or null is empty.

    Returns:
        ``(metadata, "")`` on success, else ``({}, error)``. An error that
        mentions "longer" is an over-limit value (the caller answers 413).
    """
    if value is None:
        return {}, ""
    if not isinstance(value, dict):
        return {}, "metadata must be a JSON object"
    if len(value) > MAX_META_KEYS:
        return {}, f"metadata has more than {MAX_META_KEYS} keys"
    meta: dict[str, str] = {}
    for key, item in value.items():
        if not META_KEY_RE.match(key):
            return {}, "metadata keys must match [A-Za-z0-9_.-]{1,40}"
        if not isinstance(item, str):
            return {}, f"metadata.{key} must be a string"
        if len(item) > MAX_META_VALUE:
            return {}, f"metadata.{key} is longer than {MAX_META_VALUE} characters"
        meta[key] = item
    return meta, ""


def resolve_log_path(configured: str, state_path: Path) -> Optional[Path]:
    """The turn log's path, or None when logging is off.

    A relative path sits beside the daemon's state file, like its other
    runtime files.
    """
    if not configured:
        return None
    path = Path(configured).expanduser()
    return path if path.is_absolute() else state_path.parent / path


@dataclass
class TurnLog:
    """Append answered turns to a JSON-lines file and read them back."""

    path: Path
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def append(
        self,
        handle: str,
        thread: str,
        question: str,
        answer: str,
        metadata: Mapping[str, str],
    ) -> None:
        """Write one turn durably; raises OSError if it could not be written."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "handle": handle,
            "thread": thread,
            "question": question,
            "answer": answer,
            "metadata": dict(metadata),
        }
        # ASCII escapes: a lone surrogate from JSON input is a valid str but
        # cannot be encoded as UTF-8, and must not cost the reader an answer.
        line = json.dumps(record) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._write, line)

    def _write(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Owner-only, like the thread store: the log holds every question
        # and answer.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a+b") as fh:
            if hasattr(os, "fchmod"):  # POSIX only; also narrows an older file
                os.fchmod(fh.fileno(), 0o600)
            # A failed earlier write can leave a record without its newline;
            # end it first so this record stays on a line of its own.
            end = fh.seek(0, os.SEEK_END)
            if end:
                fh.seek(end - 1)
                if fh.read(1) != b"\n":
                    fh.write(b"\n")
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())

    def read(
        self, handle: str, meta_filters: Mapping[str, str], limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Matching turns, newest first, and how many lines were unreadable.

        Args:
            handle: Only turns of this handle.
            meta_filters: Each key must be present in a turn's metadata with
                exactly this value.
            limit: At most this many turns, the most recent ones.

        Returns:
            ``(turns, skipped)``; a missing file is no turns, not an error.
        """
        if not self.path.exists():
            return [], 0
        # Only the latest `limit` matches are kept while scanning, so memory
        # stays bounded however long the log grows.
        matches: deque[dict[str, Any]] = deque(maxlen=limit)
        skipped = 0
        # Bytes, decoded line by line, so one damaged line is skipped and
        # counted rather than hiding every record after it.
        with self.path.open("rb") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                try:
                    turn = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    skipped += 1
                    continue
                meta = turn.get("metadata", {}) if isinstance(turn, dict) else None
                if not isinstance(meta, dict):
                    skipped += 1  # valid JSON, but not a turn record
                    continue
                if turn.get("handle") != handle:
                    continue
                if all(meta.get(k) == v for k, v in meta_filters.items()):
                    matches.append(turn)
        if skipped:
            logger.warning("Turn log %s: %d unreadable line(s)", self.path, skipped)
        return list(reversed(matches)), skipped
