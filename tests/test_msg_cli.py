"""Small CLI wiring checks."""

import json
import os
import sqlite3
import stat
import sys

import pytest
from click.testing import CliRunner

from claude_code_tools.amux.model import Agent as AmuxAgent
from claude_code_tools.msg.cli import (
    _ensure_watcher_running,
    _get_exact_self_agent,
    _get_self_agent,
    cli,
)
from claude_code_tools.msg.models import (
    Agent,
    AgentKind,
    ConsumerProtocol,
    ContinuationState,
    WatcherHeartbeat,
)
from claude_code_tools.msg.store import MsgStore
from tests.test_msg_migrations import create_frozen_v3_fixture


def machine_payload(result):
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.output)
    assert payload["schema"] == "msg.cli.v1"
    return payload


def invoke_with_token_fd(runner, argv, token=b"test-maintenance-token"):
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, token)
    finally:
        os.close(write_fd)
    try:
        return runner.invoke(cli, [*argv, "--token-fd", str(read_fd)])
    finally:
        os.close(read_fd)


def invoke_exit_with_fds(runner, db_path, token, generation):
    gates = json.dumps(
        {
            "schema": "msg.maintenance.postcheck.v1",
            "generation": generation,
            "db_wal_shm_unchanged_after_negative_mutation": True,
            "row_counts_unchanged_after_negative_mutation": True,
        }
    ).encode()
    token_read, token_write = os.pipe()
    gates_read, gates_write = os.pipe()
    try:
        os.write(token_write, token)
        os.write(gates_write, gates)
    finally:
        os.close(token_write)
        os.close(gates_write)
    try:
        return runner.invoke(
            cli,
            [
                "--db", str(db_path), "maintenance", "exit", "--json",
                "--token-fd", str(token_read), "--postcheck-fd", str(gates_read),
            ],
        )
    finally:
        os.close(token_read)
        os.close(gates_read)


def patch_cli_runtime(monkeypatch, store, self_agent=None):
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._ensure_watcher_running", lambda _store: None,
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._get_store", lambda **_kwargs: store,
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session", lambda _pane=None: "main",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_socket",
        lambda _pane=None: "/tmp/tmux-main",
    )
    if self_agent is not None:
        monkeypatch.setattr(
            "claude_code_tools.msg.cli._get_self_agent", lambda _store: self_agent,
        )
        monkeypatch.setattr(
            "claude_code_tools.msg.cli._get_exact_self_agent",
            lambda _store: self_agent,
        )


def patch_target_agent(
    monkeypatch, *, pane="main:1.2", session="main", kind="codex",
    pid=4242, cwd="/repo",
):
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.resolve_pane_agent",
        lambda _pane, _socket: AmuxAgent(
            pane=pane,
            session=session,
            kind=kind,
            pid=pid,
            cwd=cwd,
            extra={"pane_id": _pane},
        ),
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.process_start_identity",
        lambda actual_pid: f"linux:{actual_pid}:100",
    )


