"""Regression tests for read-only workflow process observation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from claude_code_tools import workflow_processes, workflow_runs

TIME = "2026-07-14T14:00:00Z"
HUGE_PID = 10**100


def _write_mapping(path: Path, value: dict[str, object]) -> None:
    """Write one JSON object into a run directory.

    Args:
        path: Destination JSON path.
        value: JSON-compatible object to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _state(run_id: str, *, status: str) -> dict[str, object]:
    """Build a minimal valid run state.

    Args:
        run_id: Owning run-directory name.
        status: Durable run status.

    Returns:
        A JSON-compatible state object.
    """
    state: dict[str, object] = {
        "concurrency": 1,
        "createdAt": TIME,
        "cwd": "/work",
        "runId": run_id,
        "status": status,
        "steps": {},
        "updatedAt": TIME,
        "version": 1,
        "workflowHash": "hash",
        "workflowPath": "/work/workflow.js",
    }
    if status == "completed":
        state["completedAt"] = TIME
    return state


class _FakeWin32Function:
    """Callable stand-in that accepts ctypes function metadata."""

    def __init__(
        self,
        result: int,
        callback: Callable[..., None] | None = None,
    ) -> None:
        self.argtypes: list[object] = []
        self.restype: object | None = None
        self._result = result
        self._callback = callback

    def __call__(self, *args: object) -> int:
        """Run the optional side effect and return the configured value."""
        if self._callback is not None:
            self._callback(*args)
        return self._result


class _FakeKernel32:
    """Minimal kernel32 surface used by the process probe."""

    def __init__(
        self,
        *,
        open_result: int,
        times_result: int = 1,
        times_callback: Callable[..., None] | None = None,
        wait_result: int = 258,
    ) -> None:
        self.OpenProcess = _FakeWin32Function(open_result)
        self.GetProcessTimes = _FakeWin32Function(
            times_result,
            times_callback,
        )
        self.WaitForSingleObject = _FakeWin32Function(wait_result)
        self.CloseHandle = _FakeWin32Function(1)


def _force_windows_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the public process probe through its Windows implementation."""
    monkeypatch.setattr(workflow_processes, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        workflow_processes,
        "_linux_process_identity",
        lambda _pid, *, include_legacy=True: None,
    )
    monkeypatch.setattr(
        workflow_processes,
        "_darwin_process_identity",
        lambda _pid, *, include_legacy=True: None,
    )


def test_windows_missing_pid_is_dead_without_launching_programs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Win32's normal nonexistent-PID error is a definite dead process."""
    _force_windows_probe(monkeypatch)
    monkeypatch.setattr(
        workflow_processes,
        "_load_kernel32",
        lambda: _FakeKernel32(open_result=0),
    )
    monkeypatch.setattr(workflow_processes, "_windows_last_error", lambda: 87)

    def fail_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Windows observation must not launch a program")

    monkeypatch.setattr(workflow_processes.subprocess, "run", fail_run)

    assert workflow_processes.process_start_identity(123).status == "dead"


def test_windows_probe_access_failure_is_unverifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenProcess failures other than not-found do not assert process death."""
    _force_windows_probe(monkeypatch)
    monkeypatch.setattr(
        workflow_processes,
        "_load_kernel32",
        lambda: _FakeKernel32(open_result=0),
    )
    monkeypatch.setattr(workflow_processes, "_windows_last_error", lambda: 5)

    probe = workflow_processes.process_start_identity(123)

    assert probe.status == "unverifiable"
    assert probe.detail == "OpenProcess failed with Win32 error 5"


def test_windows_native_creation_time_matches_persisted_dotnet_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native FILETIME values retain the workflow writer's identity format."""

    def set_creation_time(
        _handle: object,
        creation: object,
        *_times: object,
    ) -> None:
        value: Any = creation
        value._obj.dwLowDateTime = 123
        value._obj.dwHighDateTime = 0

    _force_windows_probe(monkeypatch)
    monkeypatch.setattr(
        workflow_processes,
        "_load_kernel32",
        lambda: _FakeKernel32(
            open_result=42,
            times_callback=set_creation_time,
        ),
    )

    probe = workflow_processes.process_start_identity(123)

    assert probe.status == "alive"
    assert probe.identity == "504911232000000123"


def test_windows_live_probe_ignores_undefined_exit_filetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live handle is authoritative even if its exit FILETIME is nonzero."""

    def set_times(
        _handle: object,
        creation: object,
        exit_time: object,
        *_times: object,
    ) -> None:
        creation_value: Any = creation
        creation_value._obj.dwLowDateTime = 123
        exit_value: Any = exit_time
        exit_value._obj.dwLowDateTime = 999

    _force_windows_probe(monkeypatch)
    monkeypatch.setattr(
        workflow_processes,
        "_load_kernel32",
        lambda: _FakeKernel32(
            open_result=42,
            times_callback=set_times,
            wait_result=258,
        ),
    )

    probe = workflow_processes.process_start_identity(123)

    assert probe.status == "alive"
    assert probe.identity == "504911232000000123"


def test_windows_signaled_process_handle_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-time handle wait proves that a Windows process terminated."""
    _force_windows_probe(monkeypatch)
    kernel32 = _FakeKernel32(open_result=42, wait_result=0)
    monkeypatch.setattr(workflow_processes, "_load_kernel32", lambda: kernel32)

    probe = workflow_processes.process_start_identity(123)

    assert probe.status == "dead"


