"""Read-only models for durable dynamic-workflow run state."""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from claude_code_tools.workflow_processes import process_start_identity
from claude_code_tools.workflow_store_io import (
    ReadWorkBudget,
    close_directory as _close_directory,
    directory_entries as _directory_entries,
    open_directory as _open_directory,
)
from claude_code_tools.workflow_store_io import read_mapping as _read_mapping
from claude_code_tools.workflow_validation import (
    MAX_STATE_STEPS,
    MAX_VALIDATION_DIAGNOSTIC_BYTES,
    NONTERMINAL_STATUSES,
    TERMINAL_STATUSES,
    as_utc as _as_utc,
    integer as _integer,
    mapping as _mapping,
    parse_timestamp,
    step_errors as _step_errors,
    string as _string,
    validate_callback as _validate_callback,
    validate_state as _validate_state,
)

FILTER_STATUSES = tuple(
    sorted(
        TERMINAL_STATUSES
        | NONTERMINAL_STATUSES
        | {"malformed", "orphaned", "stale", "unknown", "unverifiable"}
    )
)
STARTUP_GRACE_SECONDS = 5.0
COHERENCE_READ_ATTEMPTS = 3
MAX_RUN_DIRECTORIES = 1_000
MAX_SCAN_JSON_BYTES = 128 * 1024 * 1024
MAX_SINGLE_RUN_JSON_BYTES = MAX_SCAN_JSON_BYTES
MAX_PROCESS_OBSERVATION_SECONDS = 5.0
MAX_ACTIVITY_ERROR_SCAN_CHARS = 4_096
MAX_SUPPORTED_PID = (1 << 31) - 1
_LINUX_IDENTITY = re.compile(
    r"linux:[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}:[0-9]+"
)
_LINUX_COMPATIBILITY_IDENTITY = re.compile(r"linux:[0-9]+")
_DARWIN_IDENTITY = re.compile(r"darwin:[0-9]+:[0-9]+")


class WorkflowStoreError(RuntimeError):
    """Raised when the configured workflow store cannot be inspected."""


@dataclass
class ObservationReport:
    """Report whether every requested live-process observation was attempted."""

    complete: bool = True
    skipped: int = 0

    def mark_skipped(self) -> None:
        """Record one process identity skipped after the shared deadline."""
        self.complete = False
        self.skipped += 1


def workflow_home() -> Path:
    """Return the absolute configured workflow home without creating it."""
    configured = os.environ.get("CODEX_WORKFLOW_HOME")
    if configured is not None:
        return Path(os.path.abspath(configured))
    return Path(os.path.abspath(Path.home() / ".codex" / "workflows"))


def _workflow_name(workflow_path: str | None) -> str:
    """Derive a display name from a persisted workflow path."""
    if not workflow_path:
        return "unknown"
    name = workflow_path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    return name[:-3] if name.endswith(".js") else name


@dataclass(frozen=True)
class StepRecord:
    """Defensive view of one persisted agent step."""

    id: str
    label: str
    status: str
    attempt: int | None
    started_at: str | None
    completed_at: str | None
    error: str | None
    worker_pid: int | None
    thread_id: str | None

    @classmethod
    def from_mapping(cls, key: str, value: Mapping[str, object]) -> StepRecord:
        """Build a step from version-1 JSON under its authoritative key."""
        errors = _step_errors(key, value)
        status = _string(value, "status")
        return cls(
            id=key,
            label=_string(value, "label") or _string(value, "id") or key,
            status="malformed" if errors else status or "unknown",
            attempt=_integer(value, "attempt"),
            started_at=_string(value, "startedAt"),
            completed_at=_string(value, "completedAt"),
            error="; ".join(errors) if errors else _string(value, "error"),
            worker_pid=_integer(value, "workerPid"),
            thread_id=_string(value, "threadId"),
        )

    @classmethod
    def malformed(cls, key: str, value: object) -> StepRecord:
        """Preserve a malformed step as an explicit diagnostic record."""
        maximum_key_chars = MAX_VALIDATION_DIAGNOSTIC_BYTES // 4
        bounded_key = key[:maximum_key_chars]
        key_was_truncated = len(key) > maximum_key_chars
        error = (
            f"step {bounded_key!r} must be a JSON object, got "
            f"{type(value).__name__}"
        )
        if key_was_truncated:
            error += " [truncated]"
        encoded_error = error.encode("utf-8")
        if len(encoded_error) > MAX_VALIDATION_DIAGNOSTIC_BYTES:
            suffix = b" [truncated]"
            prefix = encoded_error[
                : MAX_VALIDATION_DIAGNOSTIC_BYTES - len(suffix)
            ]
            error = prefix.decode("utf-8", errors="ignore") + suffix.decode()
        return cls(
            id=key,
            label=key,
            status="malformed",
            attempt=None,
            started_at=None,
            completed_at=None,
            error=error,
            worker_pid=None,
            thread_id=None,
        )

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
        except OverflowError:
            return None

    def to_json(self, now: datetime) -> dict[str, object]:
        """Return a stable automation representation."""
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "attempt": self.attempt,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "durationSeconds": self.duration_seconds(now),
            "workerPid": self.worker_pid,
            "threadId": self.thread_id,
            "error": self.error,
        }


