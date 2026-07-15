"""Schema and chronology validation for durable workflow records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

TERMINAL_STATUSES = frozenset({"canceled", "completed", "failed"})
NONTERMINAL_STATUSES = frozenset(
    {"canceling", "paused", "pausing", "running", "starting"}
)
CALLBACK_STATUSES = frozenset(
    {"armed", "delivered", "failed", "sending", "unknown"}
)
MAX_STATE_STEPS = 1_000
MAX_CALLBACK_DELIVERY_SUBMISSIONS = 5
MAX_CALLBACK_TIMEOUT_MS = 604_800_000
MAX_VALIDATION_DIAGNOSTICS = 100
MAX_VALIDATION_DIAGNOSTIC_BYTES = 16 * 1024


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    """Truncate text without splitting a UTF-8 code point."""
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    suffix = " [truncated]"
    suffix_bytes = suffix.encode("utf-8")
    if maximum_bytes <= len(suffix_bytes):
        return suffix_bytes[:maximum_bytes].decode("utf-8", errors="ignore")
    prefix_bytes = encoded[: maximum_bytes - len(suffix_bytes)]
    prefix = prefix_bytes.decode("utf-8", errors="ignore")
    return f"{prefix}{suffix}"


class _Diagnostics:
    """Collect unique validation errors within count and byte budgets."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self._seen: set[str] = set()
        self._bytes = 0
        self.sealed = False

    def add(self, message: str) -> None:
        """Add one diagnostic if aggregate budgets still permit it."""
        if self.sealed or message in self._seen:
            return
        if len(self.messages) >= MAX_VALIDATION_DIAGNOSTICS:
            marker = "additional validation diagnostics omitted"
            previous = self.messages.pop()
            self._seen.discard(previous)
            self._bytes -= len(previous.encode("utf-8"))
            if self.messages:
                self._bytes -= 2
            separator_bytes = 2 if self.messages else 0
            remaining = (
                MAX_VALIDATION_DIAGNOSTIC_BYTES
                - self._bytes
                - separator_bytes
            )
            bounded = _truncate_utf8(marker, max(0, remaining))
            if bounded:
                self.messages.append(bounded)
                self._seen.add(bounded)
                self._bytes += separator_bytes + len(bounded.encode("utf-8"))
            self.sealed = True
            return
        separator_bytes = 2 if self.messages else 0
        remaining = (
            MAX_VALIDATION_DIAGNOSTIC_BYTES - self._bytes - separator_bytes
        )
        if remaining <= 0:
            self.sealed = True
            return
        bounded = _truncate_utf8(message, remaining)
        self.messages.append(bounded)
        self._seen.add(bounded)
        self._bytes += separator_bytes + len(bounded.encode("utf-8"))
        if bounded != message:
            self.sealed = True

    def extend(self, messages: list[str]) -> None:
        """Add diagnostics until either aggregate budget is exhausted."""
        for message in messages:
            self.add(message)
            if self.sealed:
                break

    def error(self) -> str | None:
        """Return the bounded aggregate diagnostic."""
        return "; ".join(self.messages) or None


