"""Typed paths, state, and safe file access for the Codex app server."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping

from claude_code_tools.codex_server_generation import validate_generation


ENDPOINT = "unix://"
CODEX_SERVER_OPTIONS_ENV = "CCTOOLS_CODEX_SERVER_OPTIONS"
CODEX_SERVER_GENERATION_ENV = "CCTOOLS_CODEX_SERVER_GENERATION"
STATE_VERSION = 1
START_TIMEOUT_SECONDS = 10.0
GRACEFUL_STOP_SECONDS = 60.0
FORCED_STOP_SECONDS = 10.0
POLL_SECONDS = 0.1
MINIMUM_CODEX_VERSION = (0, 136, 0)
MINIMUM_CODEX_VERSION_TEXT = ".".join(map(str, MINIMUM_CODEX_VERSION))
STATE_MAX_BYTES = 64 * 1024
STATE_MAX_DEPTH = 32
STATE_MAX_NODES = 1024
LOG_TAIL_MAX_BYTES = 64 * 1024
LOG_MAX_BYTES = 16 * 1024 * 1024
LOG_TRUNCATION_MARKER_PREFIX = b"[codex-server truncated an oversized log; generation "
LOG_TRUNCATION_GENERATION_BYTES = 32
LOG_TRUNCATION_MARKER_MAX_BYTES = (
    len(LOG_TRUNCATION_MARKER_PREFIX) + LOG_TRUNCATION_GENERATION_BYTES + 2
)
MAX_PROCESS_IDENTIFIER = (1 << 31) - 1
MAX_SERVER_GENERATIONS = 64
MAX_SERVER_GENERATION_DIRECTORIES = 256
MAX_SERVER_DIRECTORY_ENTRIES = 512


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
    generation: str | None = None

    @property
    def endpoint(self) -> str:
        """Return the Unix WebSocket endpoint represented by these paths."""
        if self.generation is None:
            return ENDPOINT
        return f"unix://{self.socket_path}"


@dataclass(frozen=True)
class LogTailSnapshot:
    """Bounded tail text and the exact checkpoint used to begin following."""

    text: str
    end: int
    generation: bytes
    suffix: bytes


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
    plugin_fingerprint: str | None = None
    codex_executable_identity: str | None = None
    codex_options: tuple[str, ...] = ()
    log_device: int | None = None
    log_inode: int | None = None
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
        plugin_fingerprint = value.get("pluginFingerprint")
        if plugin_fingerprint is not None:
            plugin_fingerprint = _nonempty_string(
                plugin_fingerprint,
                "plugin configuration fingerprint",
            )
        executable_identity = value.get("codexExecutableIdentity")
        if executable_identity is not None:
            executable_identity = _nonempty_string(
                executable_identity,
                "Codex executable identity",
            )
        raw_options = value.get("codexOptions", [])
        if (
            not isinstance(raw_options, list)
            or len(raw_options) > 128
            or any(
                not isinstance(item, str) or len(item) > 16_384 for item in raw_options
            )
        ):
            raise StateFileError("codex-server state has invalid Codex options")
        codex_options = tuple(raw_options)
        log_values = (value.get("logDevice"), value.get("logInode"))
        if all(item is None for item in log_values):
            log_device = None
            log_inode = None
        elif any(item is None for item in log_values):
            raise StateFileError("codex-server state has partial log identity")
        else:
            log_device = _nonnegative_integer(log_values[0], "log device")
            log_inode = _positive_platform_integer(log_values[1], "log inode")
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
            plugin_fingerprint=plugin_fingerprint,
            codex_executable_identity=executable_identity,
            codex_options=codex_options,
            log_device=log_device,
            log_inode=log_inode,
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
        if self.plugin_fingerprint is not None:
            value["pluginFingerprint"] = self.plugin_fingerprint
        if self.codex_executable_identity is not None:
            value["codexExecutableIdentity"] = self.codex_executable_identity
        if self.codex_options:
            value["codexOptions"] = list(self.codex_options)
        if self.log_device is not None:
            value.update(
                {
                    "logDevice": self.log_device,
                    "logInode": self.log_inode,
                }
            )
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

    @property
    def log_identity(self) -> tuple[int, int] | None:
        """Return the inode held by the supervisor, when published."""
        if self.log_device is None or self.log_inode is None:
            return None
        return self.log_device, self.log_inode


@dataclass(frozen=True)
class ServerProbe:
    """Result of checking the default app-server endpoint."""

    running: bool
    server_version: str | None = None
    method: str | None = None
    accepting: bool = False


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
            "endpoint": self.paths.endpoint,
            "generation": self.paths.generation,
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
    base = base_paths_from_env(env)
    raw_generation = env.get(CODEX_SERVER_GENERATION_ENV)
    if raw_generation is not None:
        try:
            generation = validate_generation(raw_generation)
        except ValueError as exc:
            raise CodexServerError(str(exc)) from exc
    else:
        generation = read_current_generation(base)
    if generation is None:
        return base
    return paths_for_generation(base, generation)


def base_paths_from_env(env: Mapping[str, str]) -> ServerPaths:
    """Resolve legacy base paths without consulting the generation marker."""
    home_value = env.get("CODEX_HOME")
    raw_home = Path(home_value).expanduser() if home_value else Path.home() / ".codex"
    raw_home = raw_home.absolute()
    try:
        info = raw_home.stat()
    except FileNotFoundError:
        codex_home = raw_home.resolve(strict=False)
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


def paths_for_generation(base: ServerPaths, generation: str) -> ServerPaths:
    """Resolve isolated runtime and socket paths for one generation."""
    try:
        checked = validate_generation(generation)
    except ValueError as exc:
        raise CodexServerError(str(exc)) from exc
    runtime_dir = base.runtime_dir / checked
    return ServerPaths(
        codex_home=base.codex_home,
        runtime_dir=runtime_dir,
        socket_path=base.socket_path.parent / f"g-{checked[:16]}.sock",
        state_path=runtime_dir / "state.json",
        lock_path=runtime_dir / "lifecycle.lock",
        log_path=runtime_dir / "app-server.log",
        generation=checked,
    )


def read_current_generation(base: ServerPaths) -> str | None:
    """Read the safely published default generation, if one exists."""
    marker = base.runtime_dir / "current-generation"
    info = _lstat(marker)
    if info is None:
        return None
    _require_regular_owned(info, marker, "generation marker")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CodexServerError("safe generation selection requires O_NOFOLLOW")
    try:
        fd = os.open(marker, os.O_RDONLY | os.O_NONBLOCK | no_follow)
        try:
            initial = os.fstat(fd)
            _require_regular_owned(initial, marker, "generation marker")
            raw = _read_bounded_fd(fd, 64)
            if _file_generation(os.fstat(fd)) != _file_generation(initial):
                raise CodexServerError("app-server generation changed while read")
        finally:
            os.close(fd)
        value = raw.decode("ascii").strip()
        return validate_generation(value)
    except (OSError, UnicodeError, ValueError, StateFileError) as exc:
        raise CodexServerError(
            f"cannot read generation marker {marker}: {exc}"
        ) from exc


def publish_current_generation(base: ServerPaths, generation: str) -> None:
    """Atomically select a fully certified generation for future launches."""
    checked = validate_generation(generation)
    prepare_runtime(base)
    marker = base.runtime_dir / "current-generation"
    marker_info = _lstat(marker)
    if marker_info is not None:
        _require_regular_owned(marker_info, marker, "generation marker")
    temporary = base.runtime_dir / f"current-generation.{os.getpid()}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(temporary, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(f"{checked}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        marker.chmod(0o600)
        _fsync_directory(base.runtime_dir)
    except OSError as exc:
        raise CodexServerError(f"cannot publish app-server generation: {exc}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def clear_current_generation(base: ServerPaths) -> None:
    """Remove the default-generation marker after explicit server cleanup."""
    marker = base.runtime_dir / "current-generation"
    info = _lstat(marker)
    if info is None:
        return
    _require_regular_owned(info, marker, "generation marker")
    try:
        marker.unlink()
        _fsync_directory(base.runtime_dir)
    except OSError as exc:
        raise CodexServerError(f"cannot clear generation marker: {exc}") from exc


def all_server_paths(base: ServerPaths) -> list[ServerPaths]:
    """Return bounded helper generations retaining state, a socket, or a lock."""
    from claude_code_tools.codex_server_reservation import (
        generation_has_active_reservation,
    )

    info = _lstat(base.runtime_dir)
    if info is None:
        return [base]
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise CodexServerError(f"unsafe runtime path: {base.runtime_dir}")
    if info.st_uid != os.getuid():
        raise CodexServerError(
            f"runtime directory is owned by another user: {base.runtime_dir}"
        )
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory is None:
        raise CodexServerError("safe generation scanning is unsupported")
    try:
        directory_fd = os.open(
            base.runtime_dir,
            os.O_RDONLY | no_follow | directory,
        )
    except OSError as exc:
        raise CodexServerError(
            f"cannot enumerate app-server generations: {exc}"
        ) from exc
    generations: list[str] = []
    scanned = 0
    try:
        if _file_generation(os.fstat(directory_fd)) != _file_generation(info):
            raise CodexServerError("app-server runtime changed during inspection")
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                try:
                    generation = validate_generation(entry.name)
                except ValueError:
                    scanned += 1
                    if scanned > MAX_SERVER_DIRECTORY_ENTRIES:
                        raise CodexServerError("too many app-server runtime entries")
                    continue
                try:
                    entry_info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise CodexServerError(
                        f"cannot inspect app-server generation {entry.name}: {exc}"
                    ) from exc
                if not stat.S_ISDIR(entry_info.st_mode) or stat.S_ISLNK(
                    entry_info.st_mode
                ):
                    raise CodexServerError(
                        f"unsafe app-server generation path: {entry.name}"
                    )
                if entry_info.st_uid != os.getuid():
                    raise CodexServerError(
                        f"app-server generation is owned by another user: {entry.name}"
                    )
                paths = paths_for_generation(base, generation)
                if (
                    _lstat(paths.state_path) is None
                    and _lstat(paths.socket_path) is None
                    and not generation_has_active_reservation(paths)
                ):
                    continue
                scanned += 1
                if scanned > MAX_SERVER_DIRECTORY_ENTRIES:
                    raise CodexServerError("too many app-server runtime entries")
                generations.append(generation)
                if len(generations) > MAX_SERVER_GENERATION_DIRECTORIES:
                    raise CodexServerError(
                        "too many app-server generations to inspect safely"
                    )
    finally:
        os.close(directory_fd)
    return [base, *(paths_for_generation(base, item) for item in sorted(generations))]


def require_generation_capacity(base: ServerPaths, generation: str) -> None:
    """Bound retained servers before creating another generation."""
    target = paths_for_generation(base, generation)
    retained = all_server_paths(base)[1:]
    if any(paths.generation == target.generation for paths in retained):
        return
    if len(retained) >= MAX_SERVER_GENERATIONS:
        raise CodexServerError(
            "app-server generation limit reached; exit callback-ready sessions "
            "and run `codex-server stop --force` before launching another"
        )


def prepare_runtime(paths: ServerPaths) -> None:
    """Create a private, non-symlinked helper runtime directory."""
    try:
        paths.codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        home_info = paths.codex_home.stat()
        if not stat.S_ISDIR(home_info.st_mode):
            raise CodexServerError(f"CODEX_HOME is not a directory: {paths.codex_home}")
        base_runtime = paths.codex_home / "cctools-app-server"
        base_runtime.mkdir(mode=0o700, exist_ok=True)
        base_info = base_runtime.lstat()
        if not stat.S_ISDIR(base_info.st_mode) or stat.S_ISLNK(base_info.st_mode):
            raise CodexServerError(
                f"unsafe runtime path (must be a directory): {base_runtime}"
            )
        if base_info.st_uid != os.getuid():
            raise CodexServerError(
                f"runtime directory is owned by another user: {base_runtime}"
            )
        paths.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = paths.runtime_dir.lstat()
        control_dir = paths.socket_path.parent
        control_dir.mkdir(mode=0o700, exist_ok=True)
        control_info = control_dir.lstat()
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
    if not stat.S_ISDIR(control_info.st_mode) or stat.S_ISLNK(control_info.st_mode):
        raise CodexServerError(
            f"unsafe app-server control path (must be a directory): {control_dir}"
        )
    if control_info.st_uid != os.getuid():
        raise CodexServerError(
            f"app-server control directory is owned by another user: {control_dir}"
        )
    try:
        if stat.S_IMODE(info.st_mode) != 0o700:
            paths.runtime_dir.chmod(0o700)
        if stat.S_IMODE(base_info.st_mode) != 0o700:
            base_runtime.chmod(0o700)
        if stat.S_IMODE(control_info.st_mode) != 0o700:
            control_dir.chmod(0o700)
    except OSError as exc:
        raise CodexServerError(
            f"cannot secure runtime directory {paths.runtime_dir}: {exc}"
        ) from exc


def read_state(paths: ServerPaths) -> OwnedServer | None:
    """Read and validate owned-process state without returning read evidence."""
    from claude_code_tools.codex_server_state import read_state_with_evidence

    return read_state_with_evidence(paths)[0]


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


def open_log_append(
    path: Path,
    expected_identity: tuple[int, int] | None = None,
) -> BinaryIO:
    """Open a private regular log, optionally bound to a published inode."""
    flags = os.O_APPEND | os.O_WRONLY | os.O_NONBLOCK
    if expected_identity is None:
        flags |= os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise CodexServerError(f"cannot open app-server log {path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        _require_regular_owned(info, path, "app-server log")
        _require_log_identity(info, path, expected_identity)
        os.fchmod(fd, 0o600)
        _truncate_oversized_log_fd(fd)
        _clear_nonblocking(fd)
        return os.fdopen(fd, "ab", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def open_log_reader(
    path: Path,
    expected_identity: tuple[int, int] | None = None,
) -> BinaryIO:
    """Open a regular log and optionally bind it to its supervisor's inode."""
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
        info = os.fstat(fd)
        _require_regular_owned(info, path, "app-server log")
        _require_log_identity(info, path, expected_identity)
        _clear_nonblocking(fd)
        return os.fdopen(fd, "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def log_tail(
    path: Path,
    lines: int = 20,
    expected_identity: tuple[int, int] | None = None,
) -> str:
    """Read final log lines with fixed byte and line-work bounds."""
    if lines <= 0:
        return ""
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            _require_regular_owned(info, path, "app-server log")
            _require_log_identity(info, path, expected_identity)
            data = _read_reverse_tail(fd, info.st_size, lines)
        finally:
            os.close(fd)
    except (CodexServerError, OSError):
        return ""
    return data.decode("utf-8", errors="replace").rstrip()