def test_register_json_persists_consumer_protocol(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    patch_cli_runtime(monkeypatch, store)
    patch_target_agent(monkeypatch)

    result = CliRunner().invoke(
        cli,
        [
            "register", "executor", "--pane", "%2",
            "--consumer-protocol", "first-mate.v1", "--json",
        ],
    )

    payload = machine_payload(result)
    assert payload["operation"] == "register"
    assert payload["data"]["agent"]["consumer_protocol"] == "first-mate.v1"
    loaded = store.get_agent_by_name("executor", "main", "/tmp/tmux-main")
    assert loaded.consumer_protocol is ConsumerProtocol.FIRST_MATE_V1
    assert (loaded.pid, loaded.process_start_identity, loaded.cwd) == (
        4242, "linux:4242:100", "/repo",
    )


def test_retarget_json_replaces_exact_candidate_and_refreshes_identity(
    monkeypatch, tmp_path,
):
    store = MsgStore(tmp_path / "msg.db")
    stable = store.register_agent(
        "control", "%1", "main", AgentKind.CLAUDE, "/tmp/tmux-main",
        pid=101, cwd="/old", process_start_identity="linux:101:10",
    )
    candidate = store.register_agent(
        "candidate", "%2", "main", AgentKind.CODEX, "/tmp/tmux-main",
        pid=4242, cwd="/repo", process_start_identity="linux:4242:100",
    )
    patch_cli_runtime(monkeypatch, store)
    patch_target_agent(monkeypatch)

    payload = machine_payload(
        CliRunner().invoke(
            cli,
            [
                "retarget", "--session-id", stable.session_id,
                "--pane", "%2", "--replace-candidate", candidate.session_id,
                "--json",
            ],
        )
    )

    assert payload["operation"] == "retarget"
    assert payload["data"]["agent"]["session_id"] == stable.session_id
    assert payload["data"]["agent"]["agent_kind"] == "codex"
    assert [item.session_id for item in store.list_agents("main")] == [
        stable.session_id,
    ]


def test_list_json_returns_complete_agents(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    store.register_agent(
        "control", "%1", "main", AgentKind.CLAUDE, "/tmp/tmux-main",
    )
    patch_cli_runtime(monkeypatch, store)

    payload = machine_payload(CliRunner().invoke(cli, ["list", "--json"]))

    assert payload["operation"] == "list"
    assert [agent["name"] for agent in payload["data"]["agents"]] == ["control"]


def test_status_json_reports_watcher_identity_mismatch(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    store.update_heartbeat(
        "watcher-1",
        5151,
        process_start_identity="linux:5151:88",
        distribution_version="1.25.6",
        module_sha256="old-hash",
        db_schema_version=4,
    )
    patch_cli_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.process_start_identity",
        lambda pid: f"linux:{pid}:88",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.distribution_version", lambda: "1.25.6",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.watcher_module_sha256", lambda: "new-hash",
    )

    payload = machine_payload(CliRunner().invoke(cli, ["status", "--json"]))

    assert payload["operation"] == "status"
    assert payload["data"]["watcher"]["pid"] == 5151
    assert payload["data"]["watcher_state"] == "mismatch"
    assert payload["data"]["watcher_mismatches"] == ["module_sha256"]


def test_status_json_rejects_database_schema_unsupported_by_release(
    monkeypatch, tmp_path,
):
    store = MsgStore(tmp_path / "msg.db")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("PRAGMA user_version = 3")
    store.update_heartbeat(
        "watcher-1", 5151,
        process_start_identity="linux:5151:88",
        distribution_version="1.25.6",
        module_sha256="hash",
        db_schema_version=3,
    )
    patch_cli_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.process_start_identity",
        lambda pid: f"linux:{pid}:88",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.distribution_version", lambda: "1.25.6",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.watcher_module_sha256", lambda: "hash",
    )

    payload = machine_payload(CliRunner().invoke(cli, ["status", "--json"]))

    assert payload["data"]["watcher_state"] == "mismatch"
    assert payload["data"]["watcher_mismatches"] == [
        "supported_db_schema_version",
    ]


def test_watch_status_json_uses_same_exact_identity_contract(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    store.update_heartbeat(
        "watcher-1", 5151,
        process_start_identity="linux:5151:88",
        distribution_version="1.25.6",
        module_sha256="hash",
        db_schema_version=4,
    )
    patch_cli_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.process_start_identity",
        lambda pid: f"linux:{pid}:88",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.distribution_version", lambda: "1.25.6",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.watcher_module_sha256", lambda: "hash",
    )

    payload = machine_payload(
        CliRunner().invoke(cli, ["watch", "status", "--json"])
    )

    assert payload["operation"] == "watch.status"
    assert payload["data"]["state"] == "healthy"
    assert payload["data"]["watcher"]["process_start_identity"] == (
        "linux:5151:88"
    )


def test_watch_stop_rejects_reused_pid_without_signalling(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    store.update_heartbeat(
        "watcher-1", 5151,
        process_start_identity="linux:5151:old",
        distribution_version="1.25.6",
        module_sha256="hash",
        db_schema_version=4,
    )
    patch_cli_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.process_start_identity",
        lambda _pid: "linux:5151:new",
    )
    signals = []
    monkeypatch.setattr("claude_code_tools.msg.cli.os.kill", lambda *args: signals.append(args))

    result = CliRunner().invoke(cli, ["watch", "stop", "--json"])

    assert result.exit_code != 0
    assert "process identity" in result.output
    assert signals == []


def test_watch_start_waits_for_matching_heartbeat(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    patch_cli_runtime(monkeypatch, store)
    heartbeat = WatcherHeartbeat(
        watcher_id="watcher-new",
        pid=6161,
        process_start_identity="linux:6161:99",
        distribution_version="1.25.6",
        module_sha256="hash",
        db_schema_version=4,
    )
    observations = iter(
        (
            (None, "not_running", []),
            (None, "not_running", []),
            (heartbeat, "healthy", []),
        )
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._watcher_health", lambda _store: next(observations),
    )
    spawned = []
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._spawn_watcher",
        lambda path: spawned.append(path) or 6161,
    )
    monkeypatch.setattr("claude_code_tools.msg.cli.time.sleep", lambda _secs: None)

    payload = machine_payload(
        CliRunner().invoke(cli, ["watch", "start", "--json"])
    )

    assert payload["operation"] == "watch.start"
    assert payload["data"]["started"] is True
    assert payload["data"]["watcher"]["pid"] == 6161
    assert spawned == [store.db_path]


def test_implicit_watcher_start_replaces_fresh_mismatched_identity(
    monkeypatch, tmp_path,
):
    store = MsgStore(tmp_path / "msg.db")
    heartbeat = WatcherHeartbeat(
        watcher_id="watcher-old",
        pid=6161,
        process_start_identity="linux:6161:99",
        distribution_version="old",
        module_sha256="old-hash",
        db_schema_version=4,
    )
    observations = iter(
        (
            (heartbeat, "mismatch", ["module_sha256"]),
            (heartbeat, "healthy", []),
        )
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._watcher_health", lambda _store: next(observations),
    )
    stopped = []
    spawned = []
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._stop_exact_watcher",
        lambda _store: stopped.append(heartbeat.watcher_id) or True,
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.process_start_identity",
        lambda _pid: heartbeat.process_start_identity,
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._spawn_watcher",
        lambda path: spawned.append(path) or 7171,
    )
    monkeypatch.setattr("claude_code_tools.msg.cli.time.sleep", lambda _secs: None)

    _ensure_watcher_running(store)

    assert stopped == [heartbeat.watcher_id]
    assert spawned == [store.db_path]


