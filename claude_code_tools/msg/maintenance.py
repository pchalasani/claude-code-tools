"""Crash-safe maintenance sentinel for msg schema cutovers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import stat
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .migrations import CURRENT_SCHEMA_VERSION, initialize_database

SENTINEL_SCHEMA = "msg.maintenance.v1"


class MaintenanceError(ValueError):
    """An expected maintenance failure safe to expose by stable code."""

    code = "maintenance_error"
    public_message = "maintenance operation failed"


class MaintenanceAlreadyActive(MaintenanceError):
    code = "maintenance_already_active"
    public_message = "maintenance mode is already active"


class MaintenanceTokenMismatch(MaintenanceError):
    code = "maintenance_token_mismatch"
    public_message = "maintenance token does not match"


class MaintenanceSentinelInvalid(MaintenanceError):
    code = "maintenance_sentinel_invalid"
    public_message = "maintenance sentinel is invalid"


class MaintenanceMigrationRequired(MaintenanceError):
    code = "maintenance_migration_required"
    public_message = "migration and post-migration checks must pass before exit"


class MaintenancePostcheckInvalid(MaintenanceError):
    code = "maintenance_postcheck_invalid"
    public_message = "post-migration check evidence is invalid"


def sentinel_path(db_path: str | os.PathLike[str]) -> Path:
    path = Path(db_path)
    return path.with_name(f"{path.name}.maintenance")


def is_active(db_path: str | os.PathLike[str]) -> bool:
    return os.path.lexists(sentinel_path(db_path))


def read_token_fd(fd: int) -> bytes:
    if isinstance(fd, bool) or fd < 0:
        raise ValueError("token fd must be a non-negative integer")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(4097 - total, 4096))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > 4096:
            raise ValueError("maintenance token is too large")
    token = b"".join(chunks)
    if not token:
        raise ValueError("maintenance token is empty")
    return token


def read_postcheck_fd(fd: int) -> dict:
    try:
        raw = read_token_fd(fd)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaintenancePostcheckInvalid() from exc
    if not isinstance(payload, dict):
        raise MaintenancePostcheckInvalid()
    return payload


def _token_hash(token: bytes) -> str:
    return hashlib.sha256(token).hexdigest()


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load(db_path: str | os.PathLike[str]) -> dict:
    path = sentinel_path(db_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise MaintenanceSentinelInvalid()
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise MaintenanceSentinelInvalid()
            chunks: list[bytes] = []
            total = 0
            while total <= 65536:
                chunk = os.read(fd, min(4096, 65537 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            data = b"".join(chunks)
        finally:
            os.close(fd)
        if len(data) > 65536:
            raise MaintenanceSentinelInvalid()
        payload = json.loads(data.decode("utf-8"))
        if payload.get("schema") != SENTINEL_SCHEMA:
            raise MaintenanceSentinelInvalid()
        if not isinstance(payload.get("token_sha256"), str):
            raise MaintenanceSentinelInvalid()
        return payload
    except MaintenanceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        raise MaintenanceSentinelInvalid() from exc


def _write_complete_file(path: Path, payload: dict) -> Path:
    temp = path.with_name(f".{path.name}.tmp.{uuid.uuid4().hex}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return temp


def _replace_payload(path: Path, payload: dict) -> None:
    temp = _write_complete_file(path, payload)
    try:
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.lexists(temp):
            temp.unlink()
            _fsync_directory(path.parent)


def enter(
    db_path: str | os.PathLike[str],
    token: bytes,
    *,
    _failpoint: Callable[[str], None] | None = None,
) -> dict:
    path = sentinel_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SENTINEL_SCHEMA,
        "generation": str(uuid.uuid4()),
        "token_sha256": _token_hash(token),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_schema_version": CURRENT_SCHEMA_VERSION,
    }
    temp = _write_complete_file(path, payload)
    try:
        if _failpoint:
            _failpoint("after_temp_fsync")
        try:
            os.link(temp, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise MaintenanceAlreadyActive() from exc
        if _failpoint:
            _failpoint("after_publish")
        _fsync_directory(path.parent)
        if _failpoint:
            _failpoint("after_publish_fsync")
        temp.unlink()
        _fsync_directory(path.parent)
        if _failpoint:
            _failpoint("after_temp_cleanup")
    finally:
        if os.path.lexists(temp):
            temp.unlink()
    return {key: value for key, value in payload.items() if key != "token_sha256"}


def status(db_path: str | os.PathLike[str]) -> dict:
    if not is_active(db_path):
        return {"active": False}
    payload = _load(db_path)
    return {
        "active": True,
        **{key: value for key, value in payload.items() if key != "token_sha256"},
    }


def _authorize(db_path: str | os.PathLike[str], token: bytes) -> dict:
    payload = _load(db_path)
    if not hmac.compare_digest(payload["token_sha256"], _token_hash(token)):
        raise MaintenanceTokenMismatch()
    return payload


def migrate(
    db_path: str | os.PathLike[str],
    token: bytes,
    *,
    _failpoint: Callable[[str], None] | None = None,
) -> dict:
    payload = _authorize(db_path, token)
    if _failpoint:
        _failpoint("before_schema_migration")
    connection = sqlite3.connect(db_path)
    try:
        before = connection.execute("PRAGMA user_version").fetchone()[0]
        initialize_database(connection)
        after = connection.execute("PRAGMA user_version").fetchone()[0]
        integrity = [
            row[0] for row in connection.execute("PRAGMA integrity_check(1000000)")
        ]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    finally:
        connection.close()
    if integrity != ["ok"] or foreign_keys:
        raise MaintenanceMigrationRequired()
    if _failpoint:
        _failpoint("after_schema_migration")
    payload.update(
        {
            "migration_completed_at": datetime.now(timezone.utc).isoformat(),
            "migrated_schema_version": after,
            "integrity_check": "ok",
            "foreign_key_check": "ok",
        }
    )
    _replace_payload(sentinel_path(db_path), payload)
    if _failpoint:
        _failpoint("after_sentinel_update")
    return {"from_schema_version": before, "to_schema_version": after}


def exit_mode(
    db_path: str | os.PathLike[str],
    token: bytes,
    postcheck: dict,
    *,
    _failpoint: Callable[[str], None] | None = None,
) -> bool:
    payload = _authorize(db_path, token)
    if (
        postcheck.get("schema") != "msg.maintenance.postcheck.v1"
        or postcheck.get("generation") != payload.get("generation")
        or postcheck.get(
            "db_wal_shm_unchanged_after_negative_mutation"
        ) is not True
        or postcheck.get(
            "row_counts_unchanged_after_negative_mutation"
        ) is not True
    ):
        raise MaintenancePostcheckInvalid()
    target = payload.get("target_schema_version")
    if (
        payload.get("migrated_schema_version") != target
        or payload.get("integrity_check") != "ok"
        or payload.get("foreign_key_check") != "ok"
    ):
        raise MaintenanceMigrationRequired()
    connection = sqlite3.connect(db_path)
    try:
        actual = connection.execute("PRAGMA user_version").fetchone()[0]
        integrity = [
            row[0] for row in connection.execute("PRAGMA integrity_check(1000000)")
        ]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    finally:
        connection.close()
    if actual != target or integrity != ["ok"] or foreign_keys:
        raise MaintenanceMigrationRequired()
    path = sentinel_path(db_path)
    if _failpoint:
        _failpoint("before_unlink")
    path.unlink()
    if _failpoint:
        _failpoint("after_unlink")
    _fsync_directory(path.parent)
    if _failpoint:
        _failpoint("after_dir_fsync")
    return True