def log_tail_stream(stream: BinaryIO, lines: int) -> LogTailSnapshot:
    """Read a bounded tail with its same-generation follow checkpoint."""
    fd = stream.fileno()
    for _attempt in range(3):
        snapshot_end = os.fstat(fd).st_size
        generation_size = min(snapshot_end, LOG_TRUNCATION_MARKER_MAX_BYTES)
        generation = os.pread(fd, generation_size, 0)
        suffix_start = max(0, snapshot_end - 64)
        suffix = os.pread(fd, snapshot_end - suffix_start, suffix_start)
        data = _read_reverse_tail(fd, snapshot_end, lines) if lines > 0 else b""
        if os.pread(fd, len(generation), 0) == generation:
            return LogTailSnapshot(
                text=data.decode("utf-8", errors="replace").rstrip(),
                end=snapshot_end,
                generation=generation,
                suffix=suffix,
            )
    raise CodexServerError("app-server log changed repeatedly while being read")


def trim_oversized_log(path: Path) -> None:
    """Bound an active helper log while retaining a truncation diagnostic."""
    flags = os.O_RDWR | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CodexServerError(f"cannot trim app-server log {path}: {exc}") from exc
    try:
        _require_regular_owned(os.fstat(fd), path, "app-server log")
        _truncate_oversized_log_fd(fd)
    finally:
        os.close(fd)


