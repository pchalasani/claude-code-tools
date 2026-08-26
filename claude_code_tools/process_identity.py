"""Cross-platform process-start identities resistant to PID reuse."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def process_start_identity(
    pid: int,
    *,
    platform: str | None = None,
    proc_root: Path = Path("/proc"),
) -> str | None:
    """Return a stable PID/start pair, or ``None`` when it cannot be proven."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    system = (platform or sys.platform).lower()
    if system.startswith("linux"):
        return _linux_identity(pid, proc_root)
    if system == "darwin":
        return _macos_identity(pid)
    return None


def _linux_identity(pid: int, proc_root: Path) -> str | None:
    try:
        text = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    _prefix, separator, suffix = text.rpartition(")")
    if not separator:
        return None
    fields = suffix.strip().split()
    # suffix begins at field 3 (state), so field 22 is index 19.
    if len(fields) <= 19:
        return None
    start_time = fields[19]
    if not start_time.isdigit():
        return None
    return f"linux:{pid}:{start_time}"


def _macos_identity(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    started = " ".join(result.stdout.split())
    if not started:
        return None
    return f"macos:{pid}:{started}"
