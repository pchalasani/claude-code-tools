"""Real SQLite coverage for msg database migrations."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from claude_code_tools.msg.migrations import (
    CURRENT_SCHEMA_VERSION,
    UnsupportedSchemaVersion,
    initialize_database,
)
from claude_code_tools.msg.models import (
    AgentKind,
    ConsumerProtocol,
    RegistrationIdentity,
)
from claude_code_tools.msg.store import MsgStore


FROZEN_V3_SCHEMA = (
    """CREATE TABLE agents (
        session_id TEXT PRIMARY KEY, name TEXT NOT NULL,
        pane_id TEXT NOT NULL, tmux_session TEXT NOT NULL,
        tmux_socket TEXT, display_addr TEXT, agent_kind TEXT NOT NULL,
        pid INTEGER, cwd TEXT, registered_at TEXT NOT NULL,
        last_seen TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(name, tmux_session, tmux_socket))""",
    """CREATE TABLE threads (
        id TEXT PRIMARY KEY, title TEXT NOT NULL,
        created_by TEXT NOT NULL REFERENCES agents(session_id),
        created_at TEXT NOT NULL)""",
    """CREATE TABLE thread_participants (
        thread_id TEXT NOT NULL REFERENCES threads(id),
        agent_id TEXT NOT NULL REFERENCES agents(session_id),
        PRIMARY KEY (thread_id, agent_id))""",
    """CREATE TABLE messages (
        id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id),
        from_agent TEXT NOT NULL REFERENCES agents(session_id),
        sender_name TEXT, body TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE deliveries (
        id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id),
        recipient_id TEXT NOT NULL REFERENCES agents(session_id),
        state TEXT NOT NULL DEFAULT 'pending', claimed_by TEXT,
        claim_expires_at TEXT, notify_attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT, created_at TEXT NOT NULL, notified_at TEXT,
        read_at TEXT, UNIQUE(message_id, recipient_id))""",
    """CREATE TABLE watcher_heartbeat (
        watcher_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
        last_heartbeat TEXT NOT NULL, pid INTEGER NOT NULL)""",
)


def create_frozen_v3_fixture(path) -> None:
    """Create actual v3 bytes without running current migration code."""
    with sqlite3.connect(path) as conn:
        for statement in FROZEN_V3_SCHEMA:
            conn.execute(statement)
        conn.execute(
            """INSERT INTO agents (
                session_id, name, pane_id, tmux_session, agent_kind,
                registered_at, last_seen, active
            ) VALUES ('legacy-agent', 'legacy', '%1', 'main', 'claude',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00', 1)"""
        )
        conn.execute(
            """INSERT INTO threads (id, title, created_by, created_at)
            VALUES ('thread-1', 'legacy', 'legacy-agent',
                '2026-01-01T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO thread_participants (thread_id, agent_id)
            VALUES ('thread-1', 'legacy-agent')"""
        )
        conn.execute(
            """INSERT INTO messages (
                id, thread_id, from_agent, sender_name, body, created_at
            ) VALUES ('message-1', 'thread-1', 'legacy-agent', 'legacy',
                'historical', '2026-01-01T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO deliveries (
                id, message_id, recipient_id, state, created_at
            ) VALUES ('delivery-1', 'message-1', 'legacy-agent', 'pending',
                '2026-01-01T00:00:00+00:00')"""
        )
        conn.execute("PRAGMA user_version = 3")


def test_version_three_adds_first_mate_fields_without_losing_history(tmp_path):
    path = tmp_path / "legacy-v3.db"
    create_frozen_v3_fixture(path)

    migrated = MsgStore(str(path)).get_agent_by_id("legacy-agent")

    assert migrated is not None
    assert migrated.consumer_protocol is ConsumerProtocol.LEGACY
    assert migrated.process_start_identity is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT body FROM messages").fetchone()[0] == (
            "historical"
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(agents)")
        }
        watcher_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(watcher_heartbeat)")
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"consumer_protocol", "process_start_identity"} <= columns
    assert {
        "process_start_identity",
        "distribution_version",
        "module_sha256",
        "db_schema_version",
    } <= watcher_columns
    assert "continuation_leases" in tables


