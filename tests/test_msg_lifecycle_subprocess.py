"""Real-process acceptance tests for msg lifecycle contracts."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from claude_code_tools.amux.scan import resolve_pane_agent
from claude_code_tools.msg.models import AgentKind
from claude_code_tools.msg.watcher import distribution_version, watcher_module_sha256
from claude_code_tools.process_identity import process_start_identity
from claude_code_tools.msg.store import MsgStore
from tests.test_msg_migrations import create_frozen_v3_fixture

ROOT = Path(__file__).resolve().parents[1]


def run_msg(
    argv: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "claude_code_tools.msg.cli", *argv],
        cwd=cwd,
        env=env,
        pass_fds=pass_fds,
        capture_output=True,
        text=True,
        timeout=15,
    )


def json_result(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(result.stdout.splitlines()) == 1
    return json.loads(result.stdout)


def pipe_bytes(data: bytes) -> int:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    return read_fd


def file_snapshot(db_path: Path) -> dict[str, bytes | None]:
    snapshot = {}
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        snapshot[suffix] = candidate.read_bytes() if candidate.exists() else None
    return snapshot


def row_count_snapshot(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(
        f"file:{db_path}?mode=ro&immutable=1", uri=True,
    )
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: connection.execute(
                f'SELECT count(*) FROM "{table}"'
            ).fetchone()[0]
            for table in tables
        }
    finally:
        connection.close()


def stop_watcher(db_path: Path, *, cwd: Path = ROOT, env=None) -> None:
    run_msg(["--db", str(db_path), "watch", "stop", "--json"], cwd=cwd, env=env)


@contextmanager
def disposable_tmux(tmp_path: Path, kinds: tuple[str, ...]):
    if shutil.which("tmux") is None:
        pytest.skip("tmux is required for real-pane acceptance")
    socket_path = Path(f"/tmp/cc-msg-{uuid.uuid4().hex}.sock")
    session = f"msg-{uuid.uuid4().hex[:10]}"
    panes = []
    try:
        for index, kind in enumerate(kinds):
            window = f"p{index}"
            command = f"bash -c 'exec -a {kind} sleep 120' & wait"
            if index == 0:
                argv = [
                    "tmux", "-S", str(socket_path), "new-session", "-d",
                    "-s", session, "-n", window, "-c", str(tmp_path), command,
                ]
            else:
                argv = [
                    "tmux", "-S", str(socket_path), "new-window", "-d",
                    "-t", session, "-n", window, "-c", str(tmp_path), command,
                ]
            subprocess.run(argv, check=True, capture_output=True, text=True)
            pane = subprocess.run(
                [
                    "tmux", "-S", str(socket_path), "display-message", "-p",
                    "-t", f"{session}:{window}", "#{pane_id}",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            panes.append(pane)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(resolve_pane_agent(pane, str(socket_path)) for pane in panes):
                break
            time.sleep(0.05)
        else:
            pytest.fail("disposable tmux harness processes were not discoverable")
        yield socket_path, session, panes
    finally:
        subprocess.run(
            ["tmux", "-S", str(socket_path), "kill-server"],
            capture_output=True,
            text=True,
        )


@pytest.mark.parametrize(
    "tail",
    (
        ["register", "--json"],
        ["list", "--unknown", "--json"],
        ["register", "agent", "--consumer-protocol", "future", "--json"],
    ),
)
def test_real_console_parse_errors_are_one_machine_object(tmp_path, tail):
    db_path = tmp_path / "parse.db"
    try:
        result = run_msg(["--db", str(db_path), *tail])
        assert result.returncode != 0
        assert result.stderr == ""
        assert len(result.stdout.splitlines()) == 1
        payload = json.loads(result.stdout)
        assert payload["schema"] == "msg.cli.v1"
        assert "error" in payload
        assert str(tmp_path) not in result.stdout
    finally:
        stop_watcher(db_path)


def test_oversize_send_preflight_has_no_db_or_watcher_side_effect(tmp_path):
    db_path = tmp_path / "absent.db"

    result = run_msg(
        [
            "--db", str(db_path), "send", "recipient", "x" * 65537,
            "--json",
        ]
    )

    assert result.returncode != 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"]["code"] == "send_rejected"
    for suffix in ("", "-wal", "-shm", ".watcher.lock"):
        assert not Path(f"{db_path}{suffix}").exists()


def test_oversize_send_preflight_does_not_migrate_existing_v3_database(tmp_path):
    db_path = tmp_path / "legacy-v3.db"
    create_frozen_v3_fixture(db_path)
    before = file_snapshot(db_path)
    before_rows = row_count_snapshot(db_path)

    result = run_msg(
        [
            "--db", str(db_path), "send", "recipient", "x" * 65537,
            "--json",
        ]
    )

    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == "send_rejected"
    assert file_snapshot(db_path) == before
    assert row_count_snapshot(db_path) == before_rows


@pytest.mark.parametrize(
    ("source_kind", "target_kind"),
    (("claude", "claude"), ("claude", "codex"), ("codex", "claude")),
)
def test_real_tmux_register_retarget_and_continuation_identity(
    tmp_path, source_kind, target_kind,
):
    db_path = tmp_path / "pane.db"
    with disposable_tmux(tmp_path, (source_kind, target_kind)) as (
        socket_path, session, panes,
    ):
        source_pane, target_pane = panes
        base_env = os.environ.copy()
        base_env["TMUX"] = f"{socket_path},0,0"
        try:
            source = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "register", "control",
                        "--pane", source_pane, "--consumer-protocol",
                        "first-mate.v1", "--json",
                    ],
                    env=base_env,
                )
            )["data"]["agent"]
            candidate = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "register", "candidate",
                        "--pane", target_pane, "--consumer-protocol",
                        "first-mate.v1", "--json",
                    ],
                    env=base_env,
                )
            )["data"]["agent"]
            assert source["agent_kind"] == source_kind
            assert candidate["agent_kind"] == target_kind
            assert source["pid"] != os.getpid()
            assert source["process_start_identity"]

            stop_watcher(db_path, env=base_env)
            store = MsgStore(db_path)
            source_process = resolve_pane_agent(source_pane, str(socket_path))
            assert source_process is not None
            fake_watcher_id = "test-non-delivering-heartbeat"
            store.update_heartbeat(
                fake_watcher_id,
                source_process.pid,
                process_start_identity=process_start_identity(source_process.pid),
                distribution_version=distribution_version(),
                module_sha256=watcher_module_sha256(),
                db_schema_version=store.get_schema_version(),
            )
            sender = store.register_agent(
                "sender", "%999", session, AgentKind.CLAUDE, str(socket_path),
            )
            thread = store.create_thread(
                "handoff", sender.session_id,
                [sender.session_id, source["session_id"]],
            )
            store.send_message(thread.id, sender.session_id, "preserve unread")
            unread_before = store.get_inbox(source["session_id"])

            retarget_argv = [
                "--db", str(db_path), "retarget", "--session-id",
                source["session_id"], "--pane", target_pane,
                "--replace-candidate", candidate["session_id"], "--json",
            ]
            first = json_result(run_msg(retarget_argv, env=base_env))["data"]["agent"]
            retried = json_result(run_msg(retarget_argv, env=base_env))["data"]["agent"]
            assert retried == first
            assert first["session_id"] == source["session_id"]
            assert first["agent_kind"] == target_kind
            assert store.get_inbox(source["session_id"]) == unread_before
            assert [item.id for item in store.list_threads(source["session_id"])] == [
                thread.id,
            ]

            target_env = {**base_env, "TMUX_PANE": target_pane}
            for argv, operation in (
                (
                    [
                        "continuation", "set", "--generation", "g1",
                        "--ttl", "90", "--json",
                    ],
                    "continuation.set",
                ),
                (
                    [
                        "continuation", "touch", "--generation", "g1",
                        "--ttl", "90", "--json",
                    ],
                    "continuation.touch",
                ),
                (["continuation", "status", "--json"], "continuation.status"),
            ):
                payload = json_result(
                    run_msg(["--db", str(db_path), *argv], env=target_env)
                )
                assert payload["operation"] == operation

            old_env = {**base_env, "TMUX_PANE": source_pane}
            old = run_msg(
                ["--db", str(db_path), "continuation", "status", "--json"],
                env=old_env,
            )
            assert old.returncode != 0
            assert json.loads(old.stdout)["error"]["code"] == "command_error"

            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "UPDATE agents SET process_start_identity = ? WHERE session_id = ?",
                    ("linux:reused", source["session_id"]),
                )
            reused = run_msg(
                ["--db", str(db_path), "continuation", "status", "--json"],
                env=target_env,
            )
            assert reused.returncode != 0
            assert json.loads(reused.stdout)["error"]["code"] == "command_error"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "UPDATE agents SET process_start_identity = ? WHERE session_id = ?",
                    (first["process_start_identity"], source["session_id"]),
                )
            cleared = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "continuation", "clear",
                        "--generation", "g1", "--json",
                    ],
                    env=target_env,
                )
            )
            assert cleared["data"]["cleared"] is True
        finally:
            if db_path.exists():
                MsgStore(db_path).remove_watcher("test-non-delivering-heartbeat")
            stop_watcher(db_path, env=base_env)


def test_real_tmux_peek_then_explicit_ack(tmp_path):
    db_path = tmp_path / "peek.db"
    with disposable_tmux(tmp_path, ("claude", "codex")) as (
        socket_path, session, panes,
    ):
        sender_pane, recipient_pane = panes
        base_env = os.environ.copy()
        base_env["TMUX"] = f"{socket_path},0,0"
        fake_watcher_id = "test-non-delivering-heartbeat"
        try:
            sender = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "register", "sender",
                        "--pane", sender_pane, "--consumer-protocol",
                        "first-mate.v1", "--json",
                    ],
                    env=base_env,
                )
            )["data"]["agent"]
            recipient = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "register", "recipient",
                        "--pane", recipient_pane, "--consumer-protocol",
                        "first-mate.v1", "--json",
                    ],
                    env=base_env,
                )
            )["data"]["agent"]
            stop_watcher(db_path, env=base_env)
            store = MsgStore(db_path)
            sender_process = resolve_pane_agent(sender_pane, str(socket_path))
            assert sender_process is not None
            store.update_heartbeat(
                fake_watcher_id,
                sender_process.pid,
                process_start_identity=process_start_identity(sender_process.pid),
                distribution_version=distribution_version(),
                module_sha256=watcher_module_sha256(),
                db_schema_version=store.get_schema_version(),
            )
            thread = store.create_thread(
                "real peek", sender["session_id"],
                [sender["session_id"], recipient["session_id"]],
            )
            store.send_message(thread.id, sender["session_id"], "journal first")
            recipient_env = {**base_env, "TMUX_PANE": recipient_pane}

            peeked = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "inbox", "--json", "--peek",
                        "--limit", "50",
                    ],
                    env=recipient_env,
                )
            )
            row = peeked["data"]["messages"][0]
            assert row["body"] == "journal first"
            assert store.get_inbox(recipient["session_id"])

            acked = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "ack", "--delivery",
                        row["delivery_id"], "--json",
                    ],
                    env=recipient_env,
                )
            )
            assert acked["data"]["delivery_ids"] == [row["delivery_id"]]
            empty = json_result(
                run_msg(
                    [
                        "--db", str(db_path), "inbox", "--json", "--peek",
                        "--limit", "50",
                    ],
                    env=recipient_env,
                )
            )
            assert empty["data"]["messages"] == []
        finally:
            if db_path.exists():
                MsgStore(db_path).remove_watcher(fake_watcher_id)
            stop_watcher(db_path, env=base_env)


def test_real_watcher_detects_loaded_code_drift_and_replaces_daemon(tmp_path):
    package_root = tmp_path / "runtime"
    shutil.copytree(ROOT / "claude_code_tools", package_root / "claude_code_tools")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(package_root)
    db_path = tmp_path / "watch.db"
    common = ["--db", str(db_path), "watch"]

    try:
        started = json_result(run_msg([*common, "start", "--json"], cwd=tmp_path, env=env))
        old_pid = started["data"]["watcher"]["pid"]
        watcher_file = package_root / "claude_code_tools/msg/watcher.py"
        watcher_file.write_text(
            watcher_file.read_text(encoding="utf-8") + "\n# release drift\n",
            encoding="utf-8",
        )

        mismatch = json_result(
            run_msg([*common, "status", "--json"], cwd=tmp_path, env=env)
        )
        assert mismatch["data"]["state"] == "mismatch"
        assert mismatch["data"]["mismatches"] == ["module_sha256"]

        replaced = json_result(
            run_msg([*common, "start", "--json"], cwd=tmp_path, env=env)
        )
        assert replaced["data"]["started"] is True
        assert replaced["data"]["watcher"]["pid"] != old_pid
    finally:
        run_msg([*common, "stop", "--json"], cwd=tmp_path, env=env)


@pytest.mark.parametrize(
    "mutation",
    (
        ["register", "x", "--pane", "%1", "--json"],
        ["send", "x", "body", "--json"],
        ["retarget", "--session-id", "x", "--pane", "%1", "--json"],
        ["continuation", "set", "--generation", "g", "--ttl", "90", "--json"],
        ["continuation", "touch", "--generation", "g", "--ttl", "90", "--json"],
        ["continuation", "clear", "--generation", "g", "--json"],
        ["reply", "x", "body"],
        ["thread", "create", "title", "--with", "x"],
        ["unregister", "--session-id", "x"],
        ["inbox"],
        ["watch", "start", "--json"],
    ),
)
def test_maintenance_blocks_mutators_without_db_wal_shm_drift(tmp_path, mutation):
    db_path = tmp_path / "msg.db"
    create_frozen_v3_fixture(db_path)
    token_fd = pipe_bytes(b"maintenance-token")
    try:
        json_result(
            run_msg(
                [
                    "--db", str(db_path), "maintenance", "enter", "--json",
                    "--token-fd", str(token_fd),
                ],
                pass_fds=(token_fd,),
            )
        )
    finally:
        os.close(token_fd)
    before = file_snapshot(db_path)
    before_rows = row_count_snapshot(db_path)

    blocked = run_msg(["--db", str(db_path), *mutation])

    assert blocked.returncode != 0
    if "--json" in mutation:
        assert json.loads(blocked.stdout)["error"]["code"] == "command_error"
    else:
        assert "maintenance mode is active" in blocked.stderr
    assert file_snapshot(db_path) == before
    assert row_count_snapshot(db_path) == before_rows


def test_real_token_fd_migration_and_postcheck_exit(tmp_path):
    db_path = tmp_path / "legacy-v3.db"
    create_frozen_v3_fixture(db_path)
    token = b"maintenance-token"
    token_fd = pipe_bytes(token)
    try:
        entered = json_result(
            run_msg(
                [
                    "--db", str(db_path), "maintenance", "enter", "--json",
                    "--token-fd", str(token_fd),
                ],
                pass_fds=(token_fd,),
            )
        )
    finally:
        os.close(token_fd)
    generation = entered["data"]["generation"]

    token_fd = pipe_bytes(token)
    try:
        migrated = json_result(
            run_msg(
                [
                    "--db", str(db_path), "maintenance", "migrate", "--json",
                    "--token-fd", str(token_fd),
                ],
                pass_fds=(token_fd,),
            )
        )
    finally:
        os.close(token_fd)
    assert migrated["data"] == {"from_schema_version": 3, "to_schema_version": 4}

    gates = json.dumps(
        {
            "schema": "msg.maintenance.postcheck.v1",
            "generation": generation,
            "db_wal_shm_unchanged_after_negative_mutation": True,
            "row_counts_unchanged_after_negative_mutation": True,
        }
    ).encode()
    token_fd = pipe_bytes(token)
    gates_fd = pipe_bytes(gates)
    try:
        exited = json_result(
            run_msg(
                [
                    "--db", str(db_path), "maintenance", "exit", "--json",
                    "--token-fd", str(token_fd),
                    "--postcheck-fd", str(gates_fd),
                ],
                pass_fds=(token_fd, gates_fd),
            )
        )
    finally:
        os.close(token_fd)
        os.close(gates_fd)
    assert exited["data"]["exited"] is True
