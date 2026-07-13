"""Typed paths, state, and safe file access for the Codex app server."""

from __future__ import annotations

import collections
import fcntl
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, TextIO


ENDPOINT = "unix://"
STATE_VERSION = 1
START_TIMEOUT_SECONDS = 10.0
GRACEFUL_STOP_SECONDS = 60.0
FORCED_STOP_SECONDS = 10.0
POLL_SECONDS = 0.1
MINIMUM_CODEX_VERSION = (0, 136, 0)
MINIMUM_CODEX_VERSION_TEXT = ".".join(map(str, MINIMUM_CODEX_VERSION))


class CodexServerError(RuntimeError):
    """A safe, user-facing app-server lifecycle failure."""


class StateFileError(CodexServerError):
    """An invalid or unsafe helper state file."""


@dataclass(frozen=True)
class ServerPaths:
    """Filesystem paths shared by the lifecycle commands."""

    codex_home: Path
    runtime_dir: Path
    socket_path: Path
    state_path: Path
    lock_path: Path
    log_path: Path


@dataclass(frozen=True)
class OwnedServer:
    """Durable supervisor and app-server worker ownership identity."""

    pid: int
    pgid: int
    process_started_at: str
    codex_path: str
    codex_version: str | None
    launched_at: str
    phase: str
    launch_token: str | None = None
    worker_pid: int | None = None
    worker_pgid: int | None = None
    worker_started_at: str | None = None

    @classmethod
    def from_json(cls, value: object) -> OwnedServer:
        """Validate and decode persisted ownership state.

        Args:
            value: Parsed JSON value.

        Returns:
            Validated ownership state.

        Raises:
            StateFileError: If the value cannot safely identify a process.
        """
        if not isinstance(value, dict):
            raise StateFileError("codex-server state is not a JSON object")
        if value.get("version") != STATE_VERSION:
            raise StateFileError("codex-server state has an unsupported version")

        pid = _positive_integer(value.get("pid"), "pid")
        pgid = _positive_integer(value.get("pgid"), "process group")
        started = _nonempty_string(value.get("processStartedAt"), "process identity")
        codex_path = _nonempty_string(value.get("codexPath"), "Codex path")
        codex_version = value.get("codexVersion")
        if codex_version is not None and not isinstance(codex_version, str):
            raise StateFileError("codex-server state has an invalid Codex version")
        launched = _nonempty_string(value.get("launchedAt"), "launch time")
        phase = value.get("phase")
        if phase not in {"starting", "running"}:
            raise StateFileError("codex-server state has an invalid phase")

        launch_token = value.get("launchToken")
        if launch_token is not None:
            launch_token = _nonempty_string(launch_token, "launch token")
        worker_values = (
            value.get("workerPid"),
            value.get("workerPgid"),
            value.get("workerStartedAt"),
        )
        if all(item is None for item in worker_values):
            worker_pid = None
            worker_pgid = None
            worker_started_at = None
        elif any(item is None for item in worker_values):
            raise StateFileError("codex-server state has partial worker identity")
        else:
            worker_pid = _positive_integer(worker_values[0], "worker pid")
            worker_pgid = _positive_integer(worker_values[1], "worker process group")
            worker_started_at = _nonempty_string(
                worker_values[2],
                "worker process identity",
            )

        return cls(
            pid=pid,
            pgid=pgid,
            process_started_at=started,
            codex_path=codex_path,
            codex_version=codex_version,
            launched_at=launched,
            phase=phase,
            launch_token=launch_token,
            worker_pid=worker_pid,
            worker_pgid=worker_pgid,
            worker_started_at=worker_started_at,
        )

    def as_json(self) -> dict[str, object]:
        """Return the stable JSON representation for this state."""
        value: dict[str, object] = {
            "version": STATE_VERSION,
            "pid": self.pid,
            "pgid": self.pgid,
            "processStartedAt": self.process_started_at,
            "codexPath": self.codex_path,
            "codexVersion": self.codex_version,
            "launchedAt": self.launched_at,
            "phase": self.phase,
        }
        if self.launch_token is not None:
            value["launchToken"] = self.launch_token
        if self.worker_pid is not None:
            value.update(
                {
                    "workerPid": self.worker_pid,
                    "workerPgid": self.worker_pgid,
                    "workerStartedAt": self.worker_started_at,
                }
            )
        return value

    @property
    def supervised(self) -> bool:
        """Return whether the recorded leader is the stable supervisor."""
        return self.launch_token is not None