def test_windows_pid_exists_never_calls_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lower-level Windows existence fallback is also signal-free."""

    def fail_kill(_pid: int, _signal: int) -> None:
        raise AssertionError("Windows liveness observation must not signal")

    monkeypatch.setattr(workflow_processes, "_IS_WINDOWS", True)
    monkeypatch.setattr(workflow_processes.os, "kill", fail_kill)

    assert workflow_processes._pid_exists(123) == "unverifiable"


def test_posix_legacy_probe_uses_absolute_system_ps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observational POSIX fallback cannot execute a PATH-shadowed ps."""
    command: list[str] = []

    def fake_run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        command.extend(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="S Tue Jul 14 10:00:00 2026\n",
            stderr="",
        )

    monkeypatch.setattr(workflow_processes.subprocess, "run", fake_run)

    identity, error = workflow_processes._legacy_posix_identity(123)

    assert error is None
    assert identity == "Tue Jul 14 10:00:00 2026"
    assert command[0] == "/bin/ps"


@pytest.mark.parametrize("state", ["Z", "X", "x"])
def test_posix_legacy_probe_treats_dead_states_as_dead(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """Legacy ps states for zombies and exited processes are not live."""

    def fake_run(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=f"{state} Tue Jul 14 10:00:00 2026\n",
            stderr="",
        )

    monkeypatch.setattr(workflow_processes.subprocess, "run", fake_run)

    identity, error = workflow_processes._legacy_posix_identity(123)

    assert error is None
    assert identity == "zombie"


@pytest.mark.parametrize("state", ["Z", "X", "x"])
def test_linux_procfs_probe_treats_dead_states_as_dead(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """Procfs states for zombies and exited processes are not live."""
    proc_stat = f"123 (worker) {state} " + " ".join(["0"] * 20)
    original_read_text = Path.read_text

    def fake_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == Path("/proc/123/stat"):
            return proc_stat
        if path == workflow_processes._LINUX_BOOT_ID:
            return "00000000-0000-0000-0000-000000000000\n"
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(workflow_processes.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(Path, "read_text", fake_read_text)

    probe = workflow_processes._linux_process_identity(
        123,
        include_legacy=False,
    )

    assert probe is not None
    assert probe.status == "dead"


def test_strong_linux_probe_does_not_launch_legacy_ps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers can omit the expensive compatibility probe for strong IDs."""

    def fail_legacy(_pid: int) -> tuple[str | None, str | None]:
        raise AssertionError("strong identity observation must not launch ps")

    monkeypatch.setattr(
        workflow_processes,
        "_legacy_posix_identity",
        fail_legacy,
    )

    probe = workflow_processes.process_start_identity(
        os.getpid(),
        include_legacy=False,
    )

    assert probe.status == "alive"
    assert probe.identity is not None
    assert probe.legacy_identity is None


def test_strong_durable_identity_comparison_does_not_launch_ps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dashboard identity comparison requests only the persisted ID kind."""
    initial = workflow_processes.process_start_identity(
        os.getpid(),
        include_legacy=False,
    )
    assert initial.identity is not None

    def fail_legacy(_pid: int) -> tuple[str | None, str | None]:
        raise AssertionError("strong durable identities must not launch ps")

    monkeypatch.setattr(
        workflow_processes,
        "_legacy_posix_identity",
        fail_legacy,
    )

    assert workflow_runs.observed_process_state(os.getpid(), initial.identity) is None


def test_huge_pid_probe_is_unverifiable_without_platform_conversion() -> None:
    """An arbitrary-size persisted PID cannot reach a native PID API."""
    probe = workflow_processes.process_start_identity(HUGE_PID)

    assert probe.status == "unverifiable"
    assert probe.detail == "PID exceeds supported maximum 2147483647"


def test_huge_supervisor_pid_is_isolated_to_its_run(tmp_path: Path) -> None:
    """An oversized state PID becomes a per-run observation diagnostic."""
    directory = tmp_path / "huge-supervisor"
    state = _state(directory.name, status="running")
    state["pid"] = HUGE_PID
    state["pidStartedAt"] = "darwin:1:2"
    _write_mapping(directory / "state.json", state)

    run = workflow_runs.load_run(directory)

    assert run.recorded_status == "running"
    assert run.status == "unverifiable"
    assert run.activity() == "supervisor identity cannot be verified"


def test_huge_callback_pid_is_isolated_to_its_callback(tmp_path: Path) -> None:
    """An oversized notifier PID cannot abort terminal-run inspection."""
    directory = tmp_path / "huge-notifier"
    callback: dict[str, object] = {
        "attempts": 0,
        "clientUserMessageId": "message-1",
        "createdAt": TIME,
        "deadlineAt": TIME,
        "endpoint": "unix:///tmp/app-server.sock",
        "notifierPid": HUGE_PID,
        "notifierStartedAt": "darwin:1:2",
        "runId": directory.name,
        "status": "sending",
        "terminalCompletedAt": TIME,
        "terminalStatus": "completed",
        "threadId": "thread-1",
        "timeoutMs": 1000,
        "updatedAt": TIME,
        "version": 1,
    }
    _write_mapping(
        directory / "state.json",
        _state(directory.name, status="completed"),
    )
    _write_mapping(directory / "completion-notification.json", callback)

    rendered = workflow_runs.load_run(directory).callback_json()

    assert rendered is not None
    assert rendered["status"] == "unverifiable"
    assert rendered["notifierProcessStatus"] == "unverifiable"
