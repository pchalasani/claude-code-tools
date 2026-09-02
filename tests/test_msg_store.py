"""Tests for the msg store layer."""

from __future__ import annotations

import sqlite3

import pytest

from claude_code_tools.msg.models import (
    AgentKind,
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
        pid=222,
        cwd="/original",
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

    def test_re_register_updates_pane_after_retirement(self, store):
        a1 = store.register_agent(
            name="architect",
            pane_id="%1",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        assert store.retire_agent(a1.session_id)
        a2 = store.register_agent(
            name="architect",
            pane_id="%5",
            tmux_session="test",
            agent_kind=AgentKind.CLAUDE,
        )
        assert a2.session_id != a1.session_id
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

    def test_retire_hides_routing_but_preserves_history_and_reactivates(self, store):
        agent = store.register_agent(
            name="builder",
            pane_id="%1",
            tmux_session="test",
            agent_kind=AgentKind.CODEX,
        )

        assert store.retire_agent(agent.session_id)
        assert store.get_agent_by_name("builder", "test") is None
        assert store.list_agents(tmux_session="test") == []
        assert store.get_agent_by_id(agent.session_id) is not None

        revived = store.register_agent(
            name="builder",
            pane_id="%2",
            tmux_session="test",
            agent_kind=AgentKind.CODEX,
        )
        assert revived.session_id != agent.session_id
        assert store.get_agent_by_name("builder", "test").pane_id == "%2"

    def test_retire_refuses_unread_delivery(self, store, two_agents):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "read me")

        with pytest.raises(ValueError, match="1 unread delivery"):
            store.retire_agent(recipient.session_id)

        assert recipient.session_id in {
            agent.session_id for agent in store.list_agents(tmux_session="test")
        }

    def test_retire_releases_an_expired_read_claim(self, store, two_agents):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "read before expiry")
        store.claim_pending_deliveries("stale-watcher", claim_duration_secs=-1)
        store.mark_read(recipient.session_id)

        assert store.retire_agent(recipient.session_id)
        assert store.get_agent_by_name(
            recipient.name, recipient.tmux_session, recipient.tmux_socket,
        ) is None
        with sqlite3.connect(store.db_path) as conn:
            assert conn.execute(
                "SELECT state, claimed_by, claim_expires_at FROM deliveries"
            ).fetchone() == ("read", None, None)

    def test_legacy_database_adds_active_without_losing_agents(self, tmp_path):
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE agents (
                session_id TEXT PRIMARY KEY, name TEXT, pane_id TEXT,
                tmux_session TEXT, tmux_socket TEXT, display_addr TEXT,
                agent_kind TEXT, pid INTEGER, cwd TEXT,
                registered_at TEXT, last_seen TEXT,
                UNIQUE(name, tmux_session, tmux_socket))"""
        )
        conn.execute(
            "INSERT INTO agents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old", "builder", "%1", "test", None, None, "codex", None, None, "a", "b"),
        )
        conn.commit()
        conn.close()
        assert MsgStore(str(path)).get_agent_by_name("builder", "test") is not None

    def test_new_name_on_same_pane_is_refused(self, store):
        old = store.register_agent("old", "%1", "test", AgentKind.CODEX)

        with pytest.raises(ValueError, match="pane already has"):
            store.register_agent("new", "%1", "test", AgentKind.CODEX)

        assert [agent.session_id for agent in store.list_agents("test")] == [
            old.session_id
        ]

    def test_active_name_cannot_move_without_retirement(self, store):
        old = store.register_agent("old", "%1", "test", AgentKind.CODEX)

        with pytest.raises(ValueError, match="already active at %1"):
            store.register_agent("old", "%2", "test", AgentKind.CODEX)

        assert store.get_agent_by_id(old.session_id).pane_id == "%1"

    def test_retarget_preserves_unread_delivery(self, store, two_agents):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "keep me")
        unread = store.get_inbox(recipient.session_id)

        moved = store.retarget_agent(
            recipient.session_id, "%9", "test", "/tmp/tmux-test",
            "test:1.9",
        )

        moved_fields = (
            moved.session_id, moved.pane_id, moved.display_addr, moved.pid, moved.cwd,
        )
        assert moved_fields == (
            recipient.session_id, "%9", "test:1.9", 222, "/original",
        )
        assert store.get_inbox(recipient.session_id) == unread

    def test_retarget_refuses_claim_then_succeeds_after_release(
        self, store, two_agents,
    ):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "in flight")
        delivery = store.claim_pending_deliveries("watcher")[0]

        with pytest.raises(ValueError, match="actively claimed delivery"):
            store.retarget_agent(
                recipient.session_id, "%9", "test", "/tmp/tmux-test"
            )

        assert store.get_agent_by_id(recipient.session_id).pane_id == "%2"
        assert store.claim_is_current(delivery["id"], "watcher")
        store.release_delivery(delivery["id"], "watcher")
        moved = store.retarget_agent(
            recipient.session_id, "%9", "test", "/tmp/tmux-test"
        )
        assert moved.pane_id == "%9"

    def test_retarget_releases_expired_claim_in_same_transaction(
        self, store, two_agents,
    ):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "expired")
        store.claim_pending_deliveries("old-watcher", claim_duration_secs=0)

        moved = store.retarget_agent(
            recipient.session_id, "%9", "test", "/tmp/tmux-test"
        )

        assert moved.pane_id == "%9"
        recovered = store.claim_pending_deliveries("new-watcher")
        assert [item["body"] for item in recovered] == ["expired"]

    def test_retarget_requeues_notified_and_failed_deliveries(self, store, two_agents):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "notified")
        delivery = store.claim_pending_deliveries("watcher")[0]
        store.mark_notified(delivery["id"], "watcher")

        store.send_message(thread.id, sender.session_id, "failed")
        for _ in range(3):
            delivery = store.claim_pending_deliveries("watcher")[0]
            store.mark_delivery_failed(delivery["id"], "watcher", "boom")

        store.retarget_agent(
            recipient.session_id, "%9", "test", "/tmp/tmux-test"
        )

        recovered = store.claim_pending_deliveries("new-watcher")
        assert {item["body"] for item in recovered} == {"notified", "failed"}
        assert {item["notify_attempts"] for item in recovered} == {0}
        assert {item["last_error"] for item in recovered} == {None}

    def test_retarget_resets_pending_delivery_failure_budget(self, store, two_agents):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "pending retry")
        delivery = store.claim_pending_deliveries("watcher")[0]
        store.mark_delivery_failed(delivery["id"], "watcher", "timeout")

        store.retarget_agent(
            recipient.session_id, "%9", "test", "/tmp/tmux-test",
        )

        recovered = store.claim_pending_deliveries("next-watcher")
        assert len(recovered) == 1
        assert recovered[0]["notify_attempts"] == 0
        assert recovered[0]["last_error"] is None

    @pytest.mark.parametrize(
        ("tmux_session", "tmux_socket"),
        (("other", "/tmp/tmux-test"), ("test", "/tmp/other")),
    )
    def test_retarget_refuses_wrong_scope(self, store, tmux_session, tmux_socket):
        agent = store.register_agent(
            "builder", "%1", "test", AgentKind.CODEX, "/tmp/tmux-test"
        )

        with pytest.raises(ValueError, match="outside the requested tmux scope"):
            store.retarget_agent(agent.session_id, "%9", tmux_session, tmux_socket)

        assert store.get_agent_by_id(agent.session_id).pane_id == "%1"

    def test_retarget_refuses_stale_or_occupied_target(self, store):
        stale = store.register_agent("stale", "%1", "test", AgentKind.CODEX)
        assert store.retire_agent(stale.session_id)
        with pytest.raises(ValueError, match="active registration not found"):
            store.retarget_agent(stale.session_id, "%9", "test")

        moving = store.register_agent("moving", "%2", "test", AgentKind.CODEX)
        store.register_agent("occupant", "%9", "test", AgentKind.CODEX)
        with pytest.raises(ValueError, match="target pane already has"):
            store.retarget_agent(moving.session_id, "%9", "test")

        assert store.get_agent_by_id(moving.session_id).pane_id == "%2"

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

    def test_reused_name_does_not_rewrite_historical_sender(
        self, store, two_agents,
    ):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id],
        )
        store.send_message(thread.id, sender.session_id, "historical")
        assert store.retire_agent(sender.session_id)
        replacement = store.register_agent(
            sender.name, "%3", sender.tmux_session, AgentKind.CLAUDE,
            sender.tmux_socket,
        )

        assert replacement.session_id != sender.session_id
        assert store.get_inbox(recipient.session_id)[0]["from_name"] == sender.name

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

    def test_send_refuses_a_recipient_retired_after_thread_resolution(
        self, store, two_agents,
    ):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        assert store.retire_agent(recipient.session_id)

        with pytest.raises(ValueError, match="recipient became inactive"):
            store.send_message(thread.id, sender.session_id, "late work")

        with sqlite3.connect(store.db_path) as conn:
            assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0

    def test_retired_recipient_is_not_claimed(self, store, two_agents):
        a, b = two_agents
        thread = store.create_thread(
            title="Test",
            created_by=a.session_id,
            participant_ids=[a.session_id, b.session_id],
        )
        store.send_message(thread.id, a.session_id, "Keep for history")
        store.mark_read(b.session_id)
        store.retire_agent(b.session_id)

        assert store.claim_pending_deliveries("watcher") == []

    def test_reregister_gets_fresh_session_and_old_thread_stays_closed(
        self, store, two_agents,
    ):
        a, b = two_agents
        thread = store.create_thread("Test", a.session_id, [a.session_id, b.session_id])
        store.send_message(thread.id, a.session_id, "Old work")
        store.mark_read(b.session_id)
        store.retire_agent(b.session_id)
        revived = store.register_agent(
            "tester", "%3", "test", AgentKind.CODEX, "/tmp/tmux-test"
        )

        assert revived.session_id != b.session_id
        with pytest.raises(ValueError, match="recipient became inactive"):
            store.send_message(thread.id, a.session_id, "Late old-thread work")
        assert store.get_inbox(revived.session_id) == []

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
        assert claimed[0]["recipient_tmux_socket"] == "/tmp/tmux-test"

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

    @pytest.mark.parametrize(
        ("method", "extra_args"),
        (
            ("mark_notified", ()),
            ("mark_delivery_failed", ("late failure",)),
            ("release_delivery", ()),
        ),
    )
    def test_expired_claim_owner_cannot_mutate_and_new_owner_reclaims(
        self, store, two_agents, method, extra_args,
    ):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "Expired claim")
        delivery = store.claim_pending_deliveries(
            "old-watcher", claim_duration_secs=-1,
        )[0]

        getattr(store, method)(delivery["id"], "old-watcher", *extra_args)

        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT state, claimed_by, notify_attempts, last_error "
                "FROM deliveries WHERE id = ?",
                (delivery["id"],),
            ).fetchone()
        assert row == ("claimed", "old-watcher", 0, None)

        reclaimed = store.claim_pending_deliveries("new-watcher")
        assert [item["id"] for item in reclaimed] == [delivery["id"]]
        assert store.claim_is_current(delivery["id"], "new-watcher")

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
        store.mark_notified(claimed[0]["id"], "watcher-1")

        # Should not be claimable again
        claimed2 = store.claim_pending_deliveries(
            "watcher-1",
        )
        assert len(claimed2) == 0

    def test_renew_deliveries_is_all_or_nothing(self, store, two_agents):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id],
        )
        store.send_message(thread.id, sender.session_id, "first")
        store.send_message(thread.id, sender.session_id, "second")
        deliveries = store.claim_pending_deliveries("watcher")
        delivery_ids = [delivery["id"] for delivery in deliveries]
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE deliveries SET claimed_by = 'other' WHERE id = ?",
                (delivery_ids[1],),
            )
            before = conn.execute(
                """SELECT id, claimed_by, claim_expires_at FROM deliveries
                ORDER BY id"""
            ).fetchall()

        assert not store.renew_deliveries(delivery_ids, "watcher", 120)
        with sqlite3.connect(store.db_path) as conn:
            after = conn.execute(
                """SELECT id, claimed_by, claim_expires_at FROM deliveries
                ORDER BY id"""
            ).fetchall()
        assert after == before

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
            claimed[0]["id"], "watcher-1", error="timeout",
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
                    "watcher-1",
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

    def test_releasing_expired_read_claim_preserves_read_state(
        self, store, two_agents,
    ):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "already read")
        store.claim_pending_deliveries("stale-watcher", claim_duration_secs=-1)
        store.mark_read(recipient.session_id)

        assert store.release_expired_claims() == 1
        assert store.get_inbox(recipient.session_id) == []
        with sqlite3.connect(store.db_path) as conn:
            assert conn.execute(
                "SELECT state, claimed_by, claim_expires_at FROM deliveries"
            ).fetchone() == ("read", None, None)

    def test_normal_release_does_not_consume_failure_attempts(
        self, store, two_agents,
    ):
        sender, recipient = two_agents
        thread = store.create_thread(
            "Test", sender.session_id, [sender.session_id, recipient.session_id]
        )
        store.send_message(thread.id, sender.session_id, "wait for idle")

        for i in range(5):
            delivery = store.claim_pending_deliveries(f"watcher-{i}")[0]
            assert delivery["notify_attempts"] == 0
            store.release_delivery(delivery["id"], f"watcher-{i}")

        delivery = store.claim_pending_deliveries("watcher-error")[0]
        store.mark_delivery_failed(delivery["id"], "watcher-error", "tmux failed")
        retried = store.claim_pending_deliveries("watcher-retry")[0]
        assert retried["notify_attempts"] == 1


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
