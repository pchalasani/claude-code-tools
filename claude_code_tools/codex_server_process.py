"""Process ownership, diagnostics, and supervisor launch primitives."""

from __future__ import annotations

import datetime as dt
import ctypes
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from types import FrameType
from typing import Any, Mapping, Sequence

from claude_code_tools.codex_server_models import (
    FORCED_STOP_SECONDS,
    GRACEFUL_STOP_SECONDS,
    POLL_SECONDS,
    CodexServerError,
    OwnedServer,
    ServerPaths,
    log_tail,
    log_tail_stream,
    open_log_append,
    read_state,
    remove_state,
    write_bounded_log,
    write_state,
)


DIAGNOSTIC_OUTPUT_MAX_BYTES = 64 * 1024
_LINUX_BOOT_ID = "/proc/sys/kernel/random/boot_id"
_PROC_PIDTBSDINFO = 3


def codex_executable_identity(codex_path: str) -> str:
    """Return a stable identity for the selected executable file."""
    flags = os.O_RDONLY | os.O_NONBLOCK
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CodexServerError(
            "safe Codex executable certification requires O_NOFOLLOW support"
        )
    try:
        fd = os.open(codex_path, flags | no_follow)
    except OSError as exc:
        raise CodexServerError(
            f"cannot certify Codex executable {codex_path}: {exc}"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CodexServerError(
                f"Codex executable is not a regular file: {codex_path}"
            )
    finally:
        os.close(fd)
    return ":".join(
        str(value)
        for value in (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    )


class _ProcBsdInfo(ctypes.Structure):
    """Darwin process metadata through its native start timeval."""

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


def run_diagnostic(
    command: Sequence[str],
    env: Mapping[str, str],
    timeout: float = 3.0,
) -> subprocess.CompletedProcess[str] | None:
    """Run a short command in a contained process group.

    Args:
        command: Executable and arguments.
        env: Complete child environment.
        timeout: Maximum run time in seconds.

    Returns:
        Completed command, or ``None`` after an OS failure or timeout.
    """
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        return None
    try:
        output = _bounded_diagnostic_output(process, timeout)
        if output is None:
            _kill_fresh_process_group(process)
            return None
        stdout_bytes, stderr_bytes = output
    except TimeoutError:
        _kill_fresh_process_group(process)
        return None
    except BaseException:
        _kill_fresh_process_group(process)
        raise
    _kill_fresh_process_group(process)
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=process.returncode,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )


def _bounded_diagnostic_output(
    process: subprocess.Popen[bytes],
    timeout: float,
) -> tuple[bytes, bytes] | None:
    """Drain both diagnostic pipes while retaining fixed-size suffixes."""
    assert process.stdout is not None
    assert process.stderr is not None
    streams = (process.stdout, process.stderr)
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    by_fd = {stream.fileno(): stream for stream in streams}
    buffers = {fd: bytearray() for fd in by_fd}
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream.fileno(), selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            events = selector.select(min(remaining, 0.1))
            for key, _mask in events:
                fd = key.fd
                stream = by_fd[fd]
                try:
                    chunk = os.read(fd, 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(fd)
                    stream.close()
                    continue
                target = buffers[fd]
                target.extend(chunk)
                overflow = len(target) - DIAGNOSTIC_OUTPUT_MAX_BYTES
                if overflow > 0:
                    del target[:overflow]
        if not _wait_process_exit_without_reaping(process, deadline):
            return None
    finally:
        selector.close()
        for stream in streams:
            stream.close()
    return bytes(buffers[stdout_fd]), bytes(buffers[stderr_fd])


def _wait_process_exit_without_reaping(
    process: subprocess.Popen[bytes],
    deadline: float,
) -> bool:
    """Observe child exit while preserving its PID through group cleanup."""
    wait_flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
    while True:
        try:
            result = os.waitid(os.P_PID, process.pid, wait_flags)
        except ChildProcessError:
            process.poll()
            return False
        except InterruptedError:
            continue
        if result is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SECONDS, remaining))


def process_identity(pid: int) -> str | None:
    """Return a boot-scoped native process start identity."""
    if sys.platform == "linux":
        return _linux_process_identity(pid)
    if sys.platform == "darwin":
        return _darwin_process_identity(pid)
    return None


