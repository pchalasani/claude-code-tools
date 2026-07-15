"""Read-only, platform-aware process identity probes for workflow runs."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_LINUX_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
_PROC_PIDTBSDINFO = 3
_MAX_SUPPORTED_PID = (1 << 31) - 1
_IS_WINDOWS = os.name == "nt"
_PS_EXECUTABLE = "/bin/ps"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_INVALID_PARAMETER = 87
_ERROR_NOT_FOUND = 1168
_DOTNET_FILETIME_OFFSET = 504_911_232_000_000_000
_DEAD_POSIX_PROCESS_STATES = frozenset({"Z", "X", "x"})


@dataclass(frozen=True)
class ProcessProbe:
    """Result of observing a persisted process without mutating it."""

    status: Literal["alive", "dead", "unverifiable"]
    identity: str | None = None
    legacy_identity: str | None = None
    compatibility_identities: tuple[str, ...] = ()
    detail: str | None = None


class _ProcBsdInfo(ctypes.Structure):
    """Darwin ``proc_bsdinfo`` fields through the process start timeval."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


class _FileTime(ctypes.Structure):
    """Windows ``FILETIME`` represented as two unsigned 32-bit words."""

    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


def _pid_exists(pid: int) -> Literal["alive", "dead", "unverifiable"]:
    """Check PID existence while distinguishing permission failures."""
    if pid <= 0:
        return "dead"
    if pid > _MAX_SUPPORTED_PID or _IS_WINDOWS:
        return "unverifiable"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except (OverflowError, PermissionError, OSError, ValueError):
        return "unverifiable"
    return "alive"


def _legacy_posix_identity(pid: int) -> tuple[str | None, str | None]:
    """Return the process state and legacy second-resolution start text."""
    try:
        observed = subprocess.run(
            [
                _PS_EXECUTABLE,
                "-o",
                "stat=",
                "-o",
                "lstart=",
                "-p",
                str(pid),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, str(error)
    line = observed.stdout.strip()
    pieces = line.split(maxsplit=1)
    if observed.returncode != 0 or len(pieces) != 2:
        return None, observed.stderr.strip() or "ps returned no process data"
    if pieces[0][0] in _DEAD_POSIX_PROCESS_STATES:
        return "zombie", None
    return pieces[1], None


def _windows_last_error() -> int:
    """Return the calling thread's last Win32 error code."""
    get_last_error = getattr(ctypes, "get_last_error", None)
    if get_last_error is None:
        return 0
    return int(get_last_error())


def _load_kernel32() -> object:
    """Load the trusted Windows process API library."""
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Win32 APIs are unavailable")
    return loader("kernel32", use_last_error=True)


def _windows_process_identity(pid: int) -> ProcessProbe:
    """Read a process creation timestamp without launching a child process."""
    try:
        kernel32 = _load_kernel32()
        open_process = getattr(kernel32, "OpenProcess")
        get_process_times = getattr(kernel32, "GetProcessTimes")
        wait_for_single_object = getattr(kernel32, "WaitForSingleObject")
        close_handle = getattr(kernel32, "CloseHandle")
        open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_process.restype = ctypes.c_void_p
        get_process_times.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
        ]
        get_process_times.restype = ctypes.c_int
        wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        wait_for_single_object.restype = ctypes.c_uint32
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(
            _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE,
            0,
            pid,
        )
    except (AttributeError, OSError, TypeError, ValueError) as error:
        return ProcessProbe("unverifiable", detail=str(error))
    if not handle:
        error_code = _windows_last_error()
        if error_code in {_ERROR_INVALID_PARAMETER, _ERROR_NOT_FOUND}:
            return ProcessProbe("dead")
        return ProcessProbe(
            "unverifiable",
            detail=f"OpenProcess failed with Win32 error {error_code}",
        )
    creation = _FileTime()
    exit_time = _FileTime()
    kernel_time = _FileTime()
    user_time = _FileTime()
    try:
        wait_result = wait_for_single_object(handle, 0)
        if wait_result == _WAIT_OBJECT_0:
            return ProcessProbe("dead")
        if wait_result != _WAIT_TIMEOUT:
            error_code = _windows_last_error()
            detail = (
                f"WaitForSingleObject failed with Win32 error {error_code}"
                if wait_result == _WAIT_FAILED
                else f"WaitForSingleObject returned {wait_result}"
            )
            return ProcessProbe("unverifiable", detail=detail)
        observed = get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not observed:
            error_code = _windows_last_error()
            return ProcessProbe(
                "unverifiable",
                detail=f"GetProcessTimes failed with Win32 error {error_code}",
            )
    finally:
        close_handle(handle)
    filetime_ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    return ProcessProbe(
        "alive",
        identity=str(filetime_ticks + _DOTNET_FILETIME_OFFSET),
    )


def _linux_process_identity(
    pid: int,
    *,
    include_legacy: bool = True,
) -> ProcessProbe | None:
    """Read a boot-scoped Linux kernel start-ticks identity from procfs."""
    if not os.path.exists("/proc/self/stat"):
        return None
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return ProcessProbe("dead")
    except (OSError, UnicodeError) as error:
        return ProcessProbe("unverifiable", detail=str(error))
    try:
        boot_id = _LINUX_BOOT_ID.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError) as error:
        return ProcessProbe("unverifiable", detail=str(error))
    closing_parenthesis = value.rfind(")")
    fields = value[closing_parenthesis + 2 :].split()
    if closing_parenthesis < 0 or len(fields) <= 19 or not boot_id:
        return ProcessProbe("unverifiable", detail="malformed procfs identity")
    if fields[0] in _DEAD_POSIX_PROCESS_STATES:
        return ProcessProbe("dead")
    ticks = fields[19]
    legacy = None
    if include_legacy:
        legacy, _ = _legacy_posix_identity(pid)
    return ProcessProbe(
        "alive",
        identity=f"linux:{boot_id}:{ticks}",
        legacy_identity=legacy if legacy != "zombie" else None,
        compatibility_identities=(f"linux:{ticks}",),
    )


