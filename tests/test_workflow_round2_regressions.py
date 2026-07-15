"""Regressions for continuation-review round-two workflow defects."""

from __future__ import annotations

import json
import tracemalloc
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools import (
    workflow_cli,
    workflow_runs,
    workflow_validation,
)
from claude_code_tools.workflow_cli import cli
from claude_code_tools.workflow_processes import ProcessProbe

TIME = "2026-07-14T14:00:00Z"
LATER = "2026-07-14T14:01:00Z"
STRONG_IDENTITY = "linux:00000000-0000-0000-0000-000000000000:123"
TERMINAL_TIME = "2026-07-14T14:01:00Z"
ATTEMPT_TIME = "2026-07-14T14:02:00Z"
DELIVERY_TIME = "2026-07-14T14:03:00Z"
UPDATE_TIME = "2026-07-14T14:04:00Z"
DEADLINE_TIME = UPDATE_TIME


def _state(run_id: str, *, status: str = "completed") -> dict[str, object]:
    """Build one minimal durable run state."""
    state: dict[str, object] = {
        "concurrency": 1,
        "createdAt": TIME,
        "cwd": "/work",
        "runId": run_id,
        "status": status,
        "steps": {},
        "updatedAt": TIME,
        "version": 1,
        "workflowHash": "hash",
        "workflowPath": "/work/workflow.js",
    }
    if status in workflow_runs.TERMINAL_STATUSES:
        state["completedAt"] = TIME
    return state


def _write_state(home: Path, state: dict[str, object]) -> Path:
    """Persist a state under its owning run directory."""
    directory = home / "runs" / str(state["runId"])
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    return directory


def _callback(run_id: str, *, status: str = "delivered") -> dict[str, object]:
    """Build callback metadata with producer-backed correlation fields."""
    callback: dict[str, object] = {
        "attempts": 1,
        "clientUserMessageId": "message-1",
        "createdAt": TIME,
        "deadlineAt": DEADLINE_TIME,
        "endpoint": "unix:///tmp/app-server.sock",
        "lastAttemptAt": ATTEMPT_TIME,
        "runId": run_id,
        "status": status,
        "terminalCompletedAt": TERMINAL_TIME,
        "terminalStatus": "completed",
        "threadId": "thread",
        "timeoutMs": 1000,
        "updatedAt": UPDATE_TIME,
        "version": 1,
    }
    if status == "delivered":
        callback["deliveredAt"] = DELIVERY_TIME
    if status == "sending":
        callback["notifierPid"] = 123
        callback["notifierStartedAt"] = STRONG_IDENTITY
    return callback


@pytest.mark.parametrize(
    ("changes", "diagnostic"),
    [
        (
            {"createdAt": LATER, "updatedAt": TIME, "completedAt": LATER},
            "updatedAt cannot precede createdAt",
        ),
        (
            {"startedAt": LATER, "completedAt": TIME, "updatedAt": LATER},
            "completedAt cannot precede startedAt",
        ),
    ],
)
def test_reversed_run_lifecycle_is_malformed(
    tmp_path: Path,
    changes: dict[str, object],
    diagnostic: str,
) -> None:
    """A terminal run cannot persist reversed lifecycle timestamps."""
    state = _state("reversed-run")
    state.update(changes)
    directory = _write_state(tmp_path, state)

    record = workflow_runs.load_run(directory)

    assert record.status == "malformed"
    assert diagnostic in (record.state_error or "")


@pytest.mark.parametrize(
    ("changes", "diagnostic"),
    [
        (
            {"createdAt": LATER, "completedAt": TIME, "updatedAt": LATER},
            "completedAt cannot precede createdAt",
        ),
        (
            {"runnerStartedAt": "2026-07-14T13:59:00Z"},
            "runnerStartedAt cannot precede createdAt",
        ),
        (
            {"runnerStartedAt": LATER},
            "updatedAt cannot precede runnerStartedAt",
        ),
    ],
)
def test_run_and_runner_timestamps_stay_within_run_lifecycle(
    changes: dict[str, object],
    diagnostic: str,
) -> None:
    """Completion and runner timestamps stay within producer update bounds."""
    state = _state("bounded-runner")
    state.update(changes)

    error = workflow_validation.validate_state(state, "bounded-runner")

    assert error is not None
    assert diagnostic in error


