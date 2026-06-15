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
    asker: str = ""
    tmux_window: str = ""
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


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
        except (OSError, json.JSONDecodeError):
            return
        known = {f.name for f in ThreadRecord.__dataclass_fields__.values()}
        for key, rec in data.get("records", {}).items():
            fields = {k: v for k, v in rec.items() if k in known}
            self._records[key] = ThreadRecord(**fields)
        self._fork_ids = set(data.get("fork_ids", []))

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
        """Return the record for a thread key, or None."""
        with self._lock:
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
    ) -> ThreadRecord:
        """Create a pending binding for a thread if not already present.

        Returns the existing record if the thread is already bound, so
        re-binding (e.g. a duplicate open) is a no-op.
        """
        with self._lock:
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
            )
            self._records[thread_key] = rec
            self._save_locked()
            return rec

    def upsert(self, record: ThreadRecord) -> None:
        """Insert or update a record; tracks its fork id permanently."""
        with self._lock:
            record.last_used = time.time()
            self._records[record.thread_key] = record
            if record.fork_session_id:
                self._fork_ids.add(record.fork_session_id)
            self._save_locked()

    def remove(self, thread_key: str) -> Optional[ThreadRecord]:
        """Drop a thread mapping (its fork id stays in the exclusion set)."""
        with self._lock:
            rec = self._records.pop(thread_key, None)
            if rec is not None:
                self._save_locked()
            return rec

    def rename_handle(self, old: str, new: str) -> list[ThreadRecord]:
        """Point every bound thread on handle `old` at `new`.

        Returns the updated records (live references) so the caller can also
        fix their tmux windows.
        """
        with self._lock:
            renamed = [r for r in self._records.values() if r.handle == old]
            for rec in renamed:
                rec.handle = new
            if renamed:
                self._save_locked()
            return renamed

    def all_records(self) -> list[ThreadRecord]:
        """Return a snapshot of all records."""
        with self._lock:
            return list(self._records.values())

    def known_fork_ids(self) -> set[str]:
        """All fork session ids ever created by this tunnel."""
        with self._lock:
            return set(self._fork_ids)
