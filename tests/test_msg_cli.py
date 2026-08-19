"""Small CLI wiring checks."""

from click.testing import CliRunner

from claude_code_tools.msg.cli import _ensure_watcher_running, _get_self_agent, cli
from claude_code_tools.msg.models import Agent, AgentKind
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
        "lane-4", "%4", "moscow", AgentKind.CODEX, "/tmp/tmux",
        pid=41, cwd="/original",
    )
    monkeypatch.setattr("claude_code_tools.msg.cli._ensure_watcher_running", lambda _store: None)
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session", lambda _pane=None: "moscow"
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_socket", lambda _pane=None: "/tmp/tmux",
    )
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
        "%9", "moscow:0.9", 41, "/original",
    )


def test_register_external_pane_uses_target_session_and_address(monkeypatch, tmp_path):
    path = tmp_path / "msg.db"
    detections = []
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._ensure_watcher_running", lambda _store: None,
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session",
        lambda pane=None: detections.append(("session", pane)) or "target-session",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_display_addr",
        lambda pane=None: detections.append(("address", pane)) or "target-session:1.9",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_socket",
        lambda pane=None: detections.append(("socket", pane)) or "/tmp/tmux",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--db", str(path), "register", "external", "--pane", "%9",
            "--agent", "codex",
        ],
    )

    assert result.exit_code == 0, result.output
    registered = MsgStore(path).get_agent_by_name(
        "external", "target-session", "/tmp/tmux",
    )
    assert registered is not None
    assert (registered.pane_id, registered.display_addr) == (
        "%9", "target-session:1.9",
    )
    assert detections == [
        ("session", "%9"), ("socket", "%9"), ("address", "%9"),
    ]


def test_self_resolution_includes_tmux_socket(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    store.register_agent("server-a", "%4", "shared", AgentKind.CODEX, "/tmp/a")
    expected = store.register_agent(
        "server-b", "%4", "shared", AgentKind.CODEX, "/tmp/b"
    )
    monkeypatch.setattr("claude_code_tools.msg.cli._detect_tmux_pane", lambda: "%4")
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session", lambda: "shared"
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_socket", lambda _pane=None: "/tmp/b"
    )

    assert _get_self_agent(store).session_id == expected.session_id


def test_self_resolution_fails_closed_for_duplicate_pane(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    matches = [
        Agent(name=name, pane_id="%4", tmux_session="shared", tmux_socket="/tmp/b")
        for name in ("first", "duplicate")
    ]
    monkeypatch.setattr(
        store, "list_agents", lambda tmux_session=None, tmux_socket=None: matches,
    )
    monkeypatch.setattr("claude_code_tools.msg.cli._detect_tmux_pane", lambda: "%4")
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session", lambda: "shared",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_socket", lambda _pane=None: "/tmp/b",
    )

    assert _get_self_agent(store) is None


def test_inbox_marks_only_fetched_ids_when_message_arrives_concurrently(
    monkeypatch, tmp_path,
):
    path = tmp_path / "msg.db"
    store = MsgStore(path)
    sender = store.register_agent("sender", "%1", "test", AgentKind.CLAUDE)
    recipient = store.register_agent("recipient", "%2", "test", AgentKind.CODEX)
    thread = store.create_thread(
        "work", sender.session_id, [sender.session_id, recipient.session_id]
    )
    store.send_message(thread.id, sender.session_id, "fetched first")
    original_mark_read = MsgStore.mark_read
    marked_ids = []

    def insert_then_mark(self, agent_id, thread_id=None, delivery_ids=None):
        store.send_message(thread.id, sender.session_id, "arrived concurrently")
        marked_ids.extend(delivery_ids or [])
        return original_mark_read(
            self, agent_id, thread_id=thread_id, delivery_ids=delivery_ids,
        )

    monkeypatch.setattr(
        "claude_code_tools.msg.cli._ensure_watcher_running", lambda _store: None,
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._get_self_agent", lambda _store: recipient,
    )
    monkeypatch.setattr(MsgStore, "mark_read", insert_then_mark)

    result = CliRunner().invoke(cli, ["--db", str(path), "inbox"])

    assert result.exit_code == 0, result.output
    assert "fetched first" in result.output
    assert "arrived concurrently" not in result.output
    assert len(marked_ids) == 1
    assert [message["body"] for message in store.get_inbox(recipient.session_id)] == [
        "arrived concurrently"
    ]