def _darwin_process_identity(
    pid: int,
    *,
    include_legacy: bool = True,
) -> ProcessProbe | None:
    """Read Darwin's microsecond-resolution native process start timeval."""
    if sys.platform != "darwin":
        return None
    existence = _pid_exists(pid)
    if existence != "alive":
        return ProcessProbe(existence)
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = library.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        info = _ProcBsdInfo()
        size = ctypes.sizeof(info)
        result = proc_pidinfo(
            pid,
            _PROC_PIDTBSDINFO,
            0,
            ctypes.byref(info),
            size,
        )
    except (AttributeError, OSError) as error:
        return ProcessProbe("unverifiable", detail=str(error))
    if result != size or info.pbi_pid != pid:
        existence = _pid_exists(pid)
        return ProcessProbe(existence, detail="libproc returned no process data")
    if info.pbi_status == 5:
        return ProcessProbe("dead")
    legacy = None
    if include_legacy:
        legacy, _ = _legacy_posix_identity(pid)
        if legacy == "zombie":
            return ProcessProbe("dead")
    return ProcessProbe(
        "alive",
        identity=(f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"),
        legacy_identity=legacy,
    )


def process_start_identity(
    pid: int,
    *,
    include_legacy: bool = True,
) -> ProcessProbe:
    """Read process liveness and its available start identities.

    Args:
        pid: Native process identifier to inspect.
        include_legacy: Whether to invoke the POSIX ``ps`` compatibility probe.

    Returns:
        The strongest available non-mutating process observation.
    """
    if pid <= 0:
        return ProcessProbe("dead")
    if pid > _MAX_SUPPORTED_PID:
        return ProcessProbe(
            "unverifiable",
            detail=f"PID exceeds supported maximum {_MAX_SUPPORTED_PID}",
        )
    linux_probe = _linux_process_identity(pid, include_legacy=include_legacy)
    if linux_probe is not None:
        return linux_probe
    darwin_probe = _darwin_process_identity(pid, include_legacy=include_legacy)
    if darwin_probe is not None:
        return darwin_probe
    if _IS_WINDOWS:
        return _windows_process_identity(pid)
    legacy, detail = _legacy_posix_identity(pid)
    if legacy == "zombie":
        return ProcessProbe("dead")
    if legacy is None:
        return ProcessProbe(_pid_exists(pid), detail=detail)
    return ProcessProbe("alive", legacy_identity=legacy)