def test_reversed_step_lifecycle_is_malformed(tmp_path: Path) -> None:
    """A completed step cannot finish before it starts."""
    state = _state("reversed-step")
    state["updatedAt"] = LATER
    state["completedAt"] = LATER
    state["steps"] = {
        "step": {
            "attempt": 1,
            "completedAt": TIME,
            "fingerprint": "fingerprint",
            "id": "step",
            "label": "step",
            "startedAt": LATER,
            "status": "completed",
        }
    }
    directory = _write_state(tmp_path, state)

    record = workflow_runs.load_run(directory)

    assert record.status == "malformed"
    assert "completedAt cannot precede startedAt" in (record.state_error or "")


@pytest.mark.parametrize("field", ["startedAt", "completedAt"])
def test_step_timestamps_cannot_follow_enclosing_update(field: str) -> None:
    """A state update is published after every persisted step mutation."""
    state = _state("future-step")
    state["steps"] = {
        "step": {
            "attempt": 1,
            "completedAt": TIME,
            "fingerprint": "fingerprint",
            "id": "step",
            "label": "step",
            "startedAt": TIME,
            "status": "completed",
        }
    }
    steps = state["steps"]
    assert isinstance(steps, dict)
    step = steps["step"]
    assert isinstance(step, dict)
    step[field] = LATER

    error = workflow_validation.validate_state(state, "future-step")

    assert error is not None
    assert f"step 'step': {field} cannot follow updatedAt" in error


@pytest.mark.parametrize("field", ["startedAt", "completedAt"])
@pytest.mark.parametrize(
    ("lower_field", "step_time"),
    [
        ("createdAt", "2026-07-14T13:59:00Z"),
        ("startedAt", "2026-07-14T14:00:30Z"),
    ],
)
def test_step_timestamps_cannot_precede_run_lifecycle(
    field: str,
    lower_field: str,
    step_time: str,
) -> None:
    """A step cannot exist before its enclosing run has started."""
    state = _state("early-step")
    if lower_field == "startedAt":
        state.update(
            {
                "startedAt": LATER,
                "completedAt": "2026-07-14T14:02:00Z",
                "updatedAt": "2026-07-14T14:02:00Z",
            }
        )
    state["steps"] = {
        "step": {
            "attempt": 1,
            "completedAt": step_time,
            "fingerprint": "fingerprint",
            "id": "step",
            "label": "step",
            "startedAt": step_time,
            "status": "completed",
        }
    }

    error = workflow_validation.validate_state(state, "early-step")

    assert error is not None
    assert f"step 'step': {field} cannot precede {lower_field}" in error


def test_sending_callback_requires_terminal_generation(tmp_path: Path) -> None:
    """Notifier ownership alone cannot make an impossible send state valid."""
    state = _state("sending-without-generation")
    directory = _write_state(tmp_path, state)
    callback = {
        "attempts": 0,
        "clientUserMessageId": "message-1",
        "createdAt": TIME,
        "deadlineAt": LATER,
        "endpoint": "unix:///tmp/app-server.sock",
        "notifierPid": 123,
        "notifierStartedAt": STRONG_IDENTITY,
        "runId": state["runId"],
        "status": "sending",
        "threadId": "thread",
        "timeoutMs": 1000,
        "updatedAt": TIME,
        "version": 1,
    }
    (directory / "completion-notification.json").write_text(
        json.dumps(callback),
        encoding="utf-8",
    )

    record = workflow_runs.load_run(directory)

    assert record.callback_status == "invalid"
    assert "sending callback requires terminalCompletedAt" in (
        record.callback_error or ""
    )


@pytest.mark.parametrize("status", ["delivered", "sending"])
@pytest.mark.parametrize("field", ["clientUserMessageId", "deadlineAt"])
def test_active_callback_requires_producer_correlation(
    status: str,
    field: str,
) -> None:
    """Delivery states cannot omit producer-backed correlation metadata."""
    callback = _callback("correlated", status=status)
    callback.pop(field)

    diagnostic = workflow_validation.validate_callback(callback, "correlated")

    assert diagnostic is not None
    assert f"{status} callback requires {field}" in diagnostic


def test_armed_callback_allows_correlation_to_be_unpublished() -> None:
    """The initial nonterminal callback exists before terminal correlation."""
    callback = _callback("armed", status="armed")
    for field in (
        "clientUserMessageId",
        "deadlineAt",
        "lastAttemptAt",
        "terminalCompletedAt",
        "terminalStatus",
    ):
        callback.pop(field)
    callback["attempts"] = 0
    callback["updatedAt"] = TIME

    assert workflow_validation.validate_callback(callback, "armed") is None