def write_bounded_log(stream: BinaryIO, data: bytes) -> None:
    """Append one chunk while keeping the verified log within its hard cap."""
    if not data:
        return
    fd = stream.fileno()
    info = os.fstat(fd)
    _require_regular_owned(info, Path(str(stream.name)), "app-server log")
    retained = data[-(LOG_MAX_BYTES - LOG_TRUNCATION_MARKER_MAX_BYTES) :]
    if info.st_size + len(retained) > LOG_MAX_BYTES:
        os.ftruncate(fd, 0)
        _write_all(fd, _new_log_truncation_marker())
        info = os.fstat(fd)
    available = LOG_MAX_BYTES - info.st_size
    _write_all(fd, retained[:available])


def _read_bounded_fd(fd: int, limit: int) -> bytes:
    """Read at most ``limit`` bytes and reject any additional byte."""
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise StateFileError("codex-server state exceeds the safe size limit")
    return data


def _require_bounded_state(value: object) -> None:
    """Reject state with excessive nesting or ignored structural content."""
    pending: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > STATE_MAX_DEPTH:
            raise StateFileError("codex-server state is nested too deeply")
        if nodes > STATE_MAX_NODES:
            raise StateFileError("codex-server state has too many values")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _read_reverse_tail(fd: int, size: int, lines: int) -> bytes:
    """Read backward until enough newlines or the tail byte cap is reached."""
    offset = size
    chunks: list[bytes] = []
    total = 0
    newlines = 0
    while offset > 0 and total < LOG_TAIL_MAX_BYTES and newlines <= lines:
        count = min(8192, offset, LOG_TAIL_MAX_BYTES - total)
        offset -= count
        chunk = os.pread(fd, count, offset)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        newlines += chunk.count(b"\n")
    data = b"".join(reversed(chunks))
    selected = data.splitlines(keepends=True)[-lines:]
    return b"".join(selected)