def test_maintenance_enter_uses_token_fd_without_initializing_database(tmp_path):
    db_path = tmp_path / "msg.db"
    result = invoke_with_token_fd(
        CliRunner(),
        ["--db", str(db_path), "maintenance", "enter", "--json"],
    )

    payload = machine_payload(result)
    sentinel = tmp_path / "msg.db.maintenance"
    assert payload["operation"] == "maintenance.enter"
    assert sentinel.exists()
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o600
    assert "test-maintenance-token" not in sentinel.read_text()
    assert not db_path.exists()


def test_maintenance_blocks_mutation_before_database_initialization(tmp_path):
    db_path = tmp_path / "msg.db"
    store = MsgStore(db_path)
    before = db_path.read_bytes()
    runner = CliRunner()
    entered = invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "enter", "--json"],
    )
    assert entered.exit_code == 0

    blocked = runner.invoke(
        cli,
        ["--db", str(db_path), "send", "nobody", "body", "--json"],
    )

    assert blocked.exit_code != 0
    assert "maintenance" in blocked.output.lower()
    assert db_path.read_bytes() == before


def test_maintenance_exit_requires_exact_token(tmp_path):
    db_path = tmp_path / "msg.db"
    MsgStore(db_path)
    runner = CliRunner()
    entered = invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "enter", "--json"],
        b"right-token",
    )
    generation = machine_payload(entered)["data"]["generation"]

    wrong = invoke_exit_with_fds(
        runner, db_path, b"wrong-token", generation,
    )
    assert wrong.exit_code != 0
    assert (tmp_path / "msg.db.maintenance").exists()

    premature = invoke_exit_with_fds(
        runner, db_path, b"right-token", generation,
    )
    assert premature.exit_code != 0
    assert (tmp_path / "msg.db.maintenance").exists()
    assert invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "migrate", "--json"],
        b"right-token",
    ).exit_code == 0
    exited = invoke_exit_with_fds(
        runner, db_path, b"right-token", generation,
    )
    assert machine_payload(exited)["data"]["exited"] is True
    assert not (tmp_path / "msg.db.maintenance").exists()


