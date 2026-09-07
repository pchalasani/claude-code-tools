"""Stage portable Claude conversation artifacts without copying machine state."""

from __future__ import annotations

import json
import posixpath
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from claude_code_tools.session_utils import encode_claude_project_path
from claude_code_tools.transfer_claude_artifacts import discover_scratch


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


def _fingerprint(paths: list[Path]) -> dict[str, tuple[int, int, int, int]]:
    """Capture source identity and write timestamps for a stable export."""
    result: dict[str, tuple[int, int, int, int]] = {}
    for root in paths:
        for path in _regular_files(root):
            stat = path.stat()
            result[str(path)] = (
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
    return result


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Read complete JSONL records, rejecting corrupt or partial transcripts."""
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text().split("\n"), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"Invalid JSON in {path}, line {number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Expected object in {path}, line {number}")  # noqa: TRY004
        records.append(record)
    return records


def _remap(
    path: str,
    source: str,
    destination: str,
    *,
    relative_ok: bool = False,
    label: str = "file-history path",
) -> str:
    """Map a normalized project path, preserving valid relative backup keys."""
    original = Path(path)
    root = Path(posixpath.normpath(source))
    candidate = root / original if relative_ok else original
    try:
        relative = Path(posixpath.normpath(str(candidate))).relative_to(root)
    except ValueError as error:
        raise ValueError(f"Claude {label} needs an explicit mapping: {path}") from error
    if relative_ok and not original.is_absolute():
        return str(relative)
    return str(Path(destination) / relative)


def _map_record(
    record: dict[str, Any],
    source: str,
    destination: str,
    path_mappings: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Rewrite operational metadata, never historical message/tool prose."""
    mappings = {source: destination, **(path_mappings or {})}

    def map_path(path: str, **kwargs: Any) -> str:
        for old in sorted(mappings, key=len, reverse=True):
            normalized = posixpath.normpath(path)
            if normalized == old or normalized.startswith(old.rstrip("/") + "/"):
                return _remap(path, old, mappings[old], **kwargs)
        return _remap(path, source, destination, **kwargs)

    if isinstance(record.get("cwd"), str):
        record["cwd"] = map_path(record["cwd"], label="artifact cwd")
    if record.get("type") == "file-history-snapshot":
        snapshot = record.get("snapshot", {})
        if isinstance(snapshot, dict):
            backups = snapshot.get("trackedFileBackups")
            if isinstance(backups, dict):
                mapped: dict[str, Any] = {}
                for path, value in backups.items():
                    key = map_path(path, relative_ok=True)
                    if key in mapped:
                        raise ValueError(f"Normalized file-history collision: {path}")
                    if isinstance(value, dict):
                        parent = value.get("realParentDir")
                        if isinstance(parent, str):
                            value["realParentDir"] = map_path(
                                parent,
                                label="file-history realParentDir",
                            )
                        backup_name = value.get("backupFileName")
                        if isinstance(backup_name, str):
                            normalized = Path(posixpath.normpath(backup_name))
                            if normalized.is_absolute() or ".." in normalized.parts:
                                raise ValueError(
                                    "File-history backupFileName leaves its session "
                                    f"backup directory: {backup_name}"
                                )
                            value["backupFileName"] = str(normalized)
                    mapped[key] = value
                snapshot["trackedFileBackups"] = mapped
    return record


def export_session(
    source_home: Path,
    session_id: str,
    destination_home: Path,
    destination_project: Path,
    staging: Path,
    path_mappings: dict[str, str] | list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Stage one conversation and its supported persistent companion files.

    Args:
        source_home: Explicit Claude account home.
        session_id: Full conversation UUID.
        destination_home: Absolute remote account home (not accessed locally).
        destination_project: Absolute project directory on the destination.
        staging: Empty local bundle directory; payload is written under files/.
        path_mappings: Additional absolute source-to-destination project mappings.

    Returns:
        A manifest fragment listing destination-home-relative files and warnings.

    Raises:
        ValueError: Session is ambiguous, malformed, or uses unsupported artifacts.
    """
    if isinstance(path_mappings, list):
        path_mappings = {item["source"]: item["destination"] for item in path_mappings}
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
    transcript_before = _fingerprint([transcript])
    records = _read_records(transcript)
    cwds = {
        record["cwd"]
        for record in records
        if isinstance(record.get("cwd"), str) and record["cwd"].startswith("/")
    }
    if len(cwds) != 1 and not path_mappings:
        raise ValueError(
            "Claude transcript must identify exactly one project cwd; "
            f"found {len(cwds)}. Map multiple working directories manually."
        )
    plans = sorted(
        {
            source_home / "plans" / f"{record['slug']}.md"
            for record in records
            if isinstance(record.get("slug"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]+", record["slug"])
        }
    )
    roots = [
        transcript,
        transcript.with_suffix(""),
        transcript.parent / "memory",
        source_home / "file-history" / session_id,
        source_home / "tasks" / session_id,
        *plans,
        source_home / "transfer-support" / session_id,
    ]

    def inventory() -> dict[str, tuple[int, int, int, int]]:
        """Include newly added companions and detect duplicate transcripts."""
        if list(projects.glob(f"*/{session_id}.jsonl")) != matches:
            raise ValueError("Source changed during export: transcript discovery")
        todos = sorted((source_home / "todos").glob(f"{session_id}-agent-*.json"))
        return _fingerprint([*roots, *todos])

    before = inventory()
    if any(before.get(path) != value for path, value in transcript_before.items()):
        raise ValueError("Source changed during export: transcript metadata")
    if not cwds:
        raise ValueError("Claude transcript has no absolute cwd")
    source_project = next(
        (
            cwd
            for cwd in sorted(cwds)
            if encode_claude_project_path(cwd) == transcript.parent.name
        ),
        min(cwds),
    )
    if source_project == "/":
        raise ValueError("Transferring a filesystem-root project is unsupported")
    destination = str(destination_project)
    project_relative = Path("projects") / encode_claude_project_path(destination)
    files: list[str] = []
    shared_files: list[str] = []
    warnings = [
        (
            "Historical message/tool text retains original paths; inspect referenced "
            "external files, plans and attachments before resuming."
        ),
        (
            "Credentials, settings, plugins, shell environment, live processes, "
            "scheduled jobs and workflow/team runtime are not transferred."
        ),
        (
            "Submitted-input history and project-wide session indexes are not merged; "
            "resume using the printed session UUID."
        ),
    ]

    def stage(source: Path, relative: Path, rewrite: bool = False) -> None:
        """Copy a verified file into a fresh private staging directory."""
        target = staging / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ValueError(f"Staging collision: {relative}")
        if rewrite:
            transformed = [
                _map_record(record, source_project, destination, path_mappings)
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
            source,
            project_relative / session_id / relative,
            rewrite=relative.parts[0] == "subagents" and source.suffix == ".jsonl",
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
    plans_root = source_home / "plans"
    if plans and plans_root.is_symlink():
        raise ValueError(f"Cannot transfer symlink: {plans_root}")
    missing_at_source: list[dict[str, str]] = []
    for plan in plans:
        if plan.exists():
            stage(plan, plan.relative_to(source_home))
            shared_files.append(plan.relative_to(source_home).as_posix())
        else:
            missing_at_source.append({"path": str(plan), "reason": "missing_at_source"})
    memory = transcript.parent / "memory"
    for source in _regular_files(memory):
        relative = project_relative / "memory" / source.relative_to(memory)
        stage(source, relative)
        shared_files.append(relative.as_posix())
    if shared_files:
        warnings.append(
            "Project memory and plan files may be shared; destination files "
            "must be absent or byte-identical. Memory prose retains source paths."
        )
    for category in ("tasks", "teams", "workflows", "session-env"):
        root = source_home / category
        short_runtime = (root / f"session-{session_id[:8]}").exists()
        if short_runtime or (category != "tasks" and (root / session_id).exists()):
            warnings.append(
                f"Session-linked {category} runtime exists and is excluded; "
                "recreate any needed background work explicitly."
            )
    mappings = {
        str(source_home): str(destination_home),
        source_project: destination,
        **(path_mappings or {}),
    }
    support = source_home / "transfer-support" / session_id
    old_guide = support / "path-map.json"
    available_aliases: set[str] = set()
    if old_guide.is_file():
        try:
            previous = json.loads(old_guide.read_text())
            for old, current in previous.get("path_mappings", {}).items():
                if not isinstance(old, str) or not isinstance(current, str):
                    raise ValueError("Invalid prior path mapping")  # noqa: TRY004
                if Path(current).is_file():
                    available_aliases.add(old)
                for source_prefix in sorted(mappings, key=len, reverse=True):
                    if current == source_prefix or current.startswith(
                        source_prefix + "/"
                    ):
                        mappings[old] = (
                            mappings[source_prefix] + current[len(source_prefix) :]
                        )
                        break
            missing_at_source.extend(previous.get("missing_at_source", []))
        except (OSError, ValueError, AttributeError) as error:
            raise ValueError("Cannot read prior transfer path guide") from error
    for source in _regular_files(support):
        if source == old_guide:
            continue
        relative = source.relative_to(source_home)
        stage(source, relative)
        mappings[str(source)] = str(destination_home / relative)
    scratch, gaps = discover_scratch(records, session_id, source_project)
    missing_at_source.extend(
        gap for gap in gaps if gap["path"] not in available_aliases
    )
    for source, relative in scratch:
        old = source.stat()
        stage(source, relative)
        new = source.stat()
        if (old.st_size, old.st_mtime_ns) != (new.st_size, new.st_mtime_ns):
            raise ValueError("Source scratch file changed during export")
        mappings[str(source)] = str(destination_home / relative)
        if str(source).startswith("/private/tmp/") and Path("/tmp").resolve() == Path(
            "/private/tmp"
        ):
            mappings[str(source)[len("/private") :]] = str(destination_home / relative)
    for record in records:
        snapshot = record.get("snapshot")
        backups = (
            snapshot.get("trackedFileBackups", {}) if isinstance(snapshot, dict) else {}
        )
        if not isinstance(backups, dict):
            continue
        for value in backups.values():
            name = value.get("backupFileName") if isinstance(value, dict) else None
            if isinstance(name, str):
                backup = source_home / "file-history" / session_id / name
                if not backup.is_file():
                    missing_at_source.append(
                        {"path": str(backup), "reason": "missing_at_source"}
                    )
    if scratch:
        warnings.append(
            "Scratch symlinks are materialized as regular files; "
            "consult path mappings for historical references."
        )
    if inventory() != before:
        raise ValueError("Source changed during export; stop the session and retry")
    return {
        "ok": True,
        "files": sorted(files),
        "shared_files": sorted(shared_files),
        "source_project": source_project,
        "warnings": warnings,
        "missing_at_source": missing_at_source,
        "path_mappings": mappings,
    }
