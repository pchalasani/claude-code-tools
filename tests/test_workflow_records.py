"""Regression tests for typed workflow records and validation boundaries."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_code_tools import workflow_validation
from claude_code_tools.workflow_cli_contract import (
    CALLBACK_KEYS,
    LIST_KEYS,
    RUN_KEYS,
    SHOW_RUN_KEYS,
    STEP_KEYS,
    list_payload,
    show_payload,
)
from claude_code_tools.workflow_cli_manifest import (
    CALLBACK_V1_MANIFEST,
    STATE_V1_MANIFEST,
    STEP_V1_MANIFEST,
)
from claude_code_tools.workflow_cli_projection import (
    CALLBACK_PROJECTION,
    STATE_PROJECTION,
    STEP_PROJECTION,
)
from claude_code_tools.workflow_cli_snapshots import (
    CallbackRecord,
    RunQueryResult,
    RunRecord,
    RunState,
)
from claude_code_tools.workflow_validation import (
    parse_callback,
    parse_run_record,
    parse_state,
)

TIME = "2026-07-14T14:00:00Z"
NOW = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)


def _state(run_id: str) -> dict[str, object]:
    """Return a minimal valid state mapping."""
    return {
        "concurrency": 1,
        "createdAt": TIME,
        "cwd": "/work",
        "runId": run_id,
        "status": "running",
        "steps": {},
        "updatedAt": TIME,
        "version": 1,
        "workflowHash": "abc123",
        "workflowPath": "/work/audit.js",
    }


def test_diagnostic_sealing_does_not_truncate_normalized_steps() -> None:
    """Diagnostic limits affect messages, never the normalized state."""
    state = _state("diagnostic-budget")
    steps: dict[str, object] = {f"broken-{index}": None for index in range(120)}
    steps["valid-tail"] = {
        "attempt": 1,
        "fingerprint": "tail",
        "id": "valid-tail",
        "label": "Valid tail",
        "startedAt": TIME,
        "status": "running",
    }
    state["steps"] = steps

    typed, error = parse_state(state, "diagnostic-budget")

    assert error is not None
    assert len(typed.steps) == 121
    assert typed.steps[-1].id == "valid-tail"
    assert typed.steps[-1].status == "running"


def test_domain_records_do_not_impersonate_persisted_mappings() -> None:
    """Typed domain records expose attributes instead of mapping shims."""
    state = RunState(run_id="typed")
    callback = CallbackRecord(status="armed")

    assert not hasattr(state, "get")
    assert not hasattr(callback, "get")
    with pytest.raises(TypeError):
        _ = callback["status"]  # type: ignore[index]


def test_snapshot_models_do_not_import_persistence_validation() -> None:
    """Importing snapshot models does not recreate the validation cycle."""
    script = "\n".join(
        (
            "import sys",
            "import claude_code_tools.workflow_cli_snapshots",
            "assert 'claude_code_tools.workflow_validation' not in sys.modules",
        )
    )

    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_v1_manifest_has_no_domain_or_validation_imports() -> None:
    """The schema authority stays neutral and safe for every consumer."""
    script = "\n".join(
        (
            "import sys",
            "import claude_code_tools.workflow_cli_manifest",
            "assert 'claude_code_tools.workflow_cli_snapshots' not in sys.modules",
            "assert 'claude_code_tools.workflow_validation' not in sys.modules",
        )
    )

    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_run_record_rejects_raw_mapping_values() -> None:
    """Raw persistence data must cross the diagnostic-preserving factory."""
    with pytest.raises(TypeError, match="RunRecord.state must be a RunState"):
        RunRecord(
            directory=Path("raw"),
            state=_state("raw"),  # type: ignore[arg-type]
        )


def test_run_record_raw_factory_propagates_validation_diagnostics() -> None:
    """The explicit raw boundary cannot create an apparently valid record."""
    state = _state("malformed")
    del state["workflowPath"]

    record = parse_run_record(directory=Path("malformed"), state=state)

    assert record.status == "malformed"
    assert record.state_error is not None
    assert "workflowPath must be a nonempty string" in record.state_error


def test_raw_record_boundary_parses_state_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validation-owned raw boundary does not duplicate diagnostics."""
    state = _state("single-pass")
    del state["workflowPath"]
    calls = 0
    real_parse_state = workflow_validation.parse_state

    def counting_parse_state(
        raw_state: dict[str, object],
        directory_name: str,
    ) -> tuple[RunState, str | None]:
        nonlocal calls
        calls += 1
        return real_parse_state(raw_state, directory_name)

    monkeypatch.setattr(
        workflow_validation,
        "parse_state",
        counting_parse_state,
    )

    record = workflow_validation.parse_run_record(
        directory=Path("single-pass"),
        state=state,
    )

    assert calls == 1
    assert record.state_error == "workflowPath must be a nonempty string"


