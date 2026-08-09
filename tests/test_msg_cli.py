"""Small CLI wiring checks."""

from click.testing import CliRunner

from claude_code_tools.msg.cli import cli
from claude_code_tools.msg.models import AgentKind
from claude_code_tools.msg.store import MsgStore


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
