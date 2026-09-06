"""Stage portable Claude conversation artifacts without copying machine state."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from claude_code_tools.session_utils import encode_claude_project_path


def _regular_files(root: Path) -> list[Path]:
    """List regular files, refusing symlinks rather than following them."""
    if root.is_symlink():
        raise ValueError(f"Cannot transfer symlink: {root}")
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise ValueError(f"Unsupported artifact: {root}")
    result: list[Path] = []
    for child in sorted(root.iterdir()):
        result.extend(_regular_files(child))
    return result


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Read complete JSONL records, rejecting corrupt or partial transcripts."""
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"Invalid JSON in {path}, line {number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Expected object in {path}, line {number}")
        records.append(record)
    return records


def _remap(path: str, source: str, destination: str) -> str:
    """Map one absolute project path using a directory boundary."""
    if path == source:
        return destination
    if path.startswith(source.rstrip("/") + "/"):
        return destination.rstrip("/") + path[len(source.rstrip("/")):]
    return path


def _map_record(
    record: dict[str, Any], source: str, destination: str
) -> dict[str, Any]:
    """Rewrite operational metadata, never historical message/tool prose."""
    if isinstance(record.get("cwd"), str):
        record["cwd"] = _remap(record["cwd"], source, destination)
    if record.get("type") == "file-history-snapshot":
        snapshot = record.get("snapshot", {})
        if isinstance(snapshot, dict):
            backups = snapshot.get("trackedFileBackups")
            if isinstance(backups, dict):
                snapshot["trackedFileBackups"] = {
                    _remap(path, source, destination): value
                    for path, value in backups.items()
                }
    return record


def export_session(
    source_home: Path,
    session_id: str,
    destination_home: Path,
    destination_project: Path,
    staging: Path,
) -> dict[str, Any]:
    """Stage one conversation and its supported persistent companion files.

    Args:
        source_home: Explicit Claude account home.
        session_id: Full conversation UUID.
        destination_home: Absolute remote account home (not accessed locally).
        destination_project: Absolute project directory on the destination.
        staging: Empty local bundle directory; payload is written under files/.

    Returns:
        A manifest fragment listing destination-home-relative files and warnings.

    Raises:
        ValueError: Session is ambiguous, malformed, or uses unsupported artifacts.
    """
    if str(UUID(session_id)) != session_id:
        raise ValueError("Expected canonical full session UUID")
    if not destination_home.is_absolute() or not destination_project.is_absolute():
        raise ValueError("Destination home and project must be absolute")
    projects = source_home / "projects"
    if projects.is_symlink():
        raise ValueError(f"Cannot transfer symlink: {projects}")
    matches = list(projects.glob(f"*/{session_id}.jsonl"))
    if len(matches) != 1:
        raise ValueError(f"Expected one Claude transcript; found {len(matches)}")
    transcript = matches[0]
    if transcript.parent.is_symlink():
        raise ValueError(f"Cannot transfer symlink: {transcript.parent}")
    _regular_files(transcript)
    records = _read_records(transcript)
    cwds = {
        record["cwd"] for record in records
        if isinstance(record.get("cwd"), str) and record["cwd"].startswith("/")
    }
    if len(cwds) != 1:
        raise ValueError(
            "Claude transcript must identify exactly one project cwd; "
            f"found {len(cwds)}. Map multiple working directories manually."
        )
    source_project = next(iter(cwds))
    if source_project == "/":
        raise ValueError("Transferring a filesystem-root project is unsupported")
    destination = str(destination_project)
    project_relative = Path("projects") / encode_claude_project_path(destination)
    files: list[str] = []
    shared_files: list[str] = []
    warnings = [
        "Historical message/tool text retains original paths; inspect referenced "
        "external files, plans and attachments before resuming.",
        "Credentials, settings, plugins, shell environment, live processes, "
        "scheduled jobs and workflow/team runtime are not transferred.",
        "Submitted-input history and project-wide session indexes are not merged; "
        "resume using the printed session UUID.",
    ]

    def stage(source: Path, relative: Path, rewrite: bool = False) -> None:
        """Copy a verified file into a fresh private staging directory."""
        target = staging / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ValueError(f"Staging collision: {relative}")
        if rewrite:
            transformed = [
                _map_record(record, source_project, destination)
                for record in _read_records(source)
            ]
            target.write_text("".join(json.dumps(r) + "\n" for r in transformed))
        else:
            shutil.copyfile(source, target)
        target.chmod(0o600)
        files.append(relative.as_posix())

    stage(transcript, project_relative / transcript.name, rewrite=True)
    sidecar = transcript.with_suffix("")
    for source in _regular_files(sidecar):
        relative = source.relative_to(sidecar)
        if relative.parts[0] not in {"subagents", "tool-results"}:
            raise ValueError(f"Unsupported Claude session sidecar: {relative}")
        stage(
            source, project_relative / session_id / relative,
            rewrite=source.suffix == ".jsonl",
        )
    for category in ("file-history", "tasks"):
        root = source_home / category
        if root.is_symlink():
            raise ValueError(f"Cannot transfer symlink: {root}")
        artifact = root / session_id
        for source in _regular_files(artifact):
            if source.name == ".lock":
                continue
            stage(source, source.relative_to(source_home))
    todos = source_home / "todos"
    if todos.is_symlink():
        raise ValueError(f"Cannot transfer symlink: {todos}")
    for todo in sorted(todos.glob(f"{session_id}-agent-*.json")):
        _regular_files(todo)
        stage(todo, todo.relative_to(source_home))
    memory = transcript.parent / "memory"
    for source in _regular_files(memory):
        relative = project_relative / "memory" / source.relative_to(memory)
        stage(source, relative)
        shared_files.append(relative.as_posix())
    if shared_files:
        warnings.append(
            "Project memory is shared across conversations; destination files "
            "must be absent or byte-identical. Memory prose retains source paths."
        )
    for category in ("tasks", "teams", "workflows", "session-env"):
        root = source_home / category
        if (root / session_id).exists() or (
            root / f"session-{session_id[:8]}"
        ).exists():
            if category != "tasks" or (root / f"session-{session_id[:8]}").exists():
                warnings.append(
                    f"Session-linked {category} runtime exists and is excluded; "
                    "recreate any needed background work explicitly."
                )
    return {
        "ok": True,
        "files": sorted(files),
        "shared_files": sorted(shared_files),
        "source_project": source_project,
        "warnings": warnings,
    }
