"""Regression tests for hostile and concurrently changing workflow state."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools import workflow_runs, workflow_store_io
from claude_code_tools.workflow_cli import cli
from claude_code_tools.workflow_processes import ProcessProbe

TIME = "2026-07-14T14:00:00Z"


def _symlink_or_skip(link: Path, target: Path, is_directory: bool = False) -> None:
    """Create a symlink or skip on a restricted Windows host."""
    try:
        link.symlink_to(target, target_is_directory=is_directory)
    except OSError as error:
        if os.name != "nt":
            raise
        pytest.skip(f"Windows host cannot create test symlinks: {error}")


def _fixed_process_probe(
    probe: ProcessProbe,
) -> Callable[..., ProcessProbe]:
    """Return a typed process probe accepting current optional arguments."""

    def observe(
        _pid: int,
        *,
        include_legacy: bool = True,
    ) -> ProcessProbe:
        """Return the configured observation."""
        del include_legacy
        return probe

    return observe


def _state(run_id: str, *, status: str = "completed") -> dict[str, object]:
    """Build a valid minimal durable state."""
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


def _write_mapping(path: Path, value: dict[str, object]) -> None:
    """Write a JSON mapping after creating its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _callback(
    run_id: str,
    *,
    status: str = "delivered",
    attempts: int = 1,
) -> dict[str, object]:
    """Build valid version-1 callback metadata.

    Args:
        run_id: Owning run identifier.
        status: Durable callback status.
        attempts: Number of delivery attempts.

    Returns:
        A JSON-compatible callback object.
    """
    callback: dict[str, object] = {
        "attempts": attempts,
        "clientUserMessageId": "message-1",
        "createdAt": TIME,
        "deadlineAt": "2026-07-15T14:00:00Z",
        "endpoint": "unix:///tmp/app-server.sock",
        "runId": run_id,
        "status": status,
        "threadId": "thread-1",
        "timeoutMs": 86_400_000,
        "updatedAt": TIME,
        "version": 1,
    }
    if attempts > 0:
        callback["lastAttemptAt"] = TIME
    if status == "delivered":
        callback.update(
            {
                "deliveredAt": TIME,
                "terminalCompletedAt": TIME,
                "terminalStatus": "completed",
            }
        )
    return callback


def _step(step_id: str, *, status: str = "completed") -> dict[str, object]:
    """Build a valid minimal durable step."""
    step: dict[str, object] = {
        "attempt": 1,
        "fingerprint": f"fingerprint-{step_id}",
        "id": step_id,
        "label": step_id,
        "startedAt": TIME,
        "status": status,
    }
    if status in workflow_runs.TERMINAL_STATUSES:
        step["completedAt"] = TIME
    return step


def test_empty_configured_workflow_home_matches_node_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit empty workflow home resolves from the current directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_WORKFLOW_HOME", "")
    assert workflow_runs.workflow_home() == tmp_path.resolve()


def test_configured_workflow_home_is_lexical_like_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Literal tildes and symlink components are not expanded or resolved."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _symlink_or_skip(link, real, True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_WORKFLOW_HOME", "~/runs")
    assert workflow_runs.workflow_home() == tmp_path / "~" / "runs"
    monkeypatch.setenv("CODEX_WORKFLOW_HOME", str(link / "child"))
    assert workflow_runs.workflow_home() == link / "child"


@pytest.mark.parametrize(
    ("case", "diagnostic"),
    [
        ("future-version", "version must equal 1"),
        ("wrong-owner", "does not match directory"),
        ("partial-delivery", "delivered callback requires deliveredAt"),
        ("unsupported-status", "unsupported callback status"),
        ("partial-terminal", "must be present together"),
        ("partial-notifier", "must be present together"),
    ],
)
def test_callback_schema_and_ownership_are_validated(
    tmp_path: Path,
    case: str,
    diagnostic: str,
) -> None:
    """Incompatible callback metadata is never reported as successful."""
    directory = tmp_path / case
    callback = _callback(directory.name)
    if case == "future-version":
        callback["version"] = 2
    elif case == "wrong-owner":
        callback["runId"] = "another-run"
    elif case == "partial-delivery":
        callback.pop("deliveredAt")
    elif case == "unsupported-status":
        callback["status"] = "queued"
    elif case == "partial-terminal":
        callback.pop("terminalStatus")
    else:
        callback["notifierPid"] = 123
    _write_mapping(directory / "state.json", _state(directory.name))
    _write_mapping(directory / "completion-notification.json", callback)

    run = workflow_runs.load_run(directory)

    assert run.callback_status == "invalid"
    assert run.callback_error is not None
    assert diagnostic in run.callback_error


