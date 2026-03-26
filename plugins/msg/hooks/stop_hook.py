#!/usr/bin/env python3
"""Stop hook for msg: check inbox on Claude Code stop.

Uses the same claim protocol as the watcher to prevent
double-notification. If there are pending messages,
types a notification into this pane.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> None:
    """Check for pending messages and notify if any."""
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    try:
        from claude_code_tools.msg.store import MsgStore
        from claude_code_tools.msg.models import _new_uuid
    except ImportError:
        # msg not installed, skip silently
        print(json.dumps({"decision": "approve"}))
        return

    store = MsgStore()

    # Find this agent by pane
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        print(json.dumps({"decision": "approve"}))
        return

    # Detect tmux session
    try:
        result = subprocess.run(
            [
                "tmux", "display-message",
                "-p", "#{session_name}",
            ],
            capture_output=True, text=True, timeout=5,
        )
        tmux_session = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print(json.dumps({"decision": "approve"}))
        return

    # Find our agent
    agents = store.list_agents(tmux_session=tmux_session)
    me = None
    for a in agents:
        if a.pane_id == pane_id:
            me = a
            break

    if not me:
        print(json.dumps({"decision": "approve"}))
        return

    # Check for unread messages
    messages = store.get_inbox(me.session_id)
    if not messages:
        print(json.dumps({"decision": "approve"}))
        return

    # Claim deliveries using same protocol as watcher
    claimer_id = f"stop-hook-{_new_uuid()[:8]}"
    claimed = store.claim_pending_deliveries(claimer_id)

    # Filter to only our deliveries
    our_claims = [
        d for d in claimed
        if d["recipient_id"] == me.session_id
    ]

    if not our_claims:
        # Messages exist but already claimed by watcher
        # Still inform via context
        count = len(messages)
        notification = (
            f"[MSG] {count} unread message(s) "
            f"-- run: msg inbox"
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": notification,
            }
        }))
        return

    # Build notification
    count = len(our_claims)
    notification = (
        f"[MSG] {count} new message(s) "
        f"-- run: msg inbox"
    )

    # Mark as notified
    for d in our_claims:
        store.mark_notified(d["id"])

    # Inject as context so Claude sees it on next turn
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": notification,
        }
    }))


if __name__ == "__main__":
    main()
