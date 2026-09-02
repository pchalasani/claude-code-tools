"""Cache of the last scan, so the picker opens instantly.

The picker shows cached rows immediately and kicks off a fresh scan in the
background, replacing the list when it lands. With hundreds of panes, a cold
scan is noticeable; a warm one is not.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from .model import Agent

_DEFAULT = Path.home() / ".cache" / "cc-tools" / "amux.json"


def cache_path() -> Path:
    """Location of the cache file (``AMUX_CACHE`` overrides)."""
    override = os.environ.get("AMUX_CACHE")
    return Path(override) if override else _DEFAULT


def read(max_age: float | None = None) -> tuple[list[Agent], float]:
    """Load cached agents.

    Args:
        max_age: If set, return an empty list when the cache is older than
            this many seconds.

    Returns:
        ``(agents, age_seconds)``; ``age_seconds`` is ``-1`` when no usable
        cache exists.
    """
    path = cache_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return [], -1.0

    # Syntactically valid JSON can still be the wrong shape (hand-edited file,
    # a truncated write from an older version). Never crash over a cache.
    try:
        stamp = float(raw.get("time", 0))
        age = time.time() - stamp
        # Python's json accepts the NaN literal, and every comparison against
        # nan is False -- so a NaN age passed both the negative check and the
        # max_age check, making the cache permanently "fresh".
        if not math.isfinite(age) or age < 0:
            return [], -1.0
        if max_age is not None and age > max_age:
            return [], age
        parsed = (
            Agent.from_dict(d) for d in raw.get("agents", []) if isinstance(d, dict)
        )
        agents = [a for a in parsed if a is not None]
    except (AttributeError, TypeError, ValueError):
        return [], -1.0
    return agents, age


def write(agents: list[Agent]) -> None:
    """Persist *agents* atomically, creating the cache directory if needed."""
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "time": time.time(),
            "agents": [a.to_dict() for a in agents],
        }
        # Per-process temp name: two concurrent `amux scan` runs sharing one
        # ".tmp" would race, and the loser died on a missing file.
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except OSError:
        pass  # a missing cache only costs speed, never correctness
