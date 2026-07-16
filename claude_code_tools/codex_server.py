"""Manage a shared Codex app server and launch a TUI connected to it."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from claude_code_tools.codex_server_fingerprint import (
    PluginSnapshot as _PluginSnapshot,
    hash_plugin_tree as _hash_plugin_tree,  # noqa: F401
    plugin_configuration_fingerprint as _plugin_configuration_fingerprint,  # noqa: F401
    plugin_configuration_snapshot as _plugin_configuration_snapshot,
    read_plugin_configuration as _read_plugin_configuration,  # noqa: F401
)
from claude_code_tools.codex_server_generation import server_generation

from claude_code_tools.codex_server_models import (
    CODEX_SERVER_GENERATION_ENV,
    CODEX_SERVER_OPTIONS_ENV,
    FORCED_STOP_SECONDS,
    GRACEFUL_STOP_SECONDS,
    LOG_TRUNCATION_MARKER_MAX_BYTES,
    MINIMUM_CODEX_VERSION,
    MINIMUM_CODEX_VERSION_TEXT,
    POLL_SECONDS,
    START_TIMEOUT_SECONDS,
    CodexServerError,
    OwnedServer,
    ServerPaths,
    ServerProbe,
    ServerStatus,
    StateFileError,
    all_server_paths,
    base_paths_from_env,
    clear_current_generation,
    log_tail,
    log_tail_stream,
    open_log_append,
    open_log_reader,
    paths_from_env,
    paths_for_generation,
    prepare_runtime,
    publish_current_generation,
    quarantine_invalid_state,
    read_state,
    require_generation_capacity,
    remove_state,
    write_state,
)
from claude_code_tools.codex_server_process import (
    process_group_exists,
    process_identity,
    codex_executable_identity as _codex_executable_identity,
    remove_stale_ownership,
    run_diagnostic,
    spawn_supervisor,
    state_controller_matches,
    state_worker_matches,
    terminate_owned,
    wait_for_process_group_exit,
    wait_for_process_identity,
)
from claude_code_tools.codex_server_reuse import (
    disconnect_refusal as _disconnect_refusal,
    helper_restart_reason as _reuse_restart_reason,
    require_external_compatible as _external_compatible,
)


UNKNOWN_SUBCOMMAND_MARKERS = (
    "unrecognized subcommand",
    "unknown subcommand",
    "unexpected argument 'daemon'",
)
VERSION_PATTERN = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\.(\d+)"
    r"(?P<prerelease>-[0-9A-Za-z][0-9A-Za-z.-]*)?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?(?!\d)"
)
REUSE_PROBE_ATTEMPTS = 3
REUSE_PROBE_MAX_BACKOFF_SECONDS = 0.5

# Private aliases retained for callers and tests written against the first release.
_paths = paths_from_env
_prepare_runtime = prepare_runtime
_read_state = read_state
_write_state = write_state
_remove_state = remove_state
_quarantine_invalid_state = quarantine_invalid_state
_process_identity = process_identity
_process_group_exists = process_group_exists
_remove_stale_ownership = remove_stale_ownership
_log_tail = log_tail
_log_tail_stream = log_tail_stream
_log_generation_anchor_bytes = LOG_TRUNCATION_MARKER_MAX_BYTES
_open_log_append = open_log_append
_open_log_reader = open_log_reader
_run_command = run_diagnostic
_wait_for_process_identity = wait_for_process_identity


@contextmanager
def _lifecycle_lock(paths: ServerPaths) -> Iterator[None]:
    """Serialize lifecycle operations with a private POSIX file lock."""
    prepare_runtime(paths)
    flags = os.O_CREAT | os.O_RDWR | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(paths.lock_path, flags, 0o600)
    except OSError as exc:
        raise CodexServerError(
            f"cannot open lifecycle lock {paths.lock_path}: {exc}"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise CodexServerError(
                "unsafe lifecycle lock (must be a current-user regular file): "
                f"{paths.lock_path}"
            )
        os.fchmod(fd, 0o600)
        current = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, current & ~os.O_NONBLOCK)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise CodexServerError(
            f"cannot lock app-server lifecycle at {paths.lock_path}: {exc}"
        ) from exc
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _resolve_codex(env: Mapping[str, str]) -> str:
    """Resolve a canonical executable used by both server and TUI."""
    configured = env.get("CCTOOLS_CODEX_BIN")
    candidate: str | None
    if configured:
        expanded = Path(configured).expanduser()
        candidate = shutil.which(configured, path=env.get("PATH"))
        if candidate is None and expanded.is_file():
            candidate = str(expanded.absolute())
    else:
        candidate = shutil.which("codex", path=env.get("PATH"))
    if candidate is None:
        raise CodexServerError(
            "Codex CLI was not found; install it or set CCTOOLS_CODEX_BIN"
        )
    try:
        resolved = Path(candidate).resolve(strict=True)
    except OSError as exc:
        raise CodexServerError(f"cannot resolve Codex CLI {candidate}: {exc}") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise CodexServerError(f"Codex CLI is not executable: {resolved}")
    return str(resolved)


def _command_env(env: Mapping[str, str], paths: ServerPaths) -> dict[str, str]:
    """Return a child environment pinned to the resolved Codex home."""
    child_env = dict(env)
    child_env["CODEX_HOME"] = str(paths.codex_home)
    if paths.generation is None:
        child_env.pop(CODEX_SERVER_GENERATION_ENV, None)
    else:
        child_env[CODEX_SERVER_GENERATION_ENV] = paths.generation
    return child_env


def _codex_version(codex_path: str, env: Mapping[str, str]) -> str | None:
    """Read the active Codex CLI version without failing status checks."""
    result = _run_command([codex_path, "--version"], env)
    if result is None or result.returncode != 0:
        return None
    matches = list(VERSION_PATTERN.finditer(result.stdout))
    if not matches:
        return None
    return f"codex-cli {matches[-1].group(0)}"


def _version_key(version: str | None) -> tuple[int, int, int] | None:
    """Extract the first semantic three-part version from arbitrary output."""
    match = VERSION_PATTERN.search(version or "")
    if match is None:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )


def _is_floor_prerelease(version: str | None) -> bool:
    """Return whether a version is a prerelease of the minimum core version."""
    match = VERSION_PATTERN.search(version or "")
    if match is None or match.group("prerelease") is None:
        return False
    core = tuple(int(match.group(index)) for index in (1, 2, 3))
    return core == MINIMUM_CODEX_VERSION


def _require_compatible_codex(
    codex_path: str,
    env: Mapping[str, str],
) -> str:
    """Require the Unix WebSocket protocol used by callback clients."""
    version = _codex_version(codex_path, env)
    parsed = _version_key(version)
    if parsed is None:
        detail = f" (reported {version!r})" if version else ""
        raise CodexServerError(
            "cannot determine the Codex CLI version"
            f"{detail}; install Codex {MINIMUM_CODEX_VERSION_TEXT} or newer"
        )
    if parsed < MINIMUM_CODEX_VERSION or _is_floor_prerelease(version):
        raise CodexServerError(
            f"Codex {'.'.join(str(part) for part in parsed)} is not compatible "
            "with workflow callbacks; upgrade to Codex "
            f"{MINIMUM_CODEX_VERSION_TEXT} or newer"
        )
    return version or ""


def _socket_accepts(path: Path) -> bool:
    """Return whether a Unix listener accepts a local connection."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    if not stat.S_ISSOCK(info.st_mode):
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.25)
    try:
        client.connect(str(path))
    except OSError:
        return False
    finally:
        client.close()
    return True


