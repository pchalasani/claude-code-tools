"""Shared handle registry: maps a colleague-facing handle to a session.

The `>share` UserPromptSubmit hook (a standalone stdlib script under
``plugins/agent-tunnel/hooks/``) WRITES this file; the `serve` daemon READS
it. Because the hook cannot import this package (it runs under whatever Python
Claude Code invokes), the on-disk JSON schema is duplicated there and MUST be
kept in sync with this module.

Schema (``registry.json``)::

    {
      "records": {
        "<handle>": {
          "handle": "<handle>",
          "session_id": "<claude session uuid>",
          "cwd": "<absolute project dir of that session>",
          "config_dir": "<claude config dir the session lives under>",
          "access": "read" | "write" | "bash",
          "label": "<optional friendly label>",
          "transcript_path": "<absolute .jsonl path, best-effort>",
          "created_at": <float epoch seconds>,
          "revoked": <bool>
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .locking import file_lock

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


def sanitize_label(label: str) -> Optional[str]:
    """Normalize a user-supplied label to a valid handle, or None.

    Lowercases, replaces runs of non-alphanumerics with single dashes, and
    trims dashes. Returns None if nothing valid remains.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    slug = slug[:32].rstrip("-")
    return slug if slug and HANDLE_RE.match(slug) else None


def derive_handle(session_id: str) -> str:
    """Default handle for a session: a short slug from its id."""
    compact = session_id.replace("-", "")
    return compact[:6] if compact else "session"


@dataclass
class PublishRecord:
    """A published session reachable by `handle`."""

    handle: str
    session_id: str
    cwd: str
    config_dir: str = ""  # Claude config dir the session lives under
    # "read", "write" (>share --write), or "bash"
    # (>share --dangerously-allow-bash; also enables command execution).
    access: str = "read"
    label: str = ""
    transcript_path: str = ""
    created_at: float = field(default_factory=time.time)
    revoked: bool = False


class Registry:
    """Read/write access to the shared handle registry JSON.

    Reads re-load the file each call so the daemon always sees the latest
    `>share` writes. Writes are atomic (tmp + rename).
    """

    def __init__(self, path: Path) -> None:
        """Bind to a registry file path (not required to exist yet)."""
        self.path = path

    def _read(self) -> dict[str, PublishRecord]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[str, PublishRecord] = {}
        known = {f.name for f in PublishRecord.__dataclass_fields__.values()}
        for handle, rec in data.get("records", {}).items():
            fields = {k: v for k, v in rec.items() if k in known}
            try:
                record = PublishRecord(**fields)
            except TypeError:
                continue
            # Defensive: an old hook could write access=null; treat it as read
            # so it never displays or behaves oddly (null != "write" anyway).
            record.access = record.access or "read"
            # Backfill config_dir for records written before it was tracked:
            # the transcript path is <config-dir>/projects/...
            if not record.config_dir and "/projects/" in record.transcript_path:
                record.config_dir = record.transcript_path.split("/projects/")[0]
            out[handle] = record
        return out

    def _write(self, records: dict[str, PublishRecord]) -> None:
        payload = {"records": {h: asdict(r) for h, r in records.items()}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, handle: str) -> Optional[PublishRecord]:
        """Return the active record for a handle, or None if missing/revoked."""
        rec = self._read().get(handle.strip().lower())
        if rec is None or rec.revoked:
            return None
        return rec

    def active(self) -> list[PublishRecord]:
        """All non-revoked records, newest first."""
        recs = [r for r in self._read().values() if not r.revoked]
        recs.sort(key=lambda r: r.created_at, reverse=True)
        return recs

    def upsert(self, record: PublishRecord) -> None:
        """Insert or replace a record (used by CLI/tests; hook writes its own)."""
        with file_lock(self.path):
            records = self._read()
            records[record.handle] = record
            self._write(records)

    def revoke(self, handle: str) -> bool:
        """Mark a handle revoked. Returns True if it existed."""
        with file_lock(self.path):
            records = self._read()
            rec = records.get(handle.strip().lower())
            if rec is None:
                return False
            rec.revoked = True
            self._write(records)
            return True

    def rename(self, old: str, new: str) -> tuple[bool, str]:
        """Rename handle `old` to `new`.

        Returns (ok, message). Fails if `new` is malformed, `old` is missing,
        or `new` is an active handle of a different session.
        """
        old = old.strip().lower()
        new = new.strip().lower()
        if not HANDLE_RE.match(new):
            return (
                False,
                f"Invalid handle {new!r}: letters, digits, dashes (2-32).",
            )
        if new == old:
            return (False, "New handle is the same as the old one.")
        with file_lock(self.path):
            records = self._read()
            rec = records.get(old)
            # A revoked record is hidden by get()/active(); renaming it would
            # "succeed" yet leave the new handle revoked and invisible. Treat
            # it as missing.
            if rec is None or rec.revoked:
                return (False, f"No handle {old!r} in the registry.")
            taken = records.get(new)
            if (
                taken is not None
                and not taken.revoked
                and taken.session_id != rec.session_id
            ):
                return (
                    False,
                    f"Handle {new!r} is already used by another session.",
                )
            records.pop(old, None)
            rec.handle = new
            records[new] = rec
            self._write(records)
        return (True, f"Renamed {old!r} to {new!r}.")
