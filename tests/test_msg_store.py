"""Tests for the msg store layer."""

from __future__ import annotations

import os
import tempfile

import pytest

from claude_code_tools.msg.models import (
    AgentKind,
    DeliveryState,
)
from claude_code_tools.msg.store import MsgStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh MsgStore with a temp DB."""
    db_path = str(tmp_path / "test_msg.db")
    return MsgStore(db_path=db_path)


@pytest.fixture
def two_agents(store):
    """Register two agents and return them."""
    a = store.register_agent(
        name="architect",
        pane_id="%1",
        tmux_session="test",
        agent_kind=AgentKind.CLAUDE,
        tmux_socket="/tmp/tmux-test",
        display_addr="test:1.1",
    )
    b = store.register_agent(
        name="tester",
        pane_id="%2",
        tmux_session="test",
        agent_kind=AgentKind.CODEX,
        tmux_socket="/tmp/tmux-test",
        display_addr="test:1.2",
    )
    return a, b


class TestAgentRegistration:

    def test_register_new_agent(self, store):
        agent = store.register_agent(
            name="architect",
            pane_id="%1",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        assert agent.name == "architect"
        assert agent.pane_id == "%1"
        assert agent.session_id is not None

    def test_re_register_updates_pane(self, store):
        a1 = store.register_agent(
            name="architect",
            pane_id="%1",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        a2 = store.register_agent(
            name="architect",
            pane_id="%5",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        # Same session_id, updated pane
        assert a2.session_id == a1.session_id
        assert a2.pane_id == "%5"

    def test_same_name_different_session_ok(self, store):
        a1 = store.register_agent(
            name="tester",
            pane_id="%1",
            tmux_session="session1",
            agent_kind=AgentKind.CLAUDE,
        )
        a2 = store.register_agent(
            name="tester",
            pane_id="%2",
            tmux_session="session2",
            agent_kind=AgentKind.CODEX,
        )
        assert a1.session_id != a2.session_id

    def test_get_agent_by_name(self, store, two_agents):
        a, _ = two_agents
        found = store.get_agent_by_name(
            "architect", "test", "/tmp/tmux-test",
        )
        assert found is not None
        assert found.session_id == a.session_id

    def test_get_agent_by_id(self, store, two_agents):
        a, _ = two_agents
        found = store.get_agent_by_id(a.session_id)
        assert found is not None
        assert found.name == "architect"

    def test_list_agents(self, store, two_agents):
        agents = store.list_agents(tmux_session="test")
        assert len(agents) == 2
        names = {a.name for a in agents}
        assert names == {"architect", "tester"}

    def test_list_agents_filters_by_session(
        self, store, two_agents,
    ):
        store.register_agent(
            name="other",
            pane_id="%3",
            tmux_session="other_session",
            agent_kind=AgentKind.CLAUDE,
        )
        agents = store.list_agents(tmux_session="test")
        assert len(agents) == 2

    def test_touch_agent(self, store, two_agents):
        a, _ = two_agents
        old_seen = a.last_seen
        store.touch_agent(a.session_id)
        updated = store.get_agent_by_id(a.session_id)
        assert updated.last_seen >= old_seen


class TestThreads:

    def test_create_thread(self, store, two_agents):
        a, b = two_agents
        thread = store.create_thread(
            title="Auth review",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        assert thread.title == "Auth review"
        assert thread.id is not None

    def test_get_thread(self, store, two_agents):
        a, b = two_agents
        created = store.create_thread(
            title="Test thread",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        found = store.get_thread(created.id)
        assert found is not None
        assert found.title == "Test thread"

    def test_get_thread_participants(
        self, store, two_agents,
    ):
        a, b = two_agents
        thread = store.create_thread(
            title="Test thread",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        participants = store.get_thread_participants(
            thread.id,
        )
        assert set(participants) == {
            a.session_id, b.session_id,
        }

    def test_list_threads_for_agent(
        self, store, two_agents,
    ):
        a, b = two_agents
        store.create_thread(
            title="Thread 1",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.create_thread(
            title="Thread 2",
            created_by=a.session_id,
            participant_ids=[a.session_id],
        )
        # b should only see Thread 1
        threads = store.list_threads(
            agent_id=b.session_id,
        )
        assert len(threads) == 1
        assert threads[0].title == "Thread 1"


class TestMessages:

    def test_send_message(self, store, two_agents):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        msg = store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Hello tester!",
        )
        assert msg.body == "Hello tester!"
        assert msg.thread_id == thread.id

    def test_inbox_shows_unread(self, store, two_agents):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Review this please",
        )
        inbox = store.get_inbox(b.session_id)
        assert len(inbox) == 1
        assert inbox[0]["body"] == "Review this please"
        assert inbox[0]["from_name"] == "architect"

    def test_inbox_hides_own_messages(
        self, store, two_agents,
    ):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="My own message",
        )
        # Sender should not see their own message
        inbox = store.get_inbox(a.session_id)
        assert len(inbox) == 0

    def test_inbox_filter_by_thread(
        self, store, two_agents,
    ):
        a, b = two_agents
        t1 = store.create_thread(
            title="Thread 1",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        t2 = store.create_thread(
            title="Thread 2",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=t1.id,
            from_agent=a.session_id,
            body="In thread 1",
        )
        store.send_message(
            thread_id=t2.id,
            from_agent=a.session_id,
            body="In thread 2",
        )
        inbox = store.get_inbox(
            b.session_id, thread_id=t1.id,
        )
        assert len(inbox) == 1
        assert inbox[0]["body"] == "In thread 1"

    def test_mark_read(self, store, two_agents):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Read me",
        )
        count = store.mark_read(b.session_id)
        assert count == 1

        # Inbox should be empty now
        inbox = store.get_inbox(b.session_id)
        assert len(inbox) == 0

    def test_inbox_shows_unnotified_messages(
        self, store, two_agents,
    ):
        """Inbox should show messages regardless of
        notification state (even if watcher never ran)."""
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Never notified",
        )
        # Delivery is in 'pending' state, not 'notified'
        inbox = store.get_inbox(b.session_id)
        assert len(inbox) == 1
        assert inbox[0]["body"] == "Never notified"


class TestDeliveryStateMachine:

    def test_claim_pending(self, store, two_agents):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Claim me",
        )
        claimed = store.claim_pending_deliveries(
            "watcher-1",
        )
        assert len(claimed) == 1
        assert claimed[0]["body"] == "Claim me"
        assert claimed[0]["recipient_name"] == "tester"

    def test_double_claim_prevented(
        self, store, two_agents,
    ):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Single delivery",
        )
        # First claim
        claimed1 = store.claim_pending_deliveries(
            "watcher-1",
        )
        assert len(claimed1) == 1

        # Second claim should get nothing (already claimed)
        claimed2 = store.claim_pending_deliveries(
            "watcher-2",
        )
        assert len(claimed2) == 0

    def test_mark_notified(self, store, two_agents):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Notify me",
        )
        claimed = store.claim_pending_deliveries(
            "watcher-1",
        )
        store.mark_notified(claimed[0]["id"])

        # Should not be claimable again
        claimed2 = store.claim_pending_deliveries(
            "watcher-1",
        )
        assert len(claimed2) == 0

    def test_failed_delivery_retries(
        self, store, two_agents,
    ):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Retry me",
        )
        # Claim and fail
        claimed = store.claim_pending_deliveries(
            "watcher-1",
        )
        store.mark_delivery_failed(
            claimed[0]["id"], error="timeout",
        )

        # Should be claimable again (back to pending)
        claimed2 = store.claim_pending_deliveries(
            "watcher-1",
        )
        assert len(claimed2) == 1

    def test_failed_delivery_gives_up(
        self, store, two_agents,
    ):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Give up on me",
        )
        # Fail 3 times
        for _ in range(3):
            claimed = store.claim_pending_deliveries(
                "watcher-1",
            )
            if claimed:
                store.mark_delivery_failed(
                    claimed[0]["id"],
                    error="timeout",
                    max_attempts=3,
                )

        # Should be permanently failed now
        claimed = store.claim_pending_deliveries(
            "watcher-1",
        )
        assert len(claimed) == 0

    def test_release_expired_claims(
        self, store, two_agents,
    ):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Expire me",
        )
        # Claim with 0-second duration (immediately expires)
        claimed = store.claim_pending_deliveries(
            "watcher-1", claim_duration_secs=0,
        )
        assert len(claimed) == 1

        # Release expired
        released = store.release_expired_claims()
        assert released == 1

        # Now claimable again
        claimed2 = store.claim_pending_deliveries(
            "watcher-2",
        )
        assert len(claimed2) == 1


class TestWatcherHeartbeat:

    def test_heartbeat(self, store):
        store.update_heartbeat("watcher-1", pid=1234)
        assert store.is_watcher_alive()

    def test_no_watcher(self, store):
        assert not store.is_watcher_alive()

    def test_get_watcher_info(self, store):
        store.update_heartbeat("watcher-1", pid=1234)
        info = store.get_watcher_info()
        assert len(info) == 1
        assert info[0].pid == 1234


class TestThreeAgentThread:
    """Test group thread with 3 participants."""

    def test_group_message_delivery(self, store):
        a = store.register_agent(
            name="architect",
            pane_id="%1",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        b = store.register_agent(
            name="tester",
            pane_id="%2",
            tmux_session="test",
            agent_kind=AgentKind.CODEX,
        )
        c = store.register_agent(
            name="reviewer",
            pane_id="%3",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        thread = store.create_thread(
            title="Group review",
            created_by=a.session_id,
            participant_ids=[
                a.session_id,
                b.session_id,
                c.session_id,
            ],
        )
        store.send_message(
            thread_id=thread.id,
            from_agent=a.session_id,
            body="Review the auth module",
        )

        # Both b and c should have the message
        b_inbox = store.get_inbox(b.session_id)
        c_inbox = store.get_inbox(c.session_id)
        assert len(b_inbox) == 1
        assert len(c_inbox) == 1

        # a should not
        a_inbox = store.get_inbox(a.session_id)
        assert len(a_inbox) == 0

        # b replies — a and c should see it
        store.send_message(
            thread_id=thread.id,
            from_agent=b.session_id,
            body="Looks good",
        )
        a_inbox = store.get_inbox(a.session_id)
        c_inbox = store.get_inbox(c.session_id)
        assert len(a_inbox) == 1
        assert a_inbox[0]["body"] == "Looks good"
        # c now has 2 unread (original + reply)
        assert len(c_inbox) == 2
