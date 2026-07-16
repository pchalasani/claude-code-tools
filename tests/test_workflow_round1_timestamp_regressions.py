"""Regression tests for adversarial round-one timestamp findings."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, tzinfo
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_code_tools import (
    workflow_cli,
    workflow_cli_snapshots,
    workflow_cli_store_backends,
    workflow_runs,
    workflow_store_io,
    workflow_validation,
)
from claude_code_tools.workflow_cli import cli
from claude_code_tools.workflow_cli_identity_policy import (
    RunResolution,
    RunResolutionKind,
)

QUERY_AT = datetime(2026, 7, 14, 14, tzinfo=UTC)


def _state(run_id: str, timestamp: str) -> dict[str, object]:
    """Build a minimal nonterminal durable state."""
    return {
        "concurrency": 1,
        "createdAt": timestamp,
        "cwd": "/work",
        "runId": run_id,
        "status": "running",
        "steps": {},
        "updatedAt": timestamp,
        "version": 1,
        "workflowHash": "hash",
        "workflowPath": "/work/workflow.js",
    }


def _callback(run_id: str, updated_at: str) -> dict[str, object]:
    """Build minimal callback metadata without a delivery generation."""
    return {
        "attempts": 0,
        "createdAt": "2026-07-14T14:00:00Z",
        "endpoint": "unix:///tmp/app-server.sock",
        "runId": run_id,
        "status": "unknown",
        "threadId": "thread",
        "timeoutMs": 1_000,
        "updatedAt": updated_at,
        "version": 1,
    }


def _write_snapshot(
    directory: Path,
    state: dict[str, object],
    callback: dict[str, object],
) -> None:
    """Write one state/callback pair for verified reads."""
    directory.mkdir()
    (directory / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    (directory / "completion-notification.json").write_text(
        json.dumps(callback),
        encoding="utf-8",
    )


def _install_read_clock(
    monkeypatch: pytest.MonkeyPatch,
    *readings: datetime,
) -> None:
    """Install deterministic wall-clock readings for file acquisition."""
    pending = iter(readings)

    class ReadClock(datetime):
        """Expose only the clock operation used by snapshot acquisition."""

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            value = next(pending)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(workflow_runs, "datetime", ReadClock)


def test_publications_during_query_use_per_file_completion_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State and callback updates acquired after query start remain valid."""
    directory = tmp_path / "concurrent-publication"
    _write_snapshot(
        directory,
        _state(directory.name, "2026-07-14T14:00:01Z"),
        _callback(directory.name, "2026-07-14T14:00:03Z"),
    )
    state_read_at = datetime(2026, 7, 14, 14, 0, 2, tzinfo=UTC)
    callback_read_at = datetime(2026, 7, 14, 14, 0, 4, tzinfo=UTC)
    _install_read_clock(monkeypatch, state_read_at, callback_read_at)

    with workflow_store_io.VerifiedDirectory.open(directory) as verified:
        snapshot = workflow_runs.read_validated_snapshot_once(
            verified,
            directory.name,
            budget=workflow_store_io.ReadWorkBudget(1_000_000),
            query_at=QUERY_AT,
        )

    assert snapshot.state_error is None
    assert snapshot.callback_error is None
    assert snapshot.query_at == QUERY_AT
    assert snapshot.read_completed_at == callback_read_at