def test_maintenance_machine_error_does_not_expose_database_path(tmp_path):
    db_path = tmp_path / "private" / "msg.db"
    runner = CliRunner()
    assert invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "enter", "--json"],
    ).exit_code == 0

    duplicate = invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "enter", "--json"],
    )

    payload = json.loads(duplicate.stdout)
    assert duplicate.exit_code != 0
    assert payload["error"]["code"] == "maintenance_already_active"
    assert str(tmp_path) not in duplicate.stdout


def test_maintenance_migrate_upgrades_only_with_exact_token(tmp_path):
    db_path = tmp_path / "msg.db"
    create_frozen_v3_fixture(db_path)
    runner = CliRunner()
    assert invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "enter", "--json"],
        b"migration-token",
    ).exit_code == 0

    migrated = invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "migrate", "--json"],
        b"migration-token",
    )

    payload = machine_payload(migrated)
    assert payload["data"] == {"from_schema_version": 3, "to_schema_version": 4}


def test_maintenance_future_schema_is_untouched(tmp_path):
    db_path = tmp_path / "msg.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 999")
    connection.commit()
    connection.close()
    before = db_path.read_bytes()
    runner = CliRunner()
    assert invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "enter", "--json"],
        b"future-token",
    ).exit_code == 0

    result = invoke_with_token_fd(
        runner,
        ["--db", str(db_path), "maintenance", "migrate", "--json"],
        b"future-token",
    )

    assert result.exit_code != 0
    assert db_path.read_bytes() == before


@pytest.mark.parametrize(
    ("argv", "operation"),
    (
        (["register", "--json"], "register"),
        (["list", "--unknown", "--json"], "list"),
        (
            ["register", "agent", "--consumer-protocol", "future", "--json"],
            "register",
        ),
    ),
)
def test_machine_parse_errors_are_one_versioned_json_object(argv, operation):
    result = CliRunner().invoke(cli, argv)

    assert result.exit_code != 0
    assert "Usage:" not in result.stdout
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema"] == "msg.cli.v1"
    assert payload["operation"] == operation
    assert payload["error"]["code"] in {"usage_error", "invalid_value"}


