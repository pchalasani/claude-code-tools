"""Hook commands for msg inter-agent communication.

Provides Stop and UserPromptSubmit hooks that check
for unread messages and inject notifications into the
agent's context. Used by both Claude Code and Codex CLI.

Both hooks use the same claim protocol as the watcher
to prevent double-notification.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import click

from .models import _new_uuid
from .store import MsgStore


def _find_self_agent(store: MsgStore) -> object | None:
    """Find the agent registered for this pane."""
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        return None
    tmux_socket = os.environ.get("TMUX", "").split(",", 1)[0] or None

    try:
        cmd = ["tmux"]
        if tmux_socket:
            cmd += ["-S", tmux_socket]
        cmd += ["display-message", "-t", pane_id, "-p", "#{session_name}"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=5,
        )
        tmux_session = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if not tmux_session:
        return None

    matches = [
        agent for agent in store.list_agents(tmux_session, tmux_socket)
        if agent.pane_id == pane_id and agent.tmux_socket == tmux_socket
    ]
    return matches[0] if len(matches) == 1 else None


def _check_and_notify(
    hook_event: str,
) -> None:
    """Common logic for both Stop and UserPromptSubmit.

    Reads JSON from stdin, checks DB for unread messages,
    claims deliveries, outputs JSON response.
    """
    # Read hook input
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    try:
        store = MsgStore()
    except Exception:
        _approve()
        return

    me = _find_self_agent(store)
    if not me:
        _approve()
        return

    # Claim deliveries (same protocol as watcher)
    claimer_id = f"hook-{hook_event}-{_new_uuid()[:8]}"
    claimed = store.claim_pending_deliveries(
        claimer_id, recipient_id=me.session_id,
    )
    if not claimed:
        _approve()
        return

    # Build notification
    count = len(claimed)
    senders = list(dict.fromkeys(
        delivery.get("from_name", "unknown") for delivery in claimed
    ))
    sender_str = ", ".join(senders)
    notification = (
        f"[MSG] {count} unread message(s) "
        f"from {sender_str}. "
        f"Run msg inbox when ready."
    )

    # Mark claimed as notified
    for d in claimed:
        store.mark_notified(d["id"], claimer_id)

    # Output with additionalContext
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": notification,
        }
    }))


def _approve() -> None:
    """Complete the hook without emitting a client-specific response."""
    pass


@click.group()
def cli() -> None:
    """msg-hook: Hook commands for msg notifications."""
    pass


@cli.command()
def stop() -> None:
    """Stop hook — check inbox when agent stops."""
    _check_and_notify("Stop")


@cli.command("prompt-submit")
def prompt_submit() -> None:
    """UserPromptSubmit hook — check inbox on user input."""
    _check_and_notify("UserPromptSubmit")


def main() -> None:
    """Entry point for msg-hook CLI."""
    cli()


if __name__ == "__main__":
    main()
