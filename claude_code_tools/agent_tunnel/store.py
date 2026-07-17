"""Persistent state for agent-tunnel: thread -> fork-session bindings.

A single JSON file guarded by a process-wide lock (the daemon is the only
writer; backend calls run on worker threads, hence the lock). Writes are
atomic (tmp file + rename).

A thread is *bound* (handle + expert session + project dir recorded) the
moment its Discord thread opens, before any answer exists, so follow-ups
resolve even if the first turn is still running. A bound-but-unanswered
thread has an empty `fork_session_id`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .locking import file_lock


@dataclass
class ThreadRecord:
    """One external conversation thread bound to a forked session."""

    thread_key: str
    handle: str = ""
    expert_session_id: str = ""
    project_dir: str = ""
    config_dir: str = ""
    access: str = "read"
    fork_session_id: str = ""
    backend: str = ""
    # Agent CLI the bound session runs on: "claude" or "codex". Legacy
    # records (pre-field) load as claude via the dataclass default.
    agent: str = "claude"
    asker: str = ""
    tmux_window: str = ""
    # Codex backend only: instructions the fork's context still needs —
    # "intro" (persona + outbox note; set when the file-level fork is
    # created) or "outbox" (after a live access change). Cleared after a
    # successful turn. Durable on the record because the daemon builds a
    # fresh backend per turn and may restart between a failed turn and its
    # retry; in-memory markers would silently drop the persona.
    pending_instructions: str = ""
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


# String fields normalized on load: a null (or non-string) value from a
# hand-edited/corrupted file must never crash slicing, sorting, or path ops.
_STR_FIELDS = (
    "thread_key",
    "handle",
    "expert_session_id",
    "project_dir",
    "config_dir",
    "fork_session_id",
    "backend",
    "asker",
    "tmux_window",
    "pending_instructions",
)


def _normalize_record(record: ThreadRecord) -> None:
    """Coerce every nullable/mistyped persisted field to a usable value."""
    for name in _STR_FIELDS:
        value = getattr(record, name)
        if not isinstance(value, str):
            setattr(record, name, "")
    record.access = (
        record.access if isinstance(record.access, str) and record.access
        else "read"
    )
    # Strict enum: an unknown/garbage agent (e.g. "codex " with a stray
    # space) must dispatch as claude, never be shown as one agent yet run as
    # another. Only the two known values survive.
    record.agent = record.agent if record.agent in ("claude", "codex") else "claude"
    if not isinstance(record.created_at, (int, float)):
        record.created_at = 0.0
    if not isinstance(record.last_used, (int, float)):
        record.last_used = 0.0


class TunnelStore:
    """JSON-backed store of thread records and known fork ids."""

    def __init__(self, path: Path) -> None:
        """Load existing state from `path` (or start empty)."""
        self.path = path
        self._lock = threading.Lock()
        self._records: dict[str, ThreadRecord] = {}
        self._fork_ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # ValueError covers JSONDecodeError AND UnicodeDecodeError (one
            # bad byte in state.json must not wedge the daemon).
            return
        # A hand-edited or corrupted file may hold null/non-object values at
        # any level; never let that stop the daemon from loading state.
        if not isinstance(data, dict):
            return
        known = {f.name for f in ThreadRecord.__dataclass_fields__.values()}
        records_tbl = data.get("records")
        if not isinstance(records_tbl, dict):
            records_tbl = {}
        for key, rec in records_tbl.items():
            if not isinstance(rec, dict):
                continue
            fields = {k: v for k, v in rec.items() if k in known}
            # A record missing thread_key gets it from its map key.
            fields.setdefault("thread_key", str(key))
            try:
                record = ThreadRecord(**fields)
            except TypeError:
                continue
            _normalize_record(record)
            record.thread_key = record.thread_key or str(key)
            # Single normalization point: a legacy record written before the
            # `backend` field existed loads blank, but a live `tmux_window`
            # means its fork runs under tmux. Backfill it here so every
            # consumer (dispatch, reaper, rename, forget) reads a correct
            # backend without re-deriving it — and the fix persists on save.
            if not record.backend and record.tmux_window:
                record.backend = "tmux"
            self._records[key] = record
        raw_ids = data.get("fork_ids")
        self._fork_ids = (
            {f for f in raw_ids if isinstance(f, str)}
            if isinstance(raw_ids, list)
            else set()
        )

    def _reload_locked(self) -> None:
        """Re-read state from disk, replacing the in-memory snapshot.

        Called inside the file lock before a mutation so a write merges into
        the latest on-disk state instead of clobbering concurrent changes from
        the daemon or another CLI process.
        """
        self._records = {}
        self._fork_ids = set()
        self._load()

    def _save_locked(self) -> None:
        payload = {
            "records": {k: asdict(r) for k, r in self._records.items()},
            "fork_ids": sorted(self._fork_ids),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def get(self, thread_key: str) -> Optional[ThreadRecord]:
        """Return the record for a thread key, or None.

        Re-reads under the lock so the long-lived daemon never serves a stale
        record — e.g. after a CLI `rename`/`forget` changed it on disk — which
        would otherwise be `upsert`-ed back and undo the change.
        """
        with self._lock, file_lock(self.path):
            self._reload_locked()
            return self._records.get(thread_key)

    def bind(
        self,
        thread_key: str,
        handle: str,
        expert_session_id: str,
        project_dir: str,
        backend: str,
        config_dir: str = "",
        access: str = "read",
        asker: str = "",
        agent: str = "claude",
    ) -> ThreadRecord:
        """Create a pending binding for a thread if not already present.

        Returns the existing record if the thread is already bound, so
        re-binding (e.g. a duplicate open) is a no-op.
        """
        with self._lock, file_lock(self.path):
            self._reload_locked()
            existing = self._records.get(thread_key)
            if existing is not None:
                return existing
            rec = ThreadRecord(
                thread_key=thread_key,
                handle=handle,
                expert_session_id=expert_session_id,
                project_dir=project_dir,
                config_dir=config_dir,
                access=access,
                backend=backend,
                asker=asker,
                agent=agent or "claude",
            )
            self._records[thread_key] = rec
            self._save_locked()
            return rec

    def upsert(self, record: ThreadRecord) -> None:
        """Merge caller-owned fields into the latest on-disk record.

        Re-reads under the lock, then copies only the fields the caller owns
        (fork id, tmux window, pending instructions, ``last_used``) onto the
        freshly reloaded record. A concurrent CLI ``rename``/``forget``
        during a long backend call is therefore not clobbered by this
        now-stale ``record``: a renamed handle survives and a removed thread
        is not resurrected. The fork id is always kept in the exclusion set
        so it is never reused, even for a thread forgotten mid-call.
        """
        with self._lock, file_lock(self.path):
            self._reload_locked()
            current = self._records.get(record.thread_key)
            new_fork = bool(
                record.fork_session_id
                and record.fork_session_id not in self._fork_ids
            )
            if record.fork_session_id:
                self._fork_ids.add(record.fork_session_id)
            if current is None:
                # Thread was removed (e.g. `forget`) mid-call — don't
                # resurrect it; just keep its fork id out of future reuse.
                if new_fork:
                    self._save_locked()
                return
            current.fork_session_id = record.fork_session_id
            current.tmux_window = record.tmux_window
            current.pending_instructions = record.pending_instructions
            current.last_used = time.time()
            self._save_locked()

    def remove(self, thread_key: str) -> Optional[ThreadRecord]:
        """Drop a thread mapping (its fork id stays in the exclusion set)."""
        with self._lock, file_lock(self.path):
            self._reload_locked()
            rec = self._records.pop(thread_key, None)
            if rec is not None:
                self._save_locked()
            return rec

    def rename_handle(self, old: str, new: str) -> list[ThreadRecord]:
        """Point every bound thread on handle `old` at `new`.

        Returns the updated records (live references) so the caller can also
        fix their tmux windows.
        """
        with self._lock, file_lock(self.path):
            self._reload_locked()
            renamed = [r for r in self._records.values() if r.handle == old]
            for rec in renamed:
                rec.handle = new
            if renamed:
                self._save_locked()
            return renamed

    def set_access(self, thread_key: str, access: str) -> Optional[ThreadRecord]:
        """Set a bound thread's access level and persist it.

        Lets the daemon propagate a live ``>share --write|--read|...`` re-share
        onto an already-running thread: the backend re-reads the handle's
        current registry access each turn and calls this to sync the stored
        record. Mirrors ``rename_handle``'s reload/mutate/save shape. Returns
        the updated record (a live reference), or None if the thread is no
        longer bound.
        """
        with self._lock, file_lock(self.path):
            self._reload_locked()
            rec = self._records.get(thread_key)
            if rec is not None:
                rec.access = access
                self._save_locked()
            return rec

    def all_records(self) -> list[ThreadRecord]:
        """Return a fresh snapshot of all records (re-read under the lock)."""
        with self._lock, file_lock(self.path):
            self._reload_locked()
            return list(self._records.values())

    def known_fork_ids(self) -> set[str]:
        """All fork session ids ever created (re-read under the lock)."""
        with self._lock, file_lock(self.path):
            self._reload_locked()
            return set(self._fork_ids)
