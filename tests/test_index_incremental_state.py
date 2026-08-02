"""Tests for incremental-index bookkeeping.

Files that produce no document (empty sessions, sessions run from inside the
agent home directories) used to be left out of the state file, so every launch
re-parsed them. They are now recorded -- but with the file state captured
BEFORE reading, so a session that gains content mid-scan is not lost.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from claude_code_tools.search_index import IndexState, SessionIndex


def test_snapshot_taken_before_a_write_still_triggers_reindex(
    tmp_path: Path,
) -> None:
    """Content appended after the snapshot must not look already indexed."""
    session = tmp_path / "session.jsonl"
    session.write_text("", encoding="utf-8")
    before = session.stat()

    # Simulate a live session appending its first message while we read it.
    session.write_text('{"type": "user"}\n', encoding="utf-8")
    os.utime(session, (before.st_atime, before.st_mtime + 5))

    state = IndexState(tmp_path / "state.json")
    state.mark_indexed(session, before)

    assert state.needs_reindex(session) is True


def test_marking_with_current_state_skips_next_run(tmp_path: Path) -> None:
    """An unchanged file is skipped on the next launch."""
    session = tmp_path / "session.jsonl"
    session.write_text('{"type": "user"}\n', encoding="utf-8")

    state = IndexState(tmp_path / "state.json")
    state.mark_indexed(session)

    assert state.needs_reindex(session) is False


def test_empty_session_is_recorded_and_not_reparsed(tmp_path: Path) -> None:
    """An empty session is indexed-state-recorded so launches stop re-parsing."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    empty = session_dir / "0a88c018-3bca-4e8c-84fb-6cd3912a4db4.jsonl"
    # Structurally valid JSONL, but carrying no indexable message.
    empty.write_text(
        json.dumps({"type": "summary", "summary": "nothing here"}) + "\n",
        encoding="utf-8",
    )

    index = SessionIndex(tmp_path / "index")
    stats = index.index_from_jsonl([empty], incremental=True, show_progress=False)
    assert stats["indexed"] == 0

    assert str(empty) in index.state.indexed_files

    second = index.index_from_jsonl([empty], incremental=True, show_progress=False)
    assert second["skipped"] == 1
    assert second["empty"] == 0
