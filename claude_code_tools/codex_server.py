"""Manage a shared Codex app server and launch a TUI connected to it."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Mapping

from claude_code_tools.codex_server_models import (
    ENDPOINT,
    FORCED_STOP_SECONDS,
    GRACEFUL_STOP_SECONDS,
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
    log_tail,
    open_log_append,
    open_log_reader,
    paths_from_env,
    prepare_runtime,
    quarantine_invalid_state,
    read_state,
    remove_state,
    write_state,
)
from claude_code_tools.codex_server_process import (
    process_group_exists,
    process_identity,
    remove_stale_ownership,
    run_diagnostic,
    spawn_supervisor,
    state_controller_matches,
    state_worker_matches,
    terminate_owned,
    wait_for_process_group_exit,
    wait_for_process_identity,
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
    return child_env


def _codex_version(codex_path: str, env: Mapping[str, str]) -> str | None:
    """Read the active Codex CLI version without failing status checks."""
    result = _run_command([codex_path, "--version"], env)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


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
            )
        diagnostic = ""
        if result is not None:
            diagnostic = f"{result.stdout}\n{result.stderr}".lower()
        if not any(marker in diagnostic for marker in UNKNOWN_SUBCOMMAND_MARKERS):
            return ServerProbe(running=False)
    if _socket_accepts(paths.socket_path):
        return ServerProbe(running=True, method="socket")
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
    """Refuse to start over a non-socket filesystem entry."""
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
    if probe.running:
        return ServerStatus(
            status="running",
            ownership="helper" if owned is not None else "external",
            paths=paths,
            pid=owned.pid if owned is not None else None,
            codex_path=owned.codex_path if owned is not None else None,
            codex_version=owned.codex_version if owned is not None else None,
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
                "codex-server restart for explicit recovery"
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
    codex_version: str,
    probe: ServerProbe,
) -> str | None:
    """Explain why a helper listener cannot be safely reused."""
    if not state_controller_matches(state):
        return "its durable supervisor exited"
    if state.codex_path != codex_path:
        return "the active Codex executable path changed"
    if _version_key(state.codex_version) != _version_key(codex_version):
        return "the active Codex CLI version changed"
    if probe.server_version is not None and _version_key(
        probe.server_version
    ) != _version_key(codex_version):
        return "the running app-server version differs from the Codex CLI"
    return None


def _require_external_compatible(
    codex_version: str,
    probe: ServerProbe,
) -> None:
    """Reject an external listener that cannot match the selected CLI."""
    server_key = _version_key(probe.server_version)
    cli_key = _version_key(codex_version)
    if server_key is None:
        raise CodexServerError(
            "an external app server is running, but its version could not be "
            "verified; stop it before using codex-server"
        )
    if server_key != cli_key:
        raise CodexServerError(
            f"external app-server version {probe.server_version!r} does not "
            f"match the selected Codex CLI {codex_version!r}; stop or restart "
            "the external server"
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
        if not state_controller_matches(state) or not state_worker_matches(state):
            break
        time.sleep(POLL_SECONDS)
    detail = _log_tail(paths.log_path)
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


def ensure_server(env: Mapping[str, str] | None = None) -> ServerStatus:
    """Reuse a compatible listener or start a supervised app server."""
    active_env = dict(os.environ if env is None else env)
    paths = _paths(active_env)
    codex_path = _resolve_codex(active_env)
    child_env = _command_env(active_env, paths)
    codex_version = _require_compatible_codex(codex_path, child_env)

    with _lifecycle_lock(paths):
        try:
            state = _read_state(paths)
            state_error: str | None = None
        except StateFileError as exc:
            state = None
            state_error = str(exc)

        probe = _probe_server(codex_path, child_env, paths)
        status = _status_from(paths, state, probe, state_error)
        if status.status == "running" and status.ownership == "external":
            _require_external_compatible(codex_version, probe)
            return status
        if state is not None and _state_is_owned(state):
            restart_reason = _helper_restart_reason(
                state,
                codex_path,
                codex_version,
                probe,
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
                    codex_version,
                    probe,
                )
            if restart_reason is None:
                return status
            _terminate_owned(state, graceful_seconds=2.0, forced_seconds=1.0)
            _remove_state(paths)
            state = None

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
            )
            ready = _wait_until_ready(
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
            running = replace(current, phase="running")
            _write_state(paths, running)
            return _status_from(paths, running, ready)
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


def stop_server(env: Mapping[str, str] | None = None) -> ServerStatus:
    """Stop a helper-owned server and refuse external listeners."""
    active_env = dict(os.environ if env is None else env)
    paths = _paths(active_env)
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
            if probe.running:
                raise CodexServerError(
                    "app server is running, but ownership state is invalid; "
                    "refusing to stop it"
                ) from exc
            _quarantine_invalid_state(paths)
            state = None

        if state is not None and _state_is_owned(state):
            _terminate_owned(state)
            _remove_state(paths)
        elif state is not None:
            _remove_stale_ownership(paths, state)

        probe = _probe_server(codex_path, child_env, paths)
        if probe.running:
            raise CodexServerError(
                "app server is running, but it was not started by "
                "codex-server; refusing to stop it"
            )
        return ServerStatus(status="stopped", ownership=None, paths=paths)


def restart_server(env: Mapping[str, str] | None = None) -> ServerStatus:
    """Restart a helper-owned server, refusing an external listener."""
    active_env = dict(os.environ if env is None else env)
    status = get_status(active_env)
    if status.status == "running" and status.ownership == "external":
        raise CodexServerError("app server is externally owned; refusing to restart it")
    stop_server(active_env)
    return ensure_server(active_env)


__all__ = [
    "ENDPOINT",
    "CodexServerError",
    "OwnedServer",
    "ServerPaths",
    "ServerProbe",
    "ServerStatus",
    "StateFileError",
    "ensure_server",
    "get_status",
    "restart_server",
    "stop_server",
]
