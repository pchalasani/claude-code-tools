"""Tests for msg-hook CLI commands."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from claude_code_tools.msg.hooks import cli
from claude_code_tools.msg.models import AgentKind
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
        output = json.loads(result.output)
        assert output == {"decision": "approve"}

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