@dataclass(frozen=True)
class RunRecord:
    """Normalized, read-only view of a workflow run directory."""

    directory: Path
    state: Mapping[str, object] = field(default_factory=dict)
    callback: Mapping[str, object] | None = None
    state_error: str | None = None
    callback_error: str | None = None
    supervisor_state: str | None = None
    callback_state: str | None = None
    callback_process_state: str | None = None
    abbreviation_ambiguous: bool = False

    @property
    def run_id(self) -> str:
        """Return the persisted run ID, falling back to the directory name."""
        persisted = _string(self.state, "runId")
        if persisted is not None and persisted == self.directory.name:
            return persisted
        return self.directory.name

    @property
    def abbreviated_id(self) -> str:
        """Return a compact but recognizable run ID."""
        if len(self.run_id) <= 17:
            return self.run_id
        return f"{self.run_id[:8]}~{self.run_id[-8:]}"

    @property
    def recorded_status(self) -> str:
        """Return the durable status exactly as recorded when possible."""
        return _string(self.state, "status") or "unknown"

    @property
    def status(self) -> str:
        """Return the observational status, including stale classification."""
        if self.state_error:
            return "malformed"
        if self.supervisor_state:
            return self.supervisor_state
        if self.recorded_status not in TERMINAL_STATUSES | NONTERMINAL_STATUSES:
            return "unknown"
        return self.recorded_status

    @property
    def workflow_path(self) -> str | None:
        """Return the recorded workflow path without opening it."""
        return _string(self.state, "workflowPath")

    @property
    def workflow_name(self) -> str:
        """Return a name derived from the recorded workflow path."""
        return _workflow_name(self.workflow_path)

    @property
    def created_at(self) -> str | None:
        """Return the persisted creation timestamp."""
        return _string(self.state, "createdAt")

    @property
    def updated_at(self) -> str | None:
        """Return the persisted last-update timestamp."""
        return _string(self.state, "updatedAt")

    @property
    def started_at(self) -> str | None:
        """Return the best available start timestamp."""
        return _string(self.state, "startedAt") or self.created_at

    @property
    def completed_at(self) -> str | None:
        """Return the persisted completion timestamp."""
        return _string(self.state, "completedAt")

    @property
    def error(self) -> str | None:
        """Return the run-level or parsing error."""
        return self.state_error or _string(self.state, "error")

    @property
    def callback_status(self) -> str:
        """Return the callback state, including corrupt metadata."""
        if self.callback_error:
            return "invalid"
        if self.callback is None:
            return "none"
        return self.callback_state or _string(self.callback, "status") or "unknown"

    @property
    def steps(self) -> tuple[StepRecord, ...]:
        """Return agent steps, preserving malformed entries diagnostically."""
        source = _mapping(self.state.get("steps"))
        if source is None or len(source) > MAX_STATE_STEPS:
            return ()
        records = [
            (
                StepRecord.from_mapping(str(key), child)
                if (child := _mapping(value)) is not None
                else StepRecord.malformed(str(key), value)
            )
            for key, value in source.items()
        ]
        oldest = datetime.min.replace(tzinfo=UTC)
        return tuple(
            sorted(
                records,
                key=lambda step: (_as_utc(step.started_at) or oldest, step.id),
            )
        )

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
        """Return the run's elapsed or total duration.

        Args:
            now: Current aware time for a nonterminal run.

        Returns:
            Nonnegative seconds, or ``None`` when the start is invalid.
        """
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
        except OverflowError:
            return None

    def activity(self) -> str:
        """Return a compact error or current-activity indication."""
        if self.error:
            summary = next(
                (
                    line.strip()
                    for line in self.error[
                        :MAX_ACTIVITY_ERROR_SCAN_CHARS
                    ].splitlines()
                    if line.strip()
                ),
                None,
            )
            if summary is not None:
                return summary
        if self.supervisor_state == "stale":
            return "supervisor PID was reused"
        if self.supervisor_state == "orphaned":
            return "supervisor is not running"
        if self.supervisor_state == "unverifiable":
            return "supervisor identity cannot be verified"
        running = [step.label for step in self.steps if step.status == "running"]
        if running:
            suffix = f" +{len(running) - 1}" if len(running) > 1 else ""
            return f"{running[0]}{suffix}"
        if self.state.get("cleanupPending") is True:
            return "cleanup pending"
        if self.recorded_status in TERMINAL_STATUSES:
            return self.recorded_status
        return "waiting"

    def callback_json(self) -> dict[str, object] | None:
        """Return stable callback details when callback metadata exists."""
        if self.callback_error:
            return {
                "status": "invalid",
                "attempts": None,
                "createdAt": None,
                "updatedAt": None,
                "deliveredAt": None,
                "deadlineAt": None,
                "lastAttemptAt": None,
                "terminalCompletedAt": None,
                "terminalStatus": None,
                "clientUserMessageId": None,
                "endpoint": None,
                "threadId": None,
                "timeoutMs": None,
                "turnId": None,
                "notifierPid": None,
                "notifierStartedAt": None,
                "notifierProcessStatus": None,
                "error": self.callback_error,
            }
        if self.callback is None:
            return None
        return {
            "status": self.callback_status,
            "attempts": _integer(self.callback, "attempts"),
            "createdAt": _string(self.callback, "createdAt"),
            "updatedAt": _string(self.callback, "updatedAt"),
            "deliveredAt": _string(self.callback, "deliveredAt"),
            "deadlineAt": _string(self.callback, "deadlineAt"),
            "lastAttemptAt": _string(self.callback, "lastAttemptAt"),
            "terminalCompletedAt": _string(
                self.callback,
                "terminalCompletedAt",
            ),
            "terminalStatus": _string(self.callback, "terminalStatus"),
            "clientUserMessageId": _string(
                self.callback,
                "clientUserMessageId",
            ),
            "endpoint": _string(self.callback, "endpoint"),
            "threadId": _string(self.callback, "threadId"),
            "timeoutMs": _integer(self.callback, "timeoutMs"),
            "turnId": _string(self.callback, "turnId"),
            "notifierPid": _integer(self.callback, "notifierPid"),
            "notifierStartedAt": _string(
                self.callback,
                "notifierStartedAt",
            ),
            "notifierProcessStatus": self.callback_process_state,
            "error": _string(self.callback, "error"),
        }

    def to_json(
        self,
        now: datetime,
        *,
        include_steps: bool = False,
    ) -> dict[str, object]:
        """Return a stable automation representation.

        Args:
            now: Current aware time.
            include_steps: Whether to include detailed agent-step records.

        Returns:
            A JSON-compatible run object.
        """
        progress = self.progress
        value: dict[str, object] = {
            "schemaVersion": 1,
            "runId": self.run_id,
            "abbreviatedRunId": self.abbreviated_id,
            "workflowName": self.workflow_name,
            "workflowPath": self.workflow_path,
            "recordedStatus": self.recorded_status,
            "status": self.status,
            "agentProgress": progress,
            "activeWorkers": self.active_workers,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "updatedAt": self.updated_at,
            "durationSeconds": self.duration_seconds(now),
            "callback": self.callback_json(),
            "activity": self.activity(),
            "error": self.error,
            "stateError": self.state_error,
            "callbackError": self.callback_error,
        }
        if include_steps:
            value["steps"] = [step.to_json(now) for step in self.steps]
        return value


