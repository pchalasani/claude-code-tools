"""Retirement must win the watcher delivery race."""

from __future__ import annotations

import asyncio
import fcntl

import pytest

from claude_code_tools.msg.models import AgentKind
from claude_code_tools.msg.prompt_detect import PromptState
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

    async def retire_while_waiting(_target):
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


def test_second_watcher_exits_without_starting(monkeypatch, tmp_path):
    db = tmp_path / "msg.db"
    lock = open(f"{db}.watcher.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(asyncio, "run", lambda _coroutine: pytest.fail("watcher started"))

    try:
        run_watcher(str(db))
    finally:
        lock.close()