def _socket_identity(path: Path) -> tuple[int, int] | None:
    """Return the identity of a socket path without following links."""
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISSOCK(info.st_mode):
        return None
    return info.st_dev, info.st_ino


def _socket_peer_pid(path: Path, attempts: int = 3) -> int | None:
    """Return the kernel-authenticated PID serving a stable Unix socket."""
    expected_identity = _socket_identity(path)
    if expected_identity is None:
        return None
    for attempt in range(max(1, attempts)):
        if _socket_identity(path) != expected_identity:
            return None
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.25)
        try:
            client.connect(str(path))
            if sys.platform == "darwin":
                try:
                    credentials = client.getsockopt(0, 0x002, 4)
                except OSError as exc:
                    if exc.errno == errno.ENOTCONN and attempt + 1 < attempts:
                        continue
                    return None
                peer_pid = struct.unpack("i", credentials)[0]
            else:
                peer_option = getattr(socket, "SO_PEERCRED", None)
                if peer_option is None:
                    return None
                credentials = client.getsockopt(
                    socket.SOL_SOCKET,
                    peer_option,
                    12,
                )
                peer_pid = struct.unpack("3i", credentials)[0]
            if _socket_identity(path) != expected_identity:
                return None
            return peer_pid
        except (OSError, struct.error):
            return None
        finally:
            client.close()
    return None


