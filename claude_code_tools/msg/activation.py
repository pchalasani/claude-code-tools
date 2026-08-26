"""DB-independent activation markers for fail-closed First-mate hooks."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import stat
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import Agent, ConsumerProtocol

ACTIVATION_SCHEMA = "msg.first-mate.activation.v1"


@dataclass(frozen=True)
class ActivationReceipt:
    path: Path
    generation: str


def _activation_dir(db_path: str | os.PathLike[str]) -> Path:
    return Path(db_path).parent / "first-mate-activations"


def _scope_key(
    tmux_session: str | None, tmux_socket: str | None, pane_id: str,
) -> str:
    encoded = json.dumps(
        [tmux_socket, pane_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _activation_path(
    db_path: str | os.PathLike[str],
    tmux_session: str | None,
    tmux_socket: str | None,
    pane_id: str,
) -> Path:
    return _activation_dir(db_path) / f"{_scope_key(tmux_session, tmux_socket, pane_id)}.json"


@contextmanager
def _scope_lock(
    db_path: str | os.PathLike[str],
    tmux_socket: str | None,
    pane_id: str,
):
    directory = _activation_dir(db_path)
    locks = directory / ".locks"
    locks.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(locks, 0o700)
    path = locks / f"{_scope_key(None, tmux_socket, pane_id)}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_activation(
    db_path: str | os.PathLike[str], agent: Agent,
) -> ActivationReceipt:
    """Atomically publish one private marker for a First-mate registration."""
    if agent.consumer_protocol is not ConsumerProtocol.FIRST_MATE_V1:
        raise ValueError("activation markers are only for first-mate.v1")
    directory = _activation_dir(db_path)
    directory_existed = directory.is_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if not directory_existed:
        _fsync_directory(directory.parent)
    path = _activation_path(
        db_path, agent.tmux_session, agent.tmux_socket, agent.pane_id,
    )
    generation = str(uuid.uuid4())
    payload = {
        "schema": ACTIVATION_SCHEMA,
        "session_id": agent.session_id,
        "tmux_session": agent.tmux_session,
        "tmux_socket": agent.tmux_socket,
        "pane_id": agent.pane_id,
        "agent_kind": agent.agent_kind.value,
        "pid": agent.pid,
        "process_start_identity": agent.process_start_identity,
        "cwd": agent.cwd,
        "marker_generation": generation,
    }
    with _scope_lock(db_path, agent.tmux_socket, agent.pane_id):
        temp = directory / f".{path.name}.{uuid.uuid4().hex}.tmp"
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(temp, flags, 0o600)
        try:
            data = json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
            ).encode()
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temp, path)
            _fsync_directory(directory)
        finally:
            if os.path.lexists(temp):
                temp.unlink()
    return ActivationReceipt(path=path, generation=generation)


def load_activation(
    db_path: str | os.PathLike[str],
    tmux_session: str | None,
    tmux_socket: str | None,
    pane_id: str,
) -> dict | None:
    """Load a scope marker; a corrupt existing marker stays truthy/fail-closed."""
    path = _activation_path(db_path, tmux_session, tmux_socket, pane_id)
    if not os.path.lexists(path):
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("not regular")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise ValueError("unsafe mode")
            raw = os.read(fd, 65_537)
        finally:
            os.close(fd)
        if len(raw) > 65_536:
            raise ValueError("oversize")
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("schema") != ACTIVATION_SCHEMA:
            raise ValueError("schema")
        if (
            (tmux_session is not None and payload.get("tmux_session") != tmux_session)
            or payload.get("tmux_socket") != tmux_socket
            or payload.get("pane_id") != pane_id
        ):
            raise ValueError("scope")
        return payload
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {"schema": ACTIVATION_SCHEMA, "invalid": True}


def activation_generation(
    db_path: str | os.PathLike[str], agent: Agent,
) -> str | None:
    marker = load_activation(
        db_path, agent.tmux_session, agent.tmux_socket, agent.pane_id,
    )
    generation = marker.get("marker_generation") if marker else None
    return generation if isinstance(generation, str) else None


def remove_activation(
    db_path: str | os.PathLike[str],
    agent: Agent,
    *,
    expected_generation: str,
    _before_unlink: Callable[[], None] | None = None,
) -> bool:
    path = _activation_path(
        db_path, agent.tmux_session, agent.tmux_socket, agent.pane_id,
    )
    with _scope_lock(db_path, agent.tmux_socket, agent.pane_id):
        if not os.path.lexists(path):
            return False
        current = load_activation(
            db_path, agent.tmux_session, agent.tmux_socket, agent.pane_id,
        )
        if (
            not current
            or current.get("session_id") != agent.session_id
            or current.get("marker_generation") != expected_generation
        ):
            return False
        if _before_unlink:
            _before_unlink()
        path.unlink()
        _fsync_directory(path.parent)
        return True