@pytest.mark.parametrize(
    ("case", "diagnostic"),
    [
        ("future-version", "version must equal 1"),
        ("wrong-owner", "does not match directory"),
        ("missing-required", "concurrency must be an integer"),
        ("invalid-steps", "steps must be a JSON object"),
    ],
)
def test_state_schema_and_ownership_are_validated(
    tmp_path: Path,
    case: str,
    diagnostic: str,
) -> None:
    """Unsupported and structurally corrupt state is marked malformed."""
    directory = tmp_path / case
    state = _state(directory.name)
    if case == "future-version":
        state["version"] = 2
    elif case == "wrong-owner":
        state["runId"] = "another-run"
    elif case == "missing-required":
        state.pop("concurrency")
    else:
        state["steps"] = []
    _write_mapping(directory / "state.json", state)

    run = workflow_runs.load_run(directory)

    assert run.status == "malformed"
    assert run.state_error is not None
    assert diagnostic in run.state_error


@pytest.mark.parametrize("field", ["createdAt", "updatedAt", "completedAt"])
def test_invalid_run_timestamps_make_terminal_state_malformed(
    tmp_path: Path,
    field: str,
) -> None:
    """Terminal success requires parseable durable timestamps."""
    directory = tmp_path / field
    state = _state(directory.name)
    state[field] = "not-a-time"
    _write_mapping(directory / "state.json", state)

    run = workflow_runs.load_run(directory)

    assert run.status == "malformed"
    assert run.state_error is not None
    assert f"{field} must be a valid ISO timestamp" in run.state_error


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("deliveredAt", "not-a-time", "deliveredAt must be a valid ISO timestamp"),
        (
            "terminalCompletedAt",
            "not-a-time",
            "terminalCompletedAt must be a valid ISO timestamp",
        ),
        (
            "deliveredAt",
            "2026-07-14T13:59:59Z",
            "deliveredAt cannot precede terminalCompletedAt",
        ),
    ],
)
def test_invalid_callback_timestamp_invariants_never_report_delivery(
    tmp_path: Path,
    field: str,
    value: str,
    diagnostic: str,
) -> None:
    """Malformed or contradictory callback times cannot prove delivery."""
    directory = tmp_path / field
    callback = _callback(directory.name)
    callback[field] = value
    _write_mapping(directory / "state.json", _state(directory.name))
    _write_mapping(directory / "completion-notification.json", callback)

    run = workflow_runs.load_run(directory)

    assert run.callback_status == "invalid"
    assert run.callback_error is not None
    assert diagnostic in run.callback_error


def test_terminal_run_requires_completed_timestamp(tmp_path: Path) -> None:
    """A terminal status without its completion instant is malformed."""
    directory = tmp_path / "incomplete-terminal"
    state = _state(directory.name)
    state.pop("completedAt")
    _write_mapping(directory / "state.json", state)

    run = workflow_runs.load_run(directory)

    assert run.status == "malformed"
    assert "terminal status requires completedAt" in (run.state_error or "")


def test_delivered_callback_is_unverifiable_without_valid_state(
    tmp_path: Path,
) -> None:
    """Callback success requires a compatible owning run generation."""
    directory = tmp_path / "missing-state"
    _write_mapping(
        directory / "completion-notification.json",
        _callback(directory.name),
    )

    run = workflow_runs.load_run(directory)

    assert run.status == "malformed"
    assert run.callback_status == "unverifiable"


def test_prior_delivered_callback_is_stale_after_resume(tmp_path: Path) -> None:
    """A previous terminal generation cannot satisfy a resumed run."""
    directory = tmp_path / "resumed"
    _write_mapping(directory / "state.json", _state(directory.name, status="running"))
    _write_mapping(
        directory / "completion-notification.json",
        _callback(directory.name),
    )

    run = workflow_runs.load_run(directory)

    assert run.recorded_status == "running"
    assert run.callback_status == "stale"