def _listener_matches_worker(state: OwnedServer, paths: ServerPaths) -> bool:
    """Verify that the socket peer remains in the recorded worker group."""
    if not state_worker_matches(state) or state.worker_pgid is None:
        return False
    peer_pid = _socket_peer_pid(paths.socket_path)
    if peer_pid is None:
        return False
    try:
        return os.getpgid(peer_pid) == state.worker_pgid
    except (OSError, ProcessLookupError):
        return False


def _wait_for_listener_ownership(
    state: OwnedServer,
    paths: ServerPaths,
    attempts: int = 3,
) -> bool:
    """Retry transient kernel peer lookup failures during fresh startup."""
    for attempt in range(max(1, attempts)):
        if _listener_matches_worker(state, paths):
            return True
        if attempt + 1 < attempts:
            time.sleep(POLL_SECONDS)
    return False


def _server_version_from_output(output: str) -> str | None:
    """Extract an app-server version from daemon-version JSON output."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        for key in ("appServerVersion", "serverVersion", "version"):
            version = value.get(key)
            if isinstance(version, str) and version:
                return version
    return None


def _probe_server(
    codex_path: str | None,
    env: Mapping[str, str],
    paths: ServerPaths,
) -> ServerProbe:
    """Probe the app-server protocol, with an older-CLI socket fallback."""
    if paths.generation is not None:
        accepting = _socket_accepts(paths.socket_path)
        return ServerProbe(
            running=accepting,
            method="socket" if accepting else None,
            accepting=accepting,
        )
    if codex_path:
        result = _run_command(
            [codex_path, "app-server", "daemon", "version"],
            env,
        )
        if result is not None and result.returncode == 0:
            return ServerProbe(
                running=True,
                server_version=_server_version_from_output(result.stdout),
                method="protocol",
                accepting=True,
            )
        diagnostic = ""
        if result is not None:
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
        if not any(marker in diagnostic for marker in UNKNOWN_SUBCOMMAND_MARKERS):
            accepting = _socket_accepts(paths.socket_path)
            return ServerProbe(
                running=False,
                method="socket" if accepting else None,
                accepting=accepting,
            )
    if _socket_accepts(paths.socket_path):
        return ServerProbe(running=True, method="socket", accepting=True)
    return ServerProbe(running=False)


def _retry_helper_probe(
    codex_path: str,
    env: Mapping[str, str],
    paths: ServerPaths,
    initial: ServerProbe,
    attempts: int = REUSE_PROBE_ATTEMPTS,
) -> ServerProbe:
    """Retry a failed probe before judging a verified live helper.

    Args:
        codex_path: Resolved Codex executable.
        env: Child environment pinned to the active Codex home.
        paths: Shared app-server paths.
        initial: Probe result already obtained by the caller.
        attempts: Total probes, including ``initial``.

    Returns:
        The first successful result, or the final failed result.
    """
    probe = initial
    for retry in range(1, max(1, attempts)):
        if probe.running:
            return probe
        backoff = min(
            POLL_SECONDS * (2 ** (retry - 1)),
            REUSE_PROBE_MAX_BACKOFF_SECONDS,
        )
        time.sleep(backoff)
        probe = _probe_server(codex_path, env, paths)
    return probe


def _check_socket_path(paths: ServerPaths) -> None:
    """Refuse to start over a non-socket or accepting filesystem entry."""
    try:
        info = paths.socket_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CodexServerError(
            f"cannot inspect app-server socket path {paths.socket_path}: {exc}"
        ) from exc
    if not stat.S_ISSOCK(info.st_mode):
        raise CodexServerError(
            "refusing to start: the app-server socket path contains a "
            f"non-socket entry: {paths.socket_path}"
        )
    if _socket_accepts(paths.socket_path):
        raise CodexServerError(
            "refusing to start over an accepting app-server socket whose "
            "protocol and ownership could not be certified"
        )


def _state_is_owned(state: OwnedServer | None) -> bool:
    """Return whether either recoverable process identity is still live."""
    if state is None:
        return False
    return state_controller_matches(state) or state_worker_matches(state)


def _status_from(
    paths: ServerPaths,
    state: OwnedServer | None,
    probe: ServerProbe,
    state_error: str | None = None,
) -> ServerStatus:
    """Combine process ownership and endpoint health into public status."""
    owned = state if _state_is_owned(state) else None
    endpoint_active = probe.running or probe.accepting
    listener_owned = (
        owned is not None and endpoint_active and _listener_matches_worker(owned, paths)
    )
    if endpoint_active:
        helper = owned if listener_owned else None
        return ServerStatus(
            status="running" if probe.running else "degraded",
            ownership="helper" if helper is not None else "external",
            paths=paths,
            pid=helper.pid if helper is not None else None,
            codex_path=helper.codex_path if helper is not None else None,
            codex_version=helper.codex_version if helper is not None else None,
            server_version=probe.server_version,
            probe_method=probe.method,
            detail=state_error,
        )
    if owned is not None:
        return ServerStatus(
            status="starting" if owned.phase == "starting" else "degraded",
            ownership="helper",
            paths=paths,
            pid=owned.pid,
            codex_path=owned.codex_path,
            codex_version=owned.codex_version,
            detail=(
                "process is alive but its app-server endpoint is unavailable; "
                "ownership was preserved to protect active sessions; run "
                "codex-server restart --force after exiting connected sessions "
                "for explicit recovery"
            ),
        )
    return ServerStatus(
        status="stopped",
        ownership=None,
        paths=paths,
        detail=state_error,
    )


def get_status(env: Mapping[str, str] | None = None) -> ServerStatus:
    """Inspect the shared app server without changing it."""
    active_env = dict(os.environ if env is None else env)
    paths = _paths(active_env)
    try:
        codex_path = _resolve_codex(active_env)
    except CodexServerError:
        codex_path = None
    child_env = _command_env(active_env, paths)
    try:
        state = _read_state(paths)
        state_error = None
    except StateFileError as exc:
        state = None
        state_error = str(exc)
    probe = _probe_server(codex_path, child_env, paths)
    return _status_from(paths, state, probe, state_error)


def _helper_restart_reason(
    state: OwnedServer,
    codex_path: str,
    codex_executable_identity: str,
    codex_version: str,
    probe: ServerProbe,
    plugin_fingerprint: str,
    paths: ServerPaths,
    codex_options: Sequence[str] = (),
) -> str | None:
    """Explain why a helper listener cannot be safely reused."""
    return _reuse_restart_reason(
        state,
        codex_path,
        codex_executable_identity,
        codex_version,
        probe,
        plugin_fingerprint,
        paths,
        codex_options,
        _listener_matches_worker,
        state_controller_matches,
        _version_key,
    )


def _require_unchanged_plugin_snapshot(
    paths: ServerPaths,
    expected: _PluginSnapshot,
    operation: str,
    codex_options: Sequence[str] = (),
) -> None:
    """Reject a lifecycle decision made across a plugin input change."""
    if _plugin_configuration_snapshot(paths, codex_options) != expected:
        raise CodexServerError(
            "the Codex plugin or marketplace snapshot changed during "
            f"{operation}; retry after plugin updates finish"
        )


def _same_server_launch(expected: OwnedServer, current: OwnedServer) -> bool:
    """Return whether two states name the same exact supervised launch."""
    return (
        current.pid,
        current.pgid,
        current.process_started_at,
        current.launch_token,
        current.worker_pid,
        current.worker_pgid,
        current.worker_started_at,
    ) == (
        expected.pid,
        expected.pgid,
        expected.process_started_at,
        expected.launch_token,
        expected.worker_pid,
        expected.worker_pgid,
        expected.worker_started_at,
    )


def _certify_helper_boundary(
    expected: OwnedServer,
    active_env: Mapping[str, str],
    child_env: Mapping[str, str],
    paths: ServerPaths,
    plugin_snapshot: _PluginSnapshot,
    codex_options: Sequence[str],
    operation: str,
) -> tuple[OwnedServer, ServerProbe]:
    """Fail closed unless the helper is fully certified at return time."""
    _require_unchanged_plugin_snapshot(
        paths,
        plugin_snapshot,
        operation,
        codex_options,
    )
    current = _read_state(paths)
    if current is None or not _same_server_launch(expected, current):
        raise CodexServerError(f"app-server ownership changed during {operation}")
    if not state_controller_matches(current) or not state_worker_matches(current):
        raise CodexServerError(f"app-server worker vanished during {operation}")
    codex_path = _resolve_codex(active_env)
    if codex_path != current.codex_path:
        raise CodexServerError(
            f"the selected Codex executable changed during {operation}"
        )
    executable_identity = _codex_executable_identity(codex_path)
    if (
        current.codex_executable_identity is None
        or executable_identity != current.codex_executable_identity
    ):
        raise CodexServerError(
            f"the selected Codex executable was replaced during {operation}"
        )
    codex_version = _require_compatible_codex(codex_path, child_env)
    if _version_key(codex_version) != _version_key(current.codex_version):
        raise CodexServerError(f"the selected Codex version changed during {operation}")
    probe = _probe_server(codex_path, child_env, paths)
    if not probe.running:
        raise CodexServerError(f"the app-server listener vanished during {operation}")
    if paths.generation is None and _version_key(probe.server_version) != _version_key(
        codex_version
    ):
        raise CodexServerError(
            f"the app-server version changed or was uncertified during {operation}"
        )
    if not _listener_matches_worker(current, paths):
        raise CodexServerError(
            f"the app-server listener changed during {operation}; "
            "it may have been replaced"
        )
    if not state_controller_matches(current) or not state_worker_matches(current):
        raise CodexServerError(f"app-server worker vanished during {operation}")
    if not _listener_matches_worker(current, paths):
        raise CodexServerError(
            f"the app-server listener changed during {operation}; "
            "it may have been replaced"
        )
    return current, probe


def _certified_helper_status(
    paths: ServerPaths,
    state: OwnedServer,
    probe: ServerProbe,
) -> ServerStatus:
    """Build a helper result only from final certified evidence."""
    version = probe.server_version
    if version is None:
        key = _version_key(state.codex_version)
        version = ".".join(map(str, key)) if key is not None else None
    return ServerStatus(
        status="running",
        ownership="helper",
        paths=paths,
        pid=state.pid,
        codex_path=state.codex_path,
        codex_version=state.codex_version,
        server_version=version,
        probe_method=probe.method,
    )


def _wait_until_ready(
    codex_path: str,
    child_env: Mapping[str, str],
    paths: ServerPaths,
    state: OwnedServer,
    timeout: float = START_TIMEOUT_SECONDS,
) -> ServerProbe:
    """Wait for protocol readiness while checking both owned leaders."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = _probe_server(codex_path, child_env, paths)
        if probe.running:
            return probe
        controller_matches = state_controller_matches(state)
        worker_matches = state_worker_matches(state)
        if not controller_matches or not worker_matches:
            flush_deadline = min(deadline, time.monotonic() + 0.5)
            while controller_matches and time.monotonic() < flush_deadline:
                time.sleep(POLL_SECONDS)
                controller_matches = state_controller_matches(state)
            break
        time.sleep(POLL_SECONDS)
    detail = _log_tail(
        paths.log_path,
        expected_identity=state.log_identity,
    )
    suffix = f"\n\nApp-server log:\n{detail}" if detail else ""
    raise CodexServerError(
        f"app server did not become ready within {timeout:g} seconds{suffix}"
    )


