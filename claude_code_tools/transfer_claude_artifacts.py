"""Discover narrowly scoped Claude scratch references without scanning user homes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def discover_scratch(
    records: list[dict[str, Any]],
    session_id: str,
    source_project: str | None = None,
    sidecar_root: Path | None = None,
) -> tuple[list[tuple[Path, Path]], list[dict[str, str]]]:
    """Inventory referenced temporary files and session-owned scratch directories.

    Only Claude temporary buckets explicitly mentioned in the transcript qualify.
    Arbitrary absolute paths in conversation text are not permission to copy files.
    Symlinks are materialized only for subagent logs inside selected session roots.
    """
    text = json.dumps(records, ensure_ascii=False)
    references = set(re.findall(r"/(?:private/)?tmp/claude-[^\s\"'<>\\]+", text))
    allowed_roots = [sidecar_root.resolve()] if sidecar_root else []
    if source_project:
        from claude_code_tools.session_utils import encode_claude_project_path

        project = encode_claude_project_path(source_project)
        # Native Unix Claude scratch convention. Probe exact session paths only;
        # never inventory another session or recursively search temporary roots.
        for base in (Path(tempfile.gettempdir()), Path("/tmp"), Path("/private/tmp")):
            root = base / f"claude-{os.getuid()}" / project / session_id
            if root.is_dir() and not root.is_symlink():
                references.add(str(root.resolve()))
                allowed_roots.append(root.resolve())
    files: dict[Path, Path] = {}
    missing: list[dict[str, str]] = []
    for raw in sorted(references):
        path = Path(raw.rstrip(".,;:)"))
        if ".." in path.parts:
            continue
        if not path.exists():
            missing.append({"path": str(path), "reason": "missing_at_source"})
            continue
        candidates = [path] if path.is_file() else []
        # Enumerate only a UUID-owned directory, never the whole project bucket.
        for parent in (path, *path.parents):
            if session_id in parent.name and parent.is_dir():
                candidates.extend(parent.rglob("*"))
                break
        for candidate in candidates:
            if candidate.is_symlink():
                target = candidate.resolve()
                if not (
                    target.is_file()
                    and target.suffix == ".jsonl"
                    and target.parent.name == "subagents"
                    and any(target.is_relative_to(root) for root in allowed_roots)
                ):
                    missing.append(
                        {"path": str(candidate), "reason": "unsupported_symlink_target"}
                    )
                    continue
            elif not candidate.is_file():
                continue
            # A hash separates equal basenames in different source directories.
            key = hashlib.sha256(str(candidate).encode()).hexdigest()[:16]
            files[candidate] = (
                Path("transfer-support") / session_id / "scratch" / key / candidate.name
            )
    return sorted(files.items()), missing