def test_version_four_reopen_preserves_continuation_data(tmp_path):
    path = tmp_path / "reopen-v4.db"
    store = MsgStore(str(path))
    agent = store.register_agent(
        "control", "%1", "main", AgentKind.CLAUDE,
        pid=101,
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
        process_start_identity="linux:101:1",
    )
    store.set_continuation(
        RegistrationIdentity.from_agent(agent),
        "assignment",
        ttl_secs=90,
    )

    reopened = MsgStore(str(path))

    assert reopened.get_continuation_status(agent.session_id).generation == (
        "assignment"
    )
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute(
            "SELECT count(*) FROM continuation_leases"
        ).fetchone()[0] == 1


def test_version_four_migration_failure_rolls_back_every_schema_change(tmp_path):
    path = tmp_path / "rollback-v3.db"
    create_frozen_v3_fixture(path)
    conn = sqlite3.connect(path)
    alter_calls = 0

    def deny_second_alter(action, _arg1, _arg2, _database, _trigger):
        nonlocal alter_calls
        if action == sqlite3.SQLITE_ALTER_TABLE:
            alter_calls += 1
            if alter_calls == 2:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny_second_alter)
    with pytest.raises(sqlite3.DatabaseError, match="authorized"):
        initialize_database(conn)
    conn.close()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        agent_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(agents)")
        }
        watcher_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(watcher_heartbeat)")
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert conn.execute("SELECT body FROM messages").fetchone()[0] == (
            "historical"
        )
    assert "consumer_protocol" not in agent_columns
    assert {
        "process_start_identity",
        "distribution_version",
        "module_sha256",
        "db_schema_version",
    }.isdisjoint(watcher_columns)
    assert "continuation_leases" not in tables

    reopened = MsgStore(str(path))
    assert reopened.get_agent_by_id("legacy-agent") is not None
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT body FROM messages").fetchone() == (
            "historical",
        )


def test_future_schema_is_rejected_before_database_bytes_change(tmp_path):
    path = tmp_path / "future-v5.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE future_only (marker TEXT NOT NULL)")
        conn.execute("INSERT INTO future_only VALUES ('keep')")
        conn.execute("PRAGMA user_version = 5")
    before = path.read_bytes()

    with pytest.raises(UnsupportedSchemaVersion, match="newer than supported"):
        MsgStore(str(path))

    assert path.read_bytes() == before
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT marker FROM future_only").fetchone() == (
            "keep",
        )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"future_only"}


def test_version_zero_delivery_normalization_runs_once(tmp_path):
    path = tmp_path / "origin.db"
    store = MsgStore(str(path))
    sender = store.register_agent("sender", "%1", "test", AgentKind.CLAUDE)
    recipient = store.register_agent("recipient", "%2", "test", AgentKind.CODEX)
    thread = store.create_thread(
        "work", sender.session_id, [sender.session_id, recipient.session_id],
    )
    store.send_message(thread.id, sender.session_id, "pending retry")
    store.send_message(thread.id, sender.session_id, "failed retry")
    with sqlite3.connect(path) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM deliveries")]
        conn.execute(
            """UPDATE deliveries SET state = 'pending', notify_attempts = 2,
                last_error = 'timeout', claimed_by = 'old',
                claim_expires_at = '2099-01-01T00:00:00+00:00' WHERE id = ?""",
            (ids[0],),
        )
        conn.execute(
            """UPDATE deliveries SET state = 'failed', notify_attempts = 3,
                last_error = 'gave up', claimed_by = 'old',
                claim_expires_at = '2099-01-01T00:00:00+00:00' WHERE id = ?""",
            (ids[1],),
        )
        conn.execute("PRAGMA user_version = 0")

    MsgStore(str(path))

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """SELECT state, notify_attempts, last_error, claimed_by,
                claim_expires_at FROM deliveries ORDER BY id"""
        ).fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute(
            """UPDATE deliveries SET state = 'failed', notify_attempts = 7,
                last_error = 'after migration', claimed_by = 'sentinel',
                claim_expires_at = '2099-01-01T00:00:00+00:00' WHERE id = ?""",
            (ids[0],),
        )
    assert rows == [
        ("pending", 0, None, None, None),
        ("pending", 0, None, None, None),
    ]
    assert version == CURRENT_SCHEMA_VERSION

    MsgStore(str(path))
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            """SELECT state, notify_attempts, last_error, claimed_by,
                claim_expires_at FROM deliveries WHERE id = ?""",
            (ids[0],),
        ).fetchone() == (
            "failed", 7, "after migration", "sentinel",
            "2099-01-01T00:00:00+00:00",
        )