def _linux_process_identity(pid: int) -> str | None:
    """Read Linux boot ID and kernel process start ticks."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stream:
            value = stream.read()
        with open(_LINUX_BOOT_ID, encoding="ascii") as stream:
            boot_id = stream.read().strip().lower()
    except (OSError, UnicodeError):
        return None
    closing_parenthesis = value.rfind(")")
    fields = value[closing_parenthesis + 2 :].split()
    if closing_parenthesis < 0 or len(fields) <= 19 or not boot_id:
        return None
    if fields[0] in {"X", "x", "Z"}:
        return None
    return f"linux:{boot_id}:{fields[19]}"


def _darwin_process_identity(pid: int) -> str | None:
    """Read Darwin's microsecond-resolution native process start timeval."""
    if pid <= 0:
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
        result = proc_pidinfo(
            pid,
            _PROC_PIDTBSDINFO,
            0,
            ctypes.byref(info),
            size,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if result != size or info.pbi_pid != pid or info.pbi_status == 5:
        return None
    return f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"


def state_controller_matches(state: OwnedServer) -> bool:
    """Return whether state still identifies the supervisor or legacy leader."""
    return process_matches(
        state.pid,
        state.pgid,
        state.process_started_at,
    )


def state_worker_matches(state: OwnedServer) -> bool:
    """Return whether state still identifies its supervised worker leader."""
    if (
        state.worker_pid is None
        or state.worker_pgid is None
        or state.worker_started_at is None
    ):
        return False
    return process_matches(
        state.worker_pid,
        state.worker_pgid,
        state.worker_started_at,
    )


def process_matches(pid: int, pgid: int, expected_identity: str) -> bool:
    """Validate a process start identity and its process group."""
    if process_identity(pid) != expected_identity:
        return False
    try:
        return os.getpgid(pid) == pgid
    except (OSError, ProcessLookupError):
        return False


def process_group_exists(
    pgid: int,
    *,
    deadline: float | None = None,
) -> bool:
    """Return whether a process group still has at least one member."""
    if sys.platform == "linux":
        observed = _linux_process_group_exists(pgid, deadline=deadline)
        if observed is not None:
            return observed
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _linux_process_group_exists(
    pgid: int,
    *,
    deadline: float | None = None,
) -> bool | None:
    """Return whether procfs contains a non-dead member of a process group."""
    try:
        entries = os.scandir("/proc")
    except OSError:
        return None
    complete = True
    try:
        for entry in entries:
            if deadline is not None and time.monotonic() >= deadline:
                return None
            if not entry.name.isdecimal():
                continue
            try:
                with open(f"/proc/{entry.name}/stat", encoding="utf-8") as stream:
                    value = stream.read()
            except (OSError, UnicodeError):
                complete = False
                continue
            closing_parenthesis = value.rfind(")")
            fields = value[closing_parenthesis + 2 :].split()
            if closing_parenthesis < 0 or len(fields) < 3:
                complete = False
                continue
            try:
                member_pgid = int(fields[2])
            except ValueError:
                complete = False
                continue
            if member_pgid == pgid and fields[0] not in {"X", "x", "Z"}:
                return True
    finally:
        entries.close()
    return False if complete else None


def wait_for_process_group_exit(
    pgid: int,
    timeout: float,
    reap_pid: int | None = None,
) -> bool:
    """Wait until a process group is empty, reaping its leader when possible."""
    deadline = time.monotonic() + timeout
    while True:
        _reap_process(reap_pid)
        if not process_group_exists(pgid, deadline=deadline):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SECONDS, remaining))


def terminate_owned(
    state: OwnedServer,
    graceful_seconds: float = GRACEFUL_STOP_SECONDS,
    forced_seconds: float = FORCED_STOP_SECONDS,
) -> None:
    """Stop a verified supervisor, with recoverable worker fallback."""
    if state_controller_matches(state):
        if not _signal_verified(
            state.pid,
            state.pgid,
            state.process_started_at,
            signal.SIGTERM,
        ):
            _terminate_after_controller_loss(
                state,
                graceful_seconds,
                forced_seconds,
            )
            return
        if wait_for_process_group_exit(
            state.pgid,
            graceful_seconds,
            reap_pid=state.pid,
        ):
            _require_worker_gone(state)
            return
        if state_controller_matches(state):
            _signal_verified(
                state.pid,
                state.pgid,
                state.process_started_at,
                signal.SIGTERM,
            )
        if wait_for_process_group_exit(
            state.pgid,
            forced_seconds,
            reap_pid=state.pid,
        ):
            _require_worker_gone(state)
            return
        if state_controller_matches(state):
            _kill_verified_group(
                state.pid,
                state.pgid,
                state.process_started_at,
            )
        else:
            _terminate_after_controller_loss(
                state,
                graceful_seconds,
                forced_seconds,
            )
        wait_for_process_group_exit(state.pgid, 2.0, reap_pid=state.pid)
        _kill_worker_if_verified(state)
        if process_group_exists(state.pgid):
            raise CodexServerError(
                f"app-server supervisor group {state.pgid} did not stop; "
                "ownership state was retained"
            )
        _require_worker_gone(state)
        return

    _terminate_after_controller_loss(state, graceful_seconds, forced_seconds)


