"""Immutable records for observational workflow CLI snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from claude_code_tools.workflow_cli_identity_policy import (
    RunResolution,
    abbreviate_run_id,
)

TERMINAL_STATUSES = frozenset({"canceled", "completed", "failed"})
NONTERMINAL_STATUSES = frozenset(
    {"canceling", "paused", "pausing", "running", "starting"}
)
MAX_TIMESTAMP_CHARS = 128
MAX_ACTIVITY_ERROR_SCAN_CHARS = 4_096


def parse_timestamp(value: object) -> datetime | None:
    """Parse one bounded ISO timestamp for every observational consumer."""
    if not isinstance(value, str) or not value or len(value) > MAX_TIMESTAMP_CHARS:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True)
class StepRecord:
    """Immutable normalized snapshot of one persisted workflow step."""

    id: str
    label: str
    status: str
    attempt: int | None
    started_at: str | None
    completed_at: str | None
    error: str | None
    worker_pid: int | None
    thread_id: str | None

    def duration_seconds(self, now: datetime) -> float | None:
        """Return this step's nonnegative elapsed or total duration."""
        started = parse_timestamp(self.started_at)
        if started is None:
            return None
        completed = parse_timestamp(self.completed_at)
        end = completed or (now if self.status == "running" else None)
        if end is None:
            return None
        try:
            return max(0.0, (end - started).total_seconds())
        except (OverflowError, TypeError):
            return None


@dataclass(frozen=True)
class RunState:
    """Immutable typed projection of validated durable run state."""

    run_id: str | None = None
    status: str | None = None
    workflow_path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    cleanup_pending: bool = False
    pid: int | None = None
    pid_started_at: str | None = None
    terminal_fingerprint: str | None = None
    steps: tuple[StepRecord, ...] = ()


@dataclass(frozen=True)
class CallbackRecord:
    """Immutable typed projection of validated callback metadata."""

    status: str | None = None
    attempts: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    delivered_at: str | None = None
    deadline_at: str | None = None
    last_attempt_at: str | None = None
    terminal_completed_at: str | None = None
    terminal_status: str | None = None
    terminal_fingerprint: str | None = None
    client_user_message_id: str | None = None
    endpoint: str | None = None
    thread_id: str | None = None
    timeout_ms: int | None = None
    turn_id: str | None = None
    notifier_pid: int | None = None
    notifier_started_at: str | None = None
    error: str | None = None


def _workflow_name(workflow_path: str | None) -> str:
    """Derive a display name from a persisted workflow path."""
    if not workflow_path:
        return "unknown"
    name = workflow_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    return name[:-3] if name.endswith(".js") else name