@pytest.mark.parametrize(
    ("probe", "expected_status"),
    [
        (ProcessProbe("dead"), "orphaned"),
        (ProcessProbe("unverifiable"), "unverifiable"),
        (ProcessProbe("alive", identity="darwin:9:9"), "stale"),
    ],
)
def test_zero_attempt_crashed_notifier_is_not_left_sending(
    tmp_path: Path,
    probe: ProcessProbe,
    expected_status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-delivery notifier failure exposes its observed process state."""
    directory = tmp_path / expected_status
    callback = _callback(directory.name, status="sending", attempts=0)
    callback["notifierPid"] = 123
    callback["notifierStartedAt"] = "darwin:1:2"
    callback["terminalCompletedAt"] = TIME
    callback["terminalStatus"] = "completed"
    _write_mapping(directory / "state.json", _state(directory.name))
    _write_mapping(directory / "completion-notification.json", callback)
    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        _fixed_process_probe(probe),
    )

    rendered = workflow_runs.load_run(directory).callback_json()

    assert rendered is not None
    assert rendered["status"] == expected_status
    assert rendered["notifierProcessStatus"] == expected_status


def test_unknown_recorded_status_is_selected_by_unknown_filter(
    tmp_path: Path,
) -> None:
    """Future status strings remain raw while filtering as unknown."""
    home = tmp_path / "home"
    directory = home / "runs" / "queued"
    _write_mapping(directory / "state.json", _state("queued", status="queued"))

    result = CliRunner().invoke(
        cli,
        ["--status", "unknown", "--json"],
        env={"CODEX_WORKFLOW_HOME": str(home)},
    )

    payload = json.loads(result.output)["runs"]
    assert result.exit_code == 0
    assert payload[0]["recordedStatus"] == "queued"
    assert payload[0]["status"] == "unknown"


def test_boundary_timestamp_and_invalid_creation_remain_sortable(
    tmp_path: Path,
) -> None:
    """UTC overflow is diagnostic and invalid creation falls back to update."""
    boundary = _state(
        "boundary",
        status="completed",
    )
    boundary["createdAt"] = "0001-01-01T00:00:00+14:00"
    boundary["updatedAt"] = "not-a-time"
    fallback = _state("fallback")
    fallback["createdAt"] = "not-a-time"
    fallback["updatedAt"] = "2026-07-14T16:00:00Z"
    ordinary = _state("ordinary")
    ordinary["createdAt"] = "2026-07-14T15:00:00Z"
    for state in (boundary, fallback, ordinary):
        _write_mapping(tmp_path / "runs" / str(state["runId"]) / "state.json", state)

    runs = workflow_runs.load_runs(tmp_path)

    assert [run.run_id for run in runs] == ["fallback", "ordinary", "boundary"]


def test_steps_sort_chronologically_across_offsets_with_stable_ties() -> None:
    """Step order uses UTC instants, with IDs breaking equal-time ties."""

    def step(step_id: str, started_at: str) -> dict[str, object]:
        """Build one valid completed step."""
        return {
            "attempt": 1,
            "completedAt": "2026-07-14T15:00:00Z",
            "fingerprint": f"fingerprint-{step_id}",
            "id": step_id,
            "label": step_id,
            "startedAt": started_at,
            "status": "completed",
        }

    state = _state("ordered")
    state["steps"] = {
        "late": step("late", "2026-07-14T10:00:00-04:00"),
        "same-b": step("same-b", "2026-07-14T13:00:00Z"),
        "overflow": step("overflow", "0001-01-01T00:00:00+14:00"),
        "same-a": step("same-a", "2026-07-14T09:00:00-04:00"),
    }
    run = workflow_runs.RunRecord(directory=Path("ordered"), state=state)

    assert [record.id for record in run.steps] == [
        "overflow",
        "same-a",
        "same-b",
        "late",
    ]


def test_directory_missing_state_is_preserved_as_partial_record(
    tmp_path: Path,
) -> None:
    """A producer publication window remains visible as malformed state."""
    (tmp_path / "runs" / "partial").mkdir(parents=True)

    runs = workflow_runs.load_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0].run_id == "partial"
    assert runs[0].status == "malformed"
    assert runs[0].state_error is not None


def test_run_scan_limit_fails_before_parsing_or_process_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized store has a clear aggregate bound before expensive work."""
    runs_directory = tmp_path / "runs"
    for index in range(4):
        (runs_directory / f"run-{index}").mkdir(parents=True)
    monkeypatch.setattr(workflow_runs, "MAX_RUN_DIRECTORIES", 3)

    def fail_load(_path: Path) -> workflow_runs.RunRecord:
        raise AssertionError("oversized stores must fail before loading runs")

    monkeypatch.setattr(workflow_runs, "load_run", fail_load)

    with pytest.raises(workflow_runs.WorkflowStoreError, match="safety limit of 3"):
        workflow_runs.load_runs(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        '{"value": ' + "9" * 5_000 + "}",
        '{"value": ' + "[" * 2_000 + "0" + "]" * 2_000 + "}",
    ],
)
def test_hostile_json_becomes_per_run_diagnostic(
    tmp_path: Path,
    raw: str,
) -> None:
    """Integer and recursion parser limits cannot abort the dashboard."""
    directory = tmp_path / "runs" / "hostile"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(raw, encoding="utf-8")

    runs = workflow_runs.load_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0].status == "malformed"
    assert runs[0].state_error is not None


