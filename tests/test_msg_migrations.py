"""Real SQLite coverage for msg database migrations."""

from __future__ import annotations

import sqlite3
import threading

from claude_code_tools.msg.migrations import (
    CURRENT_SCHEMA_VERSION,
    initialize_database,
)
from claude_code_tools.msg.models import AgentKind
from claude_code_tools.msg.store import MsgStore


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