def _terminate_after_controller_loss(
    state: OwnedServer,
    graceful_seconds: float,
    forced_seconds: float,
) -> None:
    """Recover a worker only after its recorded supervisor has vanished."""
    _reap_process(state.pid, state.process_started_at)
    if process_group_exists(state.pgid):
        raise CodexServerError(
            f"app-server leader {state.pid} exited before process group "
            f"{state.pgid} could be verified; ownership state was retained"
        )
    if not state.supervised:
        return
    if state_worker_matches(state):
        _terminate_worker(state, graceful_seconds, forced_seconds)
        return
    if state.worker_pid is not None:
        _reap_process(state.worker_pid, state.worker_started_at)
    if state.worker_pgid is not None and process_group_exists(state.worker_pgid):
        raise CodexServerError(
            f"app-server supervisor {state.pid} is gone and worker group "
            f"{state.worker_pgid} cannot be verified; ownership state was retained"
        )


def remove_stale_ownership(paths: ServerPaths, state: OwnedServer) -> None:
    """Remove stale state only after all recorded groups are confirmed empty."""
    if state_controller_matches(state) or state_worker_matches(state):
        raise CodexServerError("live app-server ownership cannot be discarded")
    _reap_process(state.pid, state.process_started_at)
    if state.worker_pid is not None:
        _reap_process(state.worker_pid, state.worker_started_at)
    ambiguous_groups = [state.pgid]
    if state.worker_pgid is not None:
        ambiguous_groups.append(state.worker_pgid)
    living = [pgid for pgid in ambiguous_groups if process_group_exists(pgid)]
    if living:
        groups = ", ".join(str(pgid) for pgid in living)
        raise CodexServerError(
            f"recorded app-server process group(s) {groups} still have "
            "descendants or were reused; ownership state was retained and no "
            "unverified process was signaled"
        )
    remove_state(paths)


def spawn_supervisor(
    codex_path: str,
    codex_version: str,
    child_env: Mapping[str, str],
    paths: ServerPaths,
    plugin_fingerprint: str | None = None,
    codex_executable_identity: str | None = None,
    codex_options: Sequence[str] = (),
) -> OwnedServer:
    """Spawn a durable supervisor after publishing recoverable ownership.

    Args:
        codex_path: Exact Codex executable to supervise.
        codex_version: Version reported by that executable.
        child_env: Environment shared by supervisor and app server.
        paths: Helper runtime paths.
        plugin_fingerprint: Snapshot of plugin and marketplace configuration.
        codex_executable_identity: Stable identity of the selected executable.
        codex_options: Certified global options forwarded to the worker.

    Returns:
        Ownership state including the supervised worker identity.

    Raises:
        CodexServerError: If ownership cannot be durably established.
    """
    token = str(uuid.uuid4())
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    supervisor: subprocess.Popen[bytes] | None = None
    state: OwnedServer | None = None
    deferred = DeferredTerminationSignals()
    header = (
        f"\n[{dt.datetime.now(dt.timezone.utc).isoformat()}] "
        f"starting {codex_path} app-server under supervisor\n"
    )
    try:
        with open_log_append(paths.log_path) as log_stream, deferred:
            write_bounded_log(log_stream, header.encode("utf-8"))
            log_info = os.fstat(log_stream.fileno())
            log_identity = (log_info.st_dev, log_info.st_ino)
            supervisor = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "claude_code_tools.codex_server_supervisor",
                    "--handoff-fd",
                    str(read_fd),
                    "--codex",
                    codex_path,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=dict(child_env),
                start_new_session=True,
                close_fds=True,
                pass_fds=(read_fd,),
            )
            os.close(read_fd)
            read_fd = -1
            identity = wait_for_process_identity(supervisor)
            if identity is None:
                detail = log_tail_stream(log_stream, 20).text
                suffix = f"\n\n{detail}" if detail else ""
                raise CodexServerError(
                    f"app-server supervisor exited during startup{suffix}"
                )
            pgid = os.getpgid(supervisor.pid)
            if pgid != supervisor.pid:
                raise CodexServerError(
                    "app-server supervisor did not start in its own process group"
                )
            state = OwnedServer(
                pid=supervisor.pid,
                pgid=pgid,
                process_started_at=identity,
                codex_path=codex_path,
                codex_version=codex_version,
                launched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                phase="starting",
                launch_token=token,
                plugin_fingerprint=plugin_fingerprint,
                codex_executable_identity=codex_executable_identity,
                codex_options=tuple(codex_options),
                log_device=log_identity[0],
                log_inode=log_identity[1],
            )
            write_state(paths, state)
            os.write(write_fd, f"{token}\n".encode())
            os.close(write_fd)
            write_fd = -1
            state = _wait_for_worker_state(paths, state, supervisor)
        if deferred.pending is not None:
            terminate_owned(state, graceful_seconds=1.0, forced_seconds=1.0)
            remove_state(paths)
            deferred.replay()
        return state
    except BaseException as exc:
        if write_fd >= 0:
            os.close(write_fd)
        if state is not None:
            try:
                _cleanup_failed_launch(paths, state)
            except CodexServerError as cleanup_error:
                raise cleanup_error from exc
        elif supervisor is not None:
            _kill_fresh_process_group(supervisor)
        raise
    finally:
        if read_fd >= 0:
            os.close(read_fd)


