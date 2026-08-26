"""Versioned JSON serializers for the public msg CLI boundary."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from .models import Agent, Delivery, DeliveryState, Message, WatcherHeartbeat

SCHEMA = "msg.cli.v1"


def agent_payload(agent: Agent) -> dict[str, Any]:
    return {
        "session_id": agent.session_id,
        "name": agent.name,
        "pane_id": agent.pane_id,
        "tmux_session": agent.tmux_session,
        "tmux_socket": agent.tmux_socket,
        "display_addr": agent.display_addr,
        "agent_kind": agent.agent_kind.value,
        "pid": agent.pid,
        "cwd": agent.cwd,
        "registered_at": agent.registered_at,
        "last_seen": agent.last_seen,
        "consumer_protocol": agent.consumer_protocol.value,
        "process_start_identity": agent.process_start_identity,
    }


def message_payload(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "from_agent": message.from_agent,
        "body": message.body,
        "created_at": message.created_at,
    }


def delivery_payload(
    delivery: Delivery | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(delivery, Delivery):
        values = {
            "id": delivery.id,
            "message_id": delivery.message_id,
            "recipient_id": delivery.recipient_id,
            "state": delivery.state,
            "claimed_by": delivery.claimed_by,
            "claim_expires_at": delivery.claim_expires_at,
            "notify_attempts": delivery.notify_attempts,
            "last_error": delivery.last_error,
            "created_at": delivery.created_at,
            "notified_at": delivery.notified_at,
            "read_at": delivery.read_at,
        }
    else:
        values = delivery
    state = values["state"]
    if isinstance(state, DeliveryState):
        state = state.value
    return {
        "id": values["id"],
        "message_id": values["message_id"],
        "recipient_id": values["recipient_id"],
        "state": state,
        "claimed_by": values["claimed_by"],
        "claim_expires_at": values["claim_expires_at"],
        "notify_attempts": values["notify_attempts"],
        "last_error": "delivery_failed" if values["last_error"] else None,
        "created_at": values["created_at"],
        "notified_at": values["notified_at"],
        "read_at": values["read_at"],
    }


def watcher_payload(watcher: WatcherHeartbeat) -> dict[str, Any]:
    return {
        "watcher_id": watcher.watcher_id,
        "started_at": watcher.started_at,
        "last_heartbeat": watcher.last_heartbeat,
        "pid": watcher.pid,
        "process_start_identity": watcher.process_start_identity,
        "distribution_version": watcher.distribution_version,
        "module_sha256": watcher.module_sha256,
        "db_schema_version": watcher.db_schema_version,
    }


def emit_json(operation: str, data: dict[str, Any]) -> None:
    """Write exactly one machine-readable success envelope to stdout."""
    payload = {"schema": SCHEMA, "operation": operation, "data": data}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
