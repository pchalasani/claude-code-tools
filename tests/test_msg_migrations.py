"""Real SQLite coverage for msg database migrations."""

from __future__ import annotations

import sqlite3
import threading

from claude_code_tools.msg.migrations import initialize_database
from claude_code_tools.msg.models import AgentKind
from claude_code_tools.msg.store import MsgStore


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


def test_version_two_repairs_claim_metadata_without_losing_terminals(tmp_path):
    path = tmp_path / "claims.db"
    store = MsgStore(str(path))
    sender = store.register_agent("sender", "%1", "work", AgentKind.CLAUDE)
    recipient = store.register_agent("recipient", "%2", "work", AgentKind.CODEX)
    thread = store.create_thread(
        "work", sender.session_id, [sender.session_id, recipient.session_id],
    )
    for body in ("notified", "no-owner", "no-expiry", "valid"):
        store.send_message(thread.id, sender.session_id, body)
    with sqlite3.connect(path) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM deliveries ORDER BY id")]
        values = (
            ("notified", "stale", "2000", ids[0]),
            ("claimed", None, "2099", ids[1]),
            ("claimed", "watcher", None, ids[2]),
            ("claimed", "watcher", "2099", ids[3]),
        )
        conn.executemany(
            """UPDATE deliveries SET state = ?, claimed_by = ?,
                claim_expires_at = ? WHERE id = ?""",
            values,
        )
        conn.execute("PRAGMA user_version = 1")

    MsgStore(str(path))

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """SELECT state, claimed_by, claim_expires_at FROM deliveries
            ORDER BY id"""
        ).fetchall()
    assert rows == [
        ("notified", None, None),
        ("pending", None, None),
        ("pending", None, None),
        ("claimed", "watcher", "2099"),
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "agents" in tables
