"""SQLite storage layer for the msg inter-agent messaging system."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .models import (
    Agent,
    AgentKind,
    Delivery,
    DeliveryState,
    Message,
    Thread,
    WatcherHeartbeat,
    _new_uuid,
    _now_iso,
)

DEFAULT_DB_DIR = os.path.expanduser("~/.msg")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "msg.db")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    session_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    pane_id TEXT NOT NULL,
    tmux_session TEXT NOT NULL,
    tmux_socket TEXT,
    display_addr TEXT,
    agent_kind TEXT NOT NULL,
    pid INTEGER,
    cwd TEXT,
    registered_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(name, tmux_session, tmux_socket)
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_by TEXT NOT NULL
        REFERENCES agents(session_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_participants (
    thread_id TEXT NOT NULL
        REFERENCES threads(id),
    agent_id TEXT NOT NULL
        REFERENCES agents(session_id),
    PRIMARY KEY (thread_id, agent_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL
        REFERENCES threads(id),
    from_agent TEXT NOT NULL
        REFERENCES agents(session_id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL
        REFERENCES messages(id),
    recipient_id TEXT NOT NULL
        REFERENCES agents(session_id),
    state TEXT NOT NULL DEFAULT 'pending',
    claimed_by TEXT,
    claim_expires_at TEXT,
    notify_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    notified_at TEXT,
    read_at TEXT,
    UNIQUE(message_id, recipient_id)
);

CREATE TABLE IF NOT EXISTS watcher_heartbeat (
    watcher_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    pid INTEGER NOT NULL
);
"""


