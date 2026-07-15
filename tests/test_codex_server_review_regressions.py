"""Focused regressions for continuation review server findings."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import claude_code_tools.codex_server as codex_server
import claude_code_tools.codex_server_process as server_process
from claude_code_tools.codex_server_models import (
    STATE_MAX_BYTES,
    CodexServerError,
    OwnedServer,
)
from claude_code_tools.codex_server_process import (
    process_group_exists,
    run_diagnostic,
    wait_for_process_group_exit,
)


class _FakeProcEntry:
    """Minimal deterministic procfs directory entry."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeProcEntries:
    """Closable deterministic procfs scan."""

    def __init__(self, names: list[str]) -> None:
        self._entries = [_FakeProcEntry(name) for name in names]
        self.closed = False

    def __iter__(self) -> Iterator[_FakeProcEntry]:
        return iter(self._entries)

    def close(self) -> None:
        self.closed = True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group test")
def test_successful_diagnostic_cleans_closed_stdio_descendant(
    tmp_path: Path,
) -> None:
    """A successful probe cannot leak a descendant that closed both pipes."""
    survivor = tmp_path / "diagnostic-survived"
    child = (
        "import time; from pathlib import Path; time.sleep(2); "
        f"Path({str(survivor)!r}).write_text('survived')"
    )
    parent = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', "
        f"{child!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    )

    result = run_diagnostic(
        [sys.executable, "-c", parent],
        os.environ,
        timeout=5.0,
    )

    assert result is not None and result.returncode == 0
    time.sleep(2.2)
    assert not survivor.exists()


@pytest.mark.parametrize("state", ["X", "x"])
def test_linux_dead_process_states_have_no_identity(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """Linux X/x process records cannot satisfy ownership checks."""
    stat_record = f"123 (dead worker) {state} " + " ".join(["1"] * 20)

    def fake_open(path: str, **_kwargs: object) -> io.StringIO:
        if path == "/proc/123/stat":
            return io.StringIO(stat_record)
        return io.StringIO("12345678-1234-1234-1234-123456789abc\n")

    monkeypatch.setattr("builtins.open", fake_open)

    assert server_process._linux_process_identity(123) is None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux procfs regression")
def test_zombie_only_process_group_is_not_running() -> None:
    """An unreaped zombie does not keep lifecycle cleanup pending."""
    child = os.fork()
    if child == 0:
        os.setpgid(0, 0)
        os._exit(0)
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            fields = Path(f"/proc/{child}/stat").read_text().split(") ", 1)
            if len(fields) == 2 and fields[1].startswith("Z "):
                break
            time.sleep(0.01)
        assert not process_group_exists(child)
    finally:
        os.waitpid(child, 0)


def test_unreadable_procfs_entry_falls_back_to_killpg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete procfs scan cannot prove that a group is empty."""
    entries = _FakeProcEntries(["100", "200"])
    killpg_calls: list[tuple[int, int]] = []

    def fake_open(path: str, **_kwargs: object) -> io.StringIO:
        if path == "/proc/200/stat":
            raise PermissionError(path)
        return io.StringIO("100 (readable) S 1 100")

    monkeypatch.setattr(server_process.sys, "platform", "linux")
    monkeypatch.setattr(server_process.os, "scandir", lambda _path: entries)
    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(
        server_process.os,
        "killpg",
        lambda pgid, sent: killpg_calls.append((pgid, sent)),
    )

    assert process_group_exists(999)
    assert killpg_calls == [(999, 0)]
    assert entries.closed


def test_group_exit_deadline_bounds_procfs_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long procfs scan stops at the group-exit deadline."""
    clock = [0.0]
    entries = _FakeProcEntries([str(pid) for pid in range(100, 110)])
    killpg_calls: list[tuple[int, int]] = []

    def fake_open(path: str, **_kwargs: object) -> io.StringIO:
        clock[0] += 0.25
        pid = int(path.split("/")[2])
        return io.StringIO(f"{pid} (reader) S 1 {pid}")

    monkeypatch.setattr(server_process.sys, "platform", "linux")
    monkeypatch.setattr(server_process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        server_process.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(server_process.os, "scandir", lambda _path: entries)
    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(
        server_process.os,
        "killpg",
        lambda pgid, sent: killpg_calls.append((pgid, sent)),
    )
    monkeypatch.setattr(server_process, "_reap_process", lambda _pid: None)

    assert not wait_for_process_group_exit(999, 1.0, reap_pid=999)
    assert clock[0] <= 1.25
    assert killpg_calls == [(999, 0)]
    assert entries.closed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group test")
def test_probe_cleanup_refuses_reaped_leader_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup never signals a group after its leader PID becomes reusable."""
    probe = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        start_new_session=True,
    )
    probe.wait(timeout=5.0)
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        server_process.os,
        "killpg",
        lambda pgid, sent: signals.append((pgid, sent)),
    )

    with pytest.raises(CodexServerError, match="already reaped"):
        server_process._kill_fresh_process_group(probe)

    assert signals == []


def test_noisy_version_is_canonicalized_to_fit_ownership_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted version diagnostics always fit the durable state envelope."""
    output = "x" * (64 * 1024 - 20) + "\ncodex-cli 9.9.9\n"
    result = subprocess.CompletedProcess(
        args=["codex", "--version"],
        returncode=0,
        stdout=output,
        stderr="",
    )
    monkeypatch.setattr(codex_server, "_run_command", lambda *_args: result)

    version = codex_server._codex_version("/codex", {})
    state = OwnedServer(
        pid=12_345,
        pgid=12_345,
        process_started_at="identity",
        codex_path="/codex",
        codex_version=version,
        launched_at="now",
        phase="running",
    )

    assert version == "codex-cli 9.9.9"
    assert len(json.dumps(state.as_json()).encode()) <= STATE_MAX_BYTES
