"""SQLite storage layer for the msg inter-agent messaging system."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections.abc import Callable

from .migrations import adopt_unique_legacy_registration, initialize_database
from .models import (
    Agent,
    AgentKind,
    ConsumerProtocol,
    ContinuationState,
    ContinuationStatus,
    Message,
    Thread,
    RegistrationIdentity,
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

MAX_MESSAGE_BODY_BYTES = 65_536
MAX_AGENT_NAME_BYTES = 256
MAX_SENDER_NAME_BYTES = MAX_AGENT_NAME_BYTES
DEFAULT_PEEK_LIMIT = 50
MAX_PEEK_LIMIT = 100


def validate_message_body(body: str) -> None:
    """Reject non-text, invalid UTF-8, or over-limit message bodies."""
    if not isinstance(body, str):
        raise ValueError("message body must be text")
    try:
        body_bytes = len(body.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("message body must be valid UTF-8") from exc
    if body_bytes > MAX_MESSAGE_BODY_BYTES:
        raise ValueError(
            f"message body exceeds {MAX_MESSAGE_BODY_BYTES} UTF-8 bytes"
        )


def _validate_agent_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("agent name must be non-empty text")
    try:
        size = len(name.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("agent name must be valid UTF-8") from exc
    if size > MAX_AGENT_NAME_BYTES:
        raise ValueError(f"agent name exceeds {MAX_AGENT_NAME_BYTES} UTF-8 bytes")


def _blob_sha256(
    conn: sqlite3.Connection, table: str, column: str, rowid: int,
) -> str:
    digest = hashlib.sha256()
    with conn.blobopen(table, column, rowid, readonly=True) as blob:
        while chunk := blob.read(65_536):
            digest.update(chunk)
    return digest.hexdigest()


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

    def get_schema_version(self) -> int:
        """Return the actual schema version of this exact database."""
        conn = self._get_conn()
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
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
        consumer_protocol: ConsumerProtocol = ConsumerProtocol.LEGACY,
        process_start_identity: str | None = None,
    ) -> Agent:
        """Register an agent, preserving active-session idempotency."""
        _validate_agent_name(name)
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
                existing_protocol = conn.execute(
                    """SELECT consumer_protocol FROM agents
                    WHERE session_id = ?""",
                    (session_id,),
                ).fetchone()["consumer_protocol"]
                if (
                    existing_protocol == ConsumerProtocol.FIRST_MATE_V1.value
                    and consumer_protocol is ConsumerProtocol.LEGACY
                    and conn.execute(
                        """SELECT 1 FROM continuation_leases
                        WHERE agent_id = ?""",
                        (session_id,),
                    ).fetchone()
                ):
                    raise ValueError(
                        "clear continuation before changing consumer protocol"
                    )
                conn.execute(
                    """UPDATE agents SET
                        pane_id = ?, display_addr = ?,
                        agent_kind = ?, pid = ?, cwd = ?,
                        last_seen = ?, tmux_socket = ?,
                        consumer_protocol = ?, process_start_identity = ?,
                        active = 1
                    WHERE session_id = ?""",
                    (
                        pane_id, display_addr,
                        agent_kind.value, pid, cwd,
                        now, tmux_socket, consumer_protocol.value,
                        process_start_identity, session_id,
                    ),
                )
            else:
                session_id = _new_uuid()
                conn.execute(
                    """INSERT INTO agents (
                        session_id, name, pane_id,
                        tmux_session, tmux_socket,
                        display_addr, agent_kind, pid, cwd,
                        registered_at, last_seen,
                        consumer_protocol, process_start_identity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id, name, pane_id,
                        tmux_session, tmux_socket,
                        display_addr, agent_kind.value,
                        pid, cwd, now, now,
                        consumer_protocol.value, process_start_identity,
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
            consumer_protocol=consumer_protocol,
            process_start_identity=process_start_identity,
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
            conn.execute(
                "DELETE FROM continuation_leases WHERE agent_id = ?",
                (session_id,),
            )
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
        agent_kind: AgentKind | None = None,
        pid: int | None = None,
        cwd: str | None = None,
        process_start_identity: str | None = None,
        replace_candidate_session_id: str | None = None,
        _failpoint: Callable[[str], None] | None = None,
    ) -> Agent:
        """Move one active identity, optionally replacing an exact candidate."""
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
                """SELECT * FROM agents WHERE active = 1 AND pane_id = ?
                AND tmux_session = ? AND (tmux_socket IS ? OR tmux_socket = ?)
                AND session_id != ?""",
                (pane_id, tmux_session, tmux_socket, tmux_socket, session_id),
            ).fetchall()
            if replace_candidate_session_id is None:
                if occupied:
                    raise ValueError("target pane already has an active msg registration")
            else:
                requested_kind = agent_kind or AgentKind(current["agent_kind"])
                expected_identity = (
                    pane_id,
                    tmux_session,
                    tmux_socket,
                    requested_kind.value,
                    pid,
                    process_start_identity,
                    cwd,
                )
                if not occupied:
                    candidate = conn.execute(
                        "SELECT * FROM agents WHERE session_id = ? AND active = 0",
                        (replace_candidate_session_id,),
                    ).fetchone()
                    current_identity = (
                        current["pane_id"],
                        current["tmux_session"],
                        current["tmux_socket"],
                        current["agent_kind"],
                        current["pid"],
                        current["process_start_identity"],
                        current["cwd"],
                    )
                    candidate_identity = (
                        candidate["pane_id"],
                        candidate["tmux_session"],
                        candidate["tmux_socket"],
                        candidate["agent_kind"],
                        candidate["pid"],
                        candidate["process_start_identity"],
                        candidate["cwd"],
                    ) if candidate else None
                    if (
                        current_identity == expected_identity
                        and candidate_identity == expected_identity
                    ):
                        conn.commit()
                        return self._row_to_agent(current)
                if len(occupied) != 1:
                    raise ValueError("replace candidate is not the sole target registration")
                candidate = occupied[0]
                if candidate["session_id"] != replace_candidate_session_id:
                    raise ValueError("replace candidate is not the target registration")
                candidate_identity = (
                    candidate["pane_id"],
                    candidate["tmux_session"],
                    candidate["tmux_socket"],
                    candidate["agent_kind"],
                    candidate["pid"],
                    candidate["process_start_identity"],
                    candidate["cwd"],
                )
                if expected_identity != candidate_identity:
                    raise ValueError("candidate identity mismatch")
                conn.execute(
                    RELEASE_EXPIRED_SQL,
                    (now, candidate["session_id"], candidate["session_id"]),
                )
                unread = conn.execute(
                    """SELECT count(*) FROM deliveries
                    WHERE recipient_id = ? AND state NOT IN ('read', 'retired')""",
                    (candidate["session_id"],),
                ).fetchone()[0]
                if unread:
                    raise ValueError(
                        f"candidate has {unread} unread delivery; drain it first"
                    )
                if _failpoint:
                    _failpoint("before_candidate_deactivate")
                conn.execute(
                    "DELETE FROM continuation_leases WHERE agent_id = ?",
                    (candidate["session_id"],),
                )
                changed = conn.execute(
                    """UPDATE agents SET active = 0, display_addr = NULL,
                        last_seen = ? WHERE session_id = ? AND active = 1""",
                    (now, candidate["session_id"]),
                ).rowcount
                if changed != 1:
                    raise ValueError("replace candidate changed concurrently")
                if _failpoint:
                    _failpoint("after_candidate_deactivate")

            next_kind = agent_kind or AgentKind(current["agent_kind"])
            next_pid = current["pid"] if pid is None else pid
            next_cwd = current["cwd"] if cwd is None else cwd
            next_start = (
                current["process_start_identity"]
                if process_start_identity is None
                else process_start_identity
            )
            conn.execute(
                """UPDATE agents SET pane_id = ?, display_addr = ?,
                    agent_kind = ?, pid = ?, cwd = ?, process_start_identity = ?,
                    last_seen = ?
                WHERE session_id = ? AND active = 1""",
                (
                    pane_id, display_addr, next_kind.value, next_pid, next_cwd,
                    next_start, _now_iso(), session_id,
                ),
            )
            if _failpoint:
                _failpoint("after_stable_update")
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

    # --- Continuation responsibility operations ---

    @staticmethod
    def _continuation_now(now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("continuation timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _continuation_expiry(now: datetime, ttl_secs: int) -> str:
        if (
            isinstance(ttl_secs, bool)
            or not isinstance(ttl_secs, int)
            or not 1 <= ttl_secs <= 120
        ):
            raise ValueError("continuation ttl_secs must be between 1 and 120")
        return (now + timedelta(seconds=ttl_secs)).isoformat()

    @staticmethod
    def _idle_continuation() -> ContinuationStatus:
        return ContinuationStatus(state=ContinuationState.IDLE)

    def _require_first_mate_agent(
        self, conn: sqlite3.Connection, caller: RegistrationIdentity,
    ) -> None:
        row = conn.execute(
            """SELECT consumer_protocol, tmux_session, tmux_socket,
                pane_id, pid, process_start_identity FROM agents
            WHERE session_id = ? AND active = 1""",
            (caller.session_id,),
        ).fetchone()
        if not row:
            raise ValueError("active registration not found")
        if row["consumer_protocol"] != ConsumerProtocol.FIRST_MATE_V1.value:
            raise ValueError("continuation requires first-mate.v1 registration")
        if (
            caller.pid is None
            or not caller.process_start_identity
            or row["tmux_session"] != caller.tmux_session
            or row["tmux_socket"] != caller.tmux_socket
            or row["pane_id"] != caller.pane_id
            or row["pid"] != caller.pid
            or row["process_start_identity"] != caller.process_start_identity
        ):
            raise ValueError("continuation registration identity mismatch")

    def set_continuation(
        self,
        caller: RegistrationIdentity,
        generation: str,
        ttl_secs: int,
        now: datetime | None = None,
    ) -> ContinuationStatus:
        """Arm or replace one responsibility generation for an active agent."""
        if (
            not isinstance(generation, str)
            or not 1 <= len(generation) <= 128
            or any(ord(character) < 32 or ord(character) == 127 for character in generation)
        ):
            raise ValueError("continuation generation must be 1-128 characters")
        current = self._continuation_now(now)
        current_iso = current.isoformat()
        expires_at = self._continuation_expiry(current, ttl_secs)
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._require_first_mate_agent(conn, caller)
            conn.execute(
                """INSERT INTO continuation_leases (
                    agent_id, generation, expires_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    generation = excluded.generation,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at""",
                (caller.session_id, generation, expires_at, current_iso),
            )
            conn.commit()
        finally:
            conn.close()
        return ContinuationStatus(
            state=ContinuationState.ACTIVE_FRESH,
            generation=generation,
            heartbeat_expires_at=expires_at,
            updated_at=current_iso,
        )

    def touch_continuation(
        self,
        caller: RegistrationIdentity,
        expected_generation: str,
        ttl_secs: int,
        now: datetime | None = None,
    ) -> ContinuationStatus:
        """Refresh an existing generation without ever creating one."""
        current = self._continuation_now(now)
        current_iso = current.isoformat()
        expires_at = self._continuation_expiry(current, ttl_secs)
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._require_first_mate_agent(conn, caller)
            row = conn.execute(
                """SELECT generation FROM continuation_leases
                WHERE agent_id = ?""",
                (caller.session_id,),
            ).fetchone()
            if row and row["generation"] != expected_generation:
                raise ValueError("continuation generation mismatch")
            changed = 0
            if row:
                changed = conn.execute(
                    """UPDATE continuation_leases
                    SET expires_at = ?, updated_at = ?
                    WHERE agent_id = ? AND generation = ?""",
                    (
                        expires_at, current_iso, caller.session_id,
                        expected_generation,
                    ),
                ).rowcount
            conn.commit()
        finally:
            conn.close()
        if not changed or not row:
            return self._idle_continuation()
        return ContinuationStatus(
            state=ContinuationState.ACTIVE_FRESH,
            generation=row["generation"],
            heartbeat_expires_at=expires_at,
            updated_at=current_iso,
        )

    def clear_continuation(
        self, caller: RegistrationIdentity, generation: str,
    ) -> bool:
        """Disarm only the caller's exact responsibility generation."""
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._require_first_mate_agent(conn, caller)
            changed = conn.execute(
                """DELETE FROM continuation_leases
                WHERE agent_id = ? AND generation = ?""",
                (caller.session_id, generation),
            ).rowcount
            conn.commit()
            return changed == 1
        finally:
            conn.close()

    def get_continuation_status(
        self, agent_id: str, now: datetime | None = None,
    ) -> ContinuationStatus:
        """Return fresh/stale armed state; expiry never implies idle."""
        current = self._continuation_now(now)
        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT c.generation, c.expires_at, c.updated_at
                FROM continuation_leases c
                JOIN agents a ON a.session_id = c.agent_id
                WHERE c.agent_id = ? AND a.active = 1
                    AND a.consumer_protocol = ?""",
                (agent_id, ConsumerProtocol.FIRST_MATE_V1.value),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return self._idle_continuation()
        try:
            expiry = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            state = ContinuationState.ACTIVE_STALE
        else:
            try:
                updated = datetime.fromisoformat(row["updated_at"])
            except (TypeError, ValueError):
                state = ContinuationState.ACTIVE_STALE
            else:
                if (
                    expiry.tzinfo is None
                    or expiry.utcoffset() is None
                    or updated.tzinfo is None
                    or updated.utcoffset() is None
                ):
                    state = ContinuationState.ACTIVE_STALE
                else:
                    expiry_utc = expiry.astimezone(timezone.utc)
                    updated_utc = updated.astimezone(timezone.utc)
                    lifetime = (expiry_utc - updated_utc).total_seconds()
                    if (
                        updated_utc > current
                        or not 1 <= lifetime <= 120
                    ):
                        state = ContinuationState.ACTIVE_STALE
                    elif expiry_utc > current:
                        state = ContinuationState.ACTIVE_FRESH
                    else:
                        state = ContinuationState.ACTIVE_STALE
        return ContinuationStatus(
            state=state,
            generation=row["generation"],
            heartbeat_expires_at=row["expires_at"],
            updated_at=row["updated_at"],
        )

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
        validate_message_body(body)
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

    def get_deliveries_for_message(self, message_id: str) -> list[dict]:
        """Return stable delivery identifiers and states for one message."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, recipient_id, state FROM deliveries
                WHERE message_id = ? ORDER BY id""",
                (message_id,),
            ).fetchall()
            return [
                {
                    "delivery_id": row["id"],
                    "recipient_id": row["recipient_id"],
                    "state": row["state"],
                }
                for row in rows
            ]
        finally:
            conn.close()

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

    def peek_inbox(
        self,
        agent_id: str,
        thread_id: str | None = None,
        limit: int = DEFAULT_PEEK_LIMIT,
        max_body_bytes: int = MAX_MESSAGE_BODY_BYTES,
    ) -> list[dict]:
        """Return one stable unread page without changing delivery state."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("peek limit must be an integer")
        if not 1 <= limit <= MAX_PEEK_LIMIT:
            raise ValueError(f"peek limit must be between 1 and {MAX_PEEK_LIMIT}")
        if (
            isinstance(max_body_bytes, bool)
            or not isinstance(max_body_bytes, int)
            or max_body_bytes < 1
        ):
            raise ValueError("max_body_bytes must be a positive integer")
        conn = self._get_conn()
        try:
            where_thread = "AND m.thread_id = ?" if thread_id else ""
            parameters: list[object] = [
                MAX_SENDER_NAME_BYTES, max_body_bytes, agent_id,
            ]
            if thread_id:
                parameters.append(thread_id)
            parameters.append(limit)
            rows = conn.execute(
                f"""SELECT
                    d.id AS delivery_id,
                    m.id AS message_id,
                    m.thread_id AS thread_id,
                    m.from_agent AS sender_session_id,
                    CASE WHEN length(CAST(
                        COALESCE(m.sender_name, a.name) AS BLOB
                    )) <= ? THEN COALESCE(m.sender_name, a.name)
                        ELSE NULL END AS sender_name,
                    length(CAST(
                        COALESCE(m.sender_name, a.name) AS BLOB
                    )) AS sender_name_bytes,
                    m.sender_name IS NOT NULL AS sender_name_is_snapshot,
                    m.rowid AS message_rowid,
                    a.rowid AS sender_agent_rowid,
                    CASE WHEN length(CAST(m.body AS BLOB)) <= ?
                        THEN m.body ELSE NULL END AS body,
                    length(CAST(m.body AS BLOB)) AS body_bytes,
                    m.created_at AS created_at,
                    d.state AS delivery_state
                FROM deliveries d
                JOIN messages m ON m.id = d.message_id
                JOIN agents a ON a.session_id = m.from_agent
                WHERE d.recipient_id = ?
                    AND d.state NOT IN ('read', 'retired')
                    {where_thread}
                ORDER BY m.created_at ASC, m.id ASC, d.id ASC
                LIMIT ?""",
                parameters,
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                body_too_large = item["body"] is None
                sender_too_large = item["sender_name"] is None
                item["body_too_large"] = body_too_large
                item["body_sha256"] = None
                item["sender_name_too_large"] = sender_too_large
                item["sender_name_sha256"] = None
                if body_too_large:
                    item["body_sha256"] = _blob_sha256(
                        conn, "messages", "body", item["message_rowid"],
                    )
                if sender_too_large:
                    if item["sender_name_is_snapshot"]:
                        table, column, rowid = (
                            "messages", "sender_name", item["message_rowid"],
                        )
                    else:
                        table, column, rowid = (
                            "agents", "name", item["sender_agent_rowid"],
                        )
                    item["sender_name_sha256"] = _blob_sha256(
                        conn, table, column, rowid,
                    )
                for internal in (
                    "message_rowid", "sender_agent_rowid", "sender_name_is_snapshot",
                ):
                    item.pop(internal)
                result.append(item)
                if body_too_large or sender_too_large:
                    break
            return result
        finally:
            conn.close()

    def ack_deliveries(
        self, agent_id: str, delivery_ids: list[str],
    ) -> list[str]:
        """Idempotently acknowledge an exact recipient-owned delivery set."""
        if not delivery_ids:
            raise ValueError("at least one delivery id is required")
        ordered_ids = list(dict.fromkeys(delivery_ids))
        if any(not isinstance(value, str) or not value for value in ordered_ids):
            raise ValueError("delivery ids must be non-empty strings")
        placeholders = ",".join("?" for _ in ordered_ids)
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"""SELECT id, recipient_id, state FROM deliveries
                WHERE id IN ({placeholders})""",
                ordered_ids,
            ).fetchall()
            by_id = {row["id"]: row for row in rows}
            if len(by_id) != len(ordered_ids):
                raise ValueError("delivery was not found")
            if any(by_id[value]["recipient_id"] != agent_id for value in ordered_ids):
                raise ValueError("delivery is not owned by current recipient")
            if any(by_id[value]["state"] == "retired" for value in ordered_ids):
                raise ValueError("retired delivery cannot be acknowledged")
            now = _now_iso()
            conn.execute(
                f"""UPDATE deliveries SET state = 'read', read_at = ?,
                    claimed_by = NULL, claim_expires_at = NULL
                WHERE recipient_id = ? AND state != 'read'
                    AND id IN ({placeholders})""",
                (now, agent_id, *ordered_ids),
            )
            conn.commit()
            return ordered_ids
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
        process_start_identity: str | None = None,
        distribution_version: str | None = None,
        module_sha256: str | None = None,
        db_schema_version: int | None = None,
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
                        last_heartbeat = ?, pid = ?,
                        process_start_identity = ?, distribution_version = ?,
                        module_sha256 = ?, db_schema_version = ?
                    WHERE watcher_id = ?""",
                    (
                        now, pid, process_start_identity,
                        distribution_version, module_sha256,
                        db_schema_version, watcher_id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO watcher_heartbeat
                        (watcher_id, started_at,
                         last_heartbeat, pid, process_start_identity,
                         distribution_version, module_sha256,
                         db_schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        watcher_id, now, now, pid,
                        process_start_identity, distribution_version,
                        module_sha256, db_schema_version,
                    ),
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
                    process_start_identity=r["process_start_identity"],
                    distribution_version=r["distribution_version"],
                    module_sha256=r["module_sha256"],
                    db_schema_version=r["db_schema_version"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def remove_watcher(self, watcher_id: str) -> bool:
        """Remove one exact watcher heartbeat after its process is gone."""
        conn = self._get_conn()
        try:
            changed = conn.execute(
                "DELETE FROM watcher_heartbeat WHERE watcher_id = ?",
                (watcher_id,),
            ).rowcount
            conn.commit()
            return changed == 1
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
            consumer_protocol=ConsumerProtocol(row["consumer_protocol"]),
            process_start_identity=row["process_start_identity"],
        )