def _bounded_decimal(value: str, *, maximum: int | None = None) -> int | None:
    """Parse a short decimal without invoking Python's huge-integer parser."""
    if not value or len(value) > 20 or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if maximum is None or parsed <= maximum else None


def _process_identity_kind(expected: str) -> str:
    """Classify durable, compatibility, legacy, and malformed identities."""
    if expected.startswith("linux:"):
        if _LINUX_IDENTITY.fullmatch(expected):
            ticks = _bounded_decimal(expected.rsplit(":", 1)[1])
            return "strong" if ticks is not None and ticks > 0 else "malformed"
        if _LINUX_COMPATIBILITY_IDENTITY.fullmatch(expected):
            ticks = _bounded_decimal(expected.removeprefix("linux:"))
            return "compatibility" if ticks is not None and ticks > 0 else "malformed"
        return "malformed"
    if expected.startswith("darwin:"):
        if not _DARWIN_IDENTITY.fullmatch(expected):
            return "malformed"
        raw_seconds, raw_microseconds = expected.split(":")[1:]
        seconds = _bounded_decimal(raw_seconds)
        microseconds = _bounded_decimal(raw_microseconds, maximum=999_999)
        return (
            "strong"
            if seconds is not None and seconds > 0 and microseconds is not None
            else "malformed"
        )
    if os.name == "nt" and expected.isdecimal():
        ticks = _bounded_decimal(expected)
        return "strong" if ticks is not None and ticks > 0 else "malformed"
    return "legacy"