@dataclass(frozen=True)
class ServerProbe:
    """Result of checking the default app-server endpoint."""

    running: bool
    server_version: str | None = None
    method: str | None = None


@dataclass(frozen=True)
class ServerStatus:
    """Normalized status exposed by the CLI."""

    status: str
    ownership: str | None
    paths: ServerPaths
    pid: int | None = None
    codex_path: str | None = None
    codex_version: str | None = None
    server_version: str | None = None
    probe_method: str | None = None
    detail: str | None = None

    def as_json(self) -> dict[str, object]:
        """Return a machine-readable status object."""
        return {
            "status": self.status,
            "ownership": self.ownership,
            "endpoint": ENDPOINT,
            "socketPath": str(self.paths.socket_path),
            "pid": self.pid,
            "codexPath": self.codex_path,
            "codexVersion": self.codex_version,
            "serverVersion": self.server_version,
            "probeMethod": self.probe_method,
            "logPath": (
                str(self.paths.log_path) if self.ownership == "helper" else None
            ),
            "detail": self.detail,
        }


def paths_from_env(env: Mapping[str, str]) -> ServerPaths:
    """Resolve helper paths and validate an existing ``CODEX_HOME``.

    Args:
        env: Environment containing an optional ``CODEX_HOME``.

    Returns:
        Resolved server paths.

    Raises:
        CodexServerError: If ``CODEX_HOME`` is inaccessible or not a directory.
    """
    home_value = env.get("CODEX_HOME")
    raw_home = Path(home_value).expanduser() if home_value else Path.home() / ".codex"
    raw_home = raw_home.absolute()
    try:
        info = raw_home.stat()
    except FileNotFoundError:
        codex_home = raw_home
    except OSError as exc:
        raise CodexServerError(f"cannot access CODEX_HOME {raw_home}: {exc}") from exc
    else:
        if not stat.S_ISDIR(info.st_mode):
            raise CodexServerError(f"CODEX_HOME is not a directory: {raw_home}")
        try:
            codex_home = raw_home.resolve(strict=True)
        except OSError as exc:
            raise CodexServerError(
                f"cannot resolve CODEX_HOME {raw_home}: {exc}"
            ) from exc

    runtime_dir = codex_home / "cctools-app-server"
    return ServerPaths(
        codex_home=codex_home,
        runtime_dir=runtime_dir,
        socket_path=(codex_home / "app-server-control" / "app-server-control.sock"),
        state_path=runtime_dir / "state.json",
        lock_path=runtime_dir / "lifecycle.lock",
        log_path=runtime_dir / "app-server.log",
    )


def prepare_runtime(paths: ServerPaths) -> None:
    """Create a private, non-symlinked helper runtime directory."""
    try:
        paths.codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        home_info = paths.codex_home.stat()
        if not stat.S_ISDIR(home_info.st_mode):
            raise CodexServerError(f"CODEX_HOME is not a directory: {paths.codex_home}")
        paths.runtime_dir.mkdir(mode=0o700, exist_ok=True)
        info = paths.runtime_dir.lstat()
    except CodexServerError:
        raise
    except OSError as exc:
        raise CodexServerError(
            f"cannot create runtime directory {paths.runtime_dir}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise CodexServerError(
            f"unsafe runtime path (must be a directory): {paths.runtime_dir}"
        )
    if info.st_uid != os.getuid():
        raise CodexServerError(
            f"runtime directory is owned by another user: {paths.runtime_dir}"
        )
    try:
        if stat.S_IMODE(info.st_mode) != 0o700:
            paths.runtime_dir.chmod(0o700)
    except OSError as exc:
        raise CodexServerError(
            f"cannot secure runtime directory {paths.runtime_dir}: {exc}"
        ) from exc


def read_state(paths: ServerPaths) -> OwnedServer | None:
    """Read and validate owned-process state."""
    info = _lstat(paths.state_path)
    if info is None:
        return None
    _require_regular_owned(info, paths.state_path, "state")
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(paths.state_path, flags)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise StateFileError(f"cannot read {paths.state_path}: {exc}") from exc
    return OwnedServer.from_json(value)