def _terminate_owned(
    state: OwnedServer,
    graceful_seconds: float = GRACEFUL_STOP_SECONDS,
    forced_seconds: float = FORCED_STOP_SECONDS,
    process: subprocess.Popen[bytes] | None = None,
) -> None:
    """Compatibility wrapper around verified supervised termination."""
    if process is not None and process.pid != state.pid:
        raise CodexServerError("spawned process does not match ownership state")
    terminate_owned(state, graceful_seconds, forced_seconds)


def _wait_for_process_group_exit(
    pgid: int,
    timeout: float,
    process: subprocess.Popen[bytes] | None = None,
) -> bool:
    """Compatibility wrapper for group-exit waiting."""
    return wait_for_process_group_exit(
        pgid,
        timeout,
        reap_pid=process.pid if process is not None else None,
    )


def ensure_server(
    env: Mapping[str, str] | None = None,
    *,
    codex_options: Sequence[str] = (),
) -> ServerStatus:
    """Reuse a compatible listener or start a supervised app server."""
    active_env = dict(os.environ if env is None else env)
    base_paths = base_paths_from_env(active_env)
    codex_path = _resolve_codex(active_env)
    codex_executable_identity = _codex_executable_identity(codex_path)
    base_child_env = _command_env(active_env, base_paths)
    codex_version = _require_compatible_codex(codex_path, base_child_env)
    plugin_snapshot = _plugin_configuration_snapshot(base_paths, codex_options)
    generation = server_generation(
        codex_path,
        codex_executable_identity,
        codex_version,
        plugin_snapshot.fingerprint,
        codex_options,
    )
    paths = paths_for_generation(base_paths, generation)
    require_generation_capacity(base_paths, generation)
    child_env = _command_env(active_env, paths)
    child_env[CODEX_SERVER_OPTIONS_ENV] = json.dumps(
        list(codex_options),
        separators=(",", ":"),
    )
    with _lifecycle_lock(paths):
        locked_snapshot = _plugin_configuration_snapshot(paths, codex_options)
        if locked_snapshot.fingerprint != plugin_snapshot.fingerprint:
            raise CodexServerError(
                "the Codex plugin or marketplace snapshot changed during "
                "app-server generation selection; retry after updates finish"
            )
        plugin_snapshot = locked_snapshot
        plugin_fingerprint = plugin_snapshot.fingerprint
        try:
            state = _read_state(paths)
            state_error: str | None = None
        except StateFileError as exc:
            state = None
            state_error = str(exc)

        probe = _probe_server(codex_path, child_env, paths)
        status = _status_from(paths, state, probe, state_error)
        if status.ownership == "external" and (probe.running or probe.accepting):
            if state is not None and _state_is_owned(state):
                raise CodexServerError(
                    "shared app server cannot be reused because its socket "
                    "listener is not owned by the supervised worker"
                )
            _external_compatible(codex_version, probe, _version_key)
            return status
        if state is not None and _state_is_owned(state):
            restart_reason = _helper_restart_reason(
                state,
                codex_path,
                codex_executable_identity,
                codex_version,
                probe,
                plugin_fingerprint,
                paths,
                codex_options,
            )
            if restart_reason is None and not probe.running:
                probe = _retry_helper_probe(
                    codex_path,
                    child_env,
                    paths,
                    probe,
                )
                status = _status_from(paths, state, probe, state_error)
                restart_reason = _helper_restart_reason(
                    state,
                    codex_path,
                    codex_executable_identity,
                    codex_version,
                    probe,
                    plugin_fingerprint,
                    paths,
                    codex_options,
                )
            if restart_reason is None:
                certified_state, certified_probe = _certify_helper_boundary(
                    state,
                    active_env,
                    child_env,
                    paths,
                    plugin_snapshot,
                    codex_options,
                    "app-server reuse checks",
                )
                result = _certified_helper_status(
                    paths,
                    certified_state,
                    certified_probe,
                )
                publish_current_generation(base_paths, generation)
                return result
            raise CodexServerError(
                "selected app-server generation cannot be reused because "
                f"{restart_reason}; refusing to replace an uncertified listener"
            )

        if state_error:
            _quarantine_invalid_state(paths)
        elif state is not None:
            if _state_is_owned(state):
                _terminate_owned(state, graceful_seconds=2.0, forced_seconds=1.0)
                _remove_state(paths)
            else:
                _remove_stale_ownership(paths, state)

        _check_socket_path(paths)
        starting: OwnedServer | None = None
        try:
            starting = spawn_supervisor(
                codex_path,
                codex_version,
                child_env,
                paths,
                plugin_fingerprint,
                codex_executable_identity,
                codex_options,
            )
            _wait_until_ready(
                codex_path,
                child_env,
                paths,
                starting,
            )
            current = _read_state(paths)
            if (
                current is None
                or current.launch_token != starting.launch_token
                or current.pid != starting.pid
            ):
                raise CodexServerError(
                    "app-server ownership changed before startup completed"
                )
            if not _listener_matches_worker(current, paths):
                raise CodexServerError(
                    "app-server socket is not owned by its supervised worker"
                )
            _require_unchanged_plugin_snapshot(
                paths,
                plugin_snapshot,
                "app-server startup",
                codex_options,
            )
            if not _wait_for_listener_ownership(current, paths):
                raise CodexServerError(
                    "the app-server listener changed during startup checks; "
                    "refusing to certify helper ownership"
                )
            running = replace(current, phase="running")
            _write_state(paths, running)
            certified_state, certified_probe = _certify_helper_boundary(
                running,
                active_env,
                child_env,
                paths,
                plugin_snapshot,
                codex_options,
                "app-server startup checks",
            )
            result = _certified_helper_status(
                paths,
                certified_state,
                certified_probe,
            )
            publish_current_generation(base_paths, generation)
            return result
        except BaseException as exc:
            if starting is not None:
                try:
                    _terminate_owned(
                        starting,
                        graceful_seconds=1.0,
                        forced_seconds=1.0,
                    )
                except CodexServerError as cleanup_error:
                    raise cleanup_error from exc
            try:
                _remove_state(paths)
            except StateFileError:
                pass
            raise


