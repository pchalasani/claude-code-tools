"""Small CLI wiring checks."""

import os

from click.testing import CliRunner

from claude_code_tools.msg.cli import _ensure_watcher_running, cli
from claude_code_tools.msg.models import AgentKind
from claude_code_tools.msg.store import MsgStore


def test_auto_started_watcher_uses_selected_database(monkeypatch, tmp_path):
    path = tmp_path / "msg.db"
    store = MsgStore(path)
    monkeypatch.setattr(store, "is_watcher_alive", lambda: False)

    for found, expected in (
        ({"msg": "/bin/msg"}, ["/bin/msg", "--db", path, "watch"]),
        (
            {"uv": "/bin/uv"},
            ["/bin/uv", "run", "msg", "--db", path, "watch"],
        ),
    ):
        calls = []
        with monkeypatch.context() as patch:
            patch.setattr("shutil.which", lambda name: found.get(name))
            patch.setattr("subprocess.Popen", lambda argv, **_kwargs: calls.append(argv))
            _ensure_watcher_running(store)
        assert calls == [expected]


def test_unregister_exact_session(tmp_path):
    path = tmp_path / "msg.db"
    store = MsgStore(path)
    agent = store.register_agent("lane-4", "%4", "moscow", AgentKind.CODEX)

    result = CliRunner().invoke(
        cli, ["--db", str(path), "unregister", "--session-id", agent.session_id]
    )

    assert result.exit_code == 0, result.output
    assert store.get_agent_by_name("lane-4", "moscow") is None
    assert store.get_agent_by_id(agent.session_id) is not None


def test_unregister_refuses_unread_delivery(tmp_path):
    path = tmp_path / "msg.db"
    store = MsgStore(path)
    sender = store.register_agent("moscow", "%1", "moscow", AgentKind.CLAUDE)
    recipient = store.register_agent("lane-4", "%4", "moscow", AgentKind.CODEX)
    thread = store.create_thread(
        "work", sender.session_id, [sender.session_id, recipient.session_id]
    )
    store.send_message(thread.id, sender.session_id, "fix this")

    result = CliRunner().invoke(
        cli, ["--db", str(path), "unregister", "--session-id", recipient.session_id]
    )

    assert result.exit_code == 1
    assert "1 unread delivery" in result.output
    assert store.get_agent_by_name("lane-4", "moscow") is not None


def test_retarget_exact_session(monkeypatch, tmp_path):
    path = tmp_path / "msg.db"
    store = MsgStore(path)
    agent = store.register_agent(
        "lane-4", "%4", "moscow", AgentKind.CODEX, "/tmp/tmux"
    )
    monkeypatch.setattr("claude_code_tools.msg.cli._ensure_watcher_running", lambda _store: None)
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session", lambda _pane=None: "moscow"
    )
    monkeypatch.setattr("claude_code_tools.msg.cli._detect_tmux_socket", lambda: "/tmp/tmux")
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_display_addr", lambda _pane=None: "moscow:0.9"
    )

    result = CliRunner().invoke(
        cli,
        ["--db", str(path), "retarget", "--session-id", agent.session_id, "--pane", "%9"],
    )

    assert result.exit_code == 0, result.output
    moved = store.get_agent_by_id(agent.session_id)
    assert (moved.pane_id, moved.display_addr, moved.pid, moved.cwd) == (
        "%9", "moscow:0.9", os.getpid(), os.getcwd(),
    )