def test_version_two_retires_ambiguous_null_socket_panes_only(tmp_path):
    path = tmp_path / "ambiguous.db"
    MsgStore(str(path))
    with sqlite3.connect(path) as conn:
        rows = (
            ("null-a", "builder", "%1", "work", None),
            ("null-b", "reviewer", "%1", "work", None),
            ("sock-a", "builder", "%1", "work", "/tmp/a"),
            ("sock-b", "builder", "%1", "work", "/tmp/b"),
        )
        conn.execute("DELETE FROM agents")
        conn.executemany(
            """INSERT INTO agents (
                session_id, name, pane_id, tmux_session, tmux_socket,
                agent_kind, registered_at, last_seen, active
            ) VALUES (?, ?, ?, ?, ?, 'codex', 'a', 'b', 1)""",
            rows,
        )
        conn.execute("PRAGMA user_version = 1")

    store = MsgStore(str(path))

    with sqlite3.connect(path) as conn:
        active = dict(
            conn.execute("SELECT session_id, active FROM agents ORDER BY session_id")
        )
    assert active == {
        "null-a": 0,
        "null-b": 0,
        "sock-a": 1,
        "sock-b": 1,
    }
    fresh = store.register_agent("builder", "%1", "work", AgentKind.CODEX)
    assert fresh.session_id not in active


def test_register_adopts_exactly_one_active_legacy_identity(tmp_path):
    path = tmp_path / "adopt.db"
    store = MsgStore(str(path))
    legacy = store.register_agent("builder", "%1", "work", AgentKind.CODEX)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """INSERT INTO agents (
                session_id, name, pane_id, tmux_session, tmux_socket,
                agent_kind, registered_at, last_seen, active
            ) VALUES ('retired-known', 'builder', '%1', 'work', '/tmp/tmux',
                'codex', 'a', 'b', 0)"""
        )

    adopted = store.register_agent(
        "builder", "%1", "work", AgentKind.CODEX, "/tmp/tmux",
    )

    assert adopted.session_id == legacy.session_id
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT tmux_socket FROM agents WHERE session_id = ?",
            (legacy.session_id,),
        ).fetchone()[0] == "/tmp/tmux"


def test_register_does_not_adopt_ambiguous_or_retired_legacy_rows(tmp_path):
    path = tmp_path / "do-not-adopt.db"
    store = MsgStore(str(path))
    retired = store.register_agent("retired", "%9", "work", AgentKind.CODEX)
    assert store.retire_agent(retired.session_id)
    with sqlite3.connect(path) as conn:
        for session_id in ("legacy-a", "legacy-b"):
            conn.execute(
                """INSERT INTO agents (
                    session_id, name, pane_id, tmux_session, tmux_socket,
                    agent_kind, registered_at, last_seen, active
                ) VALUES (?, 'builder', '%1', 'work', NULL,
                    'codex', 'a', 'b', 1)""",
                (session_id,),
            )

    fresh = store.register_agent(
        "builder", "%1", "work", AgentKind.CODEX, "/tmp/tmux",
    )
    replacement = store.register_agent(
        "retired", "%9", "work", AgentKind.CODEX, "/tmp/tmux",
    )

    assert fresh.session_id not in {"legacy-a", "legacy-b"}
    assert replacement.session_id != retired.session_id


