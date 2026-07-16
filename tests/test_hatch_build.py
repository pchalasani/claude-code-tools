"""Regressions for bounded, reproducible wheel preparation."""

from __future__ import annotations

import ctypes
import gc
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import hatch_build
from hatch_build import CustomBuildHook, MAX_NPM_DIAGNOSTIC_BYTES, _run_npm


ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"


class _RecordingApp:
    """Minimal Hatch application used by build-hook regressions."""

    def display_info(self, message: str) -> None:
        """Accept an informational build message.

        Args:
            message: Build-hook status text.
        """
        del message


class _FakeWindowsProcess:
    """Controllable process double for Windows Job Object regressions."""

    def __init__(self) -> None:
        """Create an already-completed process with empty output."""
        self.pid = 42
        self._handle = 99
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.killed = False
        self.completed = False

    def wait(self, timeout: float | None = None) -> int:
        """Return a successful exit status.

        Args:
            timeout: Unused process wait bound.

        Returns:
            A successful process exit status.
        """
        del timeout
        self.completed = True
        return 0

    def poll(self) -> int | None:
        """Report whether the fake process exited.

        Returns:
            A successful status after termination, otherwise ``None``.
        """
        return 0 if self.completed else None

    def kill(self) -> None:
        """Record an explicit process termination request."""
        self.killed = True
        self.completed = True


class _FakeWindowsFunction:
    """ctypes-compatible callable used by the fake Windows kernel."""

    def __init__(self, callback: Callable[..., object]) -> None:
        """Wrap a callback while accepting ctypes signature attributes."""
        self.callback = callback
        self.argtypes: list[object] | None = None
        self.restype: object | None = None

    def __call__(self, *args: object) -> object:
        """Forward a simulated native call.

        Args:
            *args: Native-call arguments.

        Returns:
            The callback's simulated native result.
        """
        return self.callback(*args)


class _FailingWindowsInput:
    """Control pipe that fails while releasing the gated npm command."""

    def write(self, data: bytes) -> int:
        """Simulate a gate process that exited before release."""
        del data
        raise BrokenPipeError("gate exited")

    def close(self) -> None:
        """Accept cleanup after the simulated write failure."""


def _target_prerequisites(makefile: str, target: str) -> list[str]:
    """Return prerequisites declared for one Make target."""
    match = re.search(rf"^{re.escape(target)}:([^\n]*)$", makefile, re.MULTILINE)
    assert match is not None, f"missing Make target: {target}"
    return match.group(1).split()


def _target_recipe(makefile: str, target: str) -> str:
    """Return the tab-indented recipe declared for one Make target."""
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n)*)",
        makefile,
        re.MULTILINE,
    )
    assert match is not None, f"missing Make recipe: {target}"
    return match.group(1)


def _make_build_hook(root: Path) -> CustomBuildHook:
    """Create a hook with inert collaborators that initialization does not use.

    Args:
        root: Temporary project root.

    Returns:
        A build hook rooted at the temporary project.
    """
    return CustomBuildHook(
        str(root),
        {},
        cast(Any, None),
        cast(Any, None),
        str(root),
        "wheel",
        cast(Any, _RecordingApp()),
    )


def _write_descendant_installer(
    path: Path,
    started: Path,
    survivor: Path,
) -> None:
    """Write a parent that leaves a pipe-holding descendant behind.

    Args:
        path: Script path to create.
        started: Marker written when the descendant starts.
        survivor: Marker written only if cleanup fails.
    """
    path.write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == 'child':\n"
        "    Path(sys.argv[2]).write_text('started', encoding='utf-8')\n"
        "    time.sleep(3)\n"
        "    Path(sys.argv[3]).write_text('survived', encoding='utf-8')\n"
        "    raise SystemExit\n"
        "subprocess.Popen([\n"
        "    sys.executable, __file__, 'child', sys.argv[2], sys.argv[3]\n"
        "])\n"
        "while not Path(sys.argv[2]).exists():\n"
        "    time.sleep(0.01)\n",
        encoding="utf-8",
    )


