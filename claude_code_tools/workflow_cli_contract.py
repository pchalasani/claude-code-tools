"""Versioned JSON contract for the observational workflow CLI."""

from __future__ import annotations

from datetime import datetime

from claude_code_tools.workflow_cli_manifest import (
    CALLBACK_V1_MANIFEST as CALLBACK_V1_MANIFEST,
    STATE_V1_MANIFEST as STATE_V1_MANIFEST,
    STEP_V1_MANIFEST as STEP_V1_MANIFEST,
    V1ObjectManifest as V1ObjectManifest,
)
from claude_code_tools.workflow_cli_snapshots import (
    RunQueryResult,
    RunRecord,
    StepRecord,
)

SCHEMA_VERSION = 1

LIST_KEYS = frozenset(
    {
        "complete",
        "limit",
        "observationComplete",
        "observationSkipped",
        "runs",
        "schemaVersion",
        "truncated",
    }
)
RUN_KEYS = frozenset(
    {
        "schemaVersion",
        "runId",
        "abbreviatedRunId",
        "workflowName",
        "workflowPath",
        "recordedStatus",
        "status",
        "agentProgress",
        "activeWorkers",
        "cwd",
        "createdAt",
        "startedAt",
        "completedAt",
        "updatedAt",
        "durationSeconds",
        "callback",
        "activity",
        "error",
        "stateError",
        "callbackError",
    }
)
SHOW_RUN_KEYS = RUN_KEYS | {"steps"}
CALLBACK_KEYS = frozenset(
    {
        "status",
        "attempts",
        "createdAt",
        "updatedAt",
        "deliveredAt",
        "deadlineAt",
        "lastAttemptAt",
        "terminalCompletedAt",
        "terminalStatus",
        "clientUserMessageId",
        "endpoint",
        "threadId",
        "timeoutMs",
        "turnId",
        "notifierPid",
        "notifierStartedAt",
        "notifierProcessStatus",
        "error",
    }
)
STEP_KEYS = frozenset(
    {
        "id",
        "label",
        "status",
        "attempt",
        "startedAt",
        "completedAt",
        "durationSeconds",
        "workerPid",
        "threadId",
        "error",
    }
)


def _normalize_text(value: str) -> str:
    """Replace lone UTF-16 surrogates at the payload contract boundary."""
    if value.isascii() or not any(
        0xD800 <= ord(character) <= 0xDFFF for character in value
    ):
        return value
    return "".join(
        "\ufffd" if 0xD800 <= ord(character) <= 0xDFFF else character
        for character in value
    )


def _normalize_json_value(value: object) -> object:
    """Recursively normalize every string inside a JSON-compatible value."""
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, dict):
        return {
            _normalize_text(key): _normalize_json_value(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    return value


def _normalize_payload(value: dict[str, object]) -> dict[str, object]:
    """Return the canonical Unicode-safe representation of one payload."""
    return {
        _normalize_text(key): _normalize_json_value(item) for key, item in value.items()
    }


def callback_payload(run: RunRecord) -> dict[str, object] | None:
    """Serialize callback observation data under the version-1 contract."""
    if run.callback_error:
        return _normalize_payload(
            {
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
                "error": run.callback_error,
            }
        )
    callback = run.callback
    if callback is None:
        return None
    return _normalize_payload(
        {
            "status": run.callback_status,
            "attempts": callback.attempts,
            "createdAt": callback.created_at,
            "updatedAt": callback.updated_at,
            "deliveredAt": callback.delivered_at,
            "deadlineAt": callback.deadline_at,
            "lastAttemptAt": callback.last_attempt_at,
            "terminalCompletedAt": callback.terminal_completed_at,
            "terminalStatus": callback.terminal_status,
            "clientUserMessageId": callback.client_user_message_id,
            "endpoint": callback.endpoint,
            "threadId": callback.thread_id,
            "timeoutMs": callback.timeout_ms,
            "turnId": callback.turn_id,
            "notifierPid": callback.notifier_pid,
            "notifierStartedAt": callback.notifier_started_at,
            "notifierProcessStatus": run.callback_process_state,
            "error": callback.error,
        }
    )


def step_payload(step: StepRecord, now: datetime) -> dict[str, object]:
    """Serialize one normalized agent step under the version-1 contract."""
    return _normalize_payload(
        {
            "id": step.id,
            "label": step.label,
            "status": step.status,
            "attempt": step.attempt,
            "startedAt": step.started_at,
            "completedAt": step.completed_at,
            "durationSeconds": step.duration_seconds(now),
            "workerPid": step.worker_pid,
            "threadId": step.thread_id,
            "error": step.error,
        }
    )


def run_payload(
    run: RunRecord,
    now: datetime,
    *,
    include_steps: bool = False,
) -> dict[str, object]:
    """Serialize one normalized run under the version-1 contract."""
    value: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run.run_id,
        "abbreviatedRunId": run.abbreviated_id,
        "workflowName": run.workflow_name,
        "workflowPath": run.workflow_path,
        "recordedStatus": run.recorded_status,
        "status": run.status,
        "agentProgress": run.progress,
        "activeWorkers": run.active_workers,
        "cwd": run.cwd,
        "createdAt": run.created_at,
        "startedAt": run.started_at,
        "completedAt": run.completed_at,
        "updatedAt": run.updated_at,
        "durationSeconds": run.duration_seconds(now),
        "callback": callback_payload(run),
        "activity": run.activity(),
        "error": run.error,
        "stateError": run.state_error,
        "callbackError": run.callback_error,
    }
    if include_steps:
        value["steps"] = [step_payload(step, now) for step in run.steps]
    return _normalize_payload(value)


def list_payload(
    result: RunQueryResult,
    now: datetime,
    *,
    limit: int,
) -> dict[str, object]:
    """Serialize the complete version-1 list envelope."""
    return _normalize_payload(
        {
            "complete": result.complete,
            "limit": limit,
            "observationComplete": result.observation_complete,
            "observationSkipped": result.observation_skipped,
            "runs": [run_payload(run, now) for run in result.records],
            "schemaVersion": SCHEMA_VERSION,
            "truncated": result.truncated,
        }
    )


def show_payload(run: RunRecord, now: datetime) -> dict[str, object]:
    """Serialize the complete version-1 show response."""
    return run_payload(run, now, include_steps=True)
