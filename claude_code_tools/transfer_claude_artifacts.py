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
    additional_projects: list[str] | None = None,
) -> tuple[list[tuple[Path, Path]], list[dict[str, str]]]:
    """Inventory referenced temporary files and session-owned scratch directories.

    Only Claude temporary buckets explicitly mentioned in the transcript qualify.
    Arbitrary absolute paths in conversation text are not permission to copy files.
    Symlinks are materialized only for subagent logs inside selected session roots.
    """
    text = json.dumps(records, ensure_ascii=False)
    references = set(re.findall(r"/(?:private/)?tmp/claude-[^\s\"'<>\\]+", text))
    allowed_roots = [sidecar_root.resolve()] if sidecar_root else []
    source_projects = set(additional_projects or [])
    if source_project:
        source_projects.add(source_project)
    for source_path in sorted(source_projects):
        from claude_code_tools.session_utils import encode_claude_project_path

        project = encode_claude_project_path(source_path)
        # Native Unix Claude scratch convention. Probe exact session paths only;
        # never inventory another session or recursively search temporary roots.
        for base in (Path(tempfile.gettempdir()), Path("/tmp"), Path("/private/tmp")):
            root = base / f"claude-{os.getuid()}" / project / session_id
            # Trust the temp base itself (including macOS /tmp), but never a
            # redirected UID bucket, encoded project, or selected session root.
            if any(
                component.is_symlink()
                for component in (root, root.parent, root.parent.parent)
            ):
                raise ValueError("Native Claude scratch root uses a symlinked ancestor")
            if root.is_dir():
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
            if (
                parent.name == session_id
                and parent.is_dir()
                and parent.resolve() in allowed_roots
            ):
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
            elif not any(
                candidate.resolve().is_relative_to(root) for root in allowed_roots
            ):
                missing.append(
                    {"path": str(candidate), "reason": "outside_selected_session"}
                )
                continue
            # A hash separates equal basenames in different source directories.
            key = hashlib.sha256(str(candidate).encode()).hexdigest()[:16]
            files[candidate] = (
                Path("transfer-support") / session_id / "scratch" / key / candidate.name
            )
    return sorted(files.items()), missing
