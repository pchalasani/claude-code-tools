"""Tests for the observational durable-workflow CLI."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, tzinfo
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from rich.console import Console
from rich.table import Table

from claude_code_tools import workflow_runs
from claude_code_tools.workflow_cli import build_runs_table, cli
from claude_code_tools.workflow_processes import ProcessProbe
from claude_code_tools.workflow_validation import parse_run_record

BASE_TIME = "2026-07-14T14:00:00.000Z"


def _state(
    run_id: str,
    *,
    status: str = "completed",
    workflow: str = "/work/audit-routes.js",
    cwd: str = "/work",
    created_at: str = BASE_TIME,
    error: str | None = None,
    steps: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the required subset of a version-1 state object."""
    normalized_steps: dict[str, object] = {}
    for key, raw_step in (steps or {}).items():
        if not isinstance(raw_step, Mapping):
            normalized_steps[key] = raw_step
            continue
        step = dict(raw_step)
        if step.get("status") in {"canceled", "completed", "failed"}:
            step.setdefault("completedAt", created_at)
        normalized_steps[key] = step
    value: dict[str, object] = {
        "concurrency": 4,
        "createdAt": created_at,
        "cwd": cwd,
        "runId": run_id,
        "status": status,
        "steps": normalized_steps,
        "updatedAt": created_at,
        "version": 1,
        "workflowHash": "abc123",
        "workflowPath": workflow,
    }
    if status in {"canceled", "completed", "failed"}:
        value["completedAt"] = created_at
    if error is not None:
        value["error"] = error
    return value


def _write_run(
    home: Path,
    state: dict[str, object],
    callback: dict[str, object] | None = None,
) -> Path:
    """Write real temporary workflow state files."""
    run_id = str(state["runId"])
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    if callback is not None:
        (directory / "completion-notification.json").write_text(
            json.dumps(callback),
            encoding="utf-8",
        )
    return directory


def _invoke(home: Path, arguments: list[str]) -> Result:
    """Invoke the CLI against an isolated workflow home."""
    return CliRunner().invoke(
        cli,
        arguments,
        env={"CODEX_WORKFLOW_HOME": str(home), "NO_COLOR": "1"},
        color=False,
    )


def test_static_output_lists_local_runs_cleanly(tmp_path: Path) -> None:
    """The explicit history view renders a clean static summary table."""
    _write_run(tmp_path, _state("20260714-one", status="completed"))

    result = _invoke(tmp_path, ["--all"])

    assert result.exit_code == 0
    assert "audit-routes" in result.output
    assert "work" in result.output
    assert "20260714-one" in result.output
    assert "completed" in result.output
    assert "\x1b[" not in result.output


