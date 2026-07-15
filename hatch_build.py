"""Custom Hatch hook for reproducible ``node_ui`` dependencies."""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

NPM_TIMEOUT_SECONDS = 180
MAX_NPM_DIAGNOSTIC_BYTES = 16 * 1024
NPM_DRAIN_TIMEOUT_SECONDS = 1
NPM_CLEANUP_TIMEOUT_SECONDS = 10
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WINDOWS_NPM_GATE = (
    "import subprocess,sys\n"
    "if sys.stdin.buffer.read(1) != b'1':\n"
    "    raise SystemExit(125)\n"
    "process = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)\n"
    "raise SystemExit(process.wait())\n"
)


def _create_windows_job(process: subprocess.Popen[bytes]) -> int | None:
    """Assign a Windows process tree to a kill-on-close Job Object."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        """Windows basic Job Object limits."""

        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        """Windows Job Object I/O counters."""

        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        """Windows extended Job Object limits."""

        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    keep_job = False
    try:
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        configured = kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if not configured:
            return None
        process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
        assigned = kernel32.AssignProcessToJobObject(job, process_handle)
        if not assigned:
            return None
        job_value = int(job)
        keep_job = True
        return job_value
    finally:
        if not keep_job:
            kernel32.CloseHandle(job)


def _close_windows_job(job: int | None) -> None:
    """Close a Job Object, terminating every process assigned to it."""
    if job is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(wintypes.HANDLE(job))


def _run_npm(command: list[str], cwd: Path) -> tuple[int, str]:
    """Run npm with a hard timeout and bounded combined diagnostics."""
    launch_command = command
    is_windows = os.name == "nt"
    if is_windows:
        launch_command = [sys.executable, "-c", _WINDOWS_NPM_GATE, *command]
    process = subprocess.Popen(
        launch_command,
        cwd=cwd,
        start_new_session=not is_windows,
        stdin=subprocess.PIPE if is_windows else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = bytearray()
    windows_job: int | None = None

    if is_windows:
        try:
            windows_job = _create_windows_job(process)
            if windows_job is None:
                raise RuntimeError(
                    "Cannot run npm safely: Windows Job Object assignment failed"
                )
            if process.stdin is None:
                raise RuntimeError("Windows npm gate has no control pipe")
            process.stdin.write(b"1")
            process.stdin.close()
        except BaseException:
            _close_windows_job(windows_job)
            if process.stdin is not None:
                process.stdin.close()
            _terminate_process_tree(process)
            if process.stdout is not None:
                process.stdout.close()
            raise

    def drain_output() -> None:
        """Drain the child pipe while retaining only its bounded tail."""
        if process.stdout is None:
            return
        try:
            while chunk := process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > MAX_NPM_DIAGNOSTIC_BYTES:
                    del output[:-MAX_NPM_DIAGNOSTIC_BYTES]
        except (OSError, ValueError):
            return

    def finish_drain() -> None:
        """Bound pipe cleanup and kill descendants retaining the write end."""
        drain_thread.join(timeout=NPM_DRAIN_TIMEOUT_SECONDS)
        if drain_thread.is_alive():
            _terminate_process_tree(process)
            drain_thread.join(timeout=NPM_DRAIN_TIMEOUT_SECONDS)

    drain_thread = threading.Thread(target=drain_output, daemon=True)
    drain_started = False
    try:
        drain_thread.start()
        drain_started = True
        return_code = process.wait(timeout=NPM_TIMEOUT_SECONDS)
        _close_windows_job(windows_job)
        windows_job = None
        _terminate_process_tree(process)
        finish_drain()
    except BaseException:
        _close_windows_job(windows_job)
        windows_job = None
        _terminate_process_tree(process)
        if drain_started:
            finish_drain()
        elif process.stdout is not None:
            process.stdout.close()
        raise
    finally:
        _close_windows_job(windows_job)
    diagnostic = bytes(output[-MAX_NPM_DIAGNOSTIC_BYTES:])
    return return_code, diagnostic.decode("utf-8", errors="replace")


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate npm and every descendant created in its process session."""
    if os.name == "nt":
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=NPM_CLEANUP_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()


class CustomBuildHook(BuildHookInterface):
    """Install locked Node dependencies outside the source tree."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize temporary dependency state used by the build."""
        super().__init__(*args, **kwargs)
        self._dependency_directory: tempfile.TemporaryDirectory[str] | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Run a clean, locked install and include it in the artifact."""
        del version
        node_ui_dir = Path(self.root) / "node_ui"
        package_json = node_ui_dir / "package.json"
        package_lock = node_ui_dir / "package-lock.json"
        if not package_json.is_file():
            raise RuntimeError("node_ui/package.json is required for builds")
        if not package_lock.is_file():
            raise RuntimeError(
                "node_ui/package-lock.json is required for reproducible builds"
            )
        if self.target_name != "wheel":
            return

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "npm is required to build this package. Install Node.js/npm "
                "and try again."
            )

        self._dependency_directory = tempfile.TemporaryDirectory(
            prefix="claude-code-node-ui-",
            ignore_cleanup_errors=True,
        )
        dependency_root = Path(self._dependency_directory.name)
        try:
            shutil.copy2(package_json, dependency_root / package_json.name)
            shutil.copy2(package_lock, dependency_root / package_lock.name)
            self.app.display_info("Installing locked node_ui dependencies...")
            return_code, diagnostic = _run_npm(
                [
                    npm,
                    "ci",
                    "--omit=dev",
                    "--omit=optional",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                ],
                dependency_root,
            )
            if return_code != 0:
                raise RuntimeError(
                    "Failed to install node_ui dependencies:\n" + diagnostic
                )
        except subprocess.TimeoutExpired as error:
            self._cleanup()
            raise RuntimeError(
                "Timed out installing node_ui dependencies after "
                f"{NPM_TIMEOUT_SECONDS} seconds"
            ) from error
        except BaseException:
            self._cleanup()
            raise

        node_modules = dependency_root / "node_modules"
        if not node_modules.is_dir():
            self._cleanup()
            raise RuntimeError("npm ci did not create node_ui/node_modules")
        force_include = build_data.setdefault("force_include", {})
        force_include[str(node_modules)] = "node_ui/node_modules"
        self.app.display_info("Locked node_ui dependencies are ready.")

    def finalize(
        self,
        version: str,
        build_data: dict[str, Any],
        artifact_path: str,
    ) -> None:
        """Remove the temporary dependency installation after the build."""
        del version, build_data, artifact_path
        self._cleanup()

    def _cleanup(self) -> None:
        """Remove any temporary dependency tree owned by this hook."""
        if self._dependency_directory is not None:
            self._dependency_directory.cleanup()
            self._dependency_directory = None
