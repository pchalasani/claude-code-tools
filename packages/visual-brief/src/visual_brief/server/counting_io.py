"""Contained file access for awaiting-answer accounting."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _read_json_object(
    run_dir: Path,
    name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read a contained JSON object and its generation."""
    path = _contained_child(run_dir, name)
    if path is None:
        return None, None
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    generation = hashlib.sha256(encoded).hexdigest()
    return (value, generation) if isinstance(value, dict) else (None, None)


def _contained_child(run_dir: Path, name: str) -> Path | None:
    """Resolve a named run file only when it stays within the run."""
    try:
        root = run_dir.resolve()
        child = (root / name).resolve()
    except (OSError, RuntimeError):
        return None
    if child == root or not child.is_relative_to(root):
        return None
    return child