def test_default_lists_only_active_workflows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal history appears only when the user explicitly requests it."""

    def alive_probe(
        _pid: int,
        *,
        include_legacy: bool = True,
        remaining_seconds: float | None = None,
        prior_probe: ProcessProbe | None = None,
    ) -> ProcessProbe:
        del include_legacy, remaining_seconds, prior_probe
        return ProcessProbe(status="alive", identity="active-process")

    monkeypatch.setattr(workflow_runs, "process_start_identity", alive_probe)
    active = _state("active-run", status="running")
    active["pid"] = os.getpid()
    active["pidStartedAt"] = "active-process"
    _write_run(tmp_path, active)
    _write_run(tmp_path, _state("failed-run", status="failed"))
    _write_run(tmp_path, _state("canceled-run", status="canceled"))
    delivered = {
        "attempts": 1,
        "clientUserMessageId": "delivered-message",
        "createdAt": BASE_TIME,
        "deadlineAt": BASE_TIME,
        "deliveredAt": BASE_TIME,
        "endpoint": "unix://",
        "lastAttemptAt": BASE_TIME,
        "runId": "completed-run",
        "status": "delivered",
        "terminalCompletedAt": BASE_TIME,
        "terminalStatus": "completed",
        "threadId": "thread-1",
        "timeoutMs": 1_000,
        "updatedAt": BASE_TIME,
        "version": 1,
    }
    _write_run(
        tmp_path,
        _state("completed-run", status="completed"),
        delivered,
    )

    default_result = _invoke(tmp_path, ["--json"])
    history_result = _invoke(tmp_path, ["--all", "--json"])

    assert default_result.exit_code == 0
    assert [item["runId"] for item in json.loads(default_result.output)["runs"]] == [
        "active-run"
    ]
    assert history_result.exit_code == 0
    assert {item["runId"] for item in json.loads(history_result.output)["runs"]} == {
        "active-run",
        "canceled-run",
        "completed-run",
        "failed-run",
    }


def test_all_and_status_are_mutually_exclusive(tmp_path: Path) -> None:
    """History and explicit-status modes cannot silently override each other."""
    result = _invoke(tmp_path, ["--all", "--status", "failed"])

    assert result.exit_code != 0
    assert "--all cannot be combined with --status" in result.output


def test_json_output_has_stable_normalized_fields(tmp_path: Path) -> None:
    """Automation output is valid JSON with a versioned normalized schema."""
    _write_run(tmp_path, _state("json-run"))

    result = _invoke(tmp_path, ["--all", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["complete"] is True
    assert payload["limit"] == 50
    assert payload["observationComplete"] is True
    assert payload["observationSkipped"] == 0
    assert payload["schemaVersion"] == 1
    assert payload["truncated"] is False
    run = payload["runs"][0]
    assert run["schemaVersion"] == 1
    assert run["runId"] == "json-run"
    assert run["cwd"] == "/work"
    assert run["status"] == "completed"
    assert run["callback"] is None
    assert run["agentProgress"] == {
        "canceled": 0,
        "completed": 0,
        "failed": 0,
        "running": 0,
        "total": 0,
    }


def test_status_filter_and_limit_are_applied_after_sorting(tmp_path: Path) -> None:
    """Filtering retains only the newest matching bounded rows."""
    _write_run(
        tmp_path,
        _state("old-complete", created_at="2026-07-14T12:00:00Z"),
    )
    _write_run(
        tmp_path,
        _state("failed-new", status="failed", created_at="2026-07-14T14:00:00Z"),
    )
    _write_run(
        tmp_path,
        _state("failed-old", status="failed", created_at="2026-07-14T13:00:00Z"),
    )

    result = _invoke(tmp_path, ["--status", "failed", "--limit", "1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [item["runId"] for item in payload["runs"]] == ["failed-new"]
    assert payload["truncated"] is True


def test_callback_state_is_separate_from_workflow_status(tmp_path: Path) -> None:
    """A failed callback does not replace a successful workflow status."""
    callback = {
        "attempts": 5,
        "clientUserMessageId": "message-1",
        "createdAt": BASE_TIME,
        "deadlineAt": BASE_TIME,
        "endpoint": "unix://",
        "error": "socket unavailable",
        "lastAttemptAt": BASE_TIME,
        "runId": "callback-run",
        "status": "failed",
        "threadId": "thread-1",
        "timeoutMs": 1000,
        "updatedAt": BASE_TIME,
        "version": 1,
    }
    _write_run(tmp_path, _state("callback-run"), callback)

    result = _invoke(tmp_path, ["--all", "--json"])

    payload = json.loads(result.output)["runs"][0]
    assert payload["status"] == "completed"
    assert payload["callback"]["status"] == "failed"
    assert payload["callback"]["error"] == "socket unavailable"
    assert payload["callback"]["endpoint"] == "unix://"
    assert payload["callback"]["clientUserMessageId"] == "message-1"
    assert payload["callback"]["deadlineAt"] == BASE_TIME
    assert payload["callback"]["timeoutMs"] == 1000


def test_malformed_state_and_callback_are_visible(tmp_path: Path) -> None:
    """Corrupt files produce diagnostic rows instead of crashing or hiding runs."""
    malformed = tmp_path / "runs" / "broken-run"
    malformed.mkdir(parents=True)
    (malformed / "state.json").write_text("{not-json", encoding="utf-8")
    valid = _write_run(tmp_path, _state("valid-run"))
    (valid / "completion-notification.json").write_text("[]", encoding="utf-8")

    result = _invoke(tmp_path, ["--all", "--json"])

    assert result.exit_code == 0
    payload = {item["runId"]: item for item in json.loads(result.output)["runs"]}
    assert payload["broken-run"]["status"] == "malformed"
    assert payload["broken-run"]["stateError"]
    assert payload["valid-run"]["status"] == "completed"
    assert payload["valid-run"]["callback"]["status"] == "invalid"


def test_legacy_supervisor_mismatch_is_unverifiable_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A locale-dependent legacy mismatch cannot prove PID reuse."""

    def probe_identity(
        _pid: int,
        *,
        include_legacy: bool = True,
        remaining_seconds: float | None = None,
        prior_probe: ProcessProbe | None = None,
    ) -> ProcessProbe:
        """Return a live legacy process identity for this test."""
        del include_legacy, remaining_seconds, prior_probe
        return ProcessProbe(
            status="alive",
            identity="observed process start identity",
            legacy_identity="observed legacy start identity",
        )

    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        probe_identity,
    )
    state = _state("stale-run", status="running")
    state["pid"] = os.getpid()
    state["pidStartedAt"] = "forged process start identity"
    directory = _write_run(tmp_path, state)
    before = (directory / "state.json").read_bytes()

    result = _invoke(tmp_path, ["--json"])

    payload = json.loads(result.output)["runs"]
    assert payload[0]["status"] == "unverifiable"
    assert payload[0]["recordedStatus"] == "running"
    assert (directory / "state.json").read_bytes() == before


