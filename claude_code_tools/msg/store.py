"""SQLite storage layer for the msg inter-agent messaging system."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .migrations import adopt_unique_legacy_registration, initialize_database
from .models import (
    Agent,
    AgentKind,
    Message,
    Thread,
    WatcherHeartbeat,
    _new_uuid,
    _now_iso,
)

DEFAULT_DB_DIR = os.environ.get(
    "MSG_DB_DIR",
    os.path.expanduser("~/.msg"),
)
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "msg.db")

RELEASE_EXPIRED_SQL = """UPDATE deliveries SET
    state = CASE WHEN state IN ('read', 'notified') THEN state ELSE 'pending' END,
    claimed_by = NULL, claim_expires_at = NULL
WHERE claimed_by IS NOT NULL AND claim_expires_at < ?
    AND (? IS NULL OR recipient_id = ?)"""


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
            initialize_database(conn)
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
        """Register an agent, preserving active-session idempotency."""
        now = _now_iso()
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_rows = conn.execute(
                """SELECT session_id, active, pane_id FROM agents
                WHERE name = ? AND tmux_session = ?
                AND (tmux_socket IS ? OR tmux_socket = ?)""",
                (name, tmux_session, tmux_socket, tmux_socket),
            ).fetchall()
            if len(existing_rows) > 1:
                raise ValueError(f"agent '{name}' has ambiguous registrations")
            existing = existing_rows[0] if existing_rows else None

            if existing and not existing["active"]:
                conn.execute(
                    "UPDATE agents SET name = ? WHERE session_id = ?",
                    (
                        f"{name}@retired:{existing['session_id']}",
                        existing["session_id"],
                    ),
                )
                existing = None

            if not existing:
                adopted = adopt_unique_legacy_registration(
                    conn, name, pane_id, tmux_session, tmux_socket,
                )
                if adopted:
                    existing = conn.execute(
                        """SELECT session_id, active, pane_id FROM agents
                        WHERE session_id = ?""",
                        (adopted,),
                    ).fetchone()

            if existing and existing["active"] and existing["pane_id"] != pane_id:
                raise ValueError(
                    f"agent '{name}' is already active at {existing['pane_id']}; "
                    "unregister it first"
                )

            others = conn.execute(
                """SELECT session_id FROM agents
                WHERE active = 1 AND pane_id = ? AND tmux_session = ?
                AND (tmux_socket IS ? OR tmux_socket = ?)
                AND (? IS NULL OR session_id != ?)""",
                (
                    pane_id, tmux_session, tmux_socket, tmux_socket,
                    existing["session_id"] if existing else None,
                    existing["session_id"] if existing else None,
                ),
            ).fetchall()
            if others:
                raise ValueError("pane already has an active msg registration")

            if existing:
                session_id = existing["session_id"]
                conn.execute(
                    """UPDATE agents SET
                        pane_id = ?, display_addr = ?,
                        agent_kind = ?, pid = ?, cwd = ?,
                        last_seen = ?, tmux_socket = ?, active = 1
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
            rows = conn.execute(
                """SELECT * FROM agents
                WHERE active = 1 AND name = ? AND tmux_session = ?
                AND (tmux_socket IS ? OR tmux_socket = ?)""",
                (name, tmux_session, tmux_socket, tmux_socket),
            ).fetchall()
            return self._row_to_agent(rows[0]) if len(rows) == 1 else None
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
        tmux_socket: str | None = None,
    ) -> list[Agent]:
        """List all registered agents, optionally filtered."""
        conn = self._get_conn()
        try:
            if tmux_session and tmux_socket is not None:
                rows = conn.execute(
                    """SELECT * FROM agents WHERE active = 1 AND tmux_session = ?
                    AND tmux_socket = ?""",
                    (tmux_session, tmux_socket),
                ).fetchall()
            elif tmux_session:
                rows = conn.execute(
                    "SELECT * FROM agents WHERE active = 1 AND tmux_session = ?",
                    (tmux_session,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agents WHERE active = 1",
                ).fetchall()
            return [self._row_to_agent(r) for r in rows]
        finally:
            conn.close()

    def retire_agent(self, session_id: str) -> bool:
        """Hide an agent from live routing while preserving its history."""
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = _now_iso()
            conn.execute(RELEASE_EXPIRED_SQL, (now, session_id, session_id))
            unread = conn.execute(
                "SELECT count(*) FROM deliveries WHERE recipient_id = ? "
                "AND state NOT IN ('read', 'retired')",
                (session_id,),
            ).fetchone()[0]
            if unread:
                raise ValueError(
                    f"agent has {unread} unread delivery; drain it before unregistering"
                )
            in_flight = conn.execute(
                "SELECT 1 FROM deliveries WHERE recipient_id = ? "
                "AND claimed_by IS NOT NULL LIMIT 1",
                (session_id,),
            ).fetchone()
            if in_flight:
                raise ValueError("agent has a delivery in flight; retry unregister")
            changed = conn.execute(
                "UPDATE agents SET active = 0, display_addr = NULL, last_seen = ? "
                "WHERE session_id = ? AND active = 1",
                (now, session_id),
            ).rowcount
            conn.commit()
            return changed == 1
        finally:
            conn.close()

    def retarget_agent(
        self,
        session_id: str,
        pane_id: str,
        tmux_session: str,
        tmux_socket: str | None = None,
        display_addr: str | None = None,
    ) -> Agent:
        """Move one exact active agent within its existing tmux scope."""
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM agents WHERE session_id = ? AND active = 1",
                (session_id,),
            ).fetchone()
            if not current:
                raise ValueError("active registration not found")
            if (
                current["tmux_session"] != tmux_session
                or current["tmux_socket"] != tmux_socket
            ):
                raise ValueError("registration is outside the requested tmux scope")
            now = _now_iso()
            conn.execute(RELEASE_EXPIRED_SQL, (now, session_id, session_id))
            claimed = conn.execute(
                "SELECT 1 FROM deliveries WHERE recipient_id = ? "
                "AND claimed_by IS NOT NULL LIMIT 1",
                (session_id,),
            ).fetchone()
            if claimed:
                raise ValueError("agent has an actively claimed delivery")
            occupied = conn.execute(
                """SELECT 1 FROM agents WHERE active = 1 AND pane_id = ?
                AND tmux_session = ? AND (tmux_socket IS ? OR tmux_socket = ?)
                AND session_id != ?""",
                (pane_id, tmux_session, tmux_socket, tmux_socket, session_id),
            ).fetchone()
            if occupied:
                raise ValueError("target pane already has an active msg registration")
            conn.execute(
                """UPDATE agents SET pane_id = ?, display_addr = ?, last_seen = ?
                WHERE session_id = ? AND active = 1""",
                (pane_id, display_addr, _now_iso(), session_id),
            )
            conn.execute(
                """UPDATE deliveries SET state = 'pending', claimed_by = NULL,
                    claim_expires_at = NULL, notify_attempts = 0,
                    last_error = NULL, notified_at = NULL
                WHERE recipient_id = ? AND claimed_by IS NULL
                    AND state NOT IN ('read', 'retired')""",
                (session_id,),
            )
            moved = conn.execute(
                "SELECT * FROM agents WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            conn.commit()
            return self._row_to_agent(moved)
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
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            participants = [
                row[0]
                for row in conn.execute(
                    "SELECT agent_id FROM thread_participants WHERE thread_id = ?",
                    (thread_id,),
                )
            ]
            inactive = conn.execute(
                "SELECT count(*) FROM agents WHERE session_id IN "
                "(SELECT agent_id FROM thread_participants WHERE thread_id = ?) "
                "AND active = 0",
                (thread_id,),
            ).fetchone()[0]
            if inactive:
                raise ValueError("message recipient became inactive; resolve it again")
            conn.execute(
                """INSERT INTO messages
                    (id, thread_id, from_agent, sender_name, body,
                     created_at)
                VALUES (?, ?, ?,
                    (SELECT name FROM agents WHERE session_id = ?), ?, ?)""",
                (msg.id, msg.thread_id, msg.from_agent, msg.from_agent,
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
                        d.state, COALESCE(m.sender_name, a.name) as from_name
                    FROM messages m
                    JOIN deliveries d
                        ON m.id = d.message_id
                    JOIN agents a
                        ON m.from_agent = a.session_id
                    WHERE d.recipient_id = ?
                        AND d.state NOT IN ('read', 'retired')
                        AND m.thread_id = ?
                    ORDER BY m.created_at ASC""",
                    (agent_id, thread_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT m.*, d.id as delivery_id,
                        d.state, COALESCE(m.sender_name, a.name) as from_name,
                        t.title as thread_title
                    FROM messages m
                    JOIN deliveries d
                        ON m.id = d.message_id
                    JOIN agents a
                        ON m.from_agent = a.session_id
                    JOIN threads t
                        ON m.thread_id = t.id
                    WHERE d.recipient_id = ?
                        AND d.state NOT IN ('read', 'retired')
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
        delivery_ids: list[str] | None = None,
    ) -> int:
        """Mark messages as read for an agent.

        Returns count of messages marked.
        """
        now = _now_iso()
        conn = self._get_conn()
        try:
            if delivery_ids is not None:
                if not delivery_ids:
                    return 0
                placeholders = ",".join("?" for _ in delivery_ids)
                cur = conn.execute(
                    f"""UPDATE deliveries SET
                        state = 'read', read_at = ?
                    WHERE recipient_id = ?
                        AND state NOT IN ('read', 'retired')
                        AND id IN ({placeholders})""",
                    (now, agent_id, *delivery_ids),
                )
            elif thread_id:
                cur = conn.execute(
                    """UPDATE deliveries SET
                        state = 'read', read_at = ?
                    WHERE recipient_id = ?
                        AND state NOT IN ('read', 'retired')
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
                        AND state NOT IN ('read', 'retired')""",
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
        recipient_id: str | None = None,
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
                    claim_expires_at = ?
                WHERE recipient_id IN (
                    SELECT session_id FROM agents WHERE active = 1
                ) AND (? IS NULL OR recipient_id = ?) AND (
                    state = 'pending'
                    OR (state = 'claimed' AND claim_expires_at < ?)
                )""",
                (claimer_id, expires_iso, recipient_id, recipient_id, now),
            )
            conn.commit()

            # Fetch what we claimed
            rows = conn.execute(
                """SELECT d.*, m.body, m.from_agent,
                    m.thread_id, t.title as thread_title,
                    COALESCE(m.sender_name, a.name) as from_name,
                    r.name as recipient_name,
                    r.pane_id as recipient_pane_id,
                    r.tmux_socket as recipient_tmux_socket,
                    r.agent_kind as recipient_agent_kind
                FROM deliveries d
                JOIN messages m ON d.message_id = m.id
                JOIN threads t ON m.thread_id = t.id
                JOIN agents a ON m.from_agent = a.session_id
                JOIN agents r ON d.recipient_id = r.session_id
                WHERE d.claimed_by = ?
                    AND d.state = 'claimed'
                    AND r.active = 1
                ORDER BY d.recipient_id, m.created_at""",
                (claimer_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def claim_is_current(self, delivery_id: str, claimer_id: str) -> bool:
        """Return whether this watcher still owns a live recipient delivery."""
        conn = self._get_conn()
        try:
            return conn.execute(
                """SELECT 1 FROM deliveries d
                JOIN agents a ON a.session_id = d.recipient_id
                WHERE d.id = ? AND d.claimed_by = ?
                    AND d.state = 'claimed' AND d.claim_expires_at >= ?
                    AND a.active = 1""",
                (delivery_id, claimer_id, _now_iso()),
            ).fetchone() is not None
        finally:
            conn.close()

    def renew_deliveries(
        self,
        delivery_ids: list[str],
        claimer_id: str,
        claim_duration_secs: int,
    ) -> bool:
        """Atomically extend a complete batch of live delivery claims."""
        if not delivery_ids:
            return False
        now = _now_iso()
        expires = datetime.now(timezone.utc) + timedelta(
            seconds=claim_duration_secs
        )
        placeholders = ",".join("?" for _ in delivery_ids)
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                f"""SELECT count(*) FROM deliveries d
                JOIN agents a ON a.session_id = d.recipient_id
                WHERE d.id IN ({placeholders}) AND d.claimed_by = ?
                    AND d.state IN ('claimed', 'read')
                    AND d.claim_expires_at >= ?
                    AND a.active = 1""",
                (*delivery_ids, claimer_id, now),
            ).fetchone()[0]
            if current != len(delivery_ids):
                conn.rollback()
                return False
            conn.execute(
                f"""UPDATE deliveries SET claim_expires_at = ?
                WHERE id IN ({placeholders})""",
                (expires.isoformat(), *delivery_ids),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def mark_notified(self, delivery_id: str, claimer_id: str) -> None:
        """Mark a delivery as notified (notification sent)."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE deliveries SET
                    state = CASE WHEN state = 'read' THEN 'read' ELSE 'notified' END,
                    notified_at = ?, claimed_by = NULL, claim_expires_at = NULL
                WHERE id = ? AND state IN ('claimed', 'read') AND claimed_by = ?
                    AND claim_expires_at >= ?""",
                (_now_iso(), delivery_id, claimer_id, _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_delivery_failed(
        self,
        delivery_id: str,
        claimer_id: str,
        error: str,
        max_attempts: int = 3,
    ) -> None:
        """Charge an actual delivery failure and retry or give up."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE deliveries SET
                    state = CASE WHEN state = 'read' THEN 'read'
                        WHEN notify_attempts + 1 >= ? THEN 'failed' ELSE 'pending' END,
                    notify_attempts = notify_attempts + (state != 'read'),
                    last_error = CASE WHEN state = 'read' THEN last_error ELSE ? END,
                    claimed_by = NULL,
                    claim_expires_at = NULL
                WHERE id = ? AND state IN ('claimed', 'read') AND claimed_by = ?
                    AND claim_expires_at >= ?""",
                (max_attempts, error, delivery_id, claimer_id, _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def release_delivery(self, delivery_id: str, claimer_id: str) -> None:
        """Release a normal busy/not-ready poll without charging a failure."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE deliveries SET
                    state = CASE WHEN state = 'read' THEN 'read' ELSE 'pending' END,
                    claimed_by = NULL,
                    claim_expires_at = NULL
                WHERE id = ? AND state IN ('claimed', 'read') AND claimed_by = ?
                    AND claim_expires_at >= ?""",
                (delivery_id, claimer_id, _now_iso()),
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
                RELEASE_EXPIRED_SQL,
                (now, None, None),
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