@pytest.mark.parametrize(
    ("attempts", "include_last_attempt", "diagnostic"),
    [
        (-1, False, "attempts cannot be negative"),
        (1, False, "attempts greater than zero requires lastAttemptAt"),
        (6, True, "attempts cannot exceed 5 submissions"),
    ],
)
def test_callback_rejects_impossible_attempt_metadata(
    attempts: int,
    include_last_attempt: bool,
    diagnostic: str,
) -> None:
    """Attempt counters retain the timestamp written by the same mutation."""
    callback = _callback("attempt-metadata", status="armed")
    callback["attempts"] = attempts
    if not include_last_attempt:
        callback.pop("lastAttemptAt", None)

    error = workflow_validation.validate_callback(
        callback,
        "attempt-metadata",
    )

    assert error is not None
    assert diagnostic in error


@pytest.mark.parametrize("timeout_ms", [0, -1, 604_800_001])
def test_callback_timeout_matches_producer_range(timeout_ms: int) -> None:
    """Persisted callbacks cannot exceed the producer's timeout range."""
    callback = _callback("timeout-range", status="armed")
    callback["timeoutMs"] = timeout_ms

    error = workflow_validation.validate_callback(callback, "timeout-range")

    assert error is not None
    assert "timeoutMs must be an integer from 1 to 604800000" in error


@pytest.mark.parametrize("timeout_ms", [1, 604_800_000])
def test_callback_timeout_accepts_producer_boundaries(timeout_ms: int) -> None:
    """Both inclusive producer timeout boundaries remain valid."""
    callback = _callback("timeout-boundary", status="armed")
    callback["timeoutMs"] = timeout_ms

    assert (
        workflow_validation.validate_callback(callback, "timeout-boundary")
        is None
    )


def test_callback_deadline_cannot_exceed_its_timeout_window() -> None:
    """A forged absolute deadline cannot extend notifier retries."""
    callback = _callback("deadline-window", status="armed")
    callback["attempts"] = 0
    callback.pop("lastAttemptAt")
    callback["deadlineAt"] = "2099-01-01T00:00:00Z"
    callback["timeoutMs"] = 1

    error = workflow_validation.validate_callback(
        callback,
        "deadline-window",
    )

    assert error is not None
    assert "deadlineAt exceeds the configured timeout window" in error