@dataclass(frozen=True)
class RunRecord:
    """Immutable typed snapshot plus observational classifications."""

    directory: Path
    state: RunState = RunState()
    callback: CallbackRecord | None = None
    state_error: str | None = None
    callback_error: str | None = None
    supervisor_state: str | None = None
    callback_state: str | None = None
    callback_process_state: str | None = None
    abbreviation_ambiguous: bool = False

    def __post_init__(self) -> None:
        """Reject raw persistence values at the typed record boundary."""
        if not isinstance(self.state, RunState):
            raise TypeError("RunRecord.state must be a RunState")
        if self.callback is not None and not isinstance(
            self.callback,
            CallbackRecord,
        ):
            raise TypeError("RunRecord.callback must be a CallbackRecord or None")

    @property
    def run_id(self) -> str:
        """Return the validated run ID, falling back to the directory name."""
        persisted = self.state.run_id
        if persisted is not None and persisted == self.directory.name:
            return persisted
        return self.directory.name

    @property
    def abbreviated_id(self) -> str:
        """Return the canonical compact run identifier."""
        return abbreviate_run_id(self.run_id)

    @property
    def recorded_status(self) -> str:
        """Return the recorded status when it is a typed nonempty string."""
        return self.state.status or "unknown"

    @property
    def status(self) -> str:
        """Return the effective observational status."""
        if self.state_error:
            return "malformed"
        if self.supervisor_state:
            return self.supervisor_state
        if self.recorded_status not in TERMINAL_STATUSES | NONTERMINAL_STATUSES:
            return "unknown"
        return self.recorded_status

    @property
    def workflow_path(self) -> str | None:
        """Return the persisted workflow path."""
        return self.state.workflow_path

    @property
    def workflow_name(self) -> str:
        """Return a display name derived from the workflow path."""
        return _workflow_name(self.workflow_path)

    @property
    def created_at(self) -> str | None:
        """Return the creation timestamp."""
        return self.state.created_at

    @property
    def updated_at(self) -> str | None:
        """Return the last-update timestamp."""
        return self.state.updated_at

    @property
    def started_at(self) -> str | None:
        """Return the best available start timestamp."""
        return self.state.started_at or self.created_at

    @property
    def completed_at(self) -> str | None:
        """Return the completion timestamp."""
        return self.state.completed_at

    @property
    def error(self) -> str | None:
        """Return the run-level or state parsing error."""
        return self.state_error or self.state.error

    @property
    def callback_status(self) -> str:
        """Return callback status including observational overrides."""
        if self.callback_error:
            return "invalid"
        if self.callback is None:
            return "none"
        return self.callback_state or self.callback.status or "unknown"

    @property
    def steps(self) -> tuple[StepRecord, ...]:
        """Return the normalized immutable step tuple."""
        return self.state.steps

    @property
    def active_workers(self) -> int:
        """Count persisted running agent steps."""
        return sum(step.status == "running" for step in self.steps)

    @property
    def progress(self) -> dict[str, int]:
        """Return stable step progress counts."""
        counts = {
            "total": len(self.steps),
            "completed": 0,
            "failed": 0,
            "canceled": 0,
            "running": 0,
        }
        for step in self.steps:
            if step.status in counts:
                counts[step.status] += 1
        return counts

    def duration_seconds(self, now: datetime) -> float | None:
        """Return the run's nonnegative elapsed or total duration."""
        started = parse_timestamp(self.started_at)
        if started is None:
            return None
        completed = parse_timestamp(self.completed_at)
        if self.recorded_status in TERMINAL_STATUSES:
            end = completed or parse_timestamp(self.updated_at)
            if end is None:
                return None
        else:
            end = now
        try:
            return max(0.0, (end - started).total_seconds())
        except (OverflowError, TypeError):
            return None

    def activity(self) -> str:
        """Return a compact error or current-activity indication."""
        if self.error:
            summary = next(
                (
                    line.strip()
                    for line in self.error[:MAX_ACTIVITY_ERROR_SCAN_CHARS].splitlines()
                    if line.strip()
                ),
                None,
            )
            if summary is not None:
                return summary
        process_messages = {
            "stale": "supervisor PID was reused",
            "orphaned": "supervisor is not running",
            "unverifiable": "supervisor identity cannot be verified",
        }
        if self.supervisor_state in process_messages:
            return process_messages[self.supervisor_state]
        running = [step.label for step in self.steps if step.status == "running"]
        if running:
            suffix = f" +{len(running) - 1}" if len(running) > 1 else ""
            return f"{running[0]}{suffix}"
        if self.state.cleanup_pending:
            return "cleanup pending"
        if self.recorded_status in TERMINAL_STATUSES:
            return self.recorded_status
        return "waiting"


@dataclass(frozen=True)
class RunQueryResult:
    """Explicit completeness metadata for one bounded store query."""

    records: tuple[RunRecord, ...]
    truncated: bool
    store_has_runs: bool
    observation_complete: bool = True
    observation_skipped: int = 0
    query_at: datetime | None = None
    read_completed_at: datetime | None = None

    @property
    def complete(self) -> bool:
        """Return whether neither rows nor process observations were omitted."""
        return not self.truncated and self.observation_complete


@dataclass(frozen=True)
class RunLookupResult:
    """One capability-bound identity resolution and optional snapshot."""

    resolution: RunResolution
    record: RunRecord | None
    query_at: datetime
    read_completed_at: datetime