def test_production_snapshot_uses_the_raw_pair_factory_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production crosses the same raw-to-typed pair boundary as tests."""
    directory = tmp_path / "factory-boundary"
    state = _state(directory.name, "2026-07-14T14:00:00Z")
    callback = _callback(directory.name, "2026-07-14T14:00:00Z")
    _write_snapshot(directory, state, callback)
    calls: list[tuple[object, object]] = []
    real_factory = workflow_runs.parse_run_record

    def counting_factory(
        owner: Path,
        *,
        state: Mapping[str, object] | None = None,
        callback: Mapping[str, object] | None = None,
    ) -> workflow_cli_snapshots.RunRecord:
        calls.append((state, callback))
        return real_factory(owner, state=state, callback=callback)

    monkeypatch.setattr(workflow_runs, "parse_run_record", counting_factory)
    with workflow_store_io.VerifiedDirectory.open(directory) as verified:
        snapshot = workflow_runs.read_validated_snapshot_once(
            verified,
            directory.name,
            budget=workflow_store_io.ReadWorkBudget(1_000_000),
            query_at=QUERY_AT,
        )

    assert snapshot.state_error is None
    assert snapshot.callback_error is None
    assert calls == [(state, callback)]


def test_state_does_not_borrow_later_callback_completion_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each file is checked against its own acquisition completion time."""
    directory = tmp_path / "future-state"
    _write_snapshot(
        directory,
        _state(directory.name, "2026-07-14T14:00:03Z"),
        _callback(directory.name, "2026-07-14T14:00:03Z"),
    )
    _install_read_clock(
        monkeypatch,
        datetime(2026, 7, 14, 14, 0, 2, tzinfo=UTC),
        datetime(2026, 7, 14, 14, 0, 4, tzinfo=UTC),
    )

    with workflow_store_io.VerifiedDirectory.open(directory) as verified:
        snapshot = workflow_runs.read_validated_snapshot_once(
            verified,
            directory.name,
            budget=workflow_store_io.ReadWorkBudget(1_000_000),
            query_at=QUERY_AT,
        )

    assert "future" in (snapshot.state_error or "")
    assert snapshot.callback_error is None


def test_single_run_observation_uses_acquisition_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run published during acquisition receives normal startup grace."""
    directory = tmp_path / "published-during-read"
    directory.mkdir()
    (directory / "state.json").write_text(
        json.dumps(_state(directory.name, "2026-07-14T14:00:01Z")),
        encoding="utf-8",
    )
    read_completed_at = datetime(2026, 7, 14, 14, 0, 2, tzinfo=UTC)
    _install_read_clock(
        monkeypatch,
        QUERY_AT,
        read_completed_at,
        read_completed_at,
    )

    record = workflow_runs.load_run(directory)

    assert record.state_error is None
    assert record.status == "running"


def test_status_filter_uses_post_scan_observation_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh run published during a scan remains in running results."""
    directory = tmp_path / "runs" / "published-during-scan"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps(_state(directory.name, "2026-07-14T14:00:01Z")),
        encoding="utf-8",
    )
    read_completed_at = datetime(2026, 7, 14, 14, 0, 2, tzinfo=UTC)
    scan_completed_at = datetime(2026, 7, 14, 14, 0, 3, tzinfo=UTC)
    _install_read_clock(
        monkeypatch,
        QUERY_AT,
        read_completed_at,
        read_completed_at,
        scan_completed_at,
    )

    result = workflow_runs.load_runs(
        tmp_path,
        statuses=("running",),
    )

    assert [record.run_id for record in result.records] == [directory.name]
    assert result.query_at == QUERY_AT
    assert result.read_completed_at == scan_completed_at


def test_case_insensitive_exact_lookup_uses_stored_spelling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows-style exact lookup cannot fabricate a differently cased ID."""
    if os.name == "nt":
        pytest.skip("native Windows behavior is covered by the backend")
    run_id = "lower-run"
    directory = tmp_path / "runs" / run_id
    state = _state(run_id, "2026-07-14T14:00:00Z")
    state.update(
        {
            "completedAt": "2026-07-14T14:00:00Z",
            "status": "completed",
        }
    )
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    original_open = workflow_cli_store_backends.PosixStoreBackend.open_directory

    def case_insensitive_open(
        backend: workflow_cli_store_backends.PosixStoreBackend,
        path: Path,
        *,
        parent_handle: int | None = None,
    ) -> int:
        if parent_handle is not None and path.name == "LOWER-RUN":
            path = path.with_name(run_id)
        return original_open(backend, path, parent_handle=parent_handle)

    def reject_enumeration(
        _backend: workflow_cli_store_backends.PosixStoreBackend,
        _handle: int,
    ) -> object:
        raise AssertionError("exact lookup must not enumerate the run catalog")

    monkeypatch.setattr(
        workflow_cli_store_backends.PosixStoreBackend,
        "open_directory",
        case_insensitive_open,
    )
    monkeypatch.setattr(
        workflow_cli_store_backends.PosixStoreBackend,
        "directory_entries",
        reject_enumeration,
    )

    lookup = workflow_runs.load_named_run("LOWER-RUN", home=tmp_path)

    assert lookup.resolution.kind is RunResolutionKind.FOUND
    assert lookup.resolution.directory == directory
    assert lookup.record is not None
    assert lookup.record.run_id == run_id
    assert lookup.record.status == "completed"
    assert lookup.record.state_error is None


def test_list_rendering_uses_post_scan_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List adapters share the acquisition-completion observation clock."""
    read_completed_at = datetime(2026, 7, 14, 14, 0, 3, tzinfo=UTC)
    query = workflow_cli_snapshots.RunQueryResult(
        (),
        False,
        False,
        query_at=QUERY_AT,
        read_completed_at=read_completed_at,
    )
    monkeypatch.setattr(workflow_cli, "load_runs", lambda **_kwargs: query)

    snapshot = workflow_cli._query_list(statuses=(), limit=20)

    assert snapshot.observed_at == read_completed_at


