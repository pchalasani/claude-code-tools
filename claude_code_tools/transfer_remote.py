"""Standard-library destination checks and non-overwriting session import."""

from __future__ import annotations

import base64
import hashlib
import json
import os
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
    home = Path(request["destination_home"]).expanduser().resolve()
    project = Path(request["destination_project"]).expanduser().resolve()
    state = project_state(project)
    result = {"ok": True, "destination_home": str(home), "project": state}
    if request["operation"] == "probe":
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
    identical = validate_files(home, manifest)
    if manifest["agent"] == "codex":
        from claude_code_tools.transfer_codex import validate_databases

        validate_databases(manifest, home)
    result["identical_files"] = identical
    if request["operation"] == "validate":
        return result
    if request["operation"] != "import":
        raise ValueError("Unknown transfer operation")
    # A second importer must not race file creation/rollback or database commit.
    home.mkdir(parents=True, exist_ok=True)
    lock = home / ".aichat-transfer.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ValueError(
            f"Transfer lock exists: {lock}. Check for another transfer; after a "
            "crash inspect partial artifacts before removing this directory."
        ) from error
    created: list[Path] = []
    try:
        validate_files(home, manifest)
        for relative, metadata in manifest["artifacts"].items():
            data = base64.b64decode(request["data"][relative], validate=True)
            if len(data) != metadata["size"] or (
                hashlib.sha256(data).hexdigest() != metadata["sha256"]
            ):
                raise ValueError(f"Bundle integrity check failed: {relative}")
            target = safe_target(home, relative)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                created.append(target)
                os.chmod(target, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(target.read_bytes()).hexdigest() != metadata["sha256"]:
                raise ValueError(f"Destination verification failed: {target}")
        if manifest["agent"] == "codex":
            from claude_code_tools.transfer_codex import import_databases

            import_databases(manifest, home)
        result["imported_files"] = len(created)
        result["verified_files"] = len(manifest["artifacts"])
        return result
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    finally:
        lock.rmdir()


def main() -> None:
    """Serve one JSON request over an SSH pipe, with an explicit success flag."""
    import sys

    try:
        response = handle_request(json.load(sys.stdin))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1) from error
    print(json.dumps(response))