def test_malformed_step_entry_is_preserved_diagnostically(tmp_path: Path) -> None:
    """A scalar step remains visible with its key and validation error."""
    directory = tmp_path / "malformed-step"
    state = _state(directory.name)
    state["steps"] = {"root/broken": ["not", "an", "object"]}
    _write_mapping(directory / "state.json", state)

    run = workflow_runs.load_run(directory)

    assert run.progress["total"] == 1
    assert run.steps[0].id == "root/broken"
    assert run.steps[0].status == "malformed"
    assert "must be a JSON object" in (run.steps[0].error or "")


def test_step_key_is_authoritative_when_embedded_id_is_forged(
    tmp_path: Path,
) -> None:
    """A mismatched embedded ID is diagnosed without losing the owning key."""
    directory = tmp_path / "forged-step-id"
    state = _state(directory.name)
    state["steps"] = {"root/owner": _step("root/forged")}
    _write_mapping(directory / "state.json", state)

    run = workflow_runs.load_run(directory)

    assert run.status == "malformed"
    assert run.steps[0].id == "root/owner"
    assert run.steps[0].status == "malformed"
    assert "does not match its owning key" in (run.steps[0].error or "")


@pytest.mark.parametrize(
    ("run_status", "step_status", "completed", "diagnostic"),
    [
        ("completed", "running", False, "terminal run cannot contain running step"),
        ("running", "running", True, "running step cannot have completedAt"),
        ("running", "completed", False, "terminal step requires completedAt"),
    ],
)
def test_run_and_step_completion_invariants_are_validated(
    tmp_path: Path,
    run_status: str,
    step_status: str,
    completed: bool,
    diagnostic: str,
) -> None:
    """Contradictory run and step lifecycle state is marked malformed."""
    directory = tmp_path / diagnostic.split()[0]
    state = _state(directory.name, status=run_status)
    step = _step("root/step", status=step_status)
    if completed:
        step["completedAt"] = TIME
    else:
        step.pop("completedAt", None)
    state["steps"] = {"root/step": step}
    _write_mapping(directory / "state.json", state)

    run = workflow_runs.load_run(directory)

    assert run.status == "malformed"
    assert diagnostic in (run.state_error or "")


def test_activity_uses_first_nonblank_error_line() -> None:
    """Leading blank persisted lines cannot erase a static error summary."""
    state = _state("leading-blank", status="failed")
    state["error"] = "\n  \n critical failure: credentials expired"

    run = workflow_runs.RunRecord(directory=Path("leading-blank"), state=state)

    assert run.activity() == "critical failure: credentials expired"