def _stop_server_at(
    active_env: Mapping[str, str],
    paths: ServerPaths,
    allow_disconnect: bool,
) -> ServerStatus:
    """Stop one selected helper generation."""
    try:
        codex_path = _resolve_codex(active_env)
    except CodexServerError:
        codex_path = None
    child_env = _command_env(active_env, paths)

    with _lifecycle_lock(paths):
        try:
            state = _read_state(paths)
        except StateFileError as exc:
            probe = _probe_server(codex_path, child_env, paths)
            if probe.running or probe.accepting:
                raise CodexServerError(
                    "app server is running, but ownership state is invalid; "
                    "refusing to stop it"
                ) from exc
            _quarantine_invalid_state(paths)
            state = None

        if state is not None and _state_is_owned(state):
            if not allow_disconnect:
                raise _disconnect_refusal("stop")
            _terminate_owned(state)
            _remove_state(paths)
        elif state is not None:
            _remove_stale_ownership(paths, state)

        probe = _probe_server(codex_path, child_env, paths)
        if probe.running or probe.accepting:
            raise CodexServerError(
                "app server is running, but it was not started by "
                "codex-server; refusing to stop it"
            )
        return ServerStatus(status="stopped", ownership=None, paths=paths)


def stop_server(
    env: Mapping[str, str] | None = None,
    *,
    allow_disconnect: bool = False,
) -> ServerStatus:
    """Stop helper-owned servers after explicit disconnect acknowledgement."""
    active_env = dict(os.environ if env is None else env)
    selected = _paths(active_env)
    if not allow_disconnect:
        return _stop_server_at(active_env, selected, False)
    base = base_paths_from_env(active_env)
    for paths in all_server_paths(base):
        _stop_server_at(active_env, paths, True)
    clear_current_generation(base)
    return ServerStatus(status="stopped", ownership=None, paths=selected)


def restart_server(
    env: Mapping[str, str] | None = None,
    *,
    allow_disconnect: bool = False,
) -> ServerStatus:
    """Restart after explicit acknowledgement that attached TUIs will exit."""
    active_env = dict(os.environ if env is None else env)
    status = get_status(active_env)
    if status.status == "running" and status.ownership == "external":
        raise CodexServerError("app server is externally owned; refusing to restart it")
    if status.ownership == "helper" and not allow_disconnect:
        raise _disconnect_refusal("restart")
    codex_options: tuple[str, ...] = ()
    if status.ownership == "helper":
        state = _read_state(_paths(active_env))
        if state is None or state.pid != status.pid:
            raise CodexServerError("app-server ownership changed before restart")
        codex_options = state.codex_options
    stop_server(active_env, allow_disconnect=allow_disconnect)
    return ensure_server(active_env, codex_options=codex_options)