def _cleanup_failed_launch(paths: ServerPaths, initial: OwnedServer) -> None:
    """Contain a failed launch using the latest matching durable identity.

    The supervisor can publish its worker while the launcher is entering its
    exception path. Reading state both before and after supervisor termination
    ensures that a newly published independent worker group is never discarded.
    """
    for _attempt in range(2):
        current = _matching_launch_state(paths, initial)
        terminate_owned(current, graceful_seconds=1.0, forced_seconds=1.0)
    latest = read_state(paths)
    if latest is None:
        return
    _require_same_launch(initial, latest)
    remove_state(paths)


def _matching_launch_state(
    paths: ServerPaths,
    initial: OwnedServer,
) -> OwnedServer:
    """Return the newest state only when it names the same exact launch."""
    latest = read_state(paths)
    if latest is None:
        return initial
    _require_same_launch(initial, latest)
    return latest


def _require_same_launch(initial: OwnedServer, latest: OwnedServer) -> None:
    """Reject cleanup if durable ownership was replaced by another launch."""
    initial_identity = (
        initial.pid,
        initial.pgid,
        initial.process_started_at,
        initial.codex_path,
        initial.launch_token,
    )
    latest_identity = (
        latest.pid,
        latest.pgid,
        latest.process_started_at,
        latest.codex_path,
        latest.launch_token,
    )
    if latest_identity != initial_identity:
        raise CodexServerError(
            "app-server ownership changed during failed-launch cleanup; "
            "the replacement state was retained"
        )


def wait_for_process_identity(
    process: subprocess.Popen[bytes],
    timeout: float = 2.0,
) -> str | None:
    """Wait briefly for ``ps`` to expose a newly spawned process."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        identity = process_identity(process.pid)
        if identity:
            return identity
        time.sleep(POLL_SECONDS)
    return None


@dataclass
class DeferredTerminationSignals:
    """Record termination signals during the ownership publication gap."""

    pending: signal.Signals | None = None
    _previous: dict[signal.Signals, Any] = field(
        init=False,
        default_factory=dict,
    )

    def __enter__(self) -> DeferredTerminationSignals:
        """Install handlers that defer, rather than discard, termination."""
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous = signal.getsignal(sig)
            self._previous[sig] = previous
            if previous != signal.SIG_IGN:
                signal.signal(sig, self._capture)
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        """Restore every original signal disposition."""
        for sig, previous in self._previous.items():
            signal.signal(sig, previous)

    def _capture(self, signum: int, _frame: FrameType | None) -> None:
        """Record the first deferred signal."""
        if self.pending is None:
            self.pending = signal.Signals(signum)

    def replay(self) -> None:
        """Re-deliver a deferred signal after safe cleanup."""
        if self.pending is None:
            return
        signal.raise_signal(self.pending)
        raise CodexServerError(f"app-server startup interrupted by {self.pending.name}")


def _wait_for_worker_state(
    paths: ServerPaths,
    initial: OwnedServer,
    supervisor: subprocess.Popen[bytes],
    timeout: float = 5.0,
) -> OwnedServer:
    """Wait for the supervisor to persist its app-server worker identity."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if supervisor.poll() is not None:
            break
        current = read_state(paths)
        if (
            current is not None
            and current.launch_token == initial.launch_token
            and current.pid == initial.pid
            and current.worker_pid is not None
        ):
            return current
        time.sleep(POLL_SECONDS)
    detail = log_tail(
        paths.log_path,
        expected_identity=initial.log_identity,
    )
    suffix = f"\n\nApp-server log:\n{detail}" if detail else ""
    raise CodexServerError(
        f"app-server supervisor did not publish worker ownership{suffix}"
    )


