"""Explicit Codex thread names from ``session_index.jsonl``.

Codex keeps two kinds of session "names": the state database's
``title`` column (an auto-captured first user message) and explicit,
user-assigned thread names appended to ``<codex-home>/
session_index.jsonl`` as JSONL records like
``{"id": ..., "thread_name": ..., "updated_at": ...}``. The resolver
treats the explicit thread name as authoritative when both exist.

Split out of ``resolve_session.py`` to respect the repo's 1000-line
file limit.
"""

from __future__ import annotations

import json
from pathlib import Path

_INDEX_FILENAME = "session_index.jsonl"


def codex_thread_names(home: Path) -> dict[str, str]:
    """Read explicit thread names for a Codex home.

    The index is append-ordered, so a later entry for the same id
    wins. Malformed lines, non-dict records, and empty ids or names
    are skipped; a missing or unreadable index yields no names.

    Args:
        home: Absolute Codex home directory.

    Returns:
        Mapping of casefolded session id to its explicit thread name.
    """
    index = home / _INDEX_FILENAME
    names: dict[str, str] = {}
    try:
        with open(index, encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(entry, dict):
                    continue
                session_id = entry.get("id")
                thread_name = entry.get("thread_name")
                if (
                    isinstance(session_id, str)
                    and session_id.strip()
                    and isinstance(thread_name, str)
                    and thread_name.strip()
                ):
                    names[session_id.strip().casefold()] = thread_name.strip()
    except OSError:
        return {}
    return names