def test_show_json_uses_post_acquisition_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Show duration and process state use the same observation instant."""
    run_id = "clocked-show"
    record = workflow_validation.parse_run_record(
        Path(run_id),
        state=_state(run_id, "2026-07-14T14:00:00Z"),
    )
    read_completed_at = datetime(2026, 7, 14, 14, 0, 3, tzinfo=UTC)
    lookup = workflow_cli_snapshots.RunLookupResult(
        RunResolution(
            RunResolutionKind.FOUND,
            run_id,
            directory=Path(run_id),
        ),
        record,
        QUERY_AT,
        read_completed_at,
    )
    monkeypatch.setattr(
        workflow_cli,
        "_load_named_run",
        lambda _run_id, _now: lookup,
    )

    result = CliRunner().invoke(cli, ["show", run_id, "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["durationSeconds"] == 3.0


def _steps(count: int, started_at: str) -> dict[str, object]:
    """Build valid running steps sharing one timestamp."""
    return {
        f"step-{index}": {
            "attempt": 1,
            "fingerprint": f"fingerprint-{index}",
            "id": f"step-{index}",
            "label": f"step-{index}",
            "startedAt": started_at,
            "status": "running",
        }
        for index in range(count)
    }


def test_overlong_timestamps_never_reach_datetime_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Megabyte timestamp components are rejected before expensive parsing."""
    real_datetime = datetime

    class GuardedDateTime(datetime):
        """Fail safely if an overlong string reaches ``fromisoformat``."""

        @classmethod
        def fromisoformat(cls, value: str) -> GuardedDateTime:
            assert len(value) <= workflow_cli_snapshots.MAX_TIMESTAMP_CHARS
            return super().fromisoformat(value)

    monkeypatch.setattr(workflow_cli_snapshots, "datetime", GuardedDateTime)
    hostile = "2026-07-14T14:00:00." + ("0" * 2_000_000) + "Z"
    state = _state("hostile-time", hostile)
    state["startedAt"] = hostile
    state["steps"] = _steps(1_000, "2026-07-14T14:00:00Z")

    _typed, error = workflow_validation.parse_state(state, "hostile-time")

    assert error is not None
    assert "createdAt must be a valid ISO timestamp" in error
    assert "startedAt must be a valid ISO timestamp" in error
    assert "updatedAt must be a valid ISO timestamp" in error
    assert workflow_cli_snapshots.datetime is not real_datetime


def test_enclosing_timestamps_are_normalized_once_for_many_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-step chronology reuses the enclosing run's normalized times."""
    parsed_values: list[str] = []

    class CountingDateTime(datetime):
        """Count calls into the native ISO parser."""

        @classmethod
        def fromisoformat(cls, value: str) -> CountingDateTime:
            parsed_values.append(value)
            return super().fromisoformat(value)

    monkeypatch.setattr(workflow_cli_snapshots, "datetime", CountingDateTime)
    created_at = "2026-07-14T14:00:00Z"
    started_at = "2026-07-14T14:00:01Z"
    step_started_at = "2026-07-14T14:00:02Z"
    updated_at = "2026-07-14T14:00:03Z"
    state = _state("many-steps", created_at)
    state["startedAt"] = started_at
    state["updatedAt"] = updated_at
    state["steps"] = _steps(1_000, step_started_at)

    _typed, error = workflow_validation.parse_state(state, "many-steps")

    assert error is None
    for value in (created_at, started_at, updated_at):
        normalized = value[:-1] + "+00:00"
        assert parsed_values.count(normalized) == 1