def _terminate_worker(
    state: OwnedServer,
    graceful_seconds: float,
    forced_seconds: float,
) -> None:
    """Stop a supervised worker when its supervisor has disappeared."""
    assert state.worker_pid is not None
    assert state.worker_pgid is not None
    assert state.worker_started_at is not None
    if not _signal_verified(
        state.worker_pid,
        state.worker_pgid,
        state.worker_started_at,
        signal.SIGTERM,
    ):
        _reap_process(state.worker_pid, state.worker_started_at)
        if process_group_exists(state.worker_pgid):
            raise CodexServerError(
                f"worker leader {state.worker_pid} exited before group "
                f"{state.worker_pgid} could be verified; ownership state was "
                "retained"
            )
        return
    if wait_for_process_group_exit(
        state.worker_pgid,
        graceful_seconds,
        reap_pid=state.worker_pid,
    ):
        return
    if state_worker_matches(state):
        _signal_verified(
            state.worker_pid,
            state.worker_pgid,
            state.worker_started_at,
            signal.SIGTERM,
        )
    if wait_for_process_group_exit(
        state.worker_pgid,
        forced_seconds,
        reap_pid=state.worker_pid,
    ):
        return
    if state_worker_matches(state):
        _kill_verified_group(
            state.worker_pid,
            state.worker_pgid,
            state.worker_started_at,
        )
    elif process_group_exists(state.worker_pgid):
        raise CodexServerError(
            f"worker leader {state.worker_pid} exited before group "
            f"{state.worker_pgid} could be verified; ownership state was retained"
        )
    if not wait_for_process_group_exit(
        state.worker_pgid,
        2.0,
        reap_pid=state.worker_pid,
    ):
        raise CodexServerError(
            f"app-server worker group {state.worker_pgid} did not stop; "
            "ownership state was retained"
        )


def _kill_worker_if_verified(state: OwnedServer) -> None:
    """Forcibly contain a worker left behind by a failed supervisor."""
    if not state_worker_matches(state):
        return
    assert state.worker_pid is not None
    assert state.worker_pgid is not None
    assert state.worker_started_at is not None
    _kill_verified_group(
        state.worker_pid,
        state.worker_pgid,
        state.worker_started_at,
    )
    wait_for_process_group_exit(
        state.worker_pgid,
        2.0,
        reap_pid=state.worker_pid,
    )


def _require_worker_gone(state: OwnedServer) -> None:
    """Ensure a supervisor did not strand its independently grouped worker."""
    if state.worker_pgid is None or not process_group_exists(state.worker_pgid):
        return
    if state_worker_matches(state):
        _kill_worker_if_verified(state)
    if process_group_exists(state.worker_pgid):
        raise CodexServerError(
            f"app-server worker group {state.worker_pgid} survived its "
            "supervisor; ownership state was retained"
        )


def _signal_verified(
    pid: int,
    pgid: int,
    identity: str,
    sent: signal.Signals,
) -> bool:
    """Signal a leader only after an immediate ownership recheck."""
    if not process_matches(pid, pgid, identity):
        return False
    try:
        os.kill(pid, sent)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise CodexServerError(f"cannot signal app-server PID {pid}: {exc}") from exc
    return True


def _kill_verified_group(pid: int, pgid: int, identity: str) -> None:
    """Kill a process group only while its recorded leader still matches."""
    if not process_matches(pid, pgid, identity):
        raise CodexServerError(
            f"refusing to kill process group {pgid}: leader identity changed"
        )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise CodexServerError(
            f"cannot kill app-server process group {pgid}: {exc}"
        ) from exc


def _kill_fresh_process_group(
    process: subprocess.Popen[bytes] | subprocess.Popen[str],
) -> None:
    """Fully clean a just-spawned diagnostic or pre-handoff supervisor."""
    if process.returncode is not None:
        raise CodexServerError(
            f"refusing to signal process group {process.pid}: leader was already reaped"
        )
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not wait_for_process_group_exit(process.pid, 2.0):
        raise CodexServerError(
            f"new process group {process.pid} could not be cleaned up"
        )


def _reap_process(
    pid: int | None,
    expected_identity: str | None = None,
) -> None:
    """Reap a direct child without blocking, when this process owns it."""
    if pid is None:
        return
    if expected_identity is not None:
        current_identity = process_identity(pid)
        if current_identity is not None and current_identity != expected_identity:
            return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
