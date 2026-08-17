"""Retirement must win the watcher delivery race."""

from __future__ import annotations

import asyncio
import fcntl
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from claude_code_tools.msg.models import AgentKind
from claude_code_tools.msg.prompt_detect import PromptState
from claude_code_tools.msg import store as store_module
from claude_code_tools.msg import watcher as watcher_module
from claude_code_tools.msg.watcher import Watcher, run_watcher


def test_retired_agent_is_not_injected(monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    sender = watcher.store.register_agent("a", "%1", "test", AgentKind.CLAUDE)
    recipient = watcher.store.register_agent("b", "%2", "test", AgentKind.CODEX)
    thread = watcher.store.create_thread(
        "Test", sender.session_id, [sender.session_id, recipient.session_id]
    )
    watcher.store.send_message(thread.id, sender.session_id, "hello")
    claimed = watcher.store.claim_pending_deliveries(watcher.watcher_id)
    sent = []

    async def retire_while_waiting(_target, _socket):
        watcher.store.release_delivery(claimed[0]["id"], watcher.watcher_id)
        watcher.store.mark_read(recipient.session_id)
        watcher.store.retire_agent(recipient.session_id)
        return True

    async def record_send(*args):
        sent.append(args)

    monkeypatch.setattr(watcher, "_check_idle", retire_while_waiting)
    monkeypatch.setattr(watcher, "_tmux_send", record_send)
    monkeypatch.setattr(
        "claude_code_tools.msg.watcher.detect_prompt_state",
        lambda *_args: PromptState.EMPTY,
    )

    asyncio.run(watcher._deliver_to_recipient(recipient.session_id, claimed))

    assert sent == []
    assert watcher.store.list_agents("test") == [sender]


def test_read_before_delivery_does_not_wake_and_clears_lease(monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    sender = watcher.store.register_agent("a", "%1", "test", AgentKind.CLAUDE)
    recipient = watcher.store.register_agent("b", "%2", "test", AgentKind.CODEX)
    thread = watcher.store.create_thread(
        "Test", sender.session_id, [sender.session_id, recipient.session_id]
    )
    watcher.store.send_message(thread.id, sender.session_id, "already read")
    claimed = watcher.store.claim_pending_deliveries(watcher.watcher_id)
    watcher.store.mark_read(recipient.session_id)
    wakes = []

    async def record_wake(*args):
        wakes.append(args)
        return True

    monkeypatch.setattr(watcher, "_check_idle", record_wake)

    asyncio.run(watcher._deliver_to_recipient(recipient.session_id, claimed))

    assert wakes == []
    with sqlite3.connect(watcher.store.db_path) as conn:
        state, owner, expires = conn.execute(
            "SELECT state, claimed_by, claim_expires_at FROM deliveries"
        ).fetchone()
    assert (state, owner, expires) == ("read", None, None)


def test_replacement_waits_until_final_claim_finishes(monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    sender = watcher.store.register_agent("a", "%1", "test", AgentKind.CLAUDE)
    recipient = watcher.store.register_agent(
        "b", "%2", "test", AgentKind.CODEX, display_addr="test:0.1"
    )
    thread = watcher.store.create_thread(
        "Test", sender.session_id, [sender.session_id, recipient.session_id]
    )
    watcher.store.send_message(thread.id, sender.session_id, "hello")
    claimed = watcher.store.claim_pending_deliveries(watcher.watcher_id)
    claim_is_current = watcher.store.claim_is_current
    checks = 0
    retirement_errors = []
    sent = []

    def try_to_replace_during_final_check(delivery_id, claimer_id):
        nonlocal checks
        current = claim_is_current(delivery_id, claimer_id)
        checks += 1
        if checks == 2:
            watcher.store.mark_read(recipient.session_id)
            try:
                watcher.store.retire_agent(recipient.session_id)
            except ValueError as exc:
                retirement_errors.append(str(exc))
        return current

    async def record_send(target, _text, _socket):
        sent.append(target)

    monkeypatch.setattr(
        watcher.store, "claim_is_current", try_to_replace_during_final_check,
    )
    monkeypatch.setattr(
        watcher, "_check_idle", lambda _target, _socket: asyncio.sleep(0, result=True)
    )
    monkeypatch.setattr(watcher, "_tmux_send", record_send)
    monkeypatch.setattr(
        "claude_code_tools.msg.watcher.detect_prompt_state",
        lambda *_args: PromptState.EMPTY,
    )

    asyncio.run(watcher._deliver_to_recipient(recipient.session_id, claimed))

    assert checks == 2
    assert retirement_errors == ["agent has a delivery in flight; retry unregister"]
    assert sent == ["%2"]
    assert watcher.store.retire_agent(recipient.session_id)
    replacement = watcher.store.register_agent(
        "b", "%3", "test", AgentKind.CODEX, display_addr="test:0.1"
    )
    assert replacement.session_id != recipient.session_id
    assert watcher.store.get_agent_by_name("b", "test").pane_id == "%3"


def test_send_finishes_after_original_lease_expiry(monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    sender = watcher.store.register_agent("a", "%1", "test", AgentKind.CLAUDE)
    recipient = watcher.store.register_agent("b", "%2", "test", AgentKind.CODEX)
    thread = watcher.store.create_thread(
        "Test", sender.session_id, [sender.session_id, recipient.session_id],
    )
    watcher.store.send_message(thread.id, sender.session_id, "hello")
    claimed = watcher.store.claim_pending_deliveries(
        watcher.watcher_id, claim_duration_secs=1,
    )
    original_expiry = datetime.fromisoformat(claimed[0]["claim_expires_at"])
    expiries = []

    async def finish_after_original_expiry(*_args):
        with sqlite3.connect(watcher.store.db_path) as conn:
            renewed = conn.execute(
                "SELECT claim_expires_at FROM deliveries"
            ).fetchone()[0]
        expiries.append(datetime.fromisoformat(renewed))
        future = original_expiry + timedelta(seconds=2)
        monkeypatch.setattr(store_module, "_now_iso", future.isoformat)

    monkeypatch.setattr(
        watcher, "_check_idle", lambda *_args: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(watcher, "_tmux_send", finish_after_original_expiry)
    monkeypatch.setattr(
        "claude_code_tools.msg.watcher.detect_prompt_state",
        lambda *_args: PromptState.EMPTY,
    )

    asyncio.run(watcher._deliver_to_recipient(recipient.session_id, claimed))

    assert expiries[0] > original_expiry + timedelta(seconds=2)
    with sqlite3.connect(watcher.store.db_path) as conn:
        assert conn.execute(
            "SELECT state, claimed_by, claim_expires_at FROM deliveries"
        ).fetchone() == ("notified", None, None)


def test_same_pane_id_on_two_tmux_servers_stays_socket_scoped(monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    sender = watcher.store.register_agent(
        "a", "%1", "shared", AgentKind.CLAUDE, "/tmp/tmux-a"
    )
    recipients = [
        watcher.store.register_agent(
            name, "%2", "shared", AgentKind.CODEX, socket
        )
        for name, socket in (("b", "/tmp/tmux-a"), ("c", "/tmp/tmux-b"))
    ]
    thread = watcher.store.create_thread(
        "Test",
        sender.session_id,
        [sender.session_id, *(r.session_id for r in recipients)],
    )
    watcher.store.send_message(thread.id, sender.session_id, "hello")
    claimed = watcher.store.claim_pending_deliveries(watcher.watcher_id)
    seen = []

    async def idle(target, socket):
        seen.append(("idle", target, socket))
        return True

    def prompt(target, _kind, socket):
        seen.append(("prompt", target, socket))
        return PromptState.EMPTY

    async def send(target, _text, socket):
        seen.append(("send", target, socket))

    monkeypatch.setattr(watcher, "_check_idle", idle)
    monkeypatch.setattr(watcher, "_tmux_send", send)
    monkeypatch.setattr("claude_code_tools.msg.watcher.detect_prompt_state", prompt)

    for recipient in recipients:
        deliveries = [d for d in claimed if d["recipient_id"] == recipient.session_id]
        asyncio.run(watcher._deliver_to_recipient(recipient.session_id, deliveries))

    for operation in ("idle", "prompt", "send"):
        assert {(target, socket) for op, target, socket in seen if op == operation} == {
            ("%2", "/tmp/tmux-a"),
            ("%2", "/tmp/tmux-b"),
        }


def test_tmux_cli_uses_registered_socket(monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    calls = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def create_subprocess(*args, **_kwargs):
        calls.append((args, _kwargs))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)

    async def exercise():
        assert await watcher._check_idle("%2", "/tmp/tmux-b")
        await watcher._tmux_send("%2", "/prompts:msg:inbox", "/tmp/tmux-b")

    asyncio.run(exercise())

    assert calls[0][0][:2] == ("tmux-cli", "wait_idle")
    assert calls[1][0][:2] == ("tmux-cli", "send")
    assert {kwargs["env"]["TMUX"] for _, kwargs in calls} == {
        "/tmp/tmux-b,0,0"
    }


@pytest.mark.parametrize(
    ("agent_kind", "expected"),
    (
        ("codex", "You have a new inter-agent message. Run msg inbox now."),
        ("claude", "/msg:inbox"),
    ),
)
def test_notification_command_matches_agent(agent_kind, expected, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))

    assert watcher._build_notification(agent_kind) == expected


@pytest.mark.parametrize("stage", ("timeout", "cancellation"))
def test_wait_idle_timeout_and_cancellation_cleanup(stage, monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    events = []
    started = asyncio.Event()

    class Process:
        returncode = None

        async def communicate(self):
            events.append("communicate")
            if events.count("communicate") == 1:
                started.set()
                await asyncio.Future()
            self.returncode = -9
            return b"", b""

        def kill(self):
            events.append("kill")

    process = Process()

    async def create_subprocess(*_args, **_kwargs):
        return process

    async def exercise():
        task = asyncio.create_task(
            watcher._check_idle("%2", "/tmp/tmux")
        )
        if stage == "cancellation":
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert not await task

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(watcher_module, "IDLE_CHECK_TIMEOUT", 0.01)
    monkeypatch.setattr(watcher_module, "IDLE_CLEANUP_TIMEOUT", 0.01)

    before = time.monotonic()
    asyncio.run(exercise())
    assert time.monotonic() - before < 0.5
    assert events == ["communicate", "kill", "communicate"]


@pytest.mark.parametrize("stage", ("creation", "communication", "cancellation"))
def test_send_timeout_kills_and_reaps_child(stage, monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))
    events = []
    started = asyncio.Event()

    class Process:
        returncode = None

        async def communicate(self):
            events.append("communicate")
            if events.count("communicate") == 1:
                started.set()
                await asyncio.Future()
            self.returncode = -9
            return b"", b""

        def kill(self):
            events.append("kill")

    process = Process()

    async def create_subprocess(*_args, **_kwargs):
        events.append("create")
        if stage == "creation":
            await asyncio.Future()
        return process

    async def exercise():
        task = asyncio.create_task(
            watcher._tmux_send("%2", "/msg:inbox", "/tmp/tmux")
        )
        if stage == "cancellation":
            await started.wait()
            task.cancel()
        expected = asyncio.CancelledError if stage == "cancellation" else TimeoutError
        with pytest.raises(expected):
            await task

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setattr(watcher_module, "SEND_TIMEOUT", 0.01)
    monkeypatch.setattr(watcher_module, "SEND_CLEANUP_TIMEOUT", 0.01)

    before = time.monotonic()
    asyncio.run(exercise())
    assert time.monotonic() - before < 0.5
    if stage == "creation":
        assert events == ["create"]
    else:
        assert events == ["create", "communicate", "kill", "communicate"]
    assert watcher_module.SEND_LEASE_SECS > watcher_module.SEND_MAX_SECS


def test_missing_tmux_socket_fails_closed(monkeypatch, tmp_path):
    watcher = Watcher(str(tmp_path / "msg.db"))

    async def unexpected_subprocess(*_args, **_kwargs):
        pytest.fail("tmux-cli must not inherit the watcher's server")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_subprocess)

    assert not asyncio.run(watcher._check_idle("%2", None))
    with pytest.raises(RuntimeError, match="tmux socket is missing"):
        asyncio.run(watcher._tmux_send("%2", "/prompts:msg:inbox", None))


def test_second_watcher_exits_without_starting(monkeypatch, tmp_path):
    db = tmp_path / "msg.db"
    lock = open(f"{db}.watcher.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(
        asyncio, "run", lambda _coroutine: pytest.fail("watcher started"),
    )

    try:
        run_watcher(str(db))
    finally:
        lock.close()