def test_future_update_is_not_treated_as_startup_grace() -> None:
    """Future timestamps cannot keep identity-less stale state active."""
    state = _state("future", status="running")

    observed = workflow_runs._supervisor_state(
        state,
        now=datetime(2026, 7, 14, 13, 59, 59, tzinfo=UTC),
    )

    assert observed == "unverifiable"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX FIFOs")
def test_non_regular_state_file_is_rejected_without_opening_blockingly(
    tmp_path: Path,
) -> None:
    """A FIFO state file becomes a malformed diagnostic without hanging."""
    directory = tmp_path / "fifo-run"
    directory.mkdir()
    os.mkfifo(directory / "state.json")
    run = workflow_runs.load_run(directory)
    assert run.status == "malformed"
    assert run.state_error == "expected a regular state file"


def test_symlinked_run_directory_is_not_discovered(
    tmp_path: Path,
) -> None:
    """Run enumeration never follows a directory entry outside the store."""
    outside = tmp_path / "outside"
    _write_mapping(outside / "state.json", _state("linked"))
    runs_directory = tmp_path / "home" / "runs"
    runs_directory.mkdir(parents=True)
    _symlink_or_skip(runs_directory / "linked", outside, True)
    assert workflow_runs.load_runs(tmp_path / "home") == []


@pytest.mark.parametrize("filename", ["state.json", "completion-notification.json"])
def test_symlinked_json_files_are_rejected(
    tmp_path: Path,
    filename: str,
) -> None:
    """State and callback reads cannot escape through file symlinks."""
    directory = tmp_path / "linked-file"
    outside = tmp_path / "outside.json"
    value = (
        _state(directory.name)
        if filename == "state.json"
        else _callback(directory.name)
    )
    _write_mapping(outside, value)
    directory.mkdir()
    if filename != "state.json":
        _write_mapping(directory / "state.json", _state(directory.name))
    _symlink_or_skip(directory / filename, outside)
    run = workflow_runs.load_run(directory)
    if filename == "state.json":
        assert run.status == "malformed"
        assert run.state_error == "expected a regular state file"
    else:
        assert run.callback_status == "invalid"
        assert run.callback_error == "expected a regular callback metadata file"


@pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"),
    reason="platform lacks race-safe no-follow opens",
)
def test_state_symlink_swap_during_open_cannot_escape_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file swapped after inspection is still refused by ``O_NOFOLLOW``."""
    directory = tmp_path / "swapped-state"
    state_path = directory / "state.json"
    outside = tmp_path / "outside-state.json"
    _write_mapping(state_path, _state(directory.name))
    _write_mapping(outside, _state(directory.name))
    real_stat = workflow_store_io.os.stat
    swapped = False

    def racing_stat(
        path: int | str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        """Replace state with a symlink after returning its regular mode."""
        nonlocal swapped
        result = real_stat(path, *args, **kwargs)
        if path == "state.json" and kwargs.get("dir_fd") is not None and not swapped:
            state_path.unlink()
            state_path.symlink_to(outside)
            swapped = True
        return result

    monkeypatch.setattr(workflow_store_io.os, "stat", racing_stat)

    run = workflow_runs.load_run(directory)

    assert swapped
    assert run.state == {}
    assert run.status == "malformed"


def test_invalid_workflow_home_is_a_clean_cli_error(tmp_path: Path) -> None:
    """A configured regular file cannot produce an internal traceback."""
    invalid_home = tmp_path / "not-a-directory"
    invalid_home.write_text("occupied", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--json"],
        env={"CODEX_WORKFLOW_HOME": str(invalid_home)},
    )

    assert result.exit_code != 0
    assert "Cannot read workflow runs" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("probe", "process_status"),
    [
        (ProcessProbe("dead"), "orphaned"),
        (ProcessProbe("alive", identity="darwin:9:9"), "stale"),
    ],
)
def test_ambiguous_delivery_keeps_unknown_status_when_notifier_is_lost(
    tmp_path: Path,
    probe: ProcessProbe,
    process_status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Notifier loss does not make a submitted callback safe to retry."""
    directory = tmp_path / "ambiguous"
    state = _state(directory.name)
    callback: dict[str, object] = {
        "attempts": 1,
        "clientUserMessageId": "message-1",
        "createdAt": TIME,
        "deadlineAt": TIME,
        "endpoint": "unix:///tmp/app-server.sock",
        "lastAttemptAt": TIME,
        "notifierPid": 999_999_999,
        "notifierStartedAt": "darwin:1:2",
        "runId": directory.name,
        "status": "sending",
        "terminalCompletedAt": TIME,
        "terminalStatus": "completed",
        "threadId": "thread-1",
        "timeoutMs": 1000,
        "updatedAt": TIME,
        "version": 1,
    }
    _write_mapping(directory / "state.json", state)
    _write_mapping(directory / "completion-notification.json", callback)
    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        _fixed_process_probe(probe),
    )

    rendered = workflow_runs.load_run(directory).callback_json()

    assert rendered is not None
    assert rendered["status"] == "unknown"
    assert rendered["attempts"] == 1
    assert rendered["notifierProcessStatus"] == process_status


