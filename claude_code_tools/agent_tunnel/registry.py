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
          "session_id": "<claude/codex session uuid>",
          "cwd": "<absolute project dir of that session>",
          "config_dir": "<agent config dir: CLAUDE_CONFIG_DIR or CODEX_HOME>",
          "agent": "claude" | "codex",
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
    config_dir: str = ""  # CLAUDE_CONFIG_DIR (claude) / CODEX_HOME (codex)
    # Which agent CLI owns the session: "claude" (Claude Code) or "codex"
    # (OpenAI Codex CLI). Records written before this field default to claude.
    agent: str = "claude"
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
        except (OSError, ValueError):
            # ValueError covers JSONDecodeError AND UnicodeDecodeError (one
            # bad byte in registry.json must not break >share or the daemon).
            return {}
        # A corrupted or hand-edited file may hold null/non-object values at
        # any level — degrade to skipping, never crash the daemon or CLI.
        if not isinstance(data, dict):
            return {}
        out: dict[str, PublishRecord] = {}
        known = {f.name for f in PublishRecord.__dataclass_fields__.values()}
        records_tbl = data.get("records")
        if not isinstance(records_tbl, dict):
            return {}
        for handle, rec in records_tbl.items():
            if not isinstance(rec, dict):
                continue
            fields = {k: v for k, v in rec.items() if k in known}
            try:
                record = PublishRecord(**fields)
            except TypeError:
                continue
            # A record without a usable session id cannot be bound, compared,
            # or displayed (`session_id[:8]`) — drop it at the load boundary.
            if not isinstance(record.session_id, str) or not record.session_id:
                continue
            # Defensive: an old hook could write nulls (or wrong types) for
            # any field (access=null was seen in the wild); normalize so
            # comparisons, sorting, and substring checks never hit None.
            record.handle = (
                record.handle
                if isinstance(record.handle, str) and record.handle
                else str(handle)
            )
            for name in ("cwd", "config_dir", "label", "transcript_path"):
                if not isinstance(getattr(record, name), str):
                    setattr(record, name, "")
            record.access = (
                record.access
                if isinstance(record.access, str) and record.access
                else "read"
            )
            # Strict enum: an unknown agent value dispatches as claude, so
            # it can't be displayed as one agent yet run through another.
            record.agent = (
                record.agent if record.agent in ("claude", "codex")
                else "claude"
            )
            if not isinstance(record.created_at, (int, float)):
                record.created_at = 0.0
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

    def publish(
        self,
        session_id: str,
        cwd: str,
        config_dir: str = "",
        agent: str = "claude",
        access: Optional[str] = None,
        label: str = "",
        transcript_path: str = "",
    ) -> tuple[Optional[str], Optional[str]]:
        """Insert/update a session's record (the out-of-session `>share`).

        Mirrors the hook's semantics: a re-published session keeps its handle
        (unless a new label is given) and preserves access/label/created_at
        unless overridden; a revoked handle may be reclaimed by anyone; a
        LIVE handle owned by a different session is a collision. ``access``
        of None preserves the prior level (defaulting to "read").

        Session identity is ``(agent, session_id)``, not the id alone: a
        claude and a codex session that (however improbably) share an id are
        distinct owners, so one can never relabel, inherit from, or overwrite
        the other's live handle.

        Returns:
            (handle, None) on success, (None, handle) on collision.
        """
        agent = agent or "claude"

        def _same_session(rec: PublishRecord) -> bool:
            return rec.session_id == session_id and rec.agent == agent

        with file_lock(self.path):
            records = self._read()
            existing = next(
                (
                    h
                    for h, r in records.items()
                    if _same_session(r) and not r.revoked
                ),
                None,
            )
            prior = records.get(existing) if existing else None
            if label:
                handle = label
                taken = records.get(handle)
                if (
                    taken is not None
                    and not taken.revoked
                    and not _same_session(taken)
                ):
                    return None, handle
                if existing and existing != handle:
                    records.pop(existing, None)
            elif existing:
                handle = existing
            else:
                handle = derive_handle(session_id)
                # Only a LIVE handle of a DIFFERENT session forces a suffix —
                # a revoked one, or one this same (agent, session) owns, is
                # reusable (same rule as the label path and Registry.rename).
                while (
                    handle in records
                    and not records[handle].revoked
                    and not _same_session(records[handle])
                ):
                    handle += "x"
            if prior is None:
                # Inherit only from a record this session already owns; a
                # revoked handle reclaimed from ANOTHER session is a fresh
                # publish and must not inherit its access/label/created_at.
                under = records.get(handle)
                if under is not None and _same_session(under):
                    prior = under
            records[handle] = PublishRecord(
                handle=handle,
                session_id=session_id,
                cwd=cwd,
                config_dir=config_dir,
                agent=agent or "claude",
                access=access or (prior.access if prior else "") or "read",
                label=label or (prior.label if prior else ""),
                transcript_path=transcript_path,
                created_at=prior.created_at if prior else time.time(),
                revoked=False,
            )
            self._write(records)
        return handle, None

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
            # Identity is (agent, session_id): a live handle owned by a
            # different (agent, session) blocks the rename — even if it
            # shares this record's session id under the other agent.
            if (
                taken is not None
                and not taken.revoked
                and (taken.session_id, taken.agent or "claude")
                != (rec.session_id, rec.agent or "claude")
            ):
                return (
                    False,
                    f"Handle {new!r} is already used by another session.",
                )
            records.pop(old, None)
            rec.handle = new
            # `>share <label>` stores label == handle; keep them in sync so
            # `published`/`!list` and new threads show the new name, not the
            # old one. A distinct (custom) label is left untouched.
            if rec.label == old:
                rec.label = new
            records[new] = rec
            self._write(records)
        return (True, f"Renamed {old!r} to {new!r}.")