def test_callback_future_update_cannot_extend_deadline_window() -> None:
    """Forged future timestamps cannot move the retry-window anchor."""
    callback = _callback("future-deadline", status="armed")
    callback["attempts"] = 0
    callback.pop("lastAttemptAt")
    callback["updatedAt"] = "2099-01-01T00:00:00Z"
    callback["deadlineAt"] = "2099-01-01T00:00:00Z"
    callback["timeoutMs"] = 1

    error = workflow_validation.validate_callback(
        callback,
        "future-deadline",
        now=datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert error is not None
    assert "updatedAt cannot be in the future" in error
    assert "deadlineAt exceeds the current timeout window" in error


def test_callback_fingerprint_marks_same_millisecond_generation_stale(
    tmp_path: Path,
) -> None:
    """Payload identity distinguishes otherwise identical generations."""
    state = _state("fingerprint-generation")
    state["terminalFingerprint"] = "b" * 64
    directory = _write_state(tmp_path, state)
    callback = _callback(str(state["runId"]))
    callback["terminalCompletedAt"] = state["completedAt"]
    callback["terminalFingerprint"] = "a" * 64
    (directory / "completion-notification.json").write_text(
        json.dumps(callback),
        encoding="utf-8",
    )

    run = workflow_runs.load_run(directory)

    assert run.callback_status == "stale"


@pytest.mark.parametrize(
    ("changes", "diagnostic"),
    [
        (
            {"terminalCompletedAt": "2026-07-14T13:59:00Z"},
            "terminalCompletedAt cannot precede createdAt",
        ),
        (
            {"deadlineAt": "2026-07-14T14:00:30Z"},
            "deadlineAt cannot precede terminalCompletedAt",
        ),
        (
            {"lastAttemptAt": "2026-07-14T14:00:30Z"},
            "lastAttemptAt cannot precede terminalCompletedAt",
        ),
        (
            {"lastAttemptAt": "2026-07-14T14:03:30Z"},
            "deliveredAt cannot precede lastAttemptAt",
        ),
        (
            {"updatedAt": "2026-07-14T14:01:30Z"},
            "updatedAt cannot precede lastAttemptAt",
        ),
    ],
)
def test_callback_chronology_rejects_impossible_snapshots(
    changes: dict[str, object],
    diagnostic: str,
) -> None:
    """Callback lifecycle timestamps follow producer mutation order."""
    callback = _callback("chronology")
    callback.update(changes)

    error = workflow_validation.validate_callback(callback, "chronology")

    assert error is not None
    assert diagnostic in error


def test_callback_confirmation_may_follow_submission_deadline() -> None:
    """An accepted submission can be confirmed just after its deadline."""
    callback = _callback("confirmation-after-deadline")
    delivered_at = "2026-07-14T14:05:00.001Z"
    callback["lastAttemptAt"] = "2026-07-14T14:04:59.999Z"
    callback["deadlineAt"] = "2026-07-14T14:05:00.000Z"
    callback["deliveredAt"] = delivered_at
    callback["updatedAt"] = delivered_at

    assert (
        workflow_validation.validate_callback(
            callback,
            "confirmation-after-deadline",
        )
        is None
    )


def test_nonterminal_run_update_cannot_precede_start(tmp_path: Path) -> None:
    """A running snapshot cannot be older than its own start timestamp."""
    state = _state("reversed-running", status="running")
    state["startedAt"] = LATER
    directory = _write_state(tmp_path, state)

    record = workflow_runs.load_run(directory)

    assert record.status == "malformed"
    assert "updatedAt cannot precede startedAt" in (record.state_error or "")


def test_oversized_step_map_is_rejected_before_step_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner's maximum bounds work before any per-step validation."""
    state = _state("too-many-steps")
    state["steps"] = {
        f"step-{index}": {}
        for index in range(workflow_validation.MAX_STATE_STEPS + 1)
    }

    def fail_step_validation(
        _key: str,
        _value: Mapping[str, object],
    ) -> list[str]:
        raise AssertionError("oversized state must skip per-step validation")

    monkeypatch.setattr(workflow_validation, "step_errors", fail_step_validation)
    monkeypatch.setattr(workflow_runs, "_step_errors", fail_step_validation)

    error = workflow_validation.validate_state(state, "too-many-steps")
    record = workflow_runs.RunRecord(
        directory=Path("too-many-steps"),
        state=state,
        state_error=error,
    )

    assert error == "steps contains 1001 entries; maximum is 1000"
    assert record.steps == ()


def test_load_run_discards_oversized_projected_step_map(tmp_path: Path) -> None:
    """A compact hostile step map is not retained after validation rejects it."""
    state = _state("projected-too-many-steps")
    state["steps"] = {str(index): None for index in range(100_000)}
    directory = _write_state(tmp_path, state)

    record = workflow_runs.load_run(directory, observe=False)

    assert record.state_error is not None
    assert "steps contains 100000 entries; maximum is 1000" in record.state_error
    assert record.state.get("steps") == {}


def test_activity_bounds_newline_heavy_error_allocation() -> None:
    """Rendering an activity summary does not split the complete error."""
    state = _state("newline-heavy-error", status="failed")
    state["error"] = "\n" * 4_000_001
    record = workflow_runs.RunRecord(
        directory=Path("newline-heavy-error"),
        state=state,
    )

    tracemalloc.start()
    try:
        activity = record.activity()
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert activity == "failed"
    assert peak_bytes < 512 * 1024


def test_direct_load_uses_practical_aggregate_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A direct observation cannot inherit the raw multi-gigabyte ceiling."""
    assert (
        workflow_runs.MAX_SINGLE_RUN_JSON_BYTES
        == workflow_runs.MAX_SCAN_JSON_BYTES
    )
    monkeypatch.setattr(workflow_runs, "MAX_SINGLE_RUN_JSON_BYTES", 256)
    state = _state("direct-budget")
    state["error"] = "x" * 1_024
    directory = _write_state(tmp_path, state)

    record = workflow_runs.load_run(directory, observe=False)

    assert record.state == {}
    assert "aggregate work limit of 256 bytes" in (record.state_error or "")


def test_validation_diagnostic_count_is_bounded() -> None:
    """Many malformed steps cannot amplify the diagnostic item count."""
    state = _state("many-errors")
    state["steps"] = {
        f"step-{index}": None
        for index in range(workflow_validation.MAX_STATE_STEPS)
    }

    error = workflow_validation.validate_state(state, "many-errors")

    assert error is not None
    assert len(error.split("; ")) <= (
        workflow_validation.MAX_VALIDATION_DIAGNOSTICS
    )
    assert "additional validation diagnostics omitted" in error


def test_validation_diagnostic_bytes_are_bounded_for_hostile_key() -> None:
    """A hostile multibyte step key cannot inflate validation output."""
    state = _state("huge-diagnostic")
    state["steps"] = {"\N{collision symbol}" * 20_000: None}

    error = workflow_validation.validate_state(state, "huge-diagnostic")

    assert error is not None
    assert len(error.encode("utf-8")) <= (
        workflow_validation.MAX_VALIDATION_DIAGNOSTIC_BYTES
    )
    assert error.endswith(" [truncated]")


def test_malformed_step_record_diagnostic_is_bounded_independently() -> None:
    """The JSON record cannot retain a hostile step key again in its error."""
    key = "x" * 4_000_000

    record = workflow_runs.StepRecord.malformed(key, None)

    assert record.id == key
    assert record.label == key
    assert record.error is not None
    assert len(record.error.encode("utf-8")) <= (
        workflow_validation.MAX_VALIDATION_DIAGNOSTIC_BYTES
    )
    assert record.error.endswith(" [truncated]")


def test_linux_compatibility_identity_mismatch_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact legacy Linux tokens stay unverifiable; mismatches are stale."""
    probe = ProcessProbe(
        "alive",
        identity=STRONG_IDENTITY,
        compatibility_identities=("linux:123",),
    )
    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        lambda _pid, *, include_legacy=True: probe,
    )

    assert workflow_runs.observed_process_state(123, "linux:123") == (
        "unverifiable"
    )
    assert workflow_runs.observed_process_state(123, "linux:124") == "stale"


