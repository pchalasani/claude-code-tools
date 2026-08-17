"""Tests for msg-hook CLI commands."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from claude_code_tools.msg.hooks import _find_self_agent, cli
from claude_code_tools.msg.models import Agent, AgentKind
from claude_code_tools.msg.store import MsgStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_msg.db")
    return MsgStore(db_path=db_path)


@pytest.fixture
def setup_agents(store):
    """Register two agents and create a thread with
    a pending message."""
    a = store.register_agent(
        name="sender",
        pane_id="%1",
        tmux_session="test",
        agent_kind=AgentKind.CLAUDE,
    )
    b = store.register_agent(
        name="receiver",
        pane_id="%2",
        tmux_session="test",
        agent_kind=AgentKind.CLAUDE,
    )
    thread = store.create_thread(
        title="Test",
        created_by=a.session_id,
        participant_ids=[a.session_id, b.session_id],
    )
    store.send_message(
        thread_id=thread.id,
        from_agent=a.session_id,
        body="Hello receiver!",
    )
    return a, b, store


def test_find_self_agent_includes_tmux_socket(store):
    store.register_agent("server-a", "%2", "shared", AgentKind.CODEX, "/tmp/a")
    expected = store.register_agent(
        "server-b", "%2", "shared", AgentKind.CODEX, "/tmp/b"
    )

    with patch.dict(
        os.environ,
        {"TMUX_PANE": "%2", "TMUX": "/tmp/b,123,0"},
    ), patch("claude_code_tools.msg.hooks.subprocess.run") as run:
        run.return_value.stdout = "shared\n"
        found = _find_self_agent(store)

    assert found.session_id == expected.session_id
    assert run.call_args.args[0][:3] == ["tmux", "-S", "/tmp/b"]


def test_find_self_agent_fails_closed_for_duplicate_pane(monkeypatch, store):
    matches = [
        Agent(name=name, pane_id="%2", tmux_session="shared", tmux_socket="/tmp/b")
        for name in ("first", "duplicate")
    ]
    monkeypatch.setattr(
        store, "list_agents", lambda tmux_session=None, tmux_socket=None: matches,
    )

    with patch.dict(
        os.environ,
        {"TMUX_PANE": "%2", "TMUX": "/tmp/b,123,0"},
    ), patch("claude_code_tools.msg.hooks.subprocess.run") as run:
        run.return_value.stdout = "shared\n"
        found = _find_self_agent(store)

    assert found is None


class TestStopHook:

    def test_no_messages_approves(self, store):
        """When no messages, hook outputs approve."""
        store.register_agent(
            name="lonely",
            pane_id="%99",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        runner = CliRunner()
        with patch.dict(os.environ, {"TMUX_PANE": "%99"}), \
             patch(
                 "claude_code_tools.msg.hooks.MsgStore",
                 return_value=store,
             ), \
             patch(
                 "claude_code_tools.msg.hooks"
                 "._find_self_agent",
             ) as mock_find:
            agents = store.list_agents(
                tmux_session="test",
            )
            mock_find.return_value = agents[0]
            result = runner.invoke(
                cli, ["stop"],
                input=json.dumps({}),
            )
        assert result.output == ""

    def test_with_messages_notifies(self, setup_agents):
        """When messages exist, hook injects context."""
        a, b, store = setup_agents
        runner = CliRunner()
        with patch.dict(os.environ, {"TMUX_PANE": "%2"}), \
             patch(
                 "claude_code_tools.msg.hooks.MsgStore",
                 return_value=store,
             ), \
             patch(
                 "claude_code_tools.msg.hooks"
                 "._find_self_agent",
                 return_value=b,
             ):
            result = runner.invoke(
                cli, ["stop"],
                input=json.dumps({}),
            )
        output = json.loads(result.output)
        assert "hookSpecificOutput" in output
        ctx = output["hookSpecificOutput"]
        assert ctx["hookEventName"] == "Stop"
        assert "unread" in ctx["additionalContext"]
        assert "sender" in ctx["additionalContext"]

    def test_claims_only_this_hooks_recipient(self, store):
        sender = store.register_agent(
            "sender", "%1", "test", AgentKind.CLAUDE,
        )
        first = store.register_agent(
            "first", "%2", "test", AgentKind.CLAUDE,
        )
        second = store.register_agent(
            "second", "%3", "test", AgentKind.CODEX,
        )
        thread = store.create_thread(
            "Test", sender.session_id,
            [sender.session_id, first.session_id, second.session_id],
        )
        store.send_message(thread.id, sender.session_id, "hello both")

        with patch(
            "claude_code_tools.msg.hooks.MsgStore", return_value=store,
        ), patch(
            "claude_code_tools.msg.hooks._find_self_agent", return_value=first,
        ):
            result = CliRunner().invoke(cli, ["stop"], input="{}")

        assert "hookSpecificOutput" in json.loads(result.output)
        claimed = store.claim_pending_deliveries("watcher")
        assert [item["recipient_id"] for item in claimed] == [second.session_id]

    def test_already_notified_message_does_not_notify_again(self, setup_agents):
        """Unread deliveries produce only one notification."""
        _sender, receiver, store = setup_agents
        claimed = store.claim_pending_deliveries(
            "watcher", recipient_id=receiver.session_id,
        )
        store.mark_notified(claimed[0]["id"], "watcher")

        with patch(
            "claude_code_tools.msg.hooks.MsgStore", return_value=store,
        ), patch(
            "claude_code_tools.msg.hooks._find_self_agent",
            return_value=receiver,
        ):
            result = CliRunner().invoke(cli, ["stop"], input="{}")

        assert result.output == ""


class TestPromptSubmitHook:

    def test_with_messages_notifies(self, setup_agents):
        """UserPromptSubmit hook injects context."""
        a, b, store = setup_agents
        runner = CliRunner()
        with patch.dict(os.environ, {"TMUX_PANE": "%2"}), \
             patch(
                 "claude_code_tools.msg.hooks.MsgStore",
                 return_value=store,
             ), \
             patch(
                 "claude_code_tools.msg.hooks"
                 "._find_self_agent",
                 return_value=b,
             ):
            result = runner.invoke(
                cli, ["prompt-submit"],
                input=json.dumps({}),
            )
        output = json.loads(result.output)
        ctx = output["hookSpecificOutput"]
        assert ctx["hookEventName"] == "UserPromptSubmit"
        assert "msg inbox" in ctx["additionalContext"]
