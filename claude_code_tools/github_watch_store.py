"""Durable SQLite state for GitHub issue-reply notifications."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def default_state_dir() -> Path:
    """Return the shared per-user state directory."""
    configured = os.environ.get("XDG_STATE_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".local/state"
    return root / "claude-code-tools/github-watch"


@dataclass(frozen=True)
class IssueWatch:
    """One pending or completed issue-reply registration."""

    watch_id: str
    repository: str
    issue_number: int
    issue_url: str
    target_kind: str
    target: dict[str, str]
    registered_at: str
    github_host: str
    github_config_dir: str | None
    status: str
    attempts: int
    comment_id: int | None = None
    comment_url: str | None = None
    delivered_at: str | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class WatcherStatus:
    """Last durable heartbeat from the shared watcher."""

    instance_id: str
    pid: int
    process_identity: str
    started_at: str
    heartbeat_at: str


@dataclass(frozen=True)
class RepositoryCursor:
    """One stable conditional-request window for a GitHub repository."""

    since_at: str
    etag: str | None


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS issue_watches (
    watch_id TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_url TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_json TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    github_host TEXT NOT NULL,
    github_config_dir TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    comment_id INTEGER,
    comment_url TEXT,
    delivered_at TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS issue_watches_pending
ON issue_watches(status, github_host, repository);

CREATE TABLE IF NOT EXISTS repository_cursors (
    cursor_key TEXT PRIMARY KEY,
    since_at TEXT NOT NULL,
    etag TEXT
);

CREATE TABLE IF NOT EXISTS watcher_status (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    instance_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    process_identity TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);
"""