def observed_process_state(pid: int, expected: str) -> str | None:
    """Compare a durable process identity with a tri-state live probe.

    Exact durable identities establish ownership. Older boot-relative Linux
    tokens and second-resolution ``ps`` values can only establish a mismatch;
    an exact compatibility match remains intentionally unverifiable.
    """
    identity_kind = _process_identity_kind(expected)
    if pid <= 0 or pid > MAX_SUPPORTED_PID or identity_kind == "malformed":
        return "unverifiable"
    probe = process_start_identity(
        pid,
        include_legacy=identity_kind == "legacy",
    )
    if probe.status == "dead":
        return "orphaned"
    if probe.status == "unverifiable":
        return "unverifiable"
    if probe.identity == expected:
        return "unverifiable" if identity_kind == "compatibility" else None
    compatibility = set(probe.compatibility_identities)
    if probe.legacy_identity is not None:
        compatibility.add(probe.legacy_identity)
    if expected in compatibility:
        return "unverifiable"
    if identity_kind == "compatibility" and probe.compatibility_identities:
        return "stale"
    if identity_kind == "strong" and probe.identity is not None:
        return "stale"
    return "unverifiable"


def _within_startup_grace(
    state: Mapping[str, object], now: datetime | None = None
) -> bool:
    """Check whether identity-less state is still in its publication window."""
    updated = _as_utc(state.get("updatedAt")) or _as_utc(state.get("createdAt"))
    if updated is None:
        return False
    current = now or datetime.now(UTC)
    try:
        age = (current.astimezone(UTC) - updated).total_seconds()
    except (OverflowError, ValueError):
        return False
    return 0.0 <= age <= STARTUP_GRACE_SECONDS


def _supervisor_state(
    state: Mapping[str, object],
    now: datetime | None = None,
    *,
    observations: dict[tuple[int, str], str | None] | None = None,
    observation_deadline: float | None = None,
    observation_report: ObservationReport | None = None,
) -> str | None:
    """Classify a nonterminal run's supervisor observation.

    Args:
        state: Durable version-1 run state.
        now: Optional current time for deterministic tests.

    Returns:
        ``orphaned``, ``stale``, or ``unverifiable`` when applicable.
    """
    status = _string(state, "status")
    if status not in NONTERMINAL_STATUSES:
        return None
    pid = _integer(state, "pid")
    expected = _string(state, "pidStartedAt")
    if (pid is not None and not 0 < pid <= MAX_SUPPORTED_PID) or (
        expected is not None and _process_identity_kind(expected) == "malformed"
    ):
        return "unverifiable"
    if pid is None and expected is None:
        return None if _within_startup_grace(state, now) else "unverifiable"
    if pid is None or expected is None:
        return "unverifiable"
    return _cached_process_state(
        pid,
        expected,
        observations,
        observation_deadline=observation_deadline,
        observation_report=observation_report,
    )


