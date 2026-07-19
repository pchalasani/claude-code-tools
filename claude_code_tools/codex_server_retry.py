"""Bounded retry policy for transient Codex plugin snapshot races."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from claude_code_tools.codex_server_models import CodexServerError


P = ParamSpec("P")
R = TypeVar("R")

PLUGIN_SNAPSHOT_ATTEMPTS = 4
PLUGIN_SNAPSHOT_BACKOFF_SECONDS = (0.05, 0.15, 0.45)


class PluginSnapshotChangedError(CodexServerError):
    """A transient race while reading or certifying plugin inputs."""


def retry_plugin_snapshot_changes(function: Callable[P, R]) -> Callable[P, R]:
    """Retry one lifecycle operation after transient plugin snapshot changes.

    Args:
        function: Lifecycle function whose failed attempt cleans up before raising.

    Returns:
        A function with the same signature and a bounded retry policy.
    """

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        for attempt in range(PLUGIN_SNAPSHOT_ATTEMPTS):
            try:
                return function(*args, **kwargs)
            except PluginSnapshotChangedError:
                if attempt + 1 >= PLUGIN_SNAPSHOT_ATTEMPTS:
                    raise
                time.sleep(PLUGIN_SNAPSHOT_BACKOFF_SECONDS[attempt])
        raise AssertionError("plugin snapshot retry loop exhausted unexpectedly")

    return wrapped