class WatchStore:
    """Concurrency-safe storage shared by the CLI and watcher daemon."""

    def __init__(self, state_dir: Path | None = None) -> None:
        """Initialize storage beneath ``state_dir``."""
        self.state_dir = (state_dir or default_state_dir()).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        self.db_path = self.state_dir / "watches.sqlite3"
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            _migrate_repository_cursors(connection)

    @property
    def lock_path(self) -> Path:
        """Return the single-daemon lock path."""
        return self.state_dir / "watcher.lock"

    @property
    def log_path(self) -> Path:
        """Return the bounded daemon log path."""
        return self.state_dir / "watcher.log"

    def add_watch(
        self,
        repository: str,
        issue_number: int,
        issue_url: str,
        target_kind: str,
        target: dict[str, str],
        github_host: str,
        github_config_dir: str | None,
    ) -> IssueWatch:
        """Persist one pending watch before the daemon is launched."""
        watch = IssueWatch(
            watch_id=str(uuid.uuid4()),
            repository=repository,
            issue_number=issue_number,
            issue_url=issue_url,
            target_kind=target_kind,
            target=dict(target),
            registered_at=_now(),
            github_host=github_host,
            github_config_dir=github_config_dir,
            status="pending",
            attempts=0,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO issue_watches (
                    watch_id, repository, issue_number, issue_url, target_kind,
                    target_json, registered_at, github_host, github_config_dir,
                    status, attempts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0)
                """,
                (
                    watch.watch_id,
                    watch.repository,
                    watch.issue_number,
                    watch.issue_url,
                    watch.target_kind,
                    json.dumps(watch.target, separators=(",", ":"), sort_keys=True),
                    watch.registered_at,
                    watch.github_host,
                    watch.github_config_dir,
                ),
            )
        return watch

    def pending_watches(self) -> list[IssueWatch]:
        """Return all watches still awaiting a reply or delivery."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM issue_watches
                WHERE status IN ('pending', 'retrying')
                ORDER BY registered_at, watch_id
                """
            ).fetchall()
        return [_watch_from_row(row) for row in rows]

    def all_watches(self, limit: int = 100) -> list[IssueWatch]:
        """Return recent watches for status display."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM issue_watches
                ORDER BY registered_at DESC, watch_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_watch_from_row(row) for row in rows]

    def mark_delivered(
        self,
        watch_id: str,
        comment_id: int,
        comment_url: str,
    ) -> None:
        """Record successful delivery without reviving a canceled watch."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE issue_watches
                SET status = 'delivered', comment_id = ?, comment_url = ?,
                    delivered_at = ?, last_error = NULL
                WHERE watch_id = ? AND status IN ('pending', 'retrying')
                """,
                (comment_id, comment_url, _now(), watch_id),
            )

    def mark_retry(self, watch_id: str, error: str) -> None:
        """Retain a watch after a bounded polling or delivery failure."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE issue_watches
                SET status = 'retrying', attempts = attempts + 1,
                    last_error = ?
                WHERE watch_id = ? AND status IN ('pending', 'retrying')
                """,
                (error[:4096], watch_id),
            )

    def mark_poll_succeeded(self, watch_ids: list[str]) -> None:
        """Clear transient polling errors after one GitHub context recovers."""
        if not watch_ids:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                UPDATE issue_watches
                SET status = 'pending', last_error = NULL
                WHERE watch_id = ? AND status = 'retrying'
                """,
                [(watch_id,) for watch_id in watch_ids],
            )

    def cancel(self, watch_id: str) -> bool:
        """Cancel one pending watch by full ID or unambiguous prefix."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT watch_id FROM issue_watches
                WHERE watch_id LIKE ? AND status IN ('pending', 'retrying')
                """,
                (f"{watch_id}%",),
            ).fetchall()
            if len(rows) != 1:
                return False
            connection.execute(
                "UPDATE issue_watches SET status = 'canceled' WHERE watch_id = ?",
                (rows[0]["watch_id"],),
            )
        return True

    def repository_cursor(
        self,
        cursor_key: str,
        fallback: str,
    ) -> RepositoryCursor:
        """Return the durable polling cursor for one GitHub context."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT since_at, etag FROM repository_cursors WHERE cursor_key = ?",
                (cursor_key,),
            ).fetchone()
        if row is None:
            return RepositoryCursor(since_at=fallback, etag=None)
        return RepositoryCursor(since_at=row["since_at"], etag=row["etag"])

    def update_repository_cursor(
        self,
        cursor_key: str,
        since_at: str,
        etag: str | None,
    ) -> None:
        """Persist one stable conditional-request window."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO repository_cursors(cursor_key, since_at, etag)
                VALUES (?, ?, ?)
                ON CONFLICT(cursor_key) DO UPDATE SET
                    since_at = excluded.since_at,
                    etag = excluded.etag
                """,
                (cursor_key, since_at, etag),
            )

    def heartbeat(
        self,
        instance_id: str,
        pid: int,
        process_identity: str,
        started_at: str,
    ) -> None:
        """Publish ownership and liveness for the lock-holding daemon."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO watcher_status(
                    singleton, instance_id, pid, process_identity,
                    started_at, heartbeat_at
                ) VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    instance_id = excluded.instance_id,
                    pid = excluded.pid,
                    process_identity = excluded.process_identity,
                    started_at = excluded.started_at,
                    heartbeat_at = excluded.heartbeat_at
                """,
                (instance_id, pid, process_identity, started_at, _now()),
            )

    def watcher_status(self) -> WatcherStatus | None:
        """Return the most recently published watcher heartbeat."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM watcher_status WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return None
        return WatcherStatus(
            instance_id=row["instance_id"],
            pid=row["pid"],
            process_identity=row["process_identity"],
            started_at=row["started_at"],
            heartbeat_at=row["heartbeat_at"],
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open, commit, and close one short SQLite transaction."""
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _watch_from_row(row: sqlite3.Row) -> IssueWatch:
    """Convert one SQLite row to its typed representation."""
    target = json.loads(row["target_json"])
    if not isinstance(target, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in target.items()
    ):
        raise ValueError("issue watch has invalid delivery target data")
    return IssueWatch(
        watch_id=row["watch_id"],
        repository=row["repository"],
        issue_number=row["issue_number"],
        issue_url=row["issue_url"],
        target_kind=row["target_kind"],
        target=target,
        registered_at=row["registered_at"],
        github_host=row["github_host"],
        github_config_dir=row["github_config_dir"],
        status=row["status"],
        attempts=row["attempts"],
        comment_id=row["comment_id"],
        comment_url=row["comment_url"],
        delivered_at=row["delivered_at"],
        last_error=row["last_error"],
    )


def _migrate_repository_cursors(connection: sqlite3.Connection) -> None:
    """Add conditional-request state to databases created by older versions."""
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(repository_cursors)")
    }
    if "etag" not in columns:
        connection.execute("ALTER TABLE repository_cursors ADD COLUMN etag TEXT")


def _now() -> str:
    """Return a stable UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="microseconds")