def _cached_process_state(
    pid: int,
    expected: str,
    observations: dict[tuple[int, str], str | None] | None,
    *,
    observation_deadline: float | None = None,
    observation_report: ObservationReport | None = None,
) -> str | None:
    """Observe a persisted identity at most once during one store scan."""
    key = (pid, expected)
    if observations is not None and key in observations:
        return observations[key]
    if observation_deadline is not None and monotonic() >= observation_deadline:
        result = "unverifiable"
        if observation_report is not None:
            observation_report.mark_skipped()
    else:
        result = observed_process_state(pid, expected)
    if observations is not None:
        observations[key] = result
    return result


def _callback_observation(
    state: Mapping[str, object],
    callback: Mapping[str, object],
    observations: dict[tuple[int, str], str | None] | None = None,
    *,
    observation_deadline: float | None = None,
    observation_report: ObservationReport | None = None,
) -> tuple[str | None, str | None]:
    """Correlate callback state with its run generation and notifier.

    Args:
        state: Valid durable run state.
        callback: Valid durable callback metadata.

    Returns:
        A delivery-status override and separate notifier process status.
    """
    terminal_completed = _string(callback, "terminalCompletedAt")
    callback_fingerprint = _string(callback, "terminalFingerprint")
    state_fingerprint = _string(state, "terminalFingerprint")
    terminal_status = _string(callback, "terminalStatus")
    if terminal_completed is not None or terminal_status is not None:
        if (
            _string(state, "status") not in TERMINAL_STATUSES
            or not _timestamps_equal(
                terminal_completed,
                _string(state, "completedAt"),
            )
            or terminal_status != _string(state, "status")
            or (
                callback_fingerprint is not None
                and state_fingerprint is not None
                and callback_fingerprint != state_fingerprint
            )
        ):
            return "stale", None
    if _string(callback, "status") != "sending":
        return None, None
    pid = _integer(callback, "notifierPid")
    expected = _string(callback, "notifierStartedAt")
    if pid is None or expected is None:
        process_state = "unverifiable"
    else:
        process_state = (
            _cached_process_state(
                pid,
                expected,
                observations,
                observation_deadline=observation_deadline,
                observation_report=observation_report,
            )
            or "running"
        )
    delivery_state = (
        "unknown"
        if (_integer(callback, "attempts") or 0) > 0
        else None
        if process_state == "running"
        else process_state
    )
    return delivery_state, process_state


def _timestamps_equal(left: object, right: object) -> bool:
    """Compare two valid persisted timestamps by instant."""
    normalized_left = _as_utc(left)
    normalized_right = _as_utc(right)
    return normalized_left is not None and normalized_left == normalized_right


def _callback_matches_generation(
    state: Mapping[str, object],
    callback: Mapping[str, object],
) -> bool:
    """Return whether callback terminal metadata matches the loaded state."""
    terminal_completed = _string(callback, "terminalCompletedAt")
    callback_fingerprint = _string(callback, "terminalFingerprint")
    state_fingerprint = _string(state, "terminalFingerprint")
    terminal_status = _string(callback, "terminalStatus")
    if terminal_completed is None and terminal_status is None:
        return True
    return (
        _string(state, "status") in TERMINAL_STATUSES
        and _timestamps_equal(terminal_completed, _string(state, "completedAt"))
        and terminal_status == _string(state, "status")
        and (
            callback_fingerprint is None
            or state_fingerprint is None
            or callback_fingerprint == state_fingerprint
        )
    )


def _discard_oversized_steps(
    state: Mapping[str, object],
) -> Mapping[str, object]:
    """Drop a rejected step map before retaining the surrounding state."""
    steps = _mapping(state.get("steps"))
    if steps is None or len(steps) <= MAX_STATE_STEPS:
        return state
    bounded = dict(state)
    bounded["steps"] = {}
    return bounded


