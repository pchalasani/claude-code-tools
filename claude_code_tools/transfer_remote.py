"""Standard-library destination checks and non-overwriting session import."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


def project_state(project: Path) -> dict[str, Any]:
    """Read a Git worktree's revision and cleanliness without changing it."""

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(project), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    if not project.is_dir():
        raise ValueError(f"Project does not exist: {project}")
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    return {
        "path": str(project.resolve()),
        "root": str(root),
        "relative": str(project.resolve().relative_to(root)),
        "head": git("rev-parse", "HEAD"),
        "changes": git("status", "--porcelain", "--untracked-files=all"),
    }


def capture_operational_projects(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Record Git evidence for actual rewritten cwd paths, not artifact mappings."""
    return [
        {
            "source": project_state(Path(pair["source"])),
            "destination": pair["destination"],
        }
        for pair in manifest.get("operational_cwds", [])
    ]


def validate_operational_projects(manifest: dict[str, Any]) -> None:
    """Require every operational worktree to match before publishing session data."""
    for pair in manifest.get("operational_projects", []):
        source = pair["source"]
        destination = project_state(Path(pair["destination"]))
        if source["changes"] or destination["changes"]:
            raise ValueError(
                f"Operational worktree must be clean: {source['path']} -> {destination['path']}"
            )
        if (source["head"], source["relative"]) != (
            destination["head"],
            destination["relative"],
        ):
            raise ValueError(
                f"Operational worktree revision/subdirectory differs: "
                f"{source['path']} -> {destination['path']}"
            )


def safe_target(home: Path, relative: str) -> Path:
    """Reject traversal and links inside the explicitly selected account home."""
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Unsafe artifact path: {relative}")
    target = home.joinpath(*path.parts)
    for item in (target, *target.parents):
        if item == home:
            break
        if item.is_symlink():
            raise ValueError(f"Destination artifact uses a symlink: {item}")
    return target


def validate_files(home: Path, manifest: dict[str, Any]) -> list[str]:
    """Identify exact existing copies and reject conflicting destination files."""
    identical = []
    if manifest.get("agent") == "claude":
        filename = f"{manifest['session_id']}.jsonl"
        planned = {
            home / relative
            for relative in manifest["artifacts"]
            if PurePosixPath(relative).name == filename
        }
        for existing in (home / "projects").glob(f"*/{filename}"):
            if existing not in planned:
                raise ValueError(
                    f"Claude session ID already exists in another project: {existing}. "
                    "Use its existing project or a separate destination account home."
                )
    for relative, metadata in manifest["artifacts"].items():
        target = safe_target(home, relative)
        if target.exists():
            if not target.is_file():
                raise ValueError(f"Destination is not a regular file: {target}")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != metadata["sha256"]:
                raise ValueError(
                    f"Destination artifact differs: {target}. "
                    "Use a separate destination account home or reconcile it first."
                )
            identical.append(relative)
    return identical


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Probe, validate, or import an explicitly scoped session bundle."""
    if request["operation"] == "export":
        from claude_code_tools.transfer_source import export_request

        return export_request(request)
    home = Path(request["destination_home"]).expanduser().resolve()
    project = Path(request["destination_project"]).expanduser().resolve()
    state = project_state(project)
    result = {"ran": True, "ok": True, "destination_home": str(home), "project": state}
    if request["operation"] == "probe":
        if request.get("agent"):
            from claude_code_tools.transfer_environment import inspect_environment

            result["environment"] = inspect_environment(request["agent"], home)
        return result
    manifest = request["manifest"]
    expected = manifest["project_state"]
    if state["changes"] or expected["changes"]:
        raise ValueError(
            "Both worktrees must be clean, including untracked files. Commit and "
            "sync intended changes first; ignored files are a separate prerequisite."
        )
    if (state["head"], state["relative"]) != (expected["head"], expected["relative"]):
        raise ValueError(
            "Destination revision/project subdirectory differs. Fetch and check "
            f"out {expected['head']} in the destination worktree first."
        )
    validate_operational_projects(manifest)
    from claude_code_tools.transfer_journal import (
        TransferJournal,
        append_jsonl_rows,
        atomic_write,
        check_active_sessions,
        metadata_content,
        missing_jsonl_rows,
        publish_json_metadata,
    )

    activity = check_active_sessions(
        manifest.get("session_ids", [manifest.get("session_id", "")])
    )
    identical = validate_files(home, manifest)
    if manifest["agent"] == "codex":
        from claude_code_tools.transfer_codex import validate_databases

        validate_databases(manifest, home)
    metadata_paths = []
    for update in manifest.get("metadata_updates", []):
        if update["path"] not in (
            "session_index.jsonl",
            "external_agent_session_imports.json",
        ):
            raise ValueError("Unsupported shared metadata path")
        path = safe_target(home, update["path"])
        metadata_content(path, update)
        metadata_paths.append((path, update))
    result.update({"identical_files": identical, "activity_check": activity})
    if request["operation"] == "validate":
        return result
    if request["operation"] != "import":
        raise ValueError("Unknown transfer operation")
    # Validate the entire bundle before publishing any destination artifact.
    decoded = {}
    for relative, metadata in manifest["artifacts"].items():
        data = base64.b64decode(request["data"][relative], validate=True)
        if (
            len(data) != metadata["size"]
            or hashlib.sha256(data).hexdigest() != metadata["sha256"]
        ):
            raise ValueError(f"Bundle integrity check failed: {relative}")
        decoded[relative] = data
    journal = TransferJournal(home, manifest, bool(request.get("recover")))
    created = 0
    try:
        check_active_sessions(
            manifest.get("session_ids", [manifest.get("session_id", "")])
        )
        validate_files(home, manifest)
        validate_operational_projects(manifest)
        journal.backup(manifest)
        journal.save("publishing_files")
        for relative, data in decoded.items():
            target = safe_target(home, relative)
            if not target.exists():
                atomic_write(target, data)
                created += 1
        validate_files(home, manifest)
        if manifest["agent"] == "codex":
            from claude_code_tools.transfer_codex import import_databases

            import_databases(
                manifest,
                home,
                before_commit=lambda: journal.save("database_commit_started"),
            )
        journal.save("updating_metadata")
        for path, update in metadata_paths:
            if update["format"] == "jsonl":
                pending = missing_jsonl_rows(path, update)
                append_jsonl_rows(path, update, pending)
                continue
            publish_json_metadata(path, update)
        validate_files(home, manifest)
        result.update(
            {
                "imported_files": created,
                "verified_files": len(manifest["artifacts"]),
                "verified_metadata": len(metadata_paths),
                "backup_directory": str(journal.directory),
            }
        )
        journal.complete()
        return result
    except BaseException as error:
        journal.failed()
        raise ValueError(
            f"Transfer interrupted; database commit outcome may be uncertain; "
            f"verified or partial state and backups retained at "
            f"{journal.directory}. Use --recover with the identical plan; "
            "never delete newer destination work."
        ) from error


def main() -> None:
    """Serve one JSON request over an SSH pipe, with an explicit success flag."""
    import sys

    try:
        response = handle_request(json.load(sys.stdin))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1) from error
    print(json.dumps(response))
