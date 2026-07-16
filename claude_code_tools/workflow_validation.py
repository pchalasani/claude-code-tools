"""Schema and chronology validation for durable workflow records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from claude_code_tools.workflow_cli_manifest import (
    CALLBACK_V1_MANIFEST,
    STATE_V1_MANIFEST,
    STEP_V1_MANIFEST,
)
from claude_code_tools.workflow_cli_snapshots import (
    CallbackRecord,
    NONTERMINAL_STATUSES,
    RunRecord,
    RunState,
    StepRecord,
    TERMINAL_STATUSES,
    parse_timestamp,
)

CALLBACK_STATUSES = frozenset({"armed", "delivered", "failed", "sending", "unknown"})
MAX_STATE_STEPS = 1_000
MAX_CALLBACK_DELIVERY_SUBMISSIONS = 5
MAX_CALLBACK_TIMEOUT_MS = 604_800_000
MAX_RUN_CONCURRENCY = 64
MAX_RUN_AGENT_INVOCATIONS = 1_000
MAX_VALIDATION_DIAGNOSTICS = 100
MAX_VALIDATION_DIAGNOSTIC_BYTES = 16 * 1024
_MAX_DIAGNOSTIC_FRAGMENT_CHARS = 1_024


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    """Truncate text without splitting a UTF-8 code point."""
    candidate = value[:maximum_bytes]
    encoded = candidate.encode("utf-8")
    if len(candidate) == len(value) and len(encoded) <= maximum_bytes:
        return value
    suffix = " [truncated]"
    suffix_bytes = suffix.encode("utf-8")
    if maximum_bytes <= len(suffix_bytes):
        return suffix_bytes[:maximum_bytes].decode("utf-8", errors="ignore")
    prefix_bytes = encoded[: maximum_bytes - len(suffix_bytes)]
    prefix = prefix_bytes.decode("utf-8", errors="ignore")
    return f"{prefix}{suffix}"


def bounded_repr(value: object) -> str:
    """Return a representation with allocation bounded before formatting."""
    if isinstance(value, str):
        prefix = value[:_MAX_DIAGNOSTIC_FRAGMENT_CHARS]
        rendered = repr(prefix)
        if len(prefix) != len(value):
            rendered += " [truncated value]"
        return rendered
    return repr(value)


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
            remaining = MAX_VALIDATION_DIAGNOSTIC_BYTES - self._bytes - separator_bytes
            bounded = _truncate_utf8(marker, max(0, remaining))
            if bounded:
                self.messages.append(bounded)
                self._seen.add(bounded)
                self._bytes += separator_bytes + len(bounded.encode("utf-8"))
            self.sealed = True
            return
        separator_bytes = 2 if self.messages else 0
        remaining = MAX_VALIDATION_DIAGNOSTIC_BYTES - self._bytes - separator_bytes
        if remaining <= 0:
            self.sealed = True
            return
        bounded = _truncate_utf8(message, remaining)
        self.messages.append(bounded)
        self._seen.add(bounded)
        self._bytes += separator_bytes + len(bounded.encode("utf-8"))
        if bounded != message:
            self.sealed = True

    def add_fragments(self, *fragments: object) -> None:
        """Add a diagnostic assembled only from bounded fragments."""
        pieces = []
        remaining = MAX_VALIDATION_DIAGNOSTIC_BYTES
        for fragment in fragments:
            text = fragment if isinstance(fragment, str) else bounded_repr(fragment)
            bounded = _truncate_utf8(text, remaining)
            pieces.append(bounded)
            remaining -= len(bounded.encode("utf-8"))
            if remaining <= 0:
                break
        self.add("".join(pieces))

    def extend(self, messages: list[str]) -> None:
        """Add diagnostics until either aggregate budget is exhausted."""
        for message in messages:
            self.add(message)
            if self.sealed:
                break

    def error(self) -> str | None:
        """Return the bounded aggregate diagnostic."""
        return "; ".join(self.messages) or None


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
    parsed = _timestamp_values(value, fields)
    return _timestamp_errors_from_values(value, fields, parsed)


def _timestamp_values(
    value: Mapping[str, object],
    fields: tuple[str, ...],
) -> dict[str, datetime | None]:
    """Normalize a bounded set of timestamp fields exactly once."""
    return {field: as_utc(value.get(field)) for field in fields}


def _timestamp_errors_from_values(
    value: Mapping[str, object],
    fields: tuple[str, ...],
    parsed: Mapping[str, datetime | None],
) -> list[str]:
    """Report invalid timestamp strings from pre-normalized values."""
    return [
        f"{key} must be a valid ISO timestamp"
        for key in fields
        if string(value, key) is not None and parsed[key] is None
    ]


def _ordered_timestamp_error(
    value: Mapping[str, object], earlier: str, later: str
) -> str | None:
    """Return an error when two present valid lifecycle times are reversed."""
    parsed = _timestamp_values(value, (earlier, later))
    return _ordered_timestamp_error_from_values(parsed, earlier, later)


def _ordered_timestamp_error_from_values(
    parsed: Mapping[str, datetime | None],
    earlier: str,
    later: str,
) -> str | None:
    """Check lifecycle ordering using pre-normalized timestamp fields."""
    earlier_time = parsed[earlier]
    later_time = parsed[later]
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
            required_strings=STEP_V1_MANIFEST.required_strings,
            required_integers=STEP_V1_MANIFEST.required_integers,
        )
    )
    embedded_id = string(value, "id")
    if embedded_id is not None and embedded_id != key:
        diagnostics.add("embedded step id does not match its owning key")
    attempt = integer(value, "attempt")
    if attempt is not None and attempt < 1:
        diagnostics.add("attempt must be a positive integer")
    status = string(value, "status")
    if status not in {"canceled", "completed", "failed", "running"}:
        if status is not None:
            diagnostics.add_fragments(
                "unsupported step status ",
                bounded_repr(status),
            )
    completed = string(value, "completedAt")
    if status in TERMINAL_STATUSES and completed is None:
        diagnostics.add("terminal step requires completedAt")
    if status == "running" and completed is not None:
        diagnostics.add("running step cannot have completedAt")
    diagnostics.extend(
        _optional_type_errors(
            value,
            strings=STEP_V1_MANIFEST.optional_strings,
            integers=STEP_V1_MANIFEST.optional_integers,
        )
    )
    diagnostics.extend(_timestamp_errors(value, STEP_V1_MANIFEST.timestamp_fields))
    chronology = _ordered_timestamp_error(value, "startedAt", "completedAt")
    if chronology is not None:
        diagnostics.add(chronology)
    return diagnostics.messages


def _normalized_step(
    key: str,
    value: Mapping[str, object],
    errors: list[str],
) -> StepRecord:
    """Build one typed step from the same pass that validated it."""
    return StepRecord(
        id=key,
        label=string(value, "label") or string(value, "id") or key,
        status="malformed" if errors else string(value, "status") or "unknown",
        attempt=integer(value, "attempt"),
        started_at=string(value, "startedAt"),
        completed_at=string(value, "completedAt"),
        error="; ".join(errors) if errors else string(value, "error"),
        worker_pid=integer(value, "workerPid"),
        thread_id=string(value, "threadId"),
    )


def _malformed_step(key: str, value: object) -> StepRecord:
    """Preserve a non-object step with a bounded diagnostic."""
    diagnostics = _Diagnostics()
    diagnostics.add_fragments(
        "step ",
        bounded_repr(key),
        " must be a JSON object, got ",
        type(value).__name__,
    )
    return StepRecord(
        id=key,
        label=key,
        status="malformed",
        attempt=None,
        started_at=None,
        completed_at=None,
        error=diagnostics.error(),
        worker_pid=None,
        thread_id=None,
    )


def parse_state(
    state: Mapping[str, object],
    directory_name: str,
) -> tuple[RunState, str | None]:
    """Validate and normalize one durable state mapping in a single pass."""
    diagnostics = _Diagnostics()
    normalized_steps: list[StepRecord] = []
    diagnostics.extend(
        _schema_errors(
            state,
            required_strings=STATE_V1_MANIFEST.required_strings,
            required_integers=STATE_V1_MANIFEST.required_integers,
        )
    )
    if integer(state, "version") != 1:
        diagnostics.add("version must equal 1")
    concurrency = integer(state, "concurrency")
    if concurrency is not None and not 1 <= concurrency <= MAX_RUN_CONCURRENCY:
        diagnostics.add(
            f"concurrency must be an integer from 1 to {MAX_RUN_CONCURRENCY}"
        )
    agent_invocations = integer(state, "agentInvocations")
    if agent_invocations is not None and agent_invocations < 0:
        diagnostics.add("agentInvocations cannot be negative")
    max_agent_invocations = integer(state, "maxAgentInvocations")
    if max_agent_invocations is not None and not (
        1 <= max_agent_invocations <= MAX_RUN_AGENT_INVOCATIONS
    ):
        diagnostics.add(
            "maxAgentInvocations must be an integer from 1 to "
            f"{MAX_RUN_AGENT_INVOCATIONS}"
        )
    run_id = string(state, "runId")
    if run_id is not None and run_id != directory_name:
        diagnostics.add_fragments(
            "runId ",
            bounded_repr(run_id),
            " does not match directory ",
            bounded_repr(directory_name),
        )
    if mapping(state.get("steps")) is None:
        diagnostics.add("steps must be a JSON object")
    diagnostics.extend(
        _optional_type_errors(
            state,
            strings=STATE_V1_MANIFEST.optional_strings,
            integers=STATE_V1_MANIFEST.optional_integers,
            booleans=STATE_V1_MANIFEST.optional_booleans,
        )
    )
    timestamp_fields = STATE_V1_MANIFEST.timestamp_fields
    state_times = _timestamp_values(state, timestamp_fields)
    diagnostics.extend(
        _timestamp_errors_from_values(state, timestamp_fields, state_times)
    )
    for earlier, later in (
        ("createdAt", "completedAt"),
        ("createdAt", "runnerStartedAt"),
        ("createdAt", "startedAt"),
        ("createdAt", "updatedAt"),
        ("runnerStartedAt", "completedAt"),
        ("runnerStartedAt", "updatedAt"),
        ("startedAt", "completedAt"),
        ("startedAt", "updatedAt"),
        ("completedAt", "updatedAt"),
    ):
        chronology = _ordered_timestamp_error_from_values(
            state_times,
            earlier,
            later,
        )
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
                f"steps contains {len(steps)} entries; maximum is {MAX_STATE_STEPS}"
            )
        else:
            for key, raw_step in steps.items():
                step_key = str(key)
                step = mapping(raw_step)
                if step is None:
                    diagnostics.add_fragments(
                        "step ",
                        bounded_repr(step_key),
                        " must be a JSON object",
                    )
                    normalized_steps.append(_malformed_step(step_key, raw_step))
                    continue
                errors = step_errors(step_key, step)
                normalized_steps.append(_normalized_step(step_key, step, errors))
                for error in errors:
                    diagnostics.add_fragments(
                        "step ",
                        bounded_repr(step_key),
                        ": ",
                        error,
                    )
                    if diagnostics.sealed:
                        break
                for step_field in ("startedAt", "completedAt"):
                    step_time = as_utc(step.get(step_field))
                    for lower_field in ("createdAt", "startedAt"):
                        lower_time = state_times[lower_field]
                        if (
                            step_time is not None
                            and lower_time is not None
                            and step_time < lower_time
                        ):
                            diagnostics.add_fragments(
                                "step ",
                                bounded_repr(step_key),
                                f": {step_field} cannot precede {lower_field}",
                            )
                    updated_time = state_times["updatedAt"]
                    if (
                        step_time is not None
                        and updated_time is not None
                        and updated_time < step_time
                    ):
                        diagnostics.add_fragments(
                            "step ",
                            bounded_repr(step_key),
                            f": {step_field} cannot follow updatedAt",
                        )
                if status in TERMINAL_STATUSES and string(step, "status") == "running":
                    diagnostics.add_fragments(
                        "terminal run cannot contain running step ",
                        bounded_repr(step_key),
                    )
    oldest = datetime.min.replace(tzinfo=UTC)
    typed = RunState(
        run_id=run_id,
        status=status,
        workflow_path=string(state, "workflowPath"),
        created_at=string(state, "createdAt"),
        updated_at=string(state, "updatedAt"),
        started_at=string(state, "startedAt"),
        completed_at=completed,
        error=string(state, "error"),
        cleanup_pending=state.get("cleanupPending") is True,
        pid=pid,
        pid_started_at=pid_started,
        terminal_fingerprint=string(state, "terminalFingerprint"),
        steps=tuple(
            sorted(
                normalized_steps,
                key=lambda step: (as_utc(step.started_at) or oldest, step.id),
            )
        ),
    )
    return typed, diagnostics.error()


def validate_state(state: Mapping[str, object], directory_name: str) -> str | None:
    """Validate the durable version-1 run-state envelope."""
    return parse_state(state, directory_name)[1]


def validate_state_observation(
    state: RunState,
    now: datetime,
) -> str | None:
    """Validate state timestamps relative to one captured observation time.

    Structural validation is deliberately timeless.  Callers that construct an
    observational snapshot use this companion check with the same instant used
    for process classification, ordering, and duration calculations.

    Args:
        state: Structurally validated typed durable state.
        now: Aware timestamp captured once for the complete observation.

    Returns:
        A bounded diagnostic, or ``None`` when ordering timestamps are not in
        the future.
    """
    diagnostics = _Diagnostics()
    observed_at = now.astimezone(UTC)
    for field, raw_value in (
        ("createdAt", state.created_at),
        ("updatedAt", state.updated_at),
    ):
        value = as_utc(raw_value)
        if value is not None and value > observed_at:
            diagnostics.add(f"{field} cannot be in the future")
    return diagnostics.error()


def parse_callback(
    callback: Mapping[str, object],
    directory_name: str,
) -> tuple[CallbackRecord, str | None]:
    """Validate and normalize callback metadata in one bounded pass."""
    diagnostics = _Diagnostics()
    typed = CallbackRecord(
        status=string(callback, "status"),
        attempts=integer(callback, "attempts"),
        created_at=string(callback, "createdAt"),
        updated_at=string(callback, "updatedAt"),
        delivered_at=string(callback, "deliveredAt"),
        deadline_at=string(callback, "deadlineAt"),
        last_attempt_at=string(callback, "lastAttemptAt"),
        terminal_completed_at=string(callback, "terminalCompletedAt"),
        terminal_status=string(callback, "terminalStatus"),
        terminal_fingerprint=string(callback, "terminalFingerprint"),
        client_user_message_id=string(callback, "clientUserMessageId"),
        endpoint=string(callback, "endpoint"),
        thread_id=string(callback, "threadId"),
        timeout_ms=integer(callback, "timeoutMs"),
        turn_id=string(callback, "turnId"),
        notifier_pid=integer(callback, "notifierPid"),
        notifier_started_at=string(callback, "notifierStartedAt"),
        error=string(callback, "error"),
    )
    diagnostics.extend(
        _schema_errors(
            callback,
            required_strings=CALLBACK_V1_MANIFEST.required_strings,
            required_integers=CALLBACK_V1_MANIFEST.required_integers,
        )
    )
    if integer(callback, "version") != 1:
        diagnostics.add("version must equal 1")
    attempts = integer(callback, "attempts")
    if attempts is not None and attempts < 0:
        diagnostics.add("attempts cannot be negative")
    if attempts is not None and attempts > MAX_CALLBACK_DELIVERY_SUBMISSIONS:
        diagnostics.add(
            f"attempts cannot exceed {MAX_CALLBACK_DELIVERY_SUBMISSIONS} submissions"
        )
    status = string(callback, "status")
    if attempts is not None and attempts > 0 and status != "delivered":
        if string(callback, "lastAttemptAt") is None:
            diagnostics.add("attempts greater than zero requires lastAttemptAt")
    timeout_ms = integer(callback, "timeoutMs")
    if timeout_ms is not None and not 1 <= timeout_ms <= MAX_CALLBACK_TIMEOUT_MS:
        diagnostics.add(
            f"timeoutMs must be an integer from 1 to {MAX_CALLBACK_TIMEOUT_MS}"
        )
    run_id = string(callback, "runId")
    if run_id is not None and run_id != directory_name:
        diagnostics.add_fragments(
            "runId ",
            bounded_repr(run_id),
            " does not match directory ",
            bounded_repr(directory_name),
        )
    if status is not None and status not in CALLBACK_STATUSES:
        diagnostics.add_fragments(
            "unsupported callback status ",
            bounded_repr(status),
        )
    terminal_completed = string(callback, "terminalCompletedAt")
    terminal_status = string(callback, "terminalStatus")
    if (terminal_completed is None) != (terminal_status is None):
        diagnostics.add(
            "terminalCompletedAt and terminalStatus must be present together"
        )
    if terminal_status is not None and terminal_status not in TERMINAL_STATUSES:
        diagnostics.add_fragments(
            "unsupported terminalStatus ",
            bounded_repr(terminal_status),
        )
    notifier_pid = integer(callback, "notifierPid")
    notifier_started = string(callback, "notifierStartedAt")
    if (notifier_pid is None) != (notifier_started is None):
        diagnostics.add("notifierPid and notifierStartedAt must be present together")
    if status in {"delivered", "sending"}:
        for field in ("clientUserMessageId", "deadlineAt"):
            if string(callback, field) is None:
                diagnostics.add(f"{status} callback requires {field}")
    if status == "delivered":
        if attempts is not None and attempts < 1:
            diagnostics.add("delivered callback requires at least one attempt")
        if string(callback, "lastAttemptAt") is None:
            diagnostics.add("delivered callback requires lastAttemptAt")
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
            strings=CALLBACK_V1_MANIFEST.optional_strings,
            integers=CALLBACK_V1_MANIFEST.optional_integers,
        )
    )
    diagnostics.extend(
        _timestamp_errors(callback, CALLBACK_V1_MANIFEST.timestamp_fields)
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
    if (
        deadline is not None
        and updated is not None
        and timeout_ms is not None
        and 1 <= timeout_ms <= MAX_CALLBACK_TIMEOUT_MS
        and deadline - updated > timedelta(milliseconds=timeout_ms)
    ):
        diagnostics.add("deadlineAt exceeds the configured timeout window")
    return typed, diagnostics.error()


def parse_run_record(
    directory: Path,
    *,
    state: Mapping[str, object] | None = None,
    callback: Mapping[str, object] | None = None,
) -> RunRecord:
    """Validate raw persistence mappings once at their typed boundary."""
    typed_state = RunState()
    state_error: str | None = None
    if state is not None:
        typed_state, state_error = parse_state(state, directory.name)
    typed_callback: CallbackRecord | None = None
    callback_error: str | None = None
    if callback is not None:
        typed_callback, callback_error = parse_callback(
            callback,
            directory.name,
        )
    return RunRecord(
        directory=directory,
        state=typed_state,
        callback=typed_callback,
        state_error=state_error,
        callback_error=callback_error,
    )


def validate_callback(
    callback: Mapping[str, object],
    directory_name: str,
) -> str | None:
    """Return the diagnostic from the canonical callback parser."""
    return parse_callback(callback, directory_name)[1]


def validate_callback_observation(
    callback: CallbackRecord,
    now: datetime,
) -> str | None:
    """Validate callback timestamps against one required snapshot instant."""
    diagnostics = _Diagnostics()
    observed_at = now.astimezone(UTC)
    updated = as_utc(callback.updated_at)
    deadline = as_utc(callback.deadline_at)
    if updated is not None and updated > observed_at:
        diagnostics.add("updatedAt cannot be in the future")
    timeout_ms = callback.timeout_ms
    if (
        deadline is not None
        and timeout_ms is not None
        and 1 <= timeout_ms <= MAX_CALLBACK_TIMEOUT_MS
        and deadline - observed_at > timedelta(milliseconds=timeout_ms)
    ):
        diagnostics.add("deadlineAt exceeds the current timeout window")
    return diagnostics.error()