def load_run(
    directory: Path,
    *,
    now: datetime | None = None,
    observe: bool = True,
    budget: ReadWorkBudget | None = None,
    observations: dict[tuple[int, str], str | None] | None = None,
    _parent_fd: int | None = None,
) -> RunRecord:
    """Load one run directory without modifying it.

    Args:
        directory: Verified run directory to inspect.
        now: Shared observation time used for startup-grace classification.
        observe: Whether to perform process observations immediately.
        budget: Optional aggregate JSON-read budget for a multi-run scan.
        observations: Optional process-observation cache shared by a scan.

    Returns:
        A defensive run record.
    """
    parent_fd = _parent_fd
    owns_parent_fd = parent_fd is None
    directory_fd: int | None = None
    try:
        if parent_fd is None:
            parent_fd = _open_directory(directory.parent)
        directory_fd = _open_directory(directory, parent_fd=parent_fd)
    except OSError as error:
        return RunRecord(directory=directory, state_error=str(error))
    finally:
        if owns_parent_fd and parent_fd is not None:
            _close_directory(parent_fd)
    state_path = directory / "state.json"
    callback_path = directory / "completion-notification.json"
    active_budget = budget or ReadWorkBudget(MAX_SINGLE_RUN_JSON_BYTES)
    try:
        state, state_error = _read_mapping(
            state_path,
            description="state",
            directory_fd=directory_fd,
            omit_result_payloads=True,
            budget=active_budget,
        )
        if state is not None:
            state_error = _validate_state(state, directory.name)
        callback, callback_error = _read_mapping(
            callback_path,
            description="callback metadata",
            directory_fd=directory_fd,
            missing_ok=True,
            budget=active_budget,
        )
        if callback is not None:
            callback_error = _validate_callback(callback, directory.name)
        if state_error is None and callback_error is None and callback is not None:
            for _ in range(COHERENCE_READ_ATTEMPTS - 1):
                if callback is None or (
                    state is not None and _callback_matches_generation(state, callback)
                ):
                    break
                state, state_error = _read_mapping(
                    state_path,
                    description="state",
                    directory_fd=directory_fd,
                    omit_result_payloads=True,
                    budget=active_budget,
                )
                callback, callback_error = _read_mapping(
                    callback_path,
                    description="callback metadata",
                    directory_fd=directory_fd,
                    missing_ok=True,
                    budget=active_budget,
                )
                if state is not None:
                    state_error = _validate_state(state, directory.name)
                if callback is not None:
                    callback_error = _validate_callback(callback, directory.name)
                if state_error is not None or callback_error is not None:
                    break
    finally:
        if directory_fd is not None:
            _close_directory(directory_fd)
    normalized = _discard_oversized_steps(state) if state is not None else {}
    record = RunRecord(
        directory=directory,
        state=normalized,
        callback=callback,
        state_error=state_error,
        callback_error=callback_error,
    )
    if not observe:
        return record
    return _observe_record(
        record,
        now or datetime.now(UTC),
        observations=observations,
    )


def _observe_record(
    record: RunRecord,
    now: datetime,
    *,
    observations: dict[tuple[int, str], str | None] | None = None,
    observation_deadline: float | None = None,
    observation_report: ObservationReport | None = None,
) -> RunRecord:
    """Attach process-derived classifications using one observation instant."""
    callback_state: str | None = None
    callback_process_state: str | None = None
    if record.callback_error is None and record.callback is not None:
        if record.state_error is not None:
            callback_state = "unverifiable"
        else:
            callback_state, callback_process_state = _callback_observation(
                record.state,
                record.callback,
                observations,
                observation_deadline=observation_deadline,
                observation_report=observation_report,
            )
    supervisor_state = (
        None
        if record.state_error
        else _supervisor_state(
            record.state,
            now,
            observations=observations,
            observation_deadline=observation_deadline,
            observation_report=observation_report,
        )
    )
    return replace(
        record,
        supervisor_state=supervisor_state,
        callback_state=callback_state,
        callback_process_state=callback_process_state,
    )


