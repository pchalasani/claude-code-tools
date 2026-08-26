"""Fail-closed First-mate activation marker tests."""

from __future__ import annotations

import stat
import threading
import time

from claude_code_tools.msg.activation import (
    load_activation,
    remove_activation,
    write_activation,
)
from claude_code_tools.msg.models import AgentKind, ConsumerProtocol
from claude_code_tools.msg.store import MsgStore


def test_activation_marker_is_atomic_private_and_scope_bound(tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    agent = store.register_agent(
        "control", "%2", "main", AgentKind.CODEX, "/tmp/tmux",
        pid=202,
        cwd="/repo",
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
        process_start_identity="linux:202:2",
    )

    receipt = write_activation(store.db_path, agent)
    path = receipt.path

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_activation(store.db_path, "main", "/tmp/tmux", "%2")
    assert loaded["session_id"] == agent.session_id
    assert loaded["process_start_identity"] == "linux:202:2"
    assert load_activation(store.db_path, None, "/tmp/tmux", "%2")[
        "session_id"
    ] == agent.session_id
    assert load_activation(store.db_path, "main", "/tmp/other", "%2") is None
    assert remove_activation(
        store.db_path, agent, expected_generation=receipt.generation,
    )
    assert load_activation(store.db_path, "main", "/tmp/tmux", "%2") is None


def test_loser_cleanup_cannot_delete_winner_marker(tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    loser = store.register_agent(
        "loser", "%2", "main", AgentKind.CODEX, "/tmp/tmux",
        pid=202,
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
        process_start_identity="linux:202:2",
    )
    winner = type(loser)(
        **{
            **loser.__dict__,
            "session_id": "winner-session",
            "name": "winner",
        }
    )
    loser_receipt = write_activation(store.db_path, loser)
    write_activation(store.db_path, winner)

    assert remove_activation(
        store.db_path, loser, expected_generation=loser_receipt.generation,
    ) is False
    marker = load_activation(store.db_path, "main", "/tmp/tmux", "%2")
    assert marker["session_id"] == winner.session_id


def test_old_generation_cannot_delete_new_same_session_marker(tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    agent = store.register_agent(
        "control", "%2", "main", AgentKind.CODEX, "/tmp/tmux",
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
    )
    old = write_activation(store.db_path, agent)
    new = write_activation(store.db_path, agent)

    assert remove_activation(
        store.db_path, agent, expected_generation=old.generation,
    ) is False
    marker = load_activation(store.db_path, "main", "/tmp/tmux", "%2")
    assert marker["marker_generation"] == new.generation


def test_scope_lock_prevents_winner_replacement_between_check_and_unlink(tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    agent = store.register_agent(
        "control", "%2", "main", AgentKind.CODEX, "/tmp/tmux",
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
    )
    old = write_activation(store.db_path, agent)
    checked = threading.Event()
    allow_unlink = threading.Event()
    writer_done = threading.Event()

    def pause():
        checked.set()
        assert allow_unlink.wait(timeout=2)

    remover = threading.Thread(
        target=lambda: remove_activation(
            store.db_path,
            agent,
            expected_generation=old.generation,
            _before_unlink=pause,
        )
    )
    winner = {}

    def write_winner():
        winner["receipt"] = write_activation(store.db_path, agent)
        writer_done.set()

    remover.start()
    assert checked.wait(timeout=2)
    writer = threading.Thread(target=write_winner)
    writer.start()
    time.sleep(0.05)
    assert not writer_done.is_set()
    allow_unlink.set()
    remover.join(timeout=2)
    writer.join(timeout=2)

    marker = load_activation(store.db_path, "main", "/tmp/tmux", "%2")
    assert marker["marker_generation"] == winner["receipt"].generation


def test_first_activation_fsyncs_db_parent_before_marker_directory(
    monkeypatch, tmp_path,
):
    store = MsgStore(tmp_path / "msg.db")
    agent = store.register_agent(
        "control", "%2", "main", AgentKind.CODEX, "/tmp/tmux",
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
    )
    calls = []
    monkeypatch.setattr(
        "claude_code_tools.msg.activation._fsync_directory",
        lambda path: calls.append(path),
    )

    write_activation(store.db_path, agent)

    assert calls == [tmp_path, tmp_path / "first-mate-activations"]