def test_state_is_reread_when_callback_belongs_to_new_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new callback cannot be paired with an earlier state generation."""
    directory = tmp_path / "racing"
    running = _state(directory.name, status="running")
    completed = _state(directory.name)
    callback: dict[str, object] = {
        "attempts": 1,
        "clientUserMessageId": "message-1",
        "createdAt": TIME,
        "deadlineAt": TIME,
        "deliveredAt": TIME,
        "endpoint": "unix:///tmp/app-server.sock",
        "lastAttemptAt": TIME,
        "runId": directory.name,
        "status": "delivered",
        "terminalCompletedAt": TIME,
        "terminalStatus": "completed",
        "threadId": "thread-1",
        "timeoutMs": 1000,
        "updatedAt": TIME,
        "version": 1,
    }
    _write_mapping(directory / "state.json", completed)
    _write_mapping(directory / "completion-notification.json", callback)
    real_read = workflow_runs._read_mapping
    state_reads = 0

    def racing_read(
        path: Path,
        *,
        description: str = "JSON",
        directory_fd: int | None = None,
        missing_ok: bool = False,
        omit_result_payloads: bool = False,
        budget: workflow_store_io.ReadWorkBudget | None = None,
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return an old state once, then the atomically published state."""
        nonlocal state_reads
        if path.name == "state.json":
            state_reads += 1
            if state_reads == 1:
                return running, None
        return real_read(
            path,
            description=description,
            directory_fd=directory_fd,
            missing_ok=missing_ok,
            omit_result_payloads=omit_result_payloads,
            budget=budget,
        )

    monkeypatch.setattr(workflow_runs, "_read_mapping", racing_read)

    run = workflow_runs.load_run(directory)

    assert state_reads == 2
    assert run.recorded_status == "completed"
    assert run.callback_status == "delivered"