def load_runs(
    home: Path | None = None,
    *,
    statuses: tuple[str, ...] = (),
    limit: int | None = None,
    now: datetime | None = None,
    observe: bool = True,
    observation_report: ObservationReport | None = None,
) -> list[RunRecord]:
    """Load sorted local run states within aggregate work and output bounds.

    ``observation_report`` is marked incomplete when the shared process-probe
    deadline prevents a live observation. Callers applying status filters can
    then disclose that an unprobed run might have matched the filter.
    """
    runs_directory = (home or workflow_home()) / "runs"
    runs_fd: int | None = None
    records: list[RunRecord] = []
    try:
        runs_fd = _open_directory(runs_directory)
        if runs_fd is None:
            raise OSError(errno.ENOTSUP, "missing verified directory handle")
        directories: list[Path] = []
        for entry_count, (name, is_directory) in enumerate(
            _directory_entries(runs_fd),
            start=1,
        ):
            if entry_count > MAX_RUN_DIRECTORIES:
                raise WorkflowStoreError(
                    "Workflow run scan exceeds the safety limit of "
                    f"{MAX_RUN_DIRECTORIES} directory entries in "
                    f"{runs_directory}"
                )
            if is_directory:
                directories.append(runs_directory / name)
        budget = ReadWorkBudget(MAX_SCAN_JSON_BYTES)
        for path in directories:
            record = load_run(
                path,
                observe=False,
                budget=budget,
                _parent_fd=runs_fd,
            )
            if budget.limit_exceeded:
                raise WorkflowStoreError(
                    "Workflow run JSON scan exceeds the aggregate work limit of "
                    f"{budget.maximum_bytes} bytes in {runs_directory}"
                )
            records.append(record)
    except FileNotFoundError:
        return []
    except OSError as error:
        raise WorkflowStoreError(
            f"Cannot read workflow runs from {runs_directory}: {error}"
        ) from error
    finally:
        if runs_fd is not None:
            _close_directory(runs_fd)

    def sort_key(run: RunRecord) -> tuple[datetime, str]:
        """Return a total, overflow-safe ordering key for one run."""
        normalized = _as_utc(run.created_at) or _as_utc(run.updated_at)
        if normalized is None:
            normalized = datetime.min.replace(tzinfo=UTC)
        return normalized, run.run_id

    sorted_records = sorted(
        records,
        key=sort_key,
        reverse=True,
    )
    abbreviation_counts: dict[str, int] = {}
    for record in sorted_records:
        abbreviation_counts[record.abbreviated_id] = (
            abbreviation_counts.get(record.abbreviated_id, 0) + 1
        )
    sorted_records = [
        replace(
            record,
            abbreviation_ambiguous=(
                abbreviation_counts[record.abbreviated_id] > 1
            ),
        )
        for record in sorted_records
    ]
    if not observe:
        selected = select_runs(sorted_records, statuses, limit or len(records))
        return selected if limit is not None or statuses else sorted_records
    observed_at = now or datetime.now(UTC)
    observations: dict[tuple[int, str], str | None] = {}
    observation_deadline = monotonic() + MAX_PROCESS_OBSERVATION_SECONDS
    wanted = set(statuses)
    selected: list[RunRecord] = []
    for record in sorted_records:
        if not _could_match_without_observation(record, wanted):
            continue
        observed = _observe_record(
            record,
            observed_at,
            observations=observations,
            observation_deadline=observation_deadline,
            observation_report=observation_report,
        )
        if wanted and observed.status not in wanted:
            continue
        selected.append(observed)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def _could_match_without_observation(
    record: RunRecord,
    wanted: set[str],
) -> bool:
    """Reject records whose effective status cannot match without probing."""
    if not wanted:
        return True
    if record.state_error:
        return "malformed" in wanted
    recorded = record.recorded_status
    if recorded not in NONTERMINAL_STATUSES:
        effective = recorded if recorded in TERMINAL_STATUSES else "unknown"
        return effective in wanted
    possible = {recorded, "orphaned", "stale", "unverifiable"}
    return not possible.isdisjoint(wanted)


def select_runs(
    runs: list[RunRecord], statuses: tuple[str, ...], limit: int
) -> list[RunRecord]:
    """Apply bounded status filtering to already sorted runs."""
    wanted = set(statuses)
    filtered = [run for run in runs if not wanted or run.status in wanted]
    return filtered[:limit]
