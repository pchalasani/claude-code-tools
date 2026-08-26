"""Stable JSON boundary tests for msg machine consumers."""

from __future__ import annotations

import json

import click
from click.testing import CliRunner

from claude_code_tools.msg.cli import json_option
from claude_code_tools.msg.json_contract import (
    agent_payload,
    delivery_payload,
    emit_json,
    message_payload,
    watcher_payload,
)
from claude_code_tools.msg.models import (
    Agent,
    AgentKind,
    ConsumerProtocol,
    Delivery,
    DeliveryState,
    Message,
    WatcherHeartbeat,
)


def test_agent_payload_is_complete_and_json_compatible():
    payload = agent_payload(
        Agent(
            session_id="agent-1",
            name="executor",
            pane_id="%2",
            tmux_session="main",
            tmux_socket="/tmp/tmux-main",
            display_addr="main:1.2",
            agent_kind=AgentKind.CODEX,
            pid=4242,
            cwd="/workspace",
            registered_at="2026-01-01T00:00:00+00:00",
            last_seen="2026-01-01T00:00:01+00:00",
            consumer_protocol=ConsumerProtocol.FIRST_MATE_V1,
            process_start_identity="linux:4242:100",
        )
    )

    assert payload == {
        "session_id": "agent-1",
        "name": "executor",
        "pane_id": "%2",
        "tmux_session": "main",
        "tmux_socket": "/tmp/tmux-main",
        "display_addr": "main:1.2",
        "agent_kind": "codex",
        "pid": 4242,
        "cwd": "/workspace",
        "registered_at": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-01T00:00:01+00:00",
        "consumer_protocol": "first-mate.v1",
        "process_start_identity": "linux:4242:100",
    }
    json.dumps(payload)


def test_message_delivery_and_watcher_payloads_use_primitive_values():
    message = Message(
        id="message-1",
        thread_id="thread-1",
        from_agent="agent-1",
        body="payload",
        created_at="2026-01-01T00:00:00+00:00",
    )
    delivery = Delivery(
        id="delivery-1",
        message_id="message-1",
        recipient_id="agent-2",
        state=DeliveryState.CLAIMED,
        claimed_by="watcher-1",
        claim_expires_at="2026-01-01T00:01:00+00:00",
        notify_attempts=2,
        last_error="retry",
        created_at="2026-01-01T00:00:01+00:00",
    )
    watcher = WatcherHeartbeat(
        watcher_id="watcher-1",
        started_at="2026-01-01T00:00:00+00:00",
        last_heartbeat="2026-01-01T00:00:10+00:00",
        pid=99,
        process_start_identity="linux:99:7",
        distribution_version="1.26.0",
        module_sha256="a" * 64,
        db_schema_version=4,
    )

    assert message_payload(message) == {
        "id": "message-1",
        "thread_id": "thread-1",
        "from_agent": "agent-1",
        "body": "payload",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    assert delivery_payload(delivery) == {
        "id": "delivery-1",
        "message_id": "message-1",
        "recipient_id": "agent-2",
        "state": "claimed",
        "claimed_by": "watcher-1",
        "claim_expires_at": "2026-01-01T00:01:00+00:00",
        "notify_attempts": 2,
        "last_error": "delivery_failed",
        "created_at": "2026-01-01T00:00:01+00:00",
        "notified_at": None,
        "read_at": None,
    }
    assert watcher_payload(watcher) == {
        "watcher_id": "watcher-1",
        "started_at": "2026-01-01T00:00:00+00:00",
        "last_heartbeat": "2026-01-01T00:00:10+00:00",
        "pid": 99,
        "process_start_identity": "linux:99:7",
        "distribution_version": "1.26.0",
        "module_sha256": "a" * 64,
        "db_schema_version": 4,
    }


def test_delivery_payload_normalizes_store_rows_without_python_repr():
    payload = delivery_payload(
        {
            "id": "delivery-2",
            "message_id": "message-2",
            "recipient_id": "agent-2",
            "state": "pending",
            "claimed_by": None,
            "claim_expires_at": None,
            "notify_attempts": 0,
            "last_error": "/private/path token=secret-canary",
            "created_at": "2026-01-01T00:00:02+00:00",
            "notified_at": None,
            "read_at": None,
        }
    )

    assert payload["state"] == "pending"
    assert payload["last_error"] == "delivery_failed"
    encoded = json.dumps(payload)
    assert "DeliveryState" not in encoded
    assert "/private/path" not in encoded
    assert "secret-canary" not in encoded


def test_emit_json_writes_one_versioned_object_and_no_stderr(capsys):
    emit_json("echo", {"z": "雪\n", "a": "é\\"})

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert captured.out == (
        '{"data": {"a": "é\\\\", "z": "雪\\n"}, '
        '"operation": "echo", "schema": "msg.cli.v1"}\n'
    )
    assert json.loads(captured.out) == {
        "schema": "msg.cli.v1",
        "operation": "echo",
        "data": {"z": "雪\n", "a": "é\\"},
    }


def test_json_option_is_a_per_command_flag():
    @click.group()
    def root():
        pass

    @root.command()
    @json_option
    def child(json_output):
        click.echo("machine" if json_output else "human")

    runner = CliRunner()
    legacy = runner.invoke(root, ["child"])
    machine = runner.invoke(root, ["child", "--json"])
    misplaced = runner.invoke(root, ["--json", "child"])

    assert legacy.exit_code == 0
    assert legacy.output == "human\n"
    assert legacy.stderr == ""
    assert machine.exit_code == 0
    assert machine.output == "machine\n"
    assert machine.stderr == ""
    assert misplaced.exit_code != 0
    assert "No such option: --json" in misplaced.output