def test_dead_supervisor_is_orphaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded supervisor that no longer exists is shown as orphaned."""
    state = _state("orphaned-run", status="running")
    state["pid"] = 999_999_999
    state["pidStartedAt"] = "Tue Jul 14 10:00:00 2026"
    _write_run(tmp_path, state)

    def dead_probe(
        _pid: int,
        *,
        include_legacy: bool = True,
        remaining_seconds: float | None = None,
        prior_probe: ProcessProbe | None = None,
    ) -> ProcessProbe:
        """Return a bounded observation for a process that exited."""
        del include_legacy, remaining_seconds, prior_probe
        return ProcessProbe("dead")

    monkeypatch.setattr(
        workflow_runs,
        "process_start_identity",
        dead_probe,
    )

    result = _invoke(tmp_path, ["--status", "orphaned", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)["runs"]
    assert payload[0]["status"] == "orphaned"
    assert payload[0]["recordedStatus"] == "running"


def test_old_state_without_supervisor_identity_is_not_orphaned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity-less legacy state is unverifiable after startup grace."""
    observed_at = datetime(2026, 7, 14, 14, 0, 6, tzinfo=UTC)

    class ObservedDateTime(datetime):
        """Provide a controlled observation time to the state reader."""

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            """Return the fixed aware observation time."""
            if tz is None:
                return observed_at
            return observed_at.astimezone(tz)

    monkeypatch.setattr(workflow_runs, "datetime", ObservedDateTime)
    _write_run(tmp_path, _state("legacy-running", status="running"))

    result = _invoke(tmp_path, ["--status", "unverifiable", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)["runs"]
    assert payload[0]["status"] == "unverifiable"
    assert payload[0]["recordedStatus"] == "running"


def test_show_renders_callback_error_and_agent_steps(tmp_path: Path) -> None:
    """Show includes detailed run, callback, error, and agent-step state."""
    steps = {
        "root/audit": {
            "attempt": 2,
            "error": "worker exhausted retries",
            "fingerprint": "def456",
            "id": "root/audit",
            "label": "Audit routes",
            "startedAt": BASE_TIME,
            "status": "failed",
            "threadId": "thread-worker",
        }
    }
    callback = {
        "attempts": 1,
        "createdAt": BASE_TIME,
        "endpoint": "unix://",
        "lastAttemptAt": BASE_TIME,
        "runId": "show-run",
        "status": "unknown",
        "threadId": "thread-main",
        "timeoutMs": 1000,
        "updatedAt": BASE_TIME,
        "version": 1,
    }
    _write_run(
        tmp_path,
        _state("show-run", status="failed", error="workflow failed", steps=steps),
        callback,
    )

    result = _invoke(tmp_path, ["show", "show-run"])

    assert result.exit_code == 0
    assert "Workflow run" in result.output
    assert "Completion callback" in result.output
    assert "workflow failed" in result.output
    assert "Audit routes" in result.output
    assert "exhausted retries" in result.output


def test_show_json_includes_detailed_steps(tmp_path: Path) -> None:
    """Show JSON adds normalized agent-step details."""
    steps = {
        "root/work": {
            "attempt": 1,
            "fingerprint": "abc",
            "id": "root/work",
            "label": "Work",
            "startedAt": BASE_TIME,
            "status": "completed",
        }
    }
    _write_run(tmp_path, _state("show-json", steps=steps))

    result = _invoke(tmp_path, ["show", "show-json", "--json"])

    payload = json.loads(result.output)
    assert payload["runId"] == "show-json"
    assert payload["cwd"] == "/work"
    assert payload["steps"][0]["id"] == "root/work"


def test_project_is_visible_in_list_and_full_directory_in_show(tmp_path: Path) -> None:
    """Human output identifies both the project and its complete launch path."""
    cwd = "/Users/example/Git/observability.fix-paper-style"
    _write_run(tmp_path, _state("project-run", cwd=cwd))

    listed = _invoke(tmp_path, ["--all"])
    shown = _invoke(tmp_path, ["show", "project-run"])

    assert listed.exit_code == 0
    assert "observability.fix-paper-style" in listed.output
    assert shown.exit_code == 0
    assert "Project" in shown.output
    assert "Working directory" in shown.output
    assert cwd in shown.output


@pytest.mark.parametrize(
    ("width", "column_count"),
    [(24, 1), (64, 2), (88, 2), (118, 2), (139, 2), (140, 9)],
)
def test_live_table_stays_within_narrow_rendering_boundary(
    width: int,
    column_count: int,
) -> None:
    """The live table combines columns and renders a spinner at narrow widths."""
    state = _state(
        "run",
        status="running",
        workflow="/a/very/long/path/with/a-long-workflow-name.js",
        error="x" * 300,
        steps={
            "root/worker": {
                "attempt": 1,
                "fingerprint": "abc",
                "id": "root/worker",
                "label": "Long running worker",
                "startedAt": BASE_TIME,
                "status": "running",
                "workerPid": os.getpid(),
                "workerStartedAt": "Tue Jul 14 10:00:00 2026",
            }
        },
    )
    run = parse_run_record(directory=Path("run"), state=state)
    now = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)
    table = build_runs_table([run], width=width, live=True, now=now)
    assert isinstance(table, Table)
    console = Console(
        color_system=None,
        force_terminal=False,
        record=True,
        width=width,
    )

    console.print(table)
    rendered = console.export_text()

    assert len(table.columns) == column_count
    assert "a-long-workfl" in rendered
    assert "work" in rendered
    assert "running" in rendered
    if width < 140:
        assert "1 worker" in rendered
    else:
        assert "Workflow / Project" in rendered
        assert "Active" in rendered
    assert max(len(line) for line in rendered.splitlines()) <= width


def test_offset_timestamps_are_sorted_chronologically(tmp_path: Path) -> None:
    """Equivalent ISO offsets are ordered by time rather than raw text."""
    _write_run(
        tmp_path,
        _state("older", created_at="2026-07-14T14:30:00+02:00"),
    )
    _write_run(
        tmp_path,
        _state("newer", created_at="2026-07-14T13:00:00+00:00"),
    )

    result = _invoke(tmp_path, ["--all", "--json"])

    assert result.exit_code == 0
    assert [item["runId"] for item in json.loads(result.output)["runs"]] == [
        "newer",
        "older",
    ]


def test_missing_workflow_home_is_a_clean_empty_result(tmp_path: Path) -> None:
    """A missing runs directory is not an error."""
    missing = tmp_path / "does-not-exist"

    static = _invoke(missing, [])
    structured = _invoke(missing, ["--json"])

    assert static.exit_code == 0
    assert "No workflow runs found" in static.output
    assert json.loads(structured.output) == {
        "complete": True,
        "limit": 50,
        "observationComplete": True,
        "observationSkipped": 0,
        "runs": [],
        "schemaVersion": 1,
        "truncated": False,
    }