def write_state(paths: ServerPaths, state: OwnedServer) -> None:
    """Atomically and durably persist private ownership state."""
    prepare_runtime(paths)
    temporary = paths.runtime_dir / f"state.{os.getpid()}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    data = json.dumps(state.as_json(), indent=2, sort_keys=True) + "\n"
    try:
        fd = os.open(temporary, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, paths.state_path)
        paths.state_path.chmod(0o600)
        _fsync_directory(paths.runtime_dir)
    except OSError as exc:
        raise CodexServerError(
            f"cannot persist app-server ownership state: {exc}"
        ) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def remove_state(paths: ServerPaths) -> None:
    """Remove a validated regular state file, if present."""
    info = _lstat(paths.state_path)
    if info is None:
        return
    _require_regular_owned(info, paths.state_path, "state")
    try:
        paths.state_path.unlink()
        _fsync_directory(paths.runtime_dir)
    except OSError as exc:
        raise StateFileError(f"cannot remove {paths.state_path}: {exc}") from exc


def quarantine_invalid_state(paths: ServerPaths) -> None:
    """Move malformed regular state aside without following links."""
    info = _lstat(paths.state_path)
    if info is None:
        return
    _require_regular_owned(info, paths.state_path, "state")
    destination = paths.runtime_dir / f"state.invalid.{time_ns()}.json"
    try:
        os.replace(paths.state_path, destination)
        destination.chmod(0o600)
        _fsync_directory(paths.runtime_dir)
    except OSError as exc:
        raise StateFileError(
            f"cannot quarantine invalid state {paths.state_path}: {exc}"
        ) from exc


def open_log_append(path: Path) -> BinaryIO:
    """Open a private regular log without following or blocking on special files."""
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise CodexServerError(f"cannot open app-server log {path}: {exc}") from exc
    try:
        _require_regular_owned(os.fstat(fd), path, "app-server log")
        os.fchmod(fd, 0o600)
        _clear_nonblocking(fd)
        return os.fdopen(fd, "ab", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def open_log_reader(path: Path) -> TextIO:
    """Open a current-user regular log without following or blocking."""
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise CodexServerError(f"no app-server log at {path}") from None
    except OSError as exc:
        raise CodexServerError(f"cannot open app-server log {path}: {exc}") from exc
    try:
        _require_regular_owned(os.fstat(fd), path, "app-server log")
        _clear_nonblocking(fd)
        return os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    except BaseException:
        os.close(fd)
        raise


def log_tail(path: Path, lines: int = 20) -> str:
    """Read the final lines of a verified log without loading the whole file."""
    try:
        with open_log_reader(path) as stream:
            tail = collections.deque(stream, maxlen=lines)
    except CodexServerError:
        return ""
    return "".join(tail).rstrip()


def _positive_integer(value: object, label: str) -> int:
    """Validate a positive process identifier."""
    if not isinstance(value, int) or value <= 1:
        raise StateFileError(f"codex-server state has an invalid {label}")
    return value


def _nonempty_string(value: object, label: str) -> str:
    """Validate a required state string."""
    if not isinstance(value, str) or not value:
        raise StateFileError(f"codex-server state has no {label}")
    return value


def _lstat(path: Path) -> os.stat_result | None:
    """Inspect a path without following symlinks."""
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateFileError(f"cannot inspect {path}: {exc}") from exc


def _require_regular_owned(
    info: os.stat_result,
    path: Path,
    label: str,
) -> None:
    """Require a current-user regular file."""
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise StateFileError(f"unsafe {label} path (must be a regular file): {path}")
    if info.st_uid != os.getuid():
        raise StateFileError(f"unsafe {label} path owned by another user: {path}")


def _clear_nonblocking(fd: int) -> None:
    """Return a verified regular descriptor to conventional blocking mode."""
    current = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, current & ~os.O_NONBLOCK)


def _fsync_directory(path: Path) -> None:
    """Durably publish a rename or unlink in a state directory."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def time_ns() -> int:
    """Return a collision-resistant timestamp for quarantine names."""
    import time

    return time.time_ns()
