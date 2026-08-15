"""Database initialization and migrations for the msg store."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

CURRENT_SCHEMA_VERSION = 3

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS agents (
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
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(name, tmux_session, tmux_socket)
    )""",
    """CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_by TEXT NOT NULL REFERENCES agents(session_id),
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS thread_participants (
        thread_id TEXT NOT NULL REFERENCES threads(id),
        agent_id TEXT NOT NULL REFERENCES agents(session_id),
        PRIMARY KEY (thread_id, agent_id)
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES threads(id),
        from_agent TEXT NOT NULL REFERENCES agents(session_id),
        sender_name TEXT,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS deliveries (
        id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL REFERENCES messages(id),
        recipient_id TEXT NOT NULL REFERENCES agents(session_id),
        state TEXT NOT NULL DEFAULT 'pending',
        claimed_by TEXT,
        claim_expires_at TEXT,
        notify_attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        notified_at TEXT,
        read_at TEXT,
        UNIQUE(message_id, recipient_id)
    )""",
    """CREATE TABLE IF NOT EXISTS watcher_heartbeat (
        watcher_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        last_heartbeat TEXT NOT NULL,
        pid INTEGER NOT NULL
    )""",
)


def _normalize_expiry(value: object) -> str | None:
    """Return an aware ISO expiry in canonical UTC form, if valid."""
    if not isinstance(value, str):
        return None
    try:
        expiry = datetime.fromisoformat(value)
    except ValueError:
        return None
    if expiry.tzinfo is None or expiry.utcoffset() is None:
        return None
    return expiry.astimezone(timezone.utc).isoformat()


def initialize_database(conn: sqlite3.Connection) -> None:
    """Create and migrate a database in one serialized transaction."""
    conn.execute("PRAGMA busy_timeout=5000")
    wal_deadline = time.monotonic() + 5
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or time.monotonic() >= wal_deadline:
                raise
            time.sleep(0.01)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)

        agent_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(agents)")
        }
        if "active" not in agent_columns:
            conn.execute(
                "ALTER TABLE agents ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
            )

        message_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(messages)")
        }
        if "sender_name" not in message_columns:
            conn.execute("ALTER TABLE messages ADD COLUMN sender_name TEXT")

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.execute(
                """UPDATE deliveries SET state = 'pending', notify_attempts = 0,
                    last_error = NULL, claimed_by = NULL, claim_expires_at = NULL
                WHERE state IN ('pending', 'failed') AND recipient_id IN (
                    SELECT session_id FROM agents WHERE active = 1
                )"""
            )

        if version < 2:
            conn.execute(
                """UPDATE agents SET active = 0, display_addr = NULL
                WHERE active = 1 AND tmux_socket IS NULL
                AND (pane_id, tmux_session) IN (
                    SELECT pane_id, tmux_session FROM agents
                    WHERE active = 1 AND tmux_socket IS NULL
                    GROUP BY pane_id, tmux_session HAVING count(*) > 1
                )"""
            )
            conn.execute(
                """UPDATE deliveries SET claimed_by = NULL,
                    claim_expires_at = NULL
                WHERE state = 'notified'
                    AND (claimed_by IS NOT NULL OR claim_expires_at IS NOT NULL)"""
            )
            conn.execute(
                """UPDATE deliveries SET state = 'pending',
                    claimed_by = NULL, claim_expires_at = NULL
                WHERE state = 'claimed'
                    AND (claimed_by IS NULL OR claim_expires_at IS NULL)"""
            )

        if version < 3:
            conn.execute(
                """UPDATE deliveries SET claimed_by = NULL,
                    claim_expires_at = NULL
                WHERE (state IS NULL OR state != 'claimed')
                    AND (claimed_by IS NOT NULL OR claim_expires_at IS NOT NULL)"""
            )
            claims = conn.execute(
                """SELECT id, claimed_by, claim_expires_at
                FROM deliveries WHERE state = 'claimed'"""
            ).fetchall()
            malformed_claims = [
                (row[0],)
                for row in claims
                if not isinstance(row[1], str)
                or not row[1]
                or _normalize_expiry(row[2]) is None
            ]
            conn.executemany(
                """UPDATE deliveries SET state = 'pending',
                    claimed_by = NULL, claim_expires_at = NULL WHERE id = ?""",
                malformed_claims,
            )
            conn.executemany(
                """UPDATE deliveries SET claim_expires_at = ? WHERE id = ?""",
                (
                    (normalized, row[0])
                    for row in claims
                    if isinstance(row[1], str)
                    and row[1]
                    and (normalized := _normalize_expiry(row[2])) is not None
                ),
            )
            conn.execute(
                """UPDATE messages SET sender_name = (
                    SELECT CASE
                        WHEN active = 0
                            AND name LIKE '%@retired:' || session_id
                        THEN substr(name, 1, length(name)
                            - length('@retired:' || session_id))
                        ELSE name
                    END FROM agents
                    WHERE session_id = messages.from_agent
                ) WHERE sender_name IS NULL"""
            )
            conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def adopt_unique_legacy_registration(
    conn: sqlite3.Connection,
    name: str,
    pane_id: str,
    tmux_session: str,
    tmux_socket: str | None,
) -> str | None:
    """Adopt one matching active NULL-socket identity into a known socket."""
    if tmux_socket is None:
        return None
    rows = conn.execute(
        """SELECT session_id, active, pane_id FROM agents
        WHERE active = 1 AND name = ? AND pane_id = ? AND tmux_session = ?
            AND tmux_socket IS NULL""",
        (name, pane_id, tmux_session),
    ).fetchall()
    if len(rows) != 1:
        return None
    session_id = str(rows[0][0])
    conn.execute(
        "UPDATE agents SET tmux_socket = ? WHERE session_id = ? AND active = 1",
        (tmux_socket, session_id),
    )
    return session_id