def test_npm_diagnostic_keeps_only_bounded_tail(tmp_path: Path) -> None:
    """A noisy dependency installer must not accumulate complete output."""
    script = tmp_path / "noisy_installer.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('x' * 1_000_000)\n"
        "sys.stdout.write('diagnostic-tail')\n",
        encoding="utf-8",
    )

    return_code, diagnostic = _run_npm(
        [sys.executable, str(script)],
        tmp_path,
    )

    assert return_code == 0
    assert len(diagnostic.encode("utf-8")) <= MAX_NPM_DIAGNOSTIC_BYTES
    assert diagnostic.endswith("diagnostic-tail")


def test_release_targets_do_not_install_node_dependencies_in_source() -> None:
    """Release builds must leave source-tree Node dependencies untouched."""
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "prep-node:" not in makefile
    for target in ("all-patch", "all-minor", "all-major"):
        assert "prep-node" not in _target_prerequisites(makefile, target)
        assert "npm install" not in _target_recipe(makefile, target)


def test_windows_job_configuration_failure_skips_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured Job Object must never receive the gated process."""
    assigned: list[object] = []
    closed: list[object] = []
    kernel32 = SimpleNamespace(
        CreateJobObjectW=_FakeWindowsFunction(lambda *_args: 7),
        SetInformationJobObject=_FakeWindowsFunction(lambda *_args: 0),
        AssignProcessToJobObject=_FakeWindowsFunction(
            lambda *args: assigned.append(args) or 1
        ),
        CloseHandle=_FakeWindowsFunction(lambda job: closed.append(job) or 1),
    )
    process = _FakeWindowsProcess()
    monkeypatch.setattr(hatch_build, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
        raising=False,
    )

    job = hatch_build._create_windows_job(cast(Any, process))

    assert job is None
    assert assigned == []
    assert closed == [7]


def test_windows_job_assignment_failure_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """npm must not run when its process tree cannot enter a Job Object."""
    process = _FakeWindowsProcess()

    def fake_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        """Return the controlled Windows process double."""
        del args, kwargs
        return cast(Any, process)

    monkeypatch.setattr(hatch_build, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(hatch_build.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(hatch_build, "_create_windows_job", lambda _process: None)
    monkeypatch.setattr(hatch_build, "_close_windows_job", lambda _job: None)
    monkeypatch.setattr(
        hatch_build.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )

    with pytest.raises(RuntimeError, match="Job Object"):
        _run_npm(["npm", "ci"], tmp_path)

    assert process.killed
    assert process.stdin.closed


def test_windows_gate_failure_closes_assigned_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release failure closes the assigned job before propagating."""
    process = _FakeWindowsProcess()
    process.stdin = cast(Any, _FailingWindowsInput())
    closed_jobs: list[int | None] = []

    def fake_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        """Return the controlled Windows process double."""
        del args, kwargs
        return cast(Any, process)

    monkeypatch.setattr(hatch_build, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(hatch_build.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(hatch_build, "_create_windows_job", lambda _process: 7)
    monkeypatch.setattr(hatch_build, "_close_windows_job", closed_jobs.append)

    with pytest.raises(BrokenPipeError, match="gate exited"):
        _run_npm(["npm", "ci"], tmp_path)

    assert 7 in closed_jobs
    assert process.killed


def test_windows_success_never_taskkills_exited_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Job closure, not an exited npm PID, must clean successful runs."""
    process = _FakeWindowsProcess()
    closed_jobs: list[int | None] = []

    def fake_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        """Return the controlled Windows process double."""
        del args, kwargs
        return cast(Any, process)

    def reject_taskkill(*args: object, **kwargs: object) -> None:
        """Fail if cleanup tries to target an already-exited parent PID."""
        del args, kwargs
        raise AssertionError("taskkill must not be used for Job Object cleanup")

    monkeypatch.setattr(hatch_build, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(hatch_build.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(hatch_build, "_create_windows_job", lambda _process: 7)
    monkeypatch.setattr(hatch_build, "_close_windows_job", closed_jobs.append)
    monkeypatch.setattr(hatch_build.subprocess, "run", reject_taskkill)

    return_code, diagnostic = _run_npm(["npm", "ci"], tmp_path)

    assert return_code == 0
    assert diagnostic == ""
    assert 7 in closed_jobs


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group test")
def test_successful_npm_parent_cleans_pipe_holding_descendant(
    tmp_path: Path,
) -> None:
    """A successful npm parent cannot leave output descendants running."""
    script = tmp_path / "successful_parent.py"
    started = tmp_path / "descendant-started"
    survivor = tmp_path / "descendant-survived"
    _write_descendant_installer(script, started, survivor)

    began = time.monotonic()
    return_code, _diagnostic = _run_npm(
        [sys.executable, str(script), "parent", str(started), str(survivor)],
        tmp_path,
    )
    elapsed = time.monotonic() - began

    assert return_code == 0
    assert elapsed < 2.5
    time.sleep(2.2)
    assert not survivor.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group test")
def test_successful_npm_parent_cleans_closed_stdio_descendant(
    tmp_path: Path,
) -> None:
    """Successful npm cleanup does not depend on inherited output pipes."""
    script = tmp_path / "closed_stdio_parent.py"
    survivor = tmp_path / "descendant-survived"
    child = (
        "import time; from pathlib import Path; time.sleep(2); "
        f"Path({str(survivor)!r}).write_text('survived')"
    )
    script.write_text(
        "import subprocess\n"
        "import sys\n"
        "subprocess.Popen(\n"
        f"    [sys.executable, '-c', {child!r}],\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n",
        encoding="utf-8",
    )

    return_code, _diagnostic = _run_npm(
        [sys.executable, str(script)],
        tmp_path,
    )

    assert return_code == 0
    time.sleep(2.2)
    assert not survivor.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group test")
def test_base_exception_terminates_npm_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupt after startup kills npm descendants before propagating."""
    script = tmp_path / "interrupted_parent.py"
    started = tmp_path / "descendant-started"
    survivor = tmp_path / "descendant-survived"
    _write_descendant_installer(script, started, survivor)
    original_wait = subprocess.Popen.wait
    interrupted = False

    def interrupt_once(
        process: subprocess.Popen[bytes],
        timeout: float | None = None,
    ) -> int:
        """Inject one interrupt after the descendant has inherited stdout."""
        nonlocal interrupted
        if not interrupted:
            deadline = time.monotonic() + 2
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            interrupted = True
            raise KeyboardInterrupt
        return original_wait(process, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", interrupt_once)

    with pytest.raises(KeyboardInterrupt):
        _run_npm(
            [
                sys.executable,
                str(script),
                "parent",
                str(started),
                str(survivor),
            ],
            tmp_path,
        )

    time.sleep(3.2)
    assert not survivor.exists()


def test_npm_timeout_terminates_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out npm command must not leave descendant work running.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Temporary attribute replacement helper.
    """
    script = tmp_path / "descendant_installer.py"
    started = tmp_path / "descendant-started"
    survivor = tmp_path / "descendant-survived"
    script.write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == 'child':\n"
        "    Path(sys.argv[2]).write_text('started', encoding='utf-8')\n"
        "    time.sleep(1.25)\n"
        "    Path(sys.argv[3]).write_text('survived', encoding='utf-8')\n"
        "    raise SystemExit\n"
        "subprocess.Popen([\n"
        "    sys.executable, __file__, 'child', sys.argv[2], sys.argv[3]\n"
        "])\n"
        "while not Path(sys.argv[2]).exists():\n"
        "    time.sleep(0.01)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hatch_build, "NPM_TIMEOUT_SECONDS", 1)

    with pytest.raises(subprocess.TimeoutExpired):
        _run_npm(
            [sys.executable, str(script), "parent", str(started), str(survivor)],
            tmp_path,
        )

    assert started.is_file()
    time.sleep(1.4)
    assert not survivor.exists()


def test_npm_startup_failure_cleans_build_hook_temp_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Popen failure preserves its error and removes copied manifests."""
    node_ui = tmp_path / "node_ui"
    node_ui.mkdir()
    (node_ui / "package.json").write_text("{}", encoding="utf-8")
    (node_ui / "package-lock.json").write_text("{}", encoding="utf-8")
    created_roots: list[Path] = []
    original_temporary_directory = tempfile.TemporaryDirectory

    def recording_temporary_directory(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | os.PathLike[str] | None = None,
        ignore_cleanup_errors: bool = False,
        *,
        delete: bool = True,
    ) -> tempfile.TemporaryDirectory[str]:
        """Record the hook-owned directory for the cleanup assertion."""
        directory = original_temporary_directory(
            suffix=suffix,
            prefix=prefix,
            dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
            delete=delete,
        )
        created_roots.append(Path(directory.name))
        return directory

    def fail_startup(*_args: object, **_kwargs: object) -> None:
        """Represent an executable that fails before process creation."""
        raise OSError("npm startup failed")

    monkeypatch.setattr(hatch_build.shutil, "which", lambda _name: "/fake/npm")
    monkeypatch.setattr(
        hatch_build.tempfile,
        "TemporaryDirectory",
        recording_temporary_directory,
    )
    monkeypatch.setattr(hatch_build.subprocess, "Popen", fail_startup)
    hook = _make_build_hook(tmp_path)

    with pytest.raises(OSError, match="npm startup failed"):
        hook.initialize("standard", {})

    assert len(created_roots) == 1
    assert not created_roots[0].exists()
    assert hook._dependency_directory is None


def test_abandoned_build_hook_cleans_dependency_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact failure must not leak an initialized dependency tree.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Temporary attribute replacement helper.
    """
    node_ui = tmp_path / "node_ui"
    node_ui.mkdir()
    (node_ui / "package.json").write_text("{}", encoding="utf-8")
    (node_ui / "package-lock.json").write_text("{}", encoding="utf-8")

    def fake_which(command: str) -> str:
        """Resolve only the npm executable requested by the hook.

        Args:
            command: Executable name requested by the hook.

        Returns:
            A stable fake npm path.
        """
        assert command == "npm"
        return "/fake/npm"

    def fake_run_npm(command: list[str], cwd: Path) -> tuple[int, str]:
        """Create the dependency directory without invoking npm.

        Args:
            command: npm command that would have run.
            cwd: Temporary dependency root.

        Returns:
            A successful exit status and empty diagnostic.
        """
        del command
        (cwd / "node_modules").mkdir()
        return 0, ""

    monkeypatch.setattr(hatch_build.shutil, "which", fake_which)
    monkeypatch.setattr(hatch_build, "_run_npm", fake_run_npm)
    hook = _make_build_hook(tmp_path)
    build_data: dict[str, Any] = {}
    hook.initialize("standard", build_data)
    force_include = cast(dict[str, str], build_data["force_include"])
    dependency_root = Path(next(iter(force_include)))
    hook_reference = weakref.ref(hook)

    assert dependency_root.is_dir()
    del hook
    gc.collect()

    assert hook_reference() is None
    assert not dependency_root.exists()


def test_wheel_dependency_install_omits_dev_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wheel includes only production dependencies from node_ui."""
    node_ui = tmp_path / "node_ui"
    node_ui.mkdir()
    (node_ui / "package.json").write_text("{}", encoding="utf-8")
    (node_ui / "package-lock.json").write_text("{}", encoding="utf-8")

    def fake_run_npm(command: list[str], cwd: Path) -> tuple[int, str]:
        node_modules = cwd / "node_modules"
        node_modules.mkdir()
        if not {"--omit=dev", "--omit=optional"}.issubset(command):
            node_modules.joinpath("react-devtools-core").mkdir()
        return 0, ""

    monkeypatch.setattr(hatch_build.shutil, "which", lambda _name: "/fake/npm")
    monkeypatch.setattr(hatch_build, "_run_npm", fake_run_npm)
    hook = _make_build_hook(tmp_path)
    build_data: dict[str, Any] = {}
    hook.initialize("standard", build_data)
    force_include = cast(dict[str, str], build_data["force_include"])
    node_modules = Path(next(iter(force_include)))

    assert not node_modules.joinpath("react-devtools-core").exists()
    hook.finalize("standard", build_data, "unused.whl")
