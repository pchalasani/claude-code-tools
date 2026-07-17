"""Fail-closed recovery for app servers started before native identities."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from claude_code_tools.codex_server_models import (
    OwnedServer,
    ServerPaths,
    StateFileError,
)
from claude_code_tools.codex_server_process import (
    process_identity,
    process_matches,
    run_diagnostic,
)
from claude_code_tools.codex_server_state import (
    StateCreationEvidence,
    read_state_with_evidence,
)


_PS_EXECUTABLE = "/bin/ps"
_NATIVE_IDENTITY_PREFIXES = ("darwin:", "linux:")
_DARWIN_IDENTITY_PREFIX = "darwin:"


def reauthenticate_legacy_state(
    paths: ServerPaths,
    state: OwnedServer | None,
    peer_pid: Callable[[Path], int | None],
    state_reader: Callable[
        [ServerPaths],
        tuple[OwnedServer | None, StateCreationEvidence | None],
    ] = read_state_with_evidence,
) -> OwnedServer | None:
    """Replace legacy start text with native identities after full validation.

    Args:
        paths: Runtime and listener paths for the persisted server.
        state: Validated ownership state, if present.
        peer_pid: Platform-specific provider for the listener process ID.
        state_reader: Descriptor-bound state reader, injectable for tests.

    Returns:
        Native reauthenticated state, the unchanged state when no migration is
        needed or safe, or ``None`` when no state was supplied.
    """
    if state is None or _has_native_identities(state):
        return state
    if not _is_complete_legacy_state(state):
        return state
    if state.worker_pid is None or state.worker_pgid is None:
        return state

    try:
        current, state_barrier = state_reader(paths)
    except StateFileError:
        return state
    if current is None or current != state or state_barrier is None:
        return state
    state = current
    assert state.worker_pid is not None
    assert state.worker_pgid is not None
    socket_identity = _socket_identity(paths.socket_path)
    if socket_identity is None or not _legacy_processes_match(state):
        return state
    if _parent_pid(state.worker_pid) != state.pid:
        return state

    listener_pid = peer_pid(paths.socket_path)
    listener_identity = _verified_group_identity(listener_pid, state.worker_pgid)
    controller_identity = process_identity(state.pid)
    worker_identity = process_identity(state.worker_pid)
    if (
        listener_identity is None
        or controller_identity is None
        or worker_identity is None
        or not all(
            _identity_predates_barrier(identity, state_barrier)
            for identity in (
                listener_identity,
                controller_identity,
                worker_identity,
            )
        )
    ):
        return state

    if not _revalidation_matches(
        paths,
        state,
        peer_pid,
        socket_identity,
        state_barrier,
        listener_pid,
        listener_identity,
        controller_identity,
        worker_identity,
    ):
        return state
    return replace(
        state,
        process_started_at=controller_identity,
        worker_started_at=worker_identity,
    )


def _has_native_identities(state: OwnedServer) -> bool:
    """Return whether both recorded leaders already use native identities."""
    return state.process_started_at.startswith(_NATIVE_IDENTITY_PREFIXES) and (
        state.worker_started_at or ""
    ).startswith(_NATIVE_IDENTITY_PREFIXES)


def _is_complete_legacy_state(state: OwnedServer) -> bool:
    """Return whether state has the exact shape produced before 1.16.2."""
    identities = (state.process_started_at, state.worker_started_at)
    return bool(
        state.supervised
        and state.worker_pid is not None
        and state.worker_pgid is not None
        and state.pid == state.pgid
        and state.worker_pid == state.worker_pgid
        and all(
            identity
            and len(identity) <= 128
            and identity.isascii()
            and not identity.startswith(_NATIVE_IDENTITY_PREFIXES)
            and not any(character in identity for character in "\r\n\0")
            for identity in identities
        )
    )


def _legacy_processes_match(state: OwnedServer) -> bool:
    """Validate both legacy start claims, groups, and their parent edge."""
    assert state.worker_pid is not None
    assert state.worker_pgid is not None
    assert state.worker_started_at is not None
    return _legacy_process_matches(
        state.pid,
        state.pgid,
        state.process_started_at,
    ) and _legacy_process_matches(
        state.worker_pid,
        state.worker_pgid,
        state.worker_started_at,
    )


def _legacy_process_matches(pid: int, pgid: int, expected: str) -> bool:
    """Compare one old textual start claim and its process group."""
    if _legacy_process_identity(pid) != expected:
        return False
    try:
        return os.getpgid(pid) == pgid
    except (OSError, ProcessLookupError):
        return False


def _legacy_process_identity(pid: int) -> str | None:
    """Read the textual start identity persisted by releases before 1.16.2."""
    result = run_diagnostic(
        [_PS_EXECUTABLE, "-o", "stat=", "-o", "lstart=", "-p", str(pid)],
        os.environ,
    )
    if result is None or result.returncode != 0:
        return None
    pieces = result.stdout.strip().split(maxsplit=1)
    if len(pieces) != 2 or not pieces[0] or pieces[0][0] in {"X", "x", "Z"}:
        return None
    return pieces[1].strip() or None


def _parent_pid(pid: int | None) -> int | None:
    """Return one live process's parent through trusted system ``ps``."""
    if pid is None:
        return None
    result = run_diagnostic(
        [_PS_EXECUTABLE, "-o", "ppid=", "-o", "stat=", "-p", str(pid)],
        os.environ,
    )
    if result is None or result.returncode != 0:
        return None
    pieces = result.stdout.strip().split()
    if len(pieces) != 2 or pieces[1][0] in {"X", "x", "Z"}:
        return None
    try:
        parent = int(pieces[0])
    except ValueError:
        return None
    return parent if parent > 0 else None