def test_long_valid_timestamp_has_consistent_json_semantics() -> None:
    """Validation and JSON duration share one timestamp parser and bound."""
    started_at = "2026-07-14T14:00:00." + ("1" * 55) + "Z"
    completed_at = "2026-07-14T14:00:00." + ("9" * 55) + "Z"
    assert len(started_at) == 76
    state = _state("long-timestamp")
    state.update(
        {
            "completedAt": completed_at,
            "createdAt": started_at,
            "startedAt": started_at,
            "status": "completed",
            "updatedAt": completed_at,
        }
    )

    record = parse_run_record(
        directory=Path("long-timestamp"),
        state=state,
    )
    payload = show_payload(record, NOW)

    assert record.state_error is None
    assert payload["status"] == "completed"
    assert payload["durationSeconds"] == pytest.approx(0.888888)
    assert payload["stateError"] is None


def test_v1_manifest_is_deeply_immutable_and_drives_projection() -> None:
    """Projection and validation share immutable versioned field policy."""
    assert STEP_PROJECTION.fields == STEP_V1_MANIFEST.projection_fields
    assert STATE_PROJECTION.fields == STATE_V1_MANIFEST.projection_fields
    assert CALLBACK_PROJECTION.fields == CALLBACK_V1_MANIFEST.projection_fields
    assert isinstance(STATE_PROJECTION.children, tuple)
    assert isinstance(STATE_V1_MANIFEST.required_strings, tuple)
    assert isinstance(STATE_V1_MANIFEST.null_projection_fields, frozenset)
    with pytest.raises(FrozenInstanceError):
        setattr(STATE_V1_MANIFEST, "required_strings", ())


def test_direct_payload_normalizes_hostile_unicode_recursively() -> None:
    """Python payload callers see the same Unicode-safe contract as JSON."""
    state = _state("hostile-unicode")
    state["error"] = "outer-\ud800-error"
    state["workflowPath"] = "/work/hostile-\udcff.js"
    state["steps"] = {
        "worker": {
            "attempt": 1,
            "fingerprint": "fingerprint",
            "id": "worker",
            "label": "nested-\ud800-label",
            "startedAt": TIME,
            "status": "running",
        }
    }

    payload = show_payload(
        parse_run_record(directory=Path("hostile-unicode"), state=state),
        NOW,
    )

    assert payload["error"] == "outer-\ufffd-error"
    assert payload["workflowPath"] == "/work/hostile-\ufffd.js"
    steps = payload["steps"]
    assert isinstance(steps, list)
    assert steps[0]["label"] == "nested-\ufffd-label"


def test_callback_parser_returns_typed_value_with_its_diagnostic() -> None:
    """Callback parsing cannot silently discard structural failures."""
    callback = {
        "attempts": 0,
        "createdAt": TIME,
        "endpoint": "ipc",
        "runId": "wrong-owner",
        "status": "armed",
        "threadId": "thread-1",
        "timeoutMs": 1000,
        "updatedAt": TIME,
        "version": 1,
    }

    typed, error = parse_callback(callback, "actual-owner")

    assert isinstance(typed, CallbackRecord)
    assert typed.status == "armed"
    assert error is not None
    assert "does not match directory" in error


def test_production_json_contract_has_exact_version_1_keys() -> None:
    """The deployed serializer exposes only the documented exact key sets."""
    state = _state("contract")
    state["steps"] = {
        "worker": {
            "attempt": 1,
            "fingerprint": "worker-fingerprint",
            "id": "worker",
            "label": "Worker",
            "startedAt": TIME,
            "status": "running",
        }
    }
    run = parse_run_record(directory=Path("contract"), state=state)
    result = RunQueryResult(
        records=(run,),
        truncated=False,
        store_has_runs=True,
    )

    listed = list_payload(result, NOW, limit=50)
    shown = show_payload(run, NOW)
    invalid_callback = RunRecord(
        directory=Path("invalid-callback"),
        callback_error="invalid callback",
    )
    invalid_payload = show_payload(invalid_callback, NOW)

    assert listed.keys() == LIST_KEYS
    assert listed["schemaVersion"] == 1
    assert listed["complete"] is True
    runs = listed["runs"]
    assert isinstance(runs, list)
    assert runs[0].keys() == RUN_KEYS
    assert shown.keys() == SHOW_RUN_KEYS
    steps = shown["steps"]
    assert isinstance(steps, list)
    assert steps[0].keys() == STEP_KEYS
    callback = invalid_payload["callback"]
    assert isinstance(callback, dict)
    assert callback.keys() == CALLBACK_KEYS

    malformed_payload = show_payload(
        RunRecord(directory=Path("malformed-contract"), state_error="broken"),
        NOW,
    )
    assert malformed_payload.keys() == SHOW_RUN_KEYS
    assert malformed_payload["status"] == "malformed"

    raw_callback = {
        "attempts": 0,
        "createdAt": TIME,
        "endpoint": "ipc",
        "runId": "contract",
        "status": "armed",
        "threadId": "thread",
        "timeoutMs": 1_000,
        "unknown": "must not leak",
        "updatedAt": TIME,
        "version": 1,
    }
    callback_run = replace(
        parse_run_record(
            directory=Path("contract"),
            state=state,
            callback=raw_callback,
        ),
        callback_process_state="alive",
    )
    callback_payload = show_payload(callback_run, NOW)["callback"]
    assert isinstance(callback_payload, dict)
    assert callback_payload.keys() == CALLBACK_KEYS
    assert callback_payload["notifierProcessStatus"] == "alive"
    assert "unknown" not in callback_payload