def _truncate_oversized_log_fd(fd: int) -> None:
    """Reset an oversized open log descriptor to a small marker."""
    if os.fstat(fd).st_size <= LOG_MAX_BYTES:
        return
    os.ftruncate(fd, 0)
    _write_all(fd, _new_log_truncation_marker())


def _new_log_truncation_marker() -> bytes:
    """Return a unique prefix so followers cannot miss truncate/regrow ABA."""
    generation = secrets.token_hex(LOG_TRUNCATION_GENERATION_BYTES // 2).encode()
    return LOG_TRUNCATION_MARKER_PREFIX + generation + b"]\n"


def _write_all(fd: int, data: bytes) -> None:
    """Write all bounded bytes to a regular file descriptor."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("app-server log write made no progress")
        view = view[written:]


def _file_generation(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return descriptor metadata that changes across writes or replacement."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _positive_integer(value: object, label: str) -> int:
    """Validate a positive process identifier."""
    if not isinstance(value, int) or value <= 1 or value > MAX_PROCESS_IDENTIFIER:
        raise StateFileError(f"codex-server state has an invalid {label}")
    return value


def _nonnegative_integer(value: object, label: str) -> int:
    """Validate a nonnegative platform identifier."""
    if not isinstance(value, int) or value < 0:
        raise StateFileError(f"codex-server state has an invalid {label}")
    return value


def _positive_platform_integer(value: object, label: str) -> int:
    """Validate a positive filesystem identifier without a PID-sized cap."""
    if not isinstance(value, int) or value <= 0:
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


def _require_log_identity(
    info: os.stat_result,
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> None:
    """Require a descriptor to name the inode published by its supervisor."""
    if expected_identity is None:
        return
    actual_identity = (info.st_dev, info.st_ino)
    if actual_identity != expected_identity:
        raise CodexServerError(
            f"app-server log path no longer names the supervisor-owned file: {path}"
        )


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
