"""Cross-process advisory file locking for the shared JSON state.

The ``serve`` daemon and one-off CLI commands (``forget``, ``rename``, ``ask``)
are separate processes that read-modify-write the same ``state.json`` /
``registry.json``. Atomic writes prevent a *corrupt* file, but not a *lost
update*: a stale in-memory snapshot can clobber the other process's change.
Holding an exclusive ``flock`` on ``<path>.lock`` around each read→modify→write
serializes the writers so the second one re-reads the first one's result.

The registry lock file (``registry.json.lock``) is the same one the standalone
``>share`` hook already uses, so they coordinate too.

Best-effort: on a platform without ``fcntl`` (or if the lock file can't be
opened) it degrades to a no-op rather than blocking the operation.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix; locking becomes a no-op
    fcntl = None  # type: ignore[assignment]


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive cross-process lock on ``<path>.lock`` for the block."""
    if fcntl is None:
        yield
        return
    lock_path = Path(f"{path}.lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "w")
        fcntl.flock(handle, fcntl.LOCK_EX)
    except OSError:
        if handle is not None:
            handle.close()
        handle = None
    try:
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()