def _socket_identity(path: Path) -> tuple[int, int] | None:
    """Return a current-user Unix socket's stable filesystem identity."""
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        return None
    return info.st_dev, info.st_ino


def _state_creation_barrier(path: Path) -> StateCreationEvidence | None:
    """Return a stable state-file identity and immutable creation timestamp."""
    try:
        info = path.lstat()
    except OSError:
        return None
    birthtime = getattr(info, "st_birthtime", None)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or not isinstance(birthtime, (float, int))
        or birthtime <= 0
    ):
        return None
    return info.st_dev, info.st_ino, int(birthtime * 1_000_000)


def _identity_predates_barrier(
    identity: str,
    barrier: StateCreationEvidence,
) -> bool:
    """Return whether a native Darwin start time predates state publication."""
    if not identity.startswith(_DARWIN_IDENTITY_PREFIX):
        return False
    pieces = identity.split(":")
    if len(pieces) != 3 or not pieces[1].isdigit() or not pieces[2].isdigit():
        return False
    seconds = int(pieces[1])
    microseconds = int(pieces[2])
    if seconds <= 0 or not 0 <= microseconds <= 999_999:
        return False
    return seconds * 1_000_000 + microseconds <= barrier[2]


def _verified_group_identity(pid: int | None, pgid: int | None) -> str | None:
    """Return a native identity only for a member of the expected group."""
    if pid is None or pgid is None:
        return None
    identity = process_identity(pid)
    if identity is None or not process_matches(pid, pgid, identity):
        return None
    return identity


def _revalidation_matches(
    paths: ServerPaths,
    state: OwnedServer,
    peer_pid: Callable[[Path], int | None],
    socket_identity: tuple[int, int],
    state_barrier: StateCreationEvidence,
    listener_pid: int | None,
    listener_identity: str,
    controller_identity: str,
    worker_identity: str,
) -> bool:
    """Close replacement races before native ownership is authorized."""
    assert state.worker_pid is not None
    assert state.worker_pgid is not None
    return bool(
        _state_creation_barrier(paths.state_path) == state_barrier
        and _socket_identity(paths.socket_path) == socket_identity
        and peer_pid(paths.socket_path) == listener_pid
        and _parent_pid(state.worker_pid) == state.pid
        and _legacy_processes_match(state)
        and process_matches(state.pid, state.pgid, controller_identity)
        and process_matches(state.worker_pid, state.worker_pgid, worker_identity)
        and process_matches(listener_pid or 0, state.worker_pgid, listener_identity)
    )
