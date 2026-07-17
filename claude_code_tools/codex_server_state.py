"""Descriptor-bound reads of Codex app-server ownership state."""

from __future__ import annotations

import json
import os

from claude_code_tools.codex_server_models import (
    STATE_MAX_BYTES,
    OwnedServer,
    ServerPaths,
    StateFileError,
    _file_generation,
    _lstat,
    _read_bounded_fd,
    _require_bounded_state,
    _require_regular_owned,
)


StateCreationEvidence = tuple[int, int, int]


def read_state_with_evidence(
    paths: ServerPaths,
) -> tuple[OwnedServer | None, StateCreationEvidence | None]:
    """Read state and bind it to the same descriptor's creation evidence."""
    info = _lstat(paths.state_path)
    if info is None:
        return None, None
    _require_regular_owned(info, paths.state_path, "state")
    try:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise StateFileError("safe state parsing requires O_NOFOLLOW support")
        fd = os.open(paths.state_path, os.O_RDONLY | os.O_NONBLOCK | no_follow)
        try:
            initial_info = os.fstat(fd)
            _require_regular_owned(initial_info, paths.state_path, "state")
            evidence = _creation_evidence(initial_info)
            data = _read_bounded_fd(fd, STATE_MAX_BYTES)
            if _file_generation(os.fstat(fd)) != _file_generation(initial_info):
                raise StateFileError(
                    "codex-server ownership state changed while being read"
                )
        finally:
            os.close(fd)
        value = json.loads(data.decode("utf-8"))
        _require_bounded_state(value)
    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise StateFileError(f"cannot read {paths.state_path}: {exc}") from exc
    return OwnedServer.from_json(value), evidence


def _creation_evidence(info: os.stat_result) -> StateCreationEvidence | None:
    """Return descriptor identity and creation time when the OS exposes it."""
    birthtime = getattr(info, "st_birthtime", None)
    if not isinstance(birthtime, (float, int)) or birthtime <= 0:
        return None
    return info.st_dev, info.st_ino, int(birthtime * 1_000_000)