class MsgStore:
    """SQLite-backed store for inter-agent messaging."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    # --- Agent operations ---

    def register_agent(
        self,
        name: str,
        pane_id: str,
        tmux_session: str,
        agent_kind: AgentKind,
        tmux_socket: str | None = None,
        display_addr: str | None = None,
        pid: int | None = None,
        cwd: str | None = None,
    ) -> Agent:
        """Register or re-register an agent session.

        If an agent with the same name+tmux_session+tmux_socket
        exists, updates its pane/pid info and keeps the session_id.
        """
        now = _now_iso()
        conn = self._get_conn()
        try:
            existing = conn.execute(
                """SELECT session_id FROM agents
                WHERE name = ? AND tmux_session = ?
                AND (tmux_socket IS ? OR tmux_socket = ?)""",
                (name, tmux_session, tmux_socket, tmux_socket),
            ).fetchone()

            if existing:
                session_id = existing["session_id"]
                conn.execute(
                    """UPDATE agents SET
                        pane_id = ?, display_addr = ?,
                        agent_kind = ?, pid = ?, cwd = ?,
                        last_seen = ?, tmux_socket = ?
                    WHERE session_id = ?""",
                    (
                        pane_id, display_addr,
                        agent_kind.value, pid, cwd,
                        now, tmux_socket, session_id,
                    ),
                )
            else:
                session_id = _new_uuid()
                conn.execute(
                    """INSERT INTO agents (
                        session_id, name, pane_id,
                        tmux_session, tmux_socket,
                        display_addr, agent_kind, pid, cwd,
                        registered_at, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id, name, pane_id,
                        tmux_session, tmux_socket,
                        display_addr, agent_kind.value,
                        pid, cwd, now, now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        return Agent(
            session_id=session_id,
            name=name,
            pane_id=pane_id,
            tmux_session=tmux_session,
            tmux_socket=tmux_socket,
            display_addr=display_addr,
            agent_kind=agent_kind,
            pid=pid,
            cwd=cwd,
            registered_at=now,
            last_seen=now,
        )

    def get_agent_by_name(
        self,
        name: str,
        tmux_session: str,
        tmux_socket: str | None = None,
    ) -> Agent | None:
        """Look up an agent by name within a tmux session."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT * FROM agents
                WHERE name = ? AND tmux_session = ?
                AND (tmux_socket IS ? OR tmux_socket = ?)""",
                (name, tmux_session, tmux_socket, tmux_socket),
            ).fetchone()
            if not row:
                return None
            return self._row_to_agent(row)
        finally:
            conn.close()

    def get_agent_by_id(self, session_id: str) -> Agent | None:
        """Look up an agent by session_id."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM agents WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_agent(row)
        finally:
            conn.close()

    def list_agents(
        self,
        tmux_session: str | None = None,
    ) -> list[Agent]:
        """List all registered agents, optionally filtered."""
        conn = self._get_conn()
        try:
            if tmux_session:
                rows = conn.execute(
                    "SELECT * FROM agents WHERE tmux_session = ?",
                    (tmux_session,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agents",
                ).fetchall()
            return [self._row_to_agent(r) for r in rows]
        finally:
            conn.close()

    def touch_agent(self, session_id: str) -> None:
        """Update last_seen for an agent."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE agents SET last_seen = ? "
                "WHERE session_id = ?",
                (_now_iso(), session_id),
            )
            conn.commit()
        finally:
            conn.close()

    # --- Thread operations ---

    def create_thread(
        self,
        title: str,
        created_by: str,
        participant_ids: list[str],
    ) -> Thread:
        """Create a thread with participants.

        Args:
            title: Thread title.
            created_by: Session ID of the creator.
            participant_ids: Session IDs of all participants
                (should include the creator).
        """
        thread = Thread(
            title=title,
            created_by=created_by,
        )
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO threads (id, title, created_by,
                    created_at)
                VALUES (?, ?, ?, ?)""",
                (thread.id, thread.title,
                 thread.created_by, thread.created_at),
            )
            for pid in participant_ids:
                conn.execute(
                    """INSERT INTO thread_participants
                        (thread_id, agent_id)
                    VALUES (?, ?)""",
                    (thread.id, pid),
                )
            conn.commit()
        finally:
            conn.close()
        return thread

    def get_or_create_thread(
        self,
        participant_ids: list[str],
        created_by: str,
    ) -> Thread:
        """Get existing thread for these participants,
        or create one.

        A thread is uniquely identified by its sorted set
        of participants. If a thread with the exact same
        participants exists, return it. Otherwise create one.
        """
        sorted_ids = sorted(participant_ids)
        conn = self._get_conn()
        try:
            # Find threads where participant set matches
            for row in conn.execute(
                "SELECT * FROM threads"
            ).fetchall():
                tid = row["id"]
                prows = conn.execute(
                    """SELECT agent_id
                    FROM thread_participants
                    WHERE thread_id = ?
                    ORDER BY agent_id""",
                    (tid,),
                ).fetchall()
                existing_ids = [r["agent_id"] for r in prows]
                if existing_ids == sorted_ids:
                    return Thread(
                        id=row["id"],
                        title=row["title"],
                        created_by=row["created_by"],
                        created_at=row["created_at"],
                    )
        finally:
            conn.close()

        # Build a title from participant names
        names = []
        for pid in sorted_ids:
            agent = self.get_agent_by_id(pid)
            if agent:
                names.append(agent.name)
        title = " <> ".join(names)

        return self.create_thread(
            title=title,
            created_by=created_by,
            participant_ids=sorted_ids,
        )

    def get_thread(self, thread_id: str) -> Thread | None:
        """Get a thread by ID."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            if not row:
                return None
            return Thread(
                id=row["id"],
                title=row["title"],
                created_by=row["created_by"],
                created_at=row["created_at"],
            )
        finally:
            conn.close()

    def get_thread_participants(
        self, thread_id: str,
    ) -> list[str]:
        """Get session IDs of all participants in a thread."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT agent_id FROM thread_participants
                WHERE thread_id = ?""",
                (thread_id,),
            ).fetchall()
            return [r["agent_id"] for r in rows]
        finally:
            conn.close()

    def list_threads(
        self, agent_id: str | None = None,
    ) -> list[Thread]:
        """List threads, optionally filtered by participant."""
        conn = self._get_conn()
        try:
            if agent_id:
                rows = conn.execute(
                    """SELECT t.* FROM threads t
                    JOIN thread_participants tp
                        ON t.id = tp.thread_id
                    WHERE tp.agent_id = ?
                    ORDER BY t.created_at DESC""",
                    (agent_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM threads "
                    "ORDER BY created_at DESC",
                ).fetchall()
            return [
                Thread(
                    id=r["id"],
                    title=r["title"],
                    created_by=r["created_by"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    # --- Message operations ---

    def send_message(
        self,
        thread_id: str,
        from_agent: str,
        body: str,
    ) -> Message:
        """Send a message in a thread.

        Creates delivery rows for all participants except
        the sender.
        """
        msg = Message(
            thread_id=thread_id,
            from_agent=from_agent,
            body=body,
        )
        participants = self.get_thread_participants(thread_id)
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO messages
                    (id, thread_id, from_agent, body,
                     created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (msg.id, msg.thread_id, msg.from_agent,
                 msg.body, msg.created_at),
            )
            for pid in participants:
                if pid == from_agent:
                    continue
                delivery_id = _new_uuid()
                conn.execute(
                    """INSERT INTO deliveries
                        (id, message_id, recipient_id,
                         state, created_at)
                    VALUES (?, ?, ?, 'pending', ?)""",
                    (delivery_id, msg.id, pid,
                     msg.created_at),
                )
            conn.commit()
        finally:
            conn.close()
        return msg

    def get_inbox(
        self,
        agent_id: str,
        thread_id: str | None = None,
    ) -> list[dict]:
        """Get unread messages for an agent.

        Returns messages where delivery state != 'read',
        regardless of notification state. This ensures
        messages are visible even if the watcher is down.

        Returns list of dicts with message + delivery info.
        """
        conn = self._get_conn()
        try:
            if thread_id:
                rows = conn.execute(
                    """SELECT m.*, d.id as delivery_id,
                        d.state, a.name as from_name
                    FROM messages m
                    JOIN deliveries d
                        ON m.id = d.message_id
                    JOIN agents a
                        ON m.from_agent = a.session_id
                    WHERE d.recipient_id = ?
                        AND d.state != 'read'
                        AND m.thread_id = ?
                    ORDER BY m.created_at ASC""",
                    (agent_id, thread_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT m.*, d.id as delivery_id,
                        d.state, a.name as from_name,
                        t.title as thread_title
                    FROM messages m
                    JOIN deliveries d
                        ON m.id = d.message_id
                    JOIN agents a
                        ON m.from_agent = a.session_id
                    JOIN threads t
                        ON m.thread_id = t.id
                    WHERE d.recipient_id = ?
                        AND d.state != 'read'
                    ORDER BY m.created_at ASC""",
                    (agent_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_read(
        self,
        agent_id: str,
        thread_id: str | None = None,
    ) -> int:
        """Mark messages as read for an agent.

        Returns count of messages marked.
        """
        now = _now_iso()
        conn = self._get_conn()
        try:
            if thread_id:
                cur = conn.execute(
                    """UPDATE deliveries SET
                        state = 'read', read_at = ?
                    WHERE recipient_id = ?
                        AND state != 'read'
                        AND message_id IN (
                            SELECT id FROM messages
                            WHERE thread_id = ?
                        )""",
                    (now, agent_id, thread_id),
                )
            else:
                cur = conn.execute(
                    """UPDATE deliveries SET
                        state = 'read', read_at = ?
                    WHERE recipient_id = ?
                        AND state != 'read'""",
                    (now, agent_id),
                )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # --- Delivery operations (for watcher/hook) ---

    def claim_pending_deliveries(
        self,
        claimer_id: str,
        claim_duration_secs: int = 60,
    ) -> list[dict]:
        """Claim pending deliveries for notification.

        Returns deliveries grouped by recipient with
        message and thread info. Uses atomic claim to
        prevent double-notification by watcher vs hook.
        """
        now = _now_iso()
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=claim_duration_secs
        )
        expires_iso = expires.isoformat()

        conn = self._get_conn()
        try:
            # Claim unclaimed pending deliveries, or those
            # with expired claims
            conn.execute(
                """UPDATE deliveries SET
                    state = 'claimed',
                    claimed_by = ?,
                    claim_expires_at = ?,
                    notify_attempts = notify_attempts + 1
                WHERE state = 'pending'
                    OR (state = 'claimed'
                        AND claim_expires_at < ?)""",
                (claimer_id, expires_iso, now),
            )
            conn.commit()

            # Fetch what we claimed
            rows = conn.execute(
                """SELECT d.*, m.body, m.from_agent,
                    m.thread_id, t.title as thread_title,
                    a.name as from_name,
                    r.name as recipient_name,
                    r.pane_id as recipient_pane_id,
                    r.tmux_session as recipient_tmux_session,
                    r.display_addr as recipient_display_addr,
                    r.agent_kind as recipient_agent_kind
                FROM deliveries d
                JOIN messages m ON d.message_id = m.id
                JOIN threads t ON m.thread_id = t.id
                JOIN agents a ON m.from_agent = a.session_id
                JOIN agents r ON d.recipient_id = r.session_id
                WHERE d.claimed_by = ?
                    AND d.state = 'claimed'
                ORDER BY d.recipient_id, m.created_at""",
                (claimer_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_notified(self, delivery_id: str) -> None:
        """Mark a delivery as notified (notification sent)."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE deliveries SET
                    state = 'notified', notified_at = ?
                WHERE id = ? AND state = 'claimed'""",
                (_now_iso(), delivery_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_delivery_failed(
        self,
        delivery_id: str,
        error: str,
        max_attempts: int = 3,
    ) -> None:
        """Mark a delivery as failed or reset to pending."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT notify_attempts FROM deliveries "
                "WHERE id = ?",
                (delivery_id,),
            ).fetchone()
            if row and row["notify_attempts"] >= max_attempts:
                new_state = DeliveryState.FAILED.value
            else:
                new_state = DeliveryState.PENDING.value

            conn.execute(
                """UPDATE deliveries SET
                    state = ?, last_error = ?,
                    claimed_by = NULL,
                    claim_expires_at = NULL
                WHERE id = ?""",
                (new_state, error, delivery_id),
            )
            conn.commit()
        finally:
            conn.close()

    def release_expired_claims(self) -> int:
        """Release deliveries with expired claims.

        Returns count of released deliveries.
        """
        now = _now_iso()
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """UPDATE deliveries SET
                    state = 'pending',
                    claimed_by = NULL,
                    claim_expires_at = NULL
                WHERE state = 'claimed'
                    AND claim_expires_at < ?""",
                (now,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # --- Watcher heartbeat ---

    def update_heartbeat(
        self,
        watcher_id: str,
        pid: int,
    ) -> None:
        """Update or create watcher heartbeat."""
        now = _now_iso()
        conn = self._get_conn()
        try:
            existing = conn.execute(
                "SELECT 1 FROM watcher_heartbeat "
                "WHERE watcher_id = ?",
                (watcher_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE watcher_heartbeat SET
                        last_heartbeat = ?, pid = ?
                    WHERE watcher_id = ?""",
                    (now, pid, watcher_id),
                )
            else:
                conn.execute(
                    """INSERT INTO watcher_heartbeat
                        (watcher_id, started_at,
                         last_heartbeat, pid)
                    VALUES (?, ?, ?, ?)""",
                    (watcher_id, now, now, pid),
                )
            conn.commit()
        finally:
            conn.close()

    def is_watcher_alive(
        self, max_age_secs: int = 30,
    ) -> bool:
        """Check if any watcher has a recent heartbeat."""
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=max_age_secs)
        ).isoformat()
        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT 1 FROM watcher_heartbeat
                WHERE last_heartbeat > ?
                LIMIT 1""",
                (cutoff,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_watcher_info(self) -> list[WatcherHeartbeat]:
        """Get all watcher heartbeat records."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM watcher_heartbeat "
                "ORDER BY last_heartbeat DESC",
            ).fetchall()
            return [
                WatcherHeartbeat(
                    watcher_id=r["watcher_id"],
                    started_at=r["started_at"],
                    last_heartbeat=r["last_heartbeat"],
                    pid=r["pid"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    # --- Helpers ---

    @staticmethod
    def _row_to_agent(row: sqlite3.Row) -> Agent:
        return Agent(
            session_id=row["session_id"],
            name=row["name"],
            pane_id=row["pane_id"],
            tmux_session=row["tmux_session"],
            tmux_socket=row["tmux_socket"],
            display_addr=row["display_addr"],
            agent_kind=AgentKind(row["agent_kind"]),
            pid=row["pid"],
            cwd=row["cwd"],
            registered_at=row["registered_at"],
            last_seen=row["last_seen"],
        )
