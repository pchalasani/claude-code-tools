"""Read-only, platform-aware process identity probes for workflow runs."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import Callable

from claude_code_tools.workflow_cli_identity_policy import (
    DOTNET_FILETIME_OFFSET,
    MAX_LINUX_START_TICKS,
    MAX_SUPPORTED_PID,
    MIN_WINDOWS_DATETIME_TICKS,
    PersistedProcessIdentity as PersistedProcessIdentity,
    ProcessObservation,
    ProcessObservationContext,
    ProcessProbe,
    ProcessProbeProvider,
    ProcessStatus,
    compare_persisted_identity,
    compare_process_claim,
    parse_bounded_decimal,
    parse_persisted_identity as parse_persisted_identity,
    parse_persisted_identity_claim,
)

_LINUX_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
_PROC_PIDTBSDINFO = 3
_MAX_SUPPORTED_PID = MAX_SUPPORTED_PID
_MAX_LINUX_START_TICKS = MAX_LINUX_START_TICKS
_DOTNET_FILETIME_OFFSET = DOTNET_FILETIME_OFFSET
_MIN_WINDOWS_DATETIME_TICKS = MIN_WINDOWS_DATETIME_TICKS
_IS_WINDOWS = os.name == "nt"
_PS_EXECUTABLE = "/bin/ps"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_INVALID_PARAMETER = 87
_ERROR_NOT_FOUND = 1168
_DEAD_POSIX_PROCESS_STATES = frozenset({"Z", "X", "x"})


class ObservationContext(ProcessObservationContext):
    """Compatibility adapter that injects the native probe provider."""

    def __init__(
        self,
        deadline: float | None = None,
        probe_factory: ProcessProbeProvider | None = None,
        clock: Callable[[], float] = monotonic,
        skipped: int = 0,
        _probes: dict[int, ProcessProbe] | None = None,
    ) -> None:
        """Initialize pure observation policy with an explicit provider."""
        provider = process_start_identity if probe_factory is None else probe_factory
        super().__init__(
            provider=provider,
            deadline=deadline,
            clock=clock,
            skipped=skipped,
            _probes=_probes,
        )


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


def observe_persisted_identity(
    pid: int,
    token: str,
    *,
    probe: ProcessProbe | None = None,
) -> ProcessObservation:
    """Observe a persisted ownership claim without signaling its process.

    Supplying ``probe`` lets a store scan cache one raw observation per PID and
    compare any number of attacker-controlled identity claims to that snapshot.
    """
    if probe is not None:
        return compare_process_claim(pid, token, probe)
    persisted = parse_persisted_identity_claim(pid, token)
    if persisted is None:
        return "unverifiable"
    observed = process_start_identity(
        pid,
        include_legacy=persisted.kind == "legacy",
    )
    return compare_persisted_identity(persisted, observed)


def _pid_exists(pid: int) -> ProcessStatus:
    """Check PID existence using process metadata, never a signal API."""
    if pid <= 0:
        return "dead"
    if pid > _MAX_SUPPORTED_PID or _IS_WINDOWS:
        return "unverifiable"
    probe = _legacy_posix_probe(pid)
    return probe.status


def _legacy_posix_probe(
    pid: int,
    *,
    remaining_seconds: float | None = None,
) -> ProcessProbe:
    """Read process state and legacy start text through trusted system ps."""
    timeout = 1.0
    if remaining_seconds is not None:
        timeout = min(timeout, remaining_seconds)
    if timeout <= 0:
        return ProcessProbe(
            "unverifiable",
            detail="process-observation budget exhausted",
        )
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
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return ProcessProbe(
            "unverifiable",
            detail=str(error),
            legacy_observed=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        return ProcessProbe(
            "unverifiable",
            detail=str(error),
            legacy_observed=True,
        )
    line = observed.stdout.strip()
    error_text = observed.stderr.strip()
    pieces = line.split(maxsplit=1)
    if observed.returncode != 0:
        return ProcessProbe(
            "unverifiable",
            detail=error_text or "ps returned no process data",
            legacy_observed=True,
        )
    if len(pieces) != 2:
        return ProcessProbe(
            "unverifiable",
            detail=error_text or "ps returned no process data",
            legacy_observed=True,
        )
    if pieces[0][0] in _DEAD_POSIX_PROCESS_STATES:
        return ProcessProbe("dead", legacy_observed=True)
    return ProcessProbe(
        "alive",
        legacy_identity=pieces[1],
        legacy_observed=True,
    )


def _enrich_with_legacy(
    pid: int,
    probe: ProcessProbe,
    *,
    remaining_seconds: float | None,
) -> ProcessProbe:
    """Add bounded POSIX compatibility evidence to one native observation."""
    if probe.legacy_observed:
        return probe
    if probe.status == "dead":
        return replace(probe, legacy_observed=True)
    if _IS_WINDOWS:
        if probe.status == "unverifiable":
            return probe
        return replace(probe, legacy_observed=True)
    legacy = _legacy_posix_probe(
        pid,
        remaining_seconds=remaining_seconds,
    )
    if legacy.status == "dead":
        return replace(legacy, family=probe.family)
    if legacy.status == "unverifiable":
        return replace(
            probe,
            detail=probe.detail or legacy.detail,
            legacy_observed=legacy.legacy_observed,
        )
    return replace(
        probe,
        status="alive",
        legacy_identity=legacy.legacy_identity,
        detail=probe.detail or legacy.detail,
        legacy_observed=True,
    )


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


def _linux_pid_liveness(pid: int) -> ProcessProbe:
    """Use a pidfd to distinguish a missing PID from hidden procfs metadata."""
    pidfd_open = getattr(os, "pidfd_open", None)
    if pidfd_open is None:
        return ProcessProbe(
            "unverifiable",
            detail="procfs metadata is unavailable and pidfd is unsupported",
        )
    try:
        descriptor = pidfd_open(pid, 0)
    except ProcessLookupError:
        return ProcessProbe("dead")
    except (OSError, OverflowError, PermissionError, ValueError) as error:
        return ProcessProbe("unverifiable", detail=str(error))
    try:
        return ProcessProbe(
            "unverifiable",
            detail="process is alive but procfs metadata is unavailable",
        )
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _linux_process_identity(pid: int) -> ProcessProbe | None:
    """Read a boot-scoped Linux kernel start-ticks identity from procfs."""
    if not os.path.exists("/proc/self/stat"):
        return None
    try:
        value = Path(f"/proc/{pid}/stat").read_bytes()
    except FileNotFoundError:
        return _linux_pid_liveness(pid)
    except OSError as error:
        return ProcessProbe("unverifiable", detail=str(error))
    closing_parenthesis = value.rfind(b")")
    fields = value[closing_parenthesis + 2 :].split()
    if closing_parenthesis < 0 or len(fields) <= 19:
        return ProcessProbe("unverifiable", detail="malformed procfs identity")
    if fields[0] in {b"Z", b"X", b"x"}:
        return ProcessProbe("dead")
    try:
        ticks = fields[19].decode("ascii")
    except UnicodeDecodeError:
        return ProcessProbe("unverifiable", detail="malformed procfs identity")
    parsed_ticks = parse_bounded_decimal(
        ticks,
        maximum=_MAX_LINUX_START_TICKS,
    )
    if parsed_ticks is None or parsed_ticks <= 0:
        return ProcessProbe("unverifiable", detail="malformed procfs identity")
    try:
        boot_id = _LINUX_BOOT_ID.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError) as error:
        boot_id = ""
        boot_error = str(error)
    else:
        boot_error = None
    if not boot_id:
        boot_error = boot_error or "procfs boot ID is unavailable"
    elif not re.fullmatch(
        r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
        boot_id,
    ):
        boot_id = ""
        boot_error = "malformed procfs boot ID"
    return ProcessProbe(
        "alive",
        identity=f"linux:{boot_id}:{ticks}" if boot_id else None,
        compatibility_identities=(f"linux:{ticks}",),
        detail=boot_error,
    )


def _darwin_process_identity(pid: int) -> ProcessProbe | None:
    """Read Darwin's microsecond-resolution native process start timeval."""
    if sys.platform != "darwin":
        return None
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
        ctypes.set_errno(0)
        result = proc_pidinfo(
            pid,
            _PROC_PIDTBSDINFO,
            0,
            ctypes.byref(info),
            size,
        )
        observed_errno = ctypes.get_errno()
    except (AttributeError, OSError) as error:
        return ProcessProbe("unverifiable", detail=str(error))
    if result != size or info.pbi_pid != pid:
        if observed_errno == errno.ESRCH:
            return ProcessProbe("dead")
        if observed_errno != 0:
            return ProcessProbe(
                "unverifiable",
                detail=(
                    "libproc failed with errno "
                    f"{observed_errno}: {os.strerror(observed_errno)}"
                ),
            )
        return ProcessProbe(
            "unverifiable",
            detail="libproc returned no process data",
        )
    if info.pbi_status == 5:
        return ProcessProbe("dead")
    return ProcessProbe(
        "alive",
        identity=(f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"),
    )


def process_start_identity(
    pid: int,
    *,
    include_legacy: bool = True,
    remaining_seconds: float | None = None,
    prior_probe: ProcessProbe | None = None,
) -> ProcessProbe:
    """Read process liveness and its available start identities.

    Args:
        pid: Native process identifier to inspect.
        include_legacy: Whether to invoke the POSIX ``ps`` compatibility probe.
        remaining_seconds: Maximum remaining wall time for blocking fallback work.
        prior_probe: Cached native generation to enrich without probing it again.

    Returns:
        The strongest available non-mutating process observation.
    """
    if prior_probe is not None:
        if not include_legacy:
            return prior_probe
        return _enrich_with_legacy(
            pid,
            prior_probe,
            remaining_seconds=remaining_seconds,
        )
    if pid <= 0:
        return ProcessProbe("dead")
    if pid > _MAX_SUPPORTED_PID:
        return ProcessProbe(
            "unverifiable",
            detail=f"PID exceeds supported maximum {_MAX_SUPPORTED_PID}",
        )
    started_at = monotonic()
    linux_probe = _linux_process_identity(pid)
    if linux_probe is not None:
        native_probe = replace(linux_probe, family="linux")
    else:
        darwin_probe = _darwin_process_identity(pid)
        if darwin_probe is not None:
            native_probe = replace(darwin_probe, family="darwin")
        elif _IS_WINDOWS:
            native_probe = replace(
                _windows_process_identity(pid),
                family="windows",
            )
        else:
            native_probe = ProcessProbe(
                "unverifiable",
                detail="native process identity is unavailable",
                family="legacy",
            )
    if not include_legacy:
        return native_probe
    legacy_budget = remaining_seconds
    if legacy_budget is not None:
        legacy_budget = max(0.0, legacy_budget - (monotonic() - started_at))
    return _enrich_with_legacy(
        pid,
        native_probe,
        remaining_seconds=legacy_budget,
    )
