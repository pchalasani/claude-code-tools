"""Real crash recovery and conservative destination metadata preservation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from claude_code_tools.transfer_journal import (
    TransferJournal,
    atomic_write,
    metadata_content,
)


def test_recover_killed_owner(tmp_path: Path) -> None:
    """A killed importer leaves flushed evidence and only identical retry is allowed."""
    home = tmp_path / "account"
    script = tmp_path / "crash.py"
    script.write_text("""import os, signal, sys
from pathlib import Path
from claude_code_tools.transfer_journal import TransferJournal, atomic_write
home = Path(sys.argv[1])
journal = TransferJournal(home, {"artifacts": {}}, False)
journal.backup({})
atomic_write(home / "session.jsonl", b"complete artifact")
journal.save("publishing_files")
os.kill(os.getpid(), signal.SIGKILL)
""")
    result = subprocess.run(
        [sys.executable, str(script), str(home)],
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        timeout=10,
        capture_output=True,
        check=False,
    )
    assert result.returncode == -9
    assert (home / "session.jsonl").read_bytes() == b"complete artifact"
    with pytest.raises(ValueError, match="identical"):
        TransferJournal(home, {"artifacts": {"different": {}}}, True)
    journal = TransferJournal(home, {"artifacts": {}}, True)
    assert journal.record["backups_complete"] is True
    retained = journal.directory
    journal.complete()
    assert not (home / ".aichat-transfer.lock").exists()
    assert json.loads((retained / "journal.json").read_text())["phase"] == "complete"
    assert (home / "session.jsonl").read_bytes() == b"complete artifact"


def test_live_owner_refused(tmp_path: Path) -> None:
    """Two importers cannot claim the account, even with recovery requested."""
    journal = TransferJournal(tmp_path, {}, False)
    try:
        with pytest.raises(ValueError, match="still alive"):
            TransferJournal(tmp_path, {}, True)
    finally:
        journal.complete()


def test_metadata_merge_preserves_unrelated_and_rejects_newer(tmp_path: Path) -> None:
    """Selected same-key divergence never overwrites destination metadata."""
    path = tmp_path / "session_index.jsonl"
    original = {"id": "other", "thread_name": "local"}
    path.write_text(json.dumps(original) + "\n")
    update = {
        "format": "jsonl",
        "key": "id",
        "container": None,
        "rows": [{"id": "selected", "thread_name": "copied"}],
    }
    data = metadata_content(path, update)
    atomic_write(path, data, replace=True)
    assert json.loads(path.read_text().splitlines()[0]) == original
    assert metadata_content(path, update) == data
    update["rows"][0]["thread_name"] = "different"
    with pytest.raises(ValueError, match="differs"):
        metadata_content(path, update)
    assert path.read_bytes() == data


def test_atomic_publication_never_overwrites(tmp_path: Path) -> None:
    """An existing user artifact survives publication races unchanged."""
    path = tmp_path / "artifact"
    atomic_write(path, b"newer user work")
    with pytest.raises(FileExistsError):
        atomic_write(path, b"old transferred work")
    assert path.read_bytes() == b"newer user work"
    assert not list(tmp_path.glob(".transfer-*"))


def test_index_publication_preserves_interleaved_append(tmp_path: Path) -> None:
    """A native append after planning survives selected-row publication byte-exactly."""
    from claude_code_tools.transfer_journal import append_jsonl_rows, missing_jsonl_rows

    path = tmp_path / "session_index.jsonl"
    original = b'{"id":"existing", "thread_name":"preserve formatting"}\n'
    native = b'{"id":"concurrent", "thread_name":"native append"}\n'
    path.write_bytes(original)
    update = {"key": "id", "rows": [{"id": "selected", "thread_name": "copied"}]}
    pending = missing_jsonl_rows(path, update)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        assert os.write(descriptor, native) == len(native)
    finally:
        os.close(descriptor)
    append_jsonl_rows(path, update, pending)
    result = path.read_bytes()
    assert result.startswith(original + native)
    assert [json.loads(line)["id"] for line in result.splitlines()] == [
        "existing",
        "concurrent",
        "selected",
    ]
    append_jsonl_rows(path, update, missing_jsonl_rows(path, update))
    assert path.read_bytes() == result


def test_index_publication_refuses_selected_conflict(tmp_path: Path) -> None:
    """A same-session rename between planning and publication is never replaced."""
    from claude_code_tools.transfer_journal import append_jsonl_rows, missing_jsonl_rows

    path = tmp_path / "session_index.jsonl"
    update = {"key": "id", "rows": [{"id": "selected", "thread_name": "copied"}]}
    pending = missing_jsonl_rows(path, update)
    newer = b'{"id":"selected","thread_name":"newer native name"}\n'
    path.write_bytes(newer)
    with pytest.raises(ValueError, match="differs"):
        append_jsonl_rows(path, update, pending)
    assert path.read_bytes() == newer


def test_incomplete_index_tail_requires_inspection(tmp_path: Path) -> None:
    """Interrupted appends are preserved rather than trimmed during recovery."""
    from claude_code_tools.transfer_journal import missing_jsonl_rows

    path = tmp_path / "session_index.jsonl"
    partial = b'{"id":"unfinished'
    path.write_bytes(partial)
    with pytest.raises(ValueError, match="Incomplete destination index"):
        missing_jsonl_rows(path, {"key": "id", "rows": []})
    assert path.read_bytes() == partial