def parse_timestamp(value: object) -> datetime | None:
    """Parse ISO time, interpreting old timezone-less state as UTC."""
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def as_utc(value: object) -> datetime | None:
    """Parse and safely normalize a timestamp to UTC."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError):
        return None


def string(mapping: Mapping[str, object], key: str) -> str | None:
    """Return a nonempty string field from a mapping."""
    value = mapping.get(key)
    return value if isinstance(value, str) and value else None


def integer(mapping: Mapping[str, object], key: str) -> int | None:
    """Return an integer field while excluding booleans."""
    value = mapping.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def mapping(value: object) -> Mapping[str, object] | None:
    """Narrow an object to a mapping."""
    return value if isinstance(value, Mapping) else None


def _schema_errors(
    value: Mapping[str, object],
    *,
    required_strings: tuple[str, ...],
    required_integers: tuple[str, ...],
) -> list[str]:
    """Validate common required primitive fields in a durable object."""
    errors = [
        f"{key} must be a nonempty string"
        for key in required_strings
        if string(value, key) is None
    ]
    errors.extend(
        f"{key} must be an integer"
        for key in required_integers
        if integer(value, key) is None
    )
    return errors


def _optional_type_errors(
    value: Mapping[str, object],
    *,
    strings: tuple[str, ...] = (),
    integers: tuple[str, ...] = (),
    booleans: tuple[str, ...] = (),
) -> list[str]:
    """Validate optional primitive fields when they are present."""
    errors = [
        f"{key} must be a nonempty string when present"
        for key in strings
        if key in value and string(value, key) is None
    ]
    errors.extend(
        f"{key} must be an integer when present"
        for key in integers
        if key in value and integer(value, key) is None
    )
    errors.extend(
        f"{key} must be a boolean when present"
        for key in booleans
        if key in value and not isinstance(value[key], bool)
    )
    return errors


def _timestamp_errors(
    value: Mapping[str, object], fields: tuple[str, ...]
) -> list[str]:
    """Validate timestamp strings present in a durable object."""
    return [
        f"{key} must be a valid ISO timestamp"
        for key in fields
        if string(value, key) is not None and as_utc(value[key]) is None
    ]


def _ordered_timestamp_error(
    value: Mapping[str, object], earlier: str, later: str
) -> str | None:
    """Return an error when two present valid lifecycle times are reversed."""
    earlier_time = as_utc(value.get(earlier))
    later_time = as_utc(value.get(later))
    if earlier_time is not None and later_time is not None:
        if later_time < earlier_time:
            return f"{later} cannot precede {earlier}"
    return None


def step_errors(key: str, value: Mapping[str, object]) -> list[str]:
    """Validate one version-1 step, including ownership and chronology."""
    diagnostics = _Diagnostics()
    diagnostics.extend(
        _schema_errors(
            value,
            required_strings=(
                "fingerprint",
                "id",
                "label",
                "startedAt",
                "status",
            ),
            required_integers=("attempt",),
        )
    )
    embedded_id = string(value, "id")
    if embedded_id is not None and embedded_id != key:
        diagnostics.add("embedded step id does not match its owning key")
    status = string(value, "status")
    if status not in {"canceled", "completed", "failed", "running"}:
        if status is not None:
            diagnostics.add(f"unsupported step status {status!r}")
    completed = string(value, "completedAt")
    if status in TERMINAL_STATUSES and completed is None:
        diagnostics.add("terminal step requires completedAt")
    if status == "running" and completed is not None:
        diagnostics.add("running step cannot have completedAt")
    diagnostics.extend(
        _optional_type_errors(
            value,
            strings=("completedAt", "error", "threadId", "workerStartedAt"),
            integers=("workerPid",),
        )
    )
    diagnostics.extend(_timestamp_errors(value, ("startedAt", "completedAt")))
    chronology = _ordered_timestamp_error(value, "startedAt", "completedAt")
    if chronology is not None:
        diagnostics.add(chronology)
    return diagnostics.messages


def validate_state(state: Mapping[str, object], directory_name: str) -> str | None:
    """Validate the durable version-1 run-state envelope."""
    diagnostics = _Diagnostics()
    diagnostics.extend(
        _schema_errors(
            state,
            required_strings=(
                "createdAt",
                "cwd",
                "runId",
                "status",
                "updatedAt",
                "workflowHash",
                "workflowPath",
            ),
            required_integers=("concurrency", "version"),
        )
    )
    if integer(state, "version") != 1:
        diagnostics.add("version must equal 1")
    run_id = string(state, "runId")
    if run_id is not None and run_id != directory_name:
        diagnostics.add(
            f"runId {run_id!r} does not match directory {directory_name!r}"
        )
    if mapping(state.get("steps")) is None:
        diagnostics.add("steps must be a JSON object")
    diagnostics.extend(
        _optional_type_errors(
            state,
            strings=(
                "completedAt",
                "engineStartedAt",
                "error",
                "pidStartedAt",
                "runnerStartedAt",
                "startedAt",
                "terminalFingerprint",
            ),
            integers=(
                "agentInvocations",
                "defaultAgentTimeoutMs",
                "enginePid",
                "maxAgentInvocations",
                "maxRuntimeMs",
                "pid",
            ),
            booleans=("cleanupPending",),
        )
    )
    timestamp_fields = (
        "completedAt",
        "createdAt",
        "runnerStartedAt",
        "startedAt",
        "updatedAt",
    )
    diagnostics.extend(_timestamp_errors(state, timestamp_fields))
    for earlier, later in (
        ("createdAt", "completedAt"),
        ("createdAt", "runnerStartedAt"),
        ("createdAt", "startedAt"),
        ("createdAt", "updatedAt"),
        ("runnerStartedAt", "updatedAt"),
        ("startedAt", "completedAt"),
        ("startedAt", "updatedAt"),
        ("completedAt", "updatedAt"),
    ):
        chronology = _ordered_timestamp_error(state, earlier, later)
        if chronology is not None:
            diagnostics.add(chronology)
    status = string(state, "status")
    completed = string(state, "completedAt")
    if status in TERMINAL_STATUSES and completed is None:
        diagnostics.add("terminal status requires completedAt")
    if status in NONTERMINAL_STATUSES and completed is not None:
        diagnostics.add("nonterminal status cannot have completedAt")
    pid = integer(state, "pid")
    pid_started = string(state, "pidStartedAt")
    if (pid is None) != (pid_started is None):
        diagnostics.add("pid and pidStartedAt must be present together")
    steps = mapping(state.get("steps"))
    if steps is not None:
        if len(steps) > MAX_STATE_STEPS:
            diagnostics.add(
                f"steps contains {len(steps)} entries; maximum is "
                f"{MAX_STATE_STEPS}"
            )
        else:
            for key, raw_step in steps.items():
                step = mapping(raw_step)
                if step is None:
                    diagnostics.add(f"step {key!r} must be a JSON object")
                    continue
                for error in step_errors(str(key), step):
                    diagnostics.add(f"step {key!r}: {error}")
                    if diagnostics.sealed:
                        break
                if diagnostics.sealed:
                    break
                for step_field in ("startedAt", "completedAt"):
                    step_time = as_utc(step.get(step_field))
                    for lower_field in ("createdAt", "startedAt"):
                        lower_time = as_utc(state.get(lower_field))
                        if (
                            step_time is not None
                            and lower_time is not None
                            and step_time < lower_time
                        ):
                            diagnostics.add(
                                f"step {key!r}: {step_field} cannot precede "
                                f"{lower_field}"
                            )
                    updated_time = as_utc(state.get("updatedAt"))
                    if (
                        step_time is not None
                        and updated_time is not None
                        and updated_time < step_time
                    ):
                        diagnostics.add(
                            f"step {key!r}: {step_field} cannot follow "
                            "updatedAt"
                        )
                if diagnostics.sealed:
                    break
                if (
                    status in TERMINAL_STATUSES
                    and string(step, "status") == "running"
                ):
                    diagnostics.add(
                        f"terminal run cannot contain running step {key!r}"
                    )
                if diagnostics.sealed:
                    break
    return diagnostics.error()


def validate_callback(
    callback: Mapping[str, object],
    directory_name: str,
    *,
    now: datetime | None = None,
) -> str | None:
    """Validate the durable version-1 callback envelope and ownership."""
    diagnostics = _Diagnostics()
    diagnostics.extend(
        _schema_errors(
            callback,
            required_strings=(
                "createdAt",
                "endpoint",
                "runId",
                "status",
                "threadId",
                "updatedAt",
            ),
            required_integers=("attempts", "timeoutMs", "version"),
        )
    )
    if integer(callback, "version") != 1:
        diagnostics.add("version must equal 1")
    attempts = integer(callback, "attempts")
    if attempts is not None and attempts < 0:
        diagnostics.add("attempts cannot be negative")
    if attempts is not None and attempts > MAX_CALLBACK_DELIVERY_SUBMISSIONS:
        diagnostics.add(
            "attempts cannot exceed "
            f"{MAX_CALLBACK_DELIVERY_SUBMISSIONS} submissions"
        )
    if attempts is not None and attempts > 0:
        if string(callback, "lastAttemptAt") is None:
            diagnostics.add("attempts greater than zero requires lastAttemptAt")
    timeout_ms = integer(callback, "timeoutMs")
    if timeout_ms is not None and not 1 <= timeout_ms <= MAX_CALLBACK_TIMEOUT_MS:
        diagnostics.add(
            "timeoutMs must be an integer from 1 to "
            f"{MAX_CALLBACK_TIMEOUT_MS}"
        )
    run_id = string(callback, "runId")
    if run_id is not None and run_id != directory_name:
        diagnostics.add(
            f"runId {run_id!r} does not match directory {directory_name!r}"
        )
    status = string(callback, "status")
    if status is not None and status not in CALLBACK_STATUSES:
        diagnostics.add(f"unsupported callback status {status!r}")
    terminal_completed = string(callback, "terminalCompletedAt")
    terminal_status = string(callback, "terminalStatus")
    if (terminal_completed is None) != (terminal_status is None):
        diagnostics.add(
            "terminalCompletedAt and terminalStatus must be present together"
        )
    if terminal_status is not None and terminal_status not in TERMINAL_STATUSES:
        diagnostics.add(f"unsupported terminalStatus {terminal_status!r}")
    notifier_pid = integer(callback, "notifierPid")
    notifier_started = string(callback, "notifierStartedAt")
    if (notifier_pid is None) != (notifier_started is None):
        diagnostics.add(
            "notifierPid and notifierStartedAt must be present together"
        )
    if status in {"delivered", "sending"}:
        for field in ("clientUserMessageId", "deadlineAt"):
            if string(callback, field) is None:
                diagnostics.add(f"{status} callback requires {field}")
    if status == "delivered":
        if string(callback, "deliveredAt") is None:
            diagnostics.add("delivered callback requires deliveredAt")
        if terminal_completed is None:
            diagnostics.add("delivered callback requires terminalCompletedAt")
        if terminal_status not in TERMINAL_STATUSES:
            diagnostics.add("delivered callback requires a terminalStatus")
    if status == "sending":
        if notifier_pid is None:
            diagnostics.add("sending callback requires notifierPid")
        if notifier_started is None:
            diagnostics.add("sending callback requires notifierStartedAt")
        if terminal_completed is None:
            diagnostics.add("sending callback requires terminalCompletedAt")
        if terminal_status not in TERMINAL_STATUSES:
            diagnostics.add("sending callback requires a terminalStatus")
    diagnostics.extend(
        _optional_type_errors(
            callback,
            strings=(
                "clientUserMessageId",
                "deadlineAt",
                "deliveredAt",
                "error",
                "lastAttemptAt",
                "notifierStartedAt",
                "terminalCompletedAt",
                "terminalFingerprint",
                "terminalStatus",
                "turnId",
            ),
            integers=("notifierPid",),
        )
    )
    diagnostics.extend(
        _timestamp_errors(
            callback,
            (
                "createdAt",
                "deadlineAt",
                "deliveredAt",
                "lastAttemptAt",
                "terminalCompletedAt",
                "updatedAt",
            ),
        )
    )
    for earlier, later in (
        ("createdAt", "terminalCompletedAt"),
        ("createdAt", "deadlineAt"),
        ("createdAt", "lastAttemptAt"),
        ("createdAt", "deliveredAt"),
        ("createdAt", "updatedAt"),
        ("terminalCompletedAt", "deadlineAt"),
        ("terminalCompletedAt", "lastAttemptAt"),
        ("terminalCompletedAt", "deliveredAt"),
        ("terminalCompletedAt", "updatedAt"),
        ("lastAttemptAt", "deliveredAt"),
        ("lastAttemptAt", "deadlineAt"),
        ("lastAttemptAt", "updatedAt"),
        ("deliveredAt", "updatedAt"),
    ):
        chronology = _ordered_timestamp_error(callback, earlier, later)
        if chronology is not None:
            diagnostics.add(chronology)
    deadline = as_utc(callback.get("deadlineAt"))
    updated = as_utc(callback.get("updatedAt"))
    observed_at = datetime.now(UTC) if now is None else now.astimezone(UTC)
    if updated is not None and updated > observed_at:
        diagnostics.add("updatedAt cannot be in the future")
    if (
        deadline is not None
        and updated is not None
        and timeout_ms is not None
        and 1 <= timeout_ms <= MAX_CALLBACK_TIMEOUT_MS
        and deadline - updated > timedelta(milliseconds=timeout_ms)
    ):
        diagnostics.add("deadlineAt exceeds the configured timeout window")
    if (
        deadline is not None
        and timeout_ms is not None
        and 1 <= timeout_ms <= MAX_CALLBACK_TIMEOUT_MS
        and deadline - observed_at > timedelta(milliseconds=timeout_ms)
    ):
        diagnostics.add("deadlineAt exceeds the current timeout window")
    return diagnostics.error()
