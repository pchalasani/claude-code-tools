"""Data models for the msg inter-agent communication system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class AgentKind(str, Enum):
    """Type of coding agent."""

    CLAUDE = "claude"
    CODEX = "codex"


class ConsumerProtocol(str, Enum):
    """How a registered agent consumes delivery notifications."""

    LEGACY = "legacy"
    FIRST_MATE_V1 = "first-mate.v1"


class ContinuationState(str, Enum):
    """Whether an agent owns an armed First-mate responsibility."""

    IDLE = "idle"
    ACTIVE_FRESH = "active_fresh"
    ACTIVE_STALE = "active_stale"


class DeliveryState(str, Enum):
    """Delivery state machine.

    Transitions:
        pending -> claimed -> notified -> read
        pending -> claimed -> failed
        claimed -> pending (busy, failure, or expired lease)
        claimed -> read (inbox wins the race)
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    NOTIFIED = "notified"
    READ = "read"
    FAILED = "failed"


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Agent:
    """A registered agent session."""

    session_id: str = field(default_factory=_new_uuid)
    name: str = ""
    pane_id: str = ""
    tmux_session: str = ""
    tmux_socket: str | None = None
    display_addr: str | None = None
    agent_kind: AgentKind = AgentKind.CLAUDE
    pid: int | None = None
    cwd: str | None = None
    registered_at: str = field(default_factory=_now_iso)
    last_seen: str = field(default_factory=_now_iso)
    consumer_protocol: ConsumerProtocol = ConsumerProtocol.LEGACY
    process_start_identity: str | None = None


@dataclass(frozen=True)
class RegistrationIdentity:
    """Exact headed-process snapshot used to authorize store mutations."""

    session_id: str
    tmux_session: str
    tmux_socket: str | None
    pane_id: str
    pid: int | None
    process_start_identity: str | None

    @classmethod
    def from_agent(cls, agent: Agent) -> RegistrationIdentity:
        return cls(
            session_id=agent.session_id,
            tmux_session=agent.tmux_session,
            tmux_socket=agent.tmux_socket,
            pane_id=agent.pane_id,
            pid=agent.pid,
            process_start_identity=agent.process_start_identity,
        )


@dataclass
class Thread:
    """A conversation thread between agents."""

    id: str = field(default_factory=_new_uuid)
    title: str = ""
    created_by: str = ""  # agent session_id
    created_at: str = field(default_factory=_now_iso)


@dataclass
class Message:
    """A message in a thread."""

    id: str = field(default_factory=_new_uuid)
    thread_id: str = ""
    from_agent: str = ""  # agent session_id
    body: str = ""
    created_at: str = field(default_factory=_now_iso)


@dataclass
class Delivery:
    """Delivery tracking for a message to a recipient."""

    id: str = field(default_factory=_new_uuid)
    message_id: str = ""
    recipient_id: str = ""  # agent session_id
    state: DeliveryState = DeliveryState.PENDING
    claimed_by: str | None = None
    claim_expires_at: str | None = None
    notify_attempts: int = 0
    last_error: str | None = None
    created_at: str = field(default_factory=_now_iso)
    notified_at: str | None = None
    read_at: str | None = None


@dataclass
class WatcherHeartbeat:
    """Watcher daemon health record."""

    watcher_id: str = field(default_factory=_new_uuid)
    started_at: str = field(default_factory=_now_iso)
    last_heartbeat: str = field(default_factory=_now_iso)
    pid: int = 0
    process_start_identity: str | None = None
    distribution_version: str | None = None
    module_sha256: str | None = None
    db_schema_version: int | None = None


@dataclass(frozen=True)
class ContinuationStatus:
    """Public projection of one agent's continuation record."""

    state: ContinuationState
    generation: str | None = None
    heartbeat_expires_at: str | None = None
    updated_at: str | None = None