def test_callback_is_reread_when_it_advances_to_state_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale callback snapshot is replaced when its atomic file advances."""
    directory = tmp_path / "inverse-racing"
    completed = _state(directory.name)
    completed["completedAt"] = "2026-07-14T15:00:00Z"
    completed["updatedAt"] = completed["completedAt"]
    current_callback = _callback(directory.name)
    current_callback["terminalCompletedAt"] = completed["completedAt"]
    current_callback["deliveredAt"] = completed["completedAt"]
    current_callback["lastAttemptAt"] = completed["completedAt"]
    current_callback["updatedAt"] = completed["completedAt"]
    old_callback = dict(current_callback)
    old_callback["terminalCompletedAt"] = TIME
    _write_mapping(directory / "state.json", completed)
    _write_mapping(
        directory / "completion-notification.json",
        current_callback,
    )
    real_read = workflow_runs._read_mapping
    callback_reads = 0

    def racing_read(
        path: Path,
        *,
        description: str = "JSON",
        directory_fd: int | None = None,
        missing_ok: bool = False,
        omit_result_payloads: bool = False,
        budget: workflow_store_io.ReadWorkBudget | None = None,
    ) -> tuple[dict[str, object] | None, str | None]:
        """Return the previous callback generation exactly once."""
        nonlocal callback_reads
        if path.name == "completion-notification.json":
            callback_reads += 1
            if callback_reads == 1:
                return old_callback, None
        return real_read(
            path,
            description=description,
            directory_fd=directory_fd,
            missing_ok=missing_ok,
            omit_result_payloads=omit_result_payloads,
            budget=budget,
        )

    monkeypatch.setattr(workflow_runs, "_read_mapping", racing_read)

    run = workflow_runs.load_run(directory)

    assert callback_reads == 2
    assert run.callback_status == "delivered"
    assert run.callback is not None
    assert run.callback["terminalCompletedAt"] == completed["completedAt"]


@pytest.mark.parametrize(
    ("probe", "expected", "observation"),
    [
        (
            ProcessProbe(
                "alive",
                identity="linux:00000000-0000-0000-0000-000000000000:123",
                compatibility_identities=("linux:123",),
            ),
            "linux:123",
            "unverifiable",
        ),
        (
            ProcessProbe(
                "alive",
                identity="linux:00000000-0000-0000-0000-000000000000:123",
            ),
            "linux:00000000-0000-0000-0000-000000000000:123",
            None,
        ),
        (
            ProcessProbe(
                "alive",
                identity="linux:11111111-1111-1111-1111-111111111111:123",
            ),
            "linux:00000000-0000-0000-0000-000000000000:123",
            "stale",
        ),
        (
            ProcessProbe("alive", identity="darwin:100:200"),
            "darwin:100:200",
            None,
        ),
        (
            ProcessProbe("alive", identity="darwin:100:201"),
            "darwin:100:200",
            "stale",
        ),
        (
            ProcessProbe(
                "alive",
                identity="darwin:100:200",
                legacy_identity="Tue Jul 14 10:00:00 2026",
            ),
            "Tue Jul 14 09:00:00 2026",
            "unverifiable",
        ),
    ],
)
def test_durable_and_legacy_process_identity_matching(
    probe: ProcessProbe,
    expected: str,
    observation: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Boot/native identities verify exactly while legacy tokens stay safe."""
    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        _fixed_process_probe(probe),
    )

    assert workflow_runs.observed_process_state(123, expected) == observation


@pytest.mark.parametrize(
    ("pid", "identity"),
    [
        (0, "darwin:100:200"),
        (-1, "darwin:100:200"),
        (123, "darwin:not-an-identity"),
        (123, "darwin:100:1000000"),
        (123, "linux:not-an-identity"),
        (123, "linux:0"),
        (123, "linux:" + "9" * 5_000),
        (123, "linux:00000000-0000-0000-0000-000000000000:0"),
        (
            123,
            "linux:00000000-0000-0000-0000-000000000000:" + "9" * 5_000,
        ),
    ],
)
def test_invalid_process_metadata_is_unverifiable_without_probe(
    pid: int,
    identity: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed ownership metadata cannot prove PID reuse or process death."""

    def fail_probe(_pid: int) -> ProcessProbe:
        raise AssertionError("invalid process metadata must not be probed")

    monkeypatch.setattr(workflow_runs, "process_start_identity", fail_probe)

    assert workflow_runs.observed_process_state(pid, identity) == "unverifiable"


@pytest.mark.parametrize(
    ("field", "value"),
    [("pid", 0), ("pidStartedAt", "linux:not-an-identity")],
)
def test_recent_invalid_supervisor_metadata_is_not_startup_grace(
    field: str,
    value: object,
) -> None:
    """Publication grace cannot make malformed ownership look healthy."""
    state = _state("invalid-supervisor", status="running")
    state[field] = value

    current = datetime(2026, 7, 14, 14, tzinfo=UTC)
    assert workflow_runs._supervisor_state(state, current) == "unverifiable"


def test_windows_numeric_identity_mismatch_proves_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw PowerShell ticks remain a stable Windows process identity."""
    monkeypatch.setattr(workflow_runs.os, "name", "nt")
    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        _fixed_process_probe(ProcessProbe("alive", identity="638881920000000001")),
    )

    assert workflow_runs.observed_process_state(123, "638881920000000000") == "stale"


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin native probe")
def test_darwin_native_identity_keeps_current_process_running() -> None:
    """The native microsecond identity verifies a healthy macOS process."""
    probe = workflow_runs.process_start_identity(os.getpid())

    assert probe.status == "alive"
    assert probe.identity is not None
    assert probe.identity.startswith("darwin:")
    assert workflow_runs.observed_process_state(os.getpid(), probe.identity) is None