def test_send_json_returns_message_and_delivery_ids(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    sender = store.register_agent(
        "control", "%1", "main", AgentKind.CLAUDE, "/tmp/tmux-main",
    )
    recipient = store.register_agent(
        "executor", "%2", "main", AgentKind.CODEX, "/tmp/tmux-main",
    )
    patch_cli_runtime(monkeypatch, store, self_agent=sender)
    monkeypatch.setattr(store, "is_watcher_alive", lambda: True)

    result = CliRunner().invoke(
        cli, ["send", recipient.name, '{"protocol":"first-mate.v1"}', "--json"],
    )

    payload = machine_payload(result)
    assert payload["operation"] == "send"
    assert payload["data"]["message_id"]
    assert len(payload["data"]["delivery_ids"]) == 1
    assert "first-mate.v1" not in result.stderr


def test_continuation_json_lifecycle_uses_exact_registration(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    agent = store.register_agent(
        "control", "%1", "main", AgentKind.CLAUDE, "/tmp/tmux-main",
        pid=101,
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
        process_start_identity="linux:101:1",
    )
    patch_cli_runtime(monkeypatch, store, self_agent=agent)
    runner = CliRunner()

    armed = machine_payload(
        runner.invoke(
            cli,
            [
                "continuation", "set", "--generation", "assignment-1",
                "--ttl", "90", "--json",
            ],
        )
    )
    touched = machine_payload(
        runner.invoke(
            cli,
            [
                "continuation", "touch", "--generation", "assignment-1",
                "--ttl", "90", "--json",
            ],
        )
    )
    status = machine_payload(
        runner.invoke(cli, ["continuation", "status", "--json"])
    )
    cleared = machine_payload(
        runner.invoke(
            cli,
            [
                "continuation", "clear", "--generation", "assignment-1",
                "--json",
            ],
        )
    )

    assert armed["data"]["continuation"]["state"] == "active_fresh"
    assert touched["data"]["continuation"]["generation"] == "assignment-1"
    assert status["data"]["continuation"]["state"] == (
        ContinuationState.ACTIVE_FRESH.value
    )
    assert cleared["data"] == {"cleared": True}


def test_continuation_machine_validation_is_stable_error(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    agent = store.register_agent(
        "control", "%1", "main", AgentKind.CLAUDE, "/tmp/tmux-main",
        pid=101,
        consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
        process_start_identity="linux:101:1",
    )
    patch_cli_runtime(monkeypatch, store, self_agent=agent)

    result = CliRunner().invoke(
        cli,
        [
            "continuation", "set", "--generation", "x" * 129,
            "--ttl", "90", "--json",
        ],
    )

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "continuation_rejected"
    assert "x" * 129 not in result.stdout


def test_auto_started_watcher_uses_selected_database(monkeypatch, tmp_path):
    path = tmp_path / "msg.db"
    store = MsgStore(path)
    heartbeat = WatcherHeartbeat(pid=123)
    observations = iter(
        ((None, "not_running", []), (heartbeat, "healthy", []))
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._watcher_health", lambda _store: next(observations),
    )
    monkeypatch.setattr("claude_code_tools.msg.cli.time.sleep", lambda _secs: None)
    calls = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda argv, **_kwargs: (
            calls.append(argv) or type("Process", (), {"pid": 123})()
        ),
    )

    _ensure_watcher_running(store)

    assert calls == [[
        sys.executable,
        "-m",
        "claude_code_tools.msg.cli",
        "--db",
        str(path),
        "watch",
    ]]


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
    patch_target_agent(
        monkeypatch, pane="moscow:0.9", session="moscow", kind="codex",
        pid=99, cwd="/moved",
    )

    result = CliRunner().invoke(
        cli,
        ["--db", str(path), "retarget", "--session-id", agent.session_id, "--pane", "%9"],
    )

    assert result.exit_code == 0, result.output
    moved = store.get_agent_by_id(agent.session_id)
    assert (moved.pane_id, moved.display_addr, moved.pid, moved.cwd) == (
        "%9", "moscow:0.9", 99, "/moved",
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
        "claude_code_tools.msg.cli._detect_tmux_socket",
        lambda pane=None: detections.append(("socket", pane)) or "/tmp/tmux",
    )
    patch_target_agent(
        monkeypatch,
        pane="target-session:1.9",
        session="target-session",
        kind="codex",
        pid=99,
        cwd="/target",
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
    assert detections == [("socket", "%9")]


def test_register_uses_session_from_exact_pane_scan(monkeypatch, tmp_path):
    path = tmp_path / "msg.db"
    store = MsgStore(path)
    patch_cli_runtime(monkeypatch, store)
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session", lambda _pane=None: "wrong",
    )
    patch_target_agent(
        monkeypatch,
        pane="target:1.9",
        session="target",
        kind="codex",
        pid=99,
        cwd="/target",
    )

    result = CliRunner().invoke(
        cli, ["register", "external", "--pane", "%9", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert store.get_agent_by_name("external", "target", "/tmp/tmux-main")
    assert store.get_agent_by_name("external", "wrong", "/tmp/tmux-main") is None


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


def test_exact_self_resolution_rejects_pid_reuse(monkeypatch, tmp_path):
    store = MsgStore(tmp_path / "msg.db")
    store.register_agent(
        "control", "%4", "shared", AgentKind.CODEX, "/tmp/b",
        pid=404, cwd="/repo", process_start_identity="linux:404:old",
    )
    monkeypatch.setattr("claude_code_tools.msg.cli._detect_tmux_pane", lambda: "%4")
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_session", lambda: "shared",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli._detect_tmux_socket", lambda _pane=None: "/tmp/b",
    )
    patch_target_agent(
        monkeypatch,
        pane="shared:1.4",
        session="shared",
        kind="codex",
        pid=404,
        cwd="/repo",
    )
    monkeypatch.setattr(
        "claude_code_tools.msg.cli.process_start_identity",
        lambda _pid: "linux:404:new",
    )

    assert _get_exact_self_agent(store) is None


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
