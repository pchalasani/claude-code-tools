"""Crash-safe app-server capacity reservations through lifecycle locks."""

from __future__ import annotations

import errno
import fcntl
import os

from claude_code_tools.codex_server_models import (
    CodexServerError,
    ServerPaths,
    _file_generation,
    _lstat,
    _require_regular_owned,
)


def generation_has_active_reservation(paths: ServerPaths) -> bool:
    """Return whether another process holds this generation's lifecycle lock.

    An unlocked lock file is inactive history. A held lock is a reservation
    that the kernel automatically releases if its launcher exits or crashes.
    """
    info = _lstat(paths.lock_path)
    if info is None:
        return False
    _require_regular_owned(info, paths.lock_path, "lifecycle lock")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise CodexServerError("safe lifecycle lock inspection requires O_NOFOLLOW")
    try:
        fd = os.open(
            paths.lock_path,
            os.O_RDWR | os.O_NONBLOCK | no_follow,
        )
    except OSError as exc:
        raise CodexServerError(
            f"cannot inspect lifecycle lock {paths.lock_path}: {exc}"
        ) from exc
    locked = False
    try:
        initial = os.fstat(fd)
        _require_regular_owned(initial, paths.lock_path, "lifecycle lock")
        if _file_generation(initial) != _file_generation(info):
            raise CodexServerError(
                f"lifecycle lock changed during inspection: {paths.lock_path}"
            )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            locked = True
        current = _lstat(paths.lock_path)
        if current is None or _file_generation(current) != _file_generation(initial):
            raise CodexServerError(
                f"lifecycle lock changed during inspection: {paths.lock_path}"
            )
        return locked
    except OSError as exc:
        raise CodexServerError(
            f"cannot inspect lifecycle lock {paths.lock_path}: {exc}"
        ) from exc
    finally:
        if not locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
