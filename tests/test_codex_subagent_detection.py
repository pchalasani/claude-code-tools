"""Tests for sub-agent detection in session metadata extraction.

Claude writes sub-agent transcripts as ``agent-*.jsonl``, so a filename check
is enough. Codex writes every thread as ``rollout-*.jsonl``, so its sub-agents
have to be recognised from the spawn markers in ``session_meta``.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_code_tools.export_session import (
    _is_codex_exec_payload,
    _is_codex_subagent_payload,
    extract_session_metadata,
)


def _write_codex_rollout(path: Path, payload: dict) -> Path:
    """Write a minimal Codex rollout file with the given session_meta payload.

    Args:
        path: File to create.
        payload: Contents of the ``session_meta`` payload object.

    Returns:
        The path written.
    """
    meta = {
        "timestamp": "2026-08-01T00:39:50.261Z",
        "type": "session_meta",
        "payload": payload,
    }
    turn = {
        "timestamp": "2026-08-01T00:39:55.000Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"text": "hi"}]},
    }
    path.write_text(
        json.dumps(meta) + "\n" + json.dumps(turn) + "\n", encoding="utf-8"
    )
    return path


def _base_payload(**extra: object) -> dict:
    """Return a Codex session_meta payload for a normal (non-sub-agent) thread."""
    payload = {
        "session_id": "019fbac3-47b7-76f1-a0ef-b0256d99b508",
        "id": "019fbac3-47b7-76f1-a0ef-b0256d99b508",
        "cwd": "/Users/x/Git/proj",
        "originator": "codex_exec",
        "source": "exec",
        "thread_source": "user",
        "git": {"branch": "main"},
    }
    payload.update(extra)
    return payload


def test_plain_codex_thread_is_not_a_subagent() -> None:
    """A user-started Codex thread carries no sub-agent markers."""
    assert _is_codex_subagent_payload(_base_payload()) is False


def test_thread_source_marks_subagent() -> None:
    """``thread_source: subagent`` identifies a spawned thread."""
    assert _is_codex_subagent_payload(_base_payload(thread_source="subagent"))


def test_source_object_marks_subagent() -> None:
    """A ``source`` object keyed by ``subagent`` identifies a spawned thread."""
    payload = _base_payload(
        source={"subagent": {"thread_spawn": {"parent_thread_id": "019f-parent"}}}
    )
    assert _is_codex_subagent_payload(payload)


def test_parent_thread_id_marks_subagent() -> None:
    """A recorded parent thread id identifies a spawned thread."""
    assert _is_codex_subagent_payload(_base_payload(parent_thread_id="019f-parent"))


def test_empty_parent_thread_id_is_not_a_marker() -> None:
    """An empty or non-string parent id is ignored rather than trusted."""
    assert _is_codex_subagent_payload(_base_payload(parent_thread_id="")) is False
    assert _is_codex_subagent_payload(_base_payload(parent_thread_id={})) is False


def test_extract_metadata_flags_codex_subagent(tmp_path: Path) -> None:
    """A spawned Codex rollout is reported as a sidechain session."""
    rollout = _write_codex_rollout(
        tmp_path / "rollout-2026-08-01T00-39-47-019fbac3.jsonl",
        _base_payload(
            thread_source="subagent",
            source={"subagent": {"thread_spawn": {"parent_thread_id": "019f-parent"}}},
            parent_thread_id="019f-parent",
            agent_nickname="reviewer",
        ),
    )

    metadata = extract_session_metadata(rollout, "codex")

    assert metadata["is_sidechain"] is True


def test_extract_metadata_leaves_plain_codex_session_alone(tmp_path: Path) -> None:
    """A user-started Codex rollout stays resumable in search results."""
    rollout = _write_codex_rollout(
        tmp_path / "rollout-2026-08-01T00-39-47-019fbac3.jsonl", _base_payload()
    )

    metadata = extract_session_metadata(rollout, "codex")

    assert metadata["is_sidechain"] is False
    assert metadata["cwd"] == "/Users/x/Git/proj"
    assert metadata["branch"] == "main"


def test_claude_subagent_still_detected_by_filename(tmp_path: Path) -> None:
    """The existing Claude sub-agent detection is unchanged."""
    session = tmp_path / "agent-a20a12f.jsonl"
    session.write_text(
        json.dumps(
            {
                "type": "user",
                "isSidechain": True,
                "cwd": "/Users/x/Git/proj",
                "timestamp": "2026-08-01T00:39:50.261Z",
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert extract_session_metadata(session, "claude")["is_sidechain"] is True


def test_claude_main_session_is_not_a_subagent(tmp_path: Path) -> None:
    """A normal Claude session file is not flagged."""
    session = tmp_path / "0a88c018-3bca-4e8c-84fb-6cd3912a4db4.jsonl"
    session.write_text(
        json.dumps(
            {
                "type": "user",
                "cwd": "/Users/x/Git/proj",
                "timestamp": "2026-08-01T00:39:50.261Z",
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert extract_session_metadata(session, "claude")["is_sidechain"] is False


def test_headless_exec_run_is_detected() -> None:
    """A `codex exec` thread is recognised as a headless run."""
    assert _is_codex_exec_payload(_base_payload()) is True


def test_interactive_cli_thread_is_not_an_exec_run() -> None:
    """A thread started in the interactive Codex TUI is not a headless run."""
    assert _is_codex_exec_payload(_base_payload(source="cli")) is False


def test_subagent_spawn_is_not_counted_as_an_exec_run() -> None:
    """A spawned sub-agent carries an object here, not the string ``exec``."""
    payload = _base_payload(
        source={"subagent": {"thread_spawn": {"parent_thread_id": "019f-parent"}}}
    )
    assert _is_codex_exec_payload(payload) is False


def test_extract_metadata_flags_headless_exec_run(tmp_path: Path) -> None:
    """A headless Codex rollout is flagged so search can hide it by default."""
    rollout = _write_codex_rollout(
        tmp_path / "rollout-2026-08-01T00-39-47-019fbac3.jsonl", _base_payload()
    )

    metadata = extract_session_metadata(rollout, "codex")

    assert metadata["is_exec_run"] is True
    assert metadata["is_sidechain"] is False


def test_interactive_codex_session_is_not_flagged(tmp_path: Path) -> None:
    """An interactive Codex session stays in default search results."""
    rollout = _write_codex_rollout(
        tmp_path / "rollout-2026-08-01T00-39-47-019fbac3.jsonl",
        _base_payload(source="cli", originator="codex-tui"),
    )

    metadata = extract_session_metadata(rollout, "codex")

    assert metadata["is_exec_run"] is False
    assert metadata["is_sidechain"] is False


def test_claude_sessions_are_never_exec_runs(tmp_path: Path) -> None:
    """The headless flag is Codex-only; Claude sessions never set it."""
    session = tmp_path / "0a88c018-3bca-4e8c-84fb-6cd3912a4db4.jsonl"
    session.write_text(
        json.dumps(
            {
                "type": "user",
                "cwd": "/Users/x/Git/proj",
                "timestamp": "2026-08-01T00:39:50.261Z",
                "message": {"role": "user", "content": "hi"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert extract_session_metadata(session, "claude")["is_exec_run"] is False


def test_forked_rollout_ignores_ancestor_metadata(tmp_path: Path) -> None:
    """A fork replays ancestor session_meta records; only the first counts.

    Without this, an interactive fork of a headless ancestor inherits
    is_exec_run and disappears from default search results.
    """
    rollout = tmp_path / "rollout-2026-08-01T00-39-47-019fbac3.jsonl"
    own = {
        "timestamp": "2026-08-01T00:39:50.261Z",
        "type": "session_meta",
        # No git info, so metadata extraction keeps scanning past this record.
        "payload": _base_payload(source="cli", originator="codex-tui", git=None),
    }
    ancestor = {
        "timestamp": "2026-07-30T00:00:00.000Z",
        "type": "session_meta",
        "payload": _base_payload(
            source={"subagent": {"thread_spawn": {"parent_thread_id": "019f-p"}}},
            thread_source="subagent",
            parent_thread_id="019f-p",
        ),
    }
    turn = {
        "timestamp": "2026-08-01T00:39:55.000Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"text": "hi"}]},
    }
    rollout.write_text(
        "\n".join(json.dumps(r) for r in (own, ancestor, turn)) + "\n",
        encoding="utf-8",
    )

    metadata = extract_session_metadata(rollout, "codex")

    assert metadata["is_exec_run"] is False
    assert metadata["is_sidechain"] is False