def test_nonterminal_compatibility_identity_mismatch_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running snapshot reports PID reuse for a mismatched Linux token."""
    state = _state("compatibility-stale", status="running")
    state.update({"pid": 123, "pidStartedAt": "linux:124"})
    directory = _write_state(tmp_path, state)
    probe = ProcessProbe(
        "alive",
        identity=STRONG_IDENTITY,
        compatibility_identities=("linux:123",),
    )
    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        lambda _pid, *, include_legacy=True: probe,
    )

    record = workflow_runs.load_run(directory)

    assert record.recorded_status == "running"
    assert record.status == "stale"
    assert record.activity() == "supervisor PID was reused"


@pytest.mark.parametrize(
    ("field", "value"),
    [("pid", 123), ("pidStartedAt", STRONG_IDENTITY)],
)
def test_partial_supervisor_pair_is_never_granted_startup_grace(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    """Only the fully absent supervisor pair represents publication startup."""
    state = _state("partial-owner", status="running")
    state[field] = value
    directory = _write_state(tmp_path, state)

    record = workflow_runs.load_run(
        directory,
        now=datetime(2026, 7, 14, 14, 0, 1, tzinfo=UTC),
    )

    assert record.status == "malformed"
    assert "pid and pidStartedAt must be present together" in (
        record.state_error or ""
    )


def test_one_cli_response_uses_one_observation_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enumeration order cannot move runs across startup grace boundaries."""
    for run_id in ("first", "second"):
        _write_state(tmp_path, _state(run_id, status="running"))
    observed_times: list[datetime | None] = []
    original = workflow_runs._supervisor_state

    def record_time(
        state: Mapping[str, object],
        now: datetime | None = None,
        *,
        observations: dict[tuple[int, str], str | None] | None = None,
        observation_deadline: float | None = None,
        observation_report: workflow_runs.ObservationReport | None = None,
    ) -> str | None:
        """Capture the observation instant while retaining real behavior."""
        observed_times.append(now)
        return original(
            state,
            now,
            observations=observations,
            observation_deadline=observation_deadline,
            observation_report=observation_report,
        )

    fixed = datetime(2026, 7, 14, 14, 0, 4, tzinfo=UTC)
    monkeypatch.setattr(workflow_cli, "_now", lambda: fixed)
    monkeypatch.setattr(workflow_runs, "_supervisor_state", record_time)

    result = CliRunner().invoke(
        cli,
        ["--json"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert observed_times == [fixed, fixed]
    assert {
        item["status"] for item in json.loads(result.output)["runs"]
    } == {"running"}


def test_limit_defers_process_probes_for_excluded_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A display limit bounds process observations as well as output rows."""
    older = _state("older", status="running")
    older.update({"pid": 101, "pidStartedAt": STRONG_IDENTITY})
    newer = _state("newer", status="running")
    newer.update(
        {
            "createdAt": LATER,
            "updatedAt": LATER,
            "pid": 202,
            "pidStartedAt": STRONG_IDENTITY,
        }
    )
    _write_state(tmp_path, older)
    _write_state(tmp_path, newer)
    observed: list[int] = []

    def observe(pid: int, _expected: str) -> str | None:
        """Record process observations without touching the host process table."""
        observed.append(pid)
        return None

    monkeypatch.setattr(workflow_runs, "observed_process_state", observe)

    records = workflow_runs.load_runs(tmp_path, limit=1)

    assert [record.run_id for record in records] == ["newer"]
    assert observed == [202]


def test_recorded_status_prefilter_skips_impossible_process_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal-only filter never probes excluded nonterminal ownership."""
    running = _state("new-running", status="running")
    running.update(
        {
            "createdAt": LATER,
            "updatedAt": LATER,
            "pid": 202,
            "pidStartedAt": STRONG_IDENTITY,
        }
    )
    _write_state(tmp_path, running)
    _write_state(tmp_path, _state("old-completed"))

    def fail_observation(_pid: int, _expected: str) -> str | None:
        raise AssertionError("excluded nonterminal run must not be probed")

    monkeypatch.setattr(
        workflow_runs,
        "observed_process_state",
        fail_observation,
    )

    records = workflow_runs.load_runs(
        tmp_path,
        statuses=("completed",),
        limit=1,
    )

    assert [record.run_id for record in records] == ["old-completed"]


def test_filtered_scan_has_aggregate_process_probe_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A status filter cannot multiply per-process timeouts across the store."""
    for index, run_id in enumerate(("run-a", "run-b", "run-c"), start=1):
        state = _state(run_id, status="running")
        state.update(
            {
                "pid": index * 101,
                "pidStartedAt": STRONG_IDENTITY,
            }
        )
        _write_state(tmp_path, state)
    clock = iter((0.0, 0.0, 6.0))
    observed: list[int] = []

    def observe(pid: int, _expected: str) -> str | None:
        """Record the only process probe allowed before the deadline."""
        observed.append(pid)
        return "orphaned"

    monkeypatch.setattr(
        workflow_runs,
        "monotonic",
        lambda: next(clock, 6.0),
    )
    monkeypatch.setattr(workflow_runs, "observed_process_state", observe)
    report = workflow_runs.ObservationReport()

    records = workflow_runs.load_runs(
        tmp_path,
        statuses=("orphaned", "unverifiable"),
        observation_report=report,
    )

    assert observed == [303]
    assert report.complete is False
    assert report.skipped == 2
    assert [record.status for record in records] == [
        "orphaned",
        "unverifiable",
        "unverifiable",
    ]


def test_json_list_discloses_incomplete_process_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON distinguishes process-probe gaps from the output row limit."""

    def incomplete_load_runs(
        *,
        statuses: tuple[str, ...] = (),
        limit: int | None = None,
        now: datetime | None = None,
        observe: bool = True,
        observation_report: workflow_runs.ObservationReport | None = None,
    ) -> list[workflow_runs.RunRecord]:
        """Emulate a filtered scan whose live-process deadline expired."""
        del statuses, limit, now
        if observe and observation_report is not None:
            observation_report.mark_skipped()
        return []

    monkeypatch.setattr(workflow_cli, "load_runs", incomplete_load_runs)

    result = CliRunner().invoke(
        cli,
        ["--status", "orphaned", "--json"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path)},
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["complete"] is False
    assert payload["observationComplete"] is False
    assert payload["observationSkipped"] == 1
    assert payload["truncated"] is False

    human = CliRunner().invoke(
        cli,
        ["--status", "orphaned"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert human.exit_code == 0
    assert "Incomplete: process-observation budget skipped 1 observation" in (
        human.output
    )