def test_version_two_backfills_retired_historical_sender_name(tmp_path):
    path = tmp_path / "sender-name.db"
    store = MsgStore(str(path))
    sender = store.register_agent("builder", "%1", "work", AgentKind.CLAUDE)
    recipient = store.register_agent("reviewer", "%2", "work", AgentKind.CODEX)
    thread = store.create_thread(
        "work", sender.session_id, [sender.session_id, recipient.session_id],
    )
    store.send_message(thread.id, sender.session_id, "historical")
    assert store.retire_agent(sender.session_id)
    retired_name = f"builder@retired:{sender.session_id}"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE agents SET name = ? WHERE session_id = ?",
            (retired_name, sender.session_id),
        )
        conn.execute("UPDATE messages SET sender_name = NULL")
        conn.execute("PRAGMA user_version = 2")

    migrated = MsgStore(str(path))
    replacement = migrated.register_agent(
        "builder", "%3", "work", AgentKind.CLAUDE,
    )

    assert replacement.session_id != sender.session_id
    inbox = migrated.get_inbox(recipient.session_id)
    assert inbox[0]["from_name"] == "builder"


def test_version_three_repairs_malformed_leases_and_nonclaims(tmp_path):
    path = tmp_path / "claims.db"
    store = MsgStore(str(path))
    sender = store.register_agent("sender", "%1", "work", AgentKind.CLAUDE)
    recipient = store.register_agent("recipient", "%2", "work", AgentKind.CODEX)
    thread = store.create_thread(
        "work", sender.session_id, [sender.session_id, recipient.session_id],
    )
    states = (
        ("notified", "stale", "bad"),
        ("read", "stale", "bad"),
        ("failed", "stale", "bad"),
        ("pending", "stale", "bad"),
        ("claimed", None, "2099-01-01T00:00:00+00:00"),
        ("claimed", "watcher", None),
        ("claimed", "watcher", "not-a-timestamp"),
        ("claimed", "watcher", "2099-01-01"),
        ("claimed", "watcher", "2099-01-01T00:00:00"),
        ("claimed", "watcher", "2099-01-01T01:30:00+01:30"),
        ("claimed", "watcher", "2099-01-01T00:00:00+00:00"),
    )
    for index in range(len(states)):
        body = str(index)
        store.send_message(thread.id, sender.session_id, body)
    with sqlite3.connect(path) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM deliveries ORDER BY id")]
        conn.executemany(
            """UPDATE deliveries SET state = ?, claimed_by = ?,
                claim_expires_at = ? WHERE id = ?""",
            ((*state, delivery_id) for state, delivery_id in zip(states, ids)),
        )
        conn.execute("PRAGMA user_version = 2")

    MsgStore(str(path))

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """SELECT state, claimed_by, claim_expires_at FROM deliveries
            ORDER BY id"""
        ).fetchall()
    assert rows == [
        ("notified", None, None),
        ("read", None, None),
        ("failed", None, None),
        ("pending", None, None),
        ("pending", None, None),
        ("pending", None, None),
        ("pending", None, None),
        ("pending", None, None),
        ("pending", None, None),
        ("claimed", "watcher", "2099-01-01T00:00:00+00:00"),
        ("claimed", "watcher", "2099-01-01T00:00:00+00:00"),
    ]


def test_expired_release_preserves_notified_terminal_state(tmp_path):
    path = tmp_path / "release.db"
    store = MsgStore(str(path))
    sender = store.register_agent("sender", "%1", "work", AgentKind.CLAUDE)
    recipient = store.register_agent("recipient", "%2", "work", AgentKind.CODEX)
    thread = store.create_thread(
        "work", sender.session_id, [sender.session_id, recipient.session_id],
    )
    store.send_message(thread.id, sender.session_id, "already notified")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE deliveries SET state = 'notified', "
            "claimed_by = 'stale-watcher', claim_expires_at = '2000'"
        )

    assert store.release_expired_claims() == 1
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT state, claimed_by, claim_expires_at FROM deliveries"
        ).fetchone()
    assert row == ("notified", None, None)


def test_concurrent_initializers_serialize_real_connections(tmp_path):
    path = tmp_path / "concurrent.db"
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def initialize() -> None:
        conn = sqlite3.connect(path)
        try:
            barrier.wait()
            initialize_database(conn)
        except BaseException as error:
            errors.append(error)
        finally:
            conn.close()

    threads = [threading.Thread(target=initialize) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == (
            CURRENT_SCHEMA_VERSION
        )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "agents" in tables
