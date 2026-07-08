"""Hostile-input and swap-race hardening for in-place trimming.

Companion to ``test_trim_in_place.py`` (kept separate to keep file
sizes manageable). Covers these contracts:

- Malformed Claude-session lines (``message: null``, non-dict
  messages, wrong-typed content, non-dict top-level JSON, invalid
  UTF-8 bytes, ...) must never crash the trim path; they pass through
  byte-for-byte.
- A writer that opened the ORIGINAL session inode before the atomic
  swap and appends only after ``os.replace`` returns is still merged
  into the trimmed file and its backup (quiet-window polling); an
  append arriving only after the quiet window lands on the orphaned
  pre-trim inode and never corrupts the live session or the backup
  (which is an independent ``shutil.copy2`` snapshot, per the backup
  contract).
- Only files positively identified as Claude sessions are trimmed:
  empty files, random text and marker-less JSONL are rejected
  untouched.
- A symlinked session path trims the REAL file; the symlink survives.
- The CLI resolves a bare session id through --claude-home.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, List

import pytest
from click.testing import CliRunner

import claude_code_tools.trim_in_place as trim_in_place_module
from claude_code_tools.aichat import trim_in_place_cmd
from claude_code_tools.trim_in_place import trim_session_in_place
from claude_code_tools.trim_session_claude import build_tool_name_mapping

from tests.test_trim_in_place import (
    SESSION_ID,
    append_entry,
    make_synthetic_session,
    read_records,
    single_json_line,
)


def hostile_entries() -> List[Any]:
    """JSON-parseable session entries hostile at every nesting level."""
    return [
        {"type": "assistant", "sessionId": SESSION_ID, "message": None},
        {"type": "assistant", "message": "not a dict"},
        {"type": "assistant", "message": {"content": "just text"}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": 42}]},
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": {"x": 1},
                        "name": ["Bash"],
                        "input": "not a dict",
                    }
                ]
            },
        },
        {"type": "user", "message": None},
        {"type": "user", "message": {"content": 42}},
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": {"evil": 1},
                        "content": [{"text": 123}],
                    }
                ]
            },
        },
        [1, 2, 3],  # non-dict top-level JSON shapes
        "just a string",
        42,
    ]


def _make_hostile_session(path: Path) -> List[str]:
    """A trimmable synthetic session with hostile tail lines appended.

    Returns:
        The hostile lines exactly as written to the file.
    """
    make_synthetic_session(path)
    hostile_lines = [json.dumps(entry) for entry in hostile_entries()]
    with open(path, "a") as f:
        for ln in hostile_lines:
            f.write(ln + "\n")
    return hostile_lines


class TestHostileSessionLines:
    """Malformed lines pass through untouched - never a traceback."""

    def test_message_null_does_not_crash_tool_mapping(
        self, tmp_path: Path
    ) -> None:
        """The exact reported repro: assistant line with message:null."""
        session = tmp_path / "s.jsonl"
        session.write_text(
            '{"type":"assistant","sessionId":"s","message":null}\n'
        )
        assert build_tool_name_mapping(session) == {}

    def test_hostile_lines_survive_apply(self, tmp_path: Path) -> None:
        """A real apply trims the good lines and copies hostile ones
        through byte-for-byte (assistant trimming exercises both
        passes over the file)."""
        session = tmp_path / "hostile.jsonl"
        hostile_lines = _make_hostile_session(session)

        result = trim_session_in_place(
            session, trim_assistant_messages=2
        )

        assert result["applied"] is True
        assert result["num_tools_trimmed"] == 2
        assert result["num_assistant_trimmed"] == 2
        final_lines = session.read_text().splitlines()
        assert len(final_lines) == 10 + len(hostile_lines)
        assert final_lines[10:] == hostile_lines
        assert "...truncated" in session.read_text()

    def test_hostile_lines_via_cli_json(self, tmp_path: Path) -> None:
        """The CLI stays on the --json contract for hostile files:
        exactly one JSON result line, exit 0."""
        session = tmp_path / "hostile.jsonl"
        _make_hostile_session(session)
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd, [str(session), "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = single_json_line(result.output)
        assert payload["applied"] is True

    def test_invalid_utf8_tail_line_round_trips(
        self, tmp_path: Path
    ) -> None:
        """A positively identified Claude session with an
        invalid-UTF-8 tail line still trims (never a
        UnicodeDecodeError); the undecodable line passes through
        byte-for-byte in both the trimmed file and the backup."""
        session = tmp_path / "s.jsonl"
        make_synthetic_session(session)
        bad_tail = b'\xff\xfe{"not": "utf8"\xff\n'
        with open(session, "ab") as f:
            f.write(bad_tail)

        # Assistant trimming exercises both read passes over the file.
        result = trim_session_in_place(
            session, trim_assistant_messages=2
        )

        assert result["applied"] is True
        assert result["num_tools_trimmed"] == 2
        assert result["num_assistant_trimmed"] == 2
        final = session.read_bytes()
        assert final.endswith(bad_tail)
        assert b"...truncated" in final
        # The backup snapshot preserves the hostile bytes too.
        backup = Path(result["backup_file"])
        assert backup.read_bytes().endswith(bad_tail)


class TestNonClaudeInputRejected:
    """Only positively-identified Claude sessions may be trimmed.

    ``detect_agent()`` defaults uncertain files to "claude"; the
    in-place path must NOT inherit that guess - it uses the strict
    detector and rejects anything without a positive Claude marker.
    """

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "random text, not json\nsecond line of noise\n",
            '{"foo": "bar"}\n{"payload": {"x": 1}}\n[1, 2, 3]\n',
        ],
        ids=["empty", "random-text", "jsonl-without-claude-markers"],
    )
    def test_rejected_untouched(
        self, tmp_path: Path, content: str
    ) -> None:
        session = tmp_path / "s.jsonl"
        session.write_text(content)

        with pytest.raises(ValueError, match="Claude sessions"):
            trim_session_in_place(session)

        assert session.read_text() == content
        # No backup, no temp file - nothing was created at all.
        assert [p.name for p in tmp_path.iterdir()] == ["s.jsonl"]


class TestSymlinkedSessionPath:
    """A symlinked session path must trim the REFERENT, not replace
    the symlink with a regular file (which would split the session
    identity between two files)."""

    def test_symlink_survives_and_target_is_trimmed(
        self, tmp_path: Path
    ) -> None:
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        real = make_synthetic_session(real_dir / "real.jsonl")
        real = real.resolve()
        link = tmp_path / "alias.jsonl"
        link.symlink_to(real)

        result = trim_session_in_place(link)

        assert result["applied"] is True
        # The result reports the REAL file, and the symlink still
        # points at it (it was not replaced by a regular file).
        assert result["session_file"] == str(real)
        assert link.is_symlink()
        assert Path(os.path.realpath(link)).samefile(real)
        # The referent was trimmed; reading through the link agrees.
        assert "...truncated" in real.read_text()
        assert link.read_text() == real.read_text()
        # Backup lives next to the real file, not next to the symlink.
        backup = Path(result["backup_file"])
        assert backup.parent == real.parent
        assert backup.exists()


class TestCliSessionIdResolution:
    """SESSION may be a bare session id, resolved via --claude-home."""

    def test_session_id_resolves_and_trims(self, tmp_path: Path) -> None:
        home = tmp_path / "claude-home"
        project = home / "projects" / "-Users-x-proj"
        project.mkdir(parents=True)
        session_id = "e2e-resolve-4d1f9c2a77"
        session = make_synthetic_session(project / f"{session_id}.jsonl")
        original_bytes = session.read_bytes()
        runner = CliRunner()

        result = runner.invoke(
            trim_in_place_cmd,
            [session_id, "--json", "--claude-home", str(home)],
        )

        assert result.exit_code == 0, result.output
        payload = single_json_line(result.output)
        assert payload["applied"] is True
        # The id resolved to the projects file, which was trimmed.
        assert payload["session_file"] == str(session.resolve())
        assert session.read_bytes() != original_bytes
        assert "...truncated" in session.read_text()
        backups = list(project.glob("*.bak"))
        assert len(backups) == 1
        assert payload["backup_file"] == str(backups[0])
        assert backups[0].read_bytes() == original_bytes


class TestDelayedAppendThroughPreSwapFd:
    """A pre-swap O_APPEND handle that writes AFTER os.replace returns
    must be caught by the post-swap quiet-window polling."""

    def test_delayed_append_is_merged_into_file_and_backup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = make_synthetic_session(tmp_path / "s.jsonl")
        line = (
            json.dumps(
                append_entry("u-delayed", "DELAYED-APPEND-MARKER")
            )
            + "\n"
        ).encode()
        # The writer opens the ORIGINAL inode before the trim begins...
        pre_fd = os.open(str(session), os.O_WRONLY | os.O_APPEND)
        original_line_count = len(read_records(session))

        # Widen the quiet window so the delayed write is deterministic
        # even on slow CI machines.
        monkeypatch.setattr(
            trim_in_place_module, "_MERGE_QUIET_SECS", 1.0
        )

        real_replace = os.replace
        timers: List[threading.Timer] = []

        def racing_replace(src: Any, dst: Any) -> Any:
            """After the real swap, write to the OLD inode with a delay."""
            rv = real_replace(src, dst)
            if Path(str(dst)) == session and not timers:
                t = threading.Timer(0.1, os.write, args=(pre_fd, line))
                timers.append(t)
                t.start()
            return rv

        monkeypatch.setattr(
            trim_in_place_module.os, "replace", racing_replace
        )

        try:
            result = trim_session_in_place(session)
        finally:
            for t in timers:
                t.join()
            os.close(pre_fd)

        assert timers, "the swap never happened"
        assert result["applied"] is True

        # The delayed append landed in the final trimmed file...
        final_text = session.read_text()
        assert "DELAYED-APPEND-MARKER" in final_text
        assert "...truncated" in final_text  # trim still applied
        records = read_records(session)
        assert len(records) == original_line_count + 1
        assert records[-1]["uuid"] == "u-delayed"

        # ...and in the backup.
        backup = Path(result["backup_file"])
        assert "DELAYED-APPEND-MARKER" in backup.read_text()

        # size_after reflects the merged tail.
        assert result["size_after"] == session.stat().st_size


class TestLateAppendAfterQuietWindow:
    """A pre-swap fd append arriving AFTER the merge quiet window is
    over (here: after the whole trim call returned) lands on the
    orphaned pre-trim inode. Per the backup contract the backup is an
    INDEPENDENT ``shutil.copy2`` snapshot - not that inode - so the
    straggler's write corrupts neither the live session nor the
    backup (best-effort by design for such pathological writers;
    Claude Code appends by path and is fully covered)."""

    def test_late_append_stays_off_session_and_backup(
        self, tmp_path: Path
    ) -> None:
        session = make_synthetic_session(tmp_path / "s.jsonl")
        original_bytes = session.read_bytes()
        # The writer opens the ORIGINAL inode before the trim begins
        # and stays silent until well past the quiet window.
        pre_fd = os.open(str(session), os.O_WRONLY | os.O_APPEND)
        line = (
            json.dumps(append_entry("u-late", "LATE-APPEND-MARKER"))
            + "\n"
        ).encode()
        try:
            result = trim_session_in_place(session)
            assert result["applied"] is True

            backup = Path(result["backup_file"])
            # The backup is an independent snapshot of the original,
            # NOT a hard link to the (pre-swap) session inode.
            assert os.fstat(pre_fd).st_ino != os.stat(backup).st_ino
            assert backup.read_bytes() == original_bytes

            # The quiet window is long over (the call returned); now
            # the straggler finally appends through its old handle.
            os.write(pre_fd, line)
        finally:
            os.close(pre_fd)

        # The write went to the orphaned inode: the backup snapshot
        # and the trimmed live session are both untouched by it.
        assert backup.read_bytes() == original_bytes
        assert "LATE-APPEND-MARKER" not in session.read_text()
        assert "...truncated" in session.read_text()
