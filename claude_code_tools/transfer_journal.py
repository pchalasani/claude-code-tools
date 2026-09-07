"""Private transfer backups, durable progress, and conservative shared indexes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any


def atomic_write(path: Path, data: bytes, replace: bool = False) -> None:
    """Publish fully flushed private bytes; default refuses an existing target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".transfer-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary_path, path)
        else:
            os.link(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def owner_alive(pid: int | None) -> bool:
    """Treat inaccessible or reused live PIDs conservatively as active."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def check_active_sessions(ids: list[str]) -> dict[str, Any]:
    """Refuse observable native resume processes; do not claim full detection."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    ids = [sid for sid in ids if sid]
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or int(parts[0]) == os.getpid():
            continue
        command = parts[1]
        if any(sid in command for sid in ids) and any(
            executable in command.split()[:2]
            or any(token.endswith("/" + executable) for token in command.split()[:2])
            for executable in ("claude", "codex")
        ):
            raise ValueError("Selected destination session has a live agent process")
    return {
        "ran": True,
        "ok": True,
        "scope": "Native command lines containing selected IDs; renamed or server-hosted "
        "sessions cannot be proven inactive. Exit destination sessions first.",
    }


class TransferJournal:
    """Retain recoverable evidence without ever rolling back newer user work."""

    def __init__(self, home: Path, manifest: dict[str, Any], recover: bool) -> None:
        self.home = home
        self.lock = home / ".aichat-transfer.lock"
        digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()
        ).hexdigest()
        home.mkdir(parents=True, exist_ok=True)
        self.guard = os.open(
            home / ".aichat-transfer.guard", os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.guard)
            self.guard = None
            raise ValueError("Transfer owner is still alive; import refused")
        try:
            self.lock.mkdir(mode=0o700)
        except FileExistsError:
            if not recover:
                raise ValueError(
                    "Transfer lock exists; use --recover for the same plan"
                )
            record_path = self.lock / "owner.json"
            if not record_path.is_file():
                raise ValueError("Transfer lock has no journal; inspect it manually")
            previous = json.loads(record_path.read_text())
            if owner_alive(previous.get("pid")):
                raise ValueError("Transfer owner is still alive; recovery refused")
            if previous["digest"] != digest:
                raise ValueError("Recovery requires the identical transfer plan")
            self.directory = Path(previous["directory"])
            self.record = previous
            self.record["pid"] = os.getpid()
            self.save("recovering")
            return
        self.directory = home / ".aichat-transfer-backups" / uuid.uuid4().hex
        self.directory.mkdir(parents=True, mode=0o700)
        self.record = {
            "pid": os.getpid(),
            "digest": digest,
            "directory": str(self.directory),
            "phase": "created",
            "backups_complete": False,
        }
        atomic_write(self.directory / "manifest.json", json.dumps(manifest).encode())
        self.save("created")

    def __del__(self) -> None:
        """Release the kernel guard if initialization fails."""
        self.release()

    def release(self) -> None:
        """Close the per-account kernel lock after finishing or failing."""
        if getattr(self, "guard", None) is not None:
            os.close(self.guard)
            self.guard = None

    def save(self, phase: str) -> None:
        """Persist intent both in the lock and the retained private evidence."""
        self.record["phase"] = phase
        data = json.dumps(self.record).encode()
        atomic_write(self.directory / "journal.json", data, replace=True)
        atomic_write(self.lock / "owner.json", data, replace=True)

    def backup(self, manifest: dict[str, Any]) -> None:
        """Snapshot databases and index files before the first destination write."""
        if self.record["backups_complete"]:
            return
        backup = self.directory / "before"
        backup.mkdir(exist_ok=True, mode=0o700)
        for database in manifest.get("databases", []):
            path = self.home / database["name"]
            target = backup / database["name"]
            target.unlink(missing_ok=True)
            with (
                closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as src,
                closing(sqlite3.connect(target)) as dest,
            ):
                src.backup(dest)
                if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Database backup verification failed")
            target.chmod(0o600)
        for update in manifest.get("metadata_updates", []):
            path = self.home / update["path"]
            if path.exists():
                target = backup / update["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                target.chmod(0o600)
        self.record["backups_complete"] = True
        self.save("backed_up")

    def failed(self) -> None:
        """Permit explicit recovery after an ordinary failure in a live harness."""
        self.record["pid"] = None
        self.save("interrupted")
        self.release()

    def complete(self) -> None:
        """Retain backups and release only this owned import lock."""
        self.record["pid"] = None
        self.save("complete")
        (self.lock / "owner.json").unlink()
        self.lock.rmdir()
        self.release()


def metadata_content(path: Path, update: dict[str, Any]) -> bytes:
    """Merge selected keys only, refusing differing destination history."""
    raw = path.read_bytes() if path.exists() else b""
    container = update.get("container")
    if update["format"] == "jsonl":
        rows = [json.loads(line) for line in raw.splitlines() if line]
        document = None
    elif update["format"] == "json" and container == "records":
        document = json.loads(raw) if raw else {"records": []}
        rows = document["records"]
    else:
        raise ValueError("Unsupported metadata update format")
    key = update["key"]
    by_key = {row[key]: row for row in rows if key in row}
    for row in update["rows"]:
        identity = row[key]
        if identity in by_key:
            if by_key[identity] != row:
                raise ValueError(f"Destination metadata differs: {path.name}")
        else:
            rows.append(row)
            by_key[identity] = row
    if document is None:
        return b"".join((json.dumps(row) + "\n").encode() for row in rows)
    return json.dumps(document).encode()
