"""Adversarial round-one regressions for the workflow CLI boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner

from claude_code_tools.workflow_cli import cli
from claude_code_tools.workflow_cli_contract import run_payload
from claude_code_tools.workflow_cli_snapshots import RunRecord, RunState
from claude_code_tools.workflow_runs import _directory_names
from claude_code_tools.workflow_store_io import VerifiedDirectory

BASE_TIME = "2026-07-14T14:00:00Z"
REPOSITORY = Path(__file__).parents[1]
SUBPROCESS_TIMEOUT_SECONDS = 10
EXPECTED_LIST_KEYS = {
    "complete",
    "limit",
    "observationComplete",
    "observationSkipped",
    "runs",
    "schemaVersion",
    "truncated",
}
EXPECTED_RUN_KEYS = {
    "abbreviatedRunId",
    "activeWorkers",
    "activity",
    "agentProgress",
    "callback",
    "callbackError",
    "completedAt",
    "createdAt",
    "cwd",
    "durationSeconds",
    "error",
    "recordedStatus",
    "runId",
    "schemaVersion",
    "startedAt",
    "stateError",
    "status",
    "updatedAt",
    "workflowName",
    "workflowPath",
}
EXPECTED_CALLBACK_KEYS = {
    "attempts",
    "clientUserMessageId",
    "createdAt",
    "deadlineAt",
    "deliveredAt",
    "endpoint",
    "error",
    "lastAttemptAt",
    "notifierPid",
    "notifierProcessStatus",
    "notifierStartedAt",
    "status",
    "terminalCompletedAt",
    "terminalStatus",
    "threadId",
    "timeoutMs",
    "turnId",
    "updatedAt",
}
EXPECTED_STEP_KEYS = {
    "attempt",
    "completedAt",
    "durationSeconds",
    "error",
    "id",
    "label",
    "startedAt",
    "status",
    "threadId",
    "workerPid",
}


def _write_run(
    home: Path,
    run_id: str,
    *,
    workflow_path: str = "/work/a.js",
    include_details: bool = False,
) -> None:
    """Write one completed run under an isolated workflow home."""
    state = {
        "completedAt": BASE_TIME,
        "concurrency": 1,
        "createdAt": BASE_TIME,
        "cwd": "/work",
        "runId": run_id,
        "status": "completed",
        "steps": (
            {
                "worker": {
                    "attempt": 1,
                    "completedAt": BASE_TIME,
                    "id": "worker",
                    "label": "Worker",
                    "startedAt": BASE_TIME,
                    "status": "completed",
                    "threadId": "thread-1",
                    "workerPid": 123,
                }
            }
            if include_details
            else {}
        ),
        "updatedAt": BASE_TIME,
        "version": 1,
        "workflowHash": "abc123",
        "workflowPath": workflow_path,
    }
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    if include_details:
        callback = {
            "attempts": 0,
            "createdAt": BASE_TIME,
            "endpoint": "unix://callback",
            "runId": run_id,
            "status": "armed",
            "threadId": "thread-1",
            "timeoutMs": 1_000,
            "updatedAt": BASE_TIME,
            "version": 1,
        }
        (directory / "completion-notification.json").write_text(
            json.dumps(callback),
            encoding="utf-8",
        )


def test_schema_v1_uses_independent_literal_exact_keys(tmp_path: Path) -> None:
    """Production key constants cannot silently redefine the v1 oracle."""
    _write_run(tmp_path, "schema-run", include_details=True)
    runner = CliRunner()
    environment = {"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"}

    listed = runner.invoke(cli, ["--all", "--json"], env=environment)
    shown = runner.invoke(cli, ["show", "schema-run", "--json"], env=environment)

    assert listed.exit_code == 0, listed.output
    list_payload = json.loads(listed.output)
    assert list_payload.keys() == EXPECTED_LIST_KEYS
    assert list_payload["runs"][0].keys() == EXPECTED_RUN_KEYS
    assert shown.exit_code == 0, shown.output
    show_payload = json.loads(shown.output)
    assert show_payload.keys() == EXPECTED_RUN_KEYS | {"steps"}
    assert show_payload["callback"].keys() == EXPECTED_CALLBACK_KEYS
    assert show_payload["steps"][0].keys() == EXPECTED_STEP_KEYS


def _run_json(
    home: Path,
    encoding: str = "utf-8",
) -> subprocess.CompletedProcess[bytes]:
    """Run the JSON CLI with a selected Python stdio encoding."""
    environment = {
        **os.environ,
        "CODEX_WORKFLOW_HOME": str(home),
        "NO_COLOR": "1",
        "PYTHONIOENCODING": encoding,
        "PYTHONPATH": str(REPOSITORY),
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "claude_code_tools.workflow_cli",
            "--all",
            "--json",
        ],
        check=False,
        capture_output=True,
        cwd=REPOSITORY,
        env=environment,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def test_presentation_imports_exclude_repository_infrastructure() -> None:
    """The JSON contract and renderer import only neutral snapshot models."""
    program = "\n".join(
        [
            "import sys",
            "import claude_code_tools.workflow_cli_contract",
            "import claude_code_tools.workflow_cli_rendering",
            "forbidden = {",
            "    'claude_code_tools.workflow_runs',",
            "    'claude_code_tools.workflow_processes',",
            "    'claude_code_tools.workflow_validation',",
            "    'claude_code_tools.workflow_store_io',",
            "    'claude_code_tools.workflow_store_backends',",
            "    'claude_code_tools.workflow_store_projection',",
            "    'claude_code_tools.workflow_cli_store_projection',",
            "}",
            "loaded = sorted(forbidden.intersection(sys.modules))",
            "assert not loaded, loaded",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        cwd=REPOSITORY,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY)},
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert completed.returncode == 0, completed.stderr


def test_json_is_utf8_independent_of_python_stdio_encoding(tmp_path: Path) -> None:
    """Machine output is UTF-8 even when Python text stdout is UTF-16."""
    _write_run(tmp_path, "utf8-run")

    completed = _run_json(tmp_path, "utf-16")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(b"{")
    assert json.loads(completed.stdout.decode("utf-8"))["runs"][0]["runId"] == (
        "utf8-run"
    )


def test_json_replaces_lone_surrogates_for_strict_consumers(tmp_path: Path) -> None:
    """Persisted non-scalar Unicode cannot poison otherwise valid JSON."""
    _write_run(
        tmp_path,
        "surrogate-run",
        workflow_path="/work/unsafe-\ud800.js",
    )

    completed = _run_json(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert b"\\ud800" not in completed.stdout
    payload = json.loads(completed.stdout.decode("utf-8"))
    workflow_path = payload["runs"][0]["workflowPath"]
    assert workflow_path == "/work/unsafe-\ufffd.js"
    workflow_path.encode("utf-8", errors="strict")
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is required for the strict-consumer assertion")
    consumed = subprocess.run(
        [jq, "."],
        input=completed.stdout,
        check=False,
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    assert consumed.returncode == 0, consumed.stderr


def test_show_json_accepts_escaped_lone_surrogate_step_key(
    tmp_path: Path,
) -> None:
    """An escaped surrogate step key cannot invalidate a readable state."""
    run_id = "surrogate-step-key"
    step_id = "\ud800"
    _write_run(tmp_path, run_id)
    state_path = tmp_path / "runs" / run_id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["steps"] = {
        step_id: {
            "attempt": 1,
            "completedAt": BASE_TIME,
            "fingerprint": "abc123",
            "id": step_id,
            "label": "Surrogate key",
            "startedAt": BASE_TIME,
            "status": "completed",
        }
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    runner = CliRunner()
    environment = {"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"}

    shown = runner.invoke(cli, ["show", run_id, "--json"], env=environment)

    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)
    assert payload["stateError"] is None
    assert payload["steps"][0]["id"] == "\ufffd"
    assert payload["steps"][0]["label"] == "Surrogate key"


def test_json_normalization_reuses_clean_large_strings() -> None:
    """Payload normalization does not duplicate retained valid state."""
    value = "x" * 2_000_000
    run = RunRecord(
        directory=Path("large-clean-string"),
        state=RunState(workflow_path=value),
    )

    normalized = run_payload(run, datetime(2026, 7, 14, tzinfo=UTC))

    assert normalized["workflowPath"] is value


@pytest.mark.skipif(os.name == "nt", reason="POSIX surrogateescape behavior")
def test_json_rejects_undecodable_posix_run_directory_names(
    tmp_path: Path,
) -> None:
    """Distinct byte names cannot collapse into one emitted run identity."""

    class FakeDirectoryCatalog:
        """Supply names that Linux can return through surrogateescape."""

        def entries(self) -> Iterator[tuple[str, bool]]:
            yield "bad\udc80", True
            yield "bad\udc81", True
            yield "bad\ufffd", True

    names = _directory_names(
        cast(VerifiedDirectory, FakeDirectoryCatalog()),
        tmp_path / "runs",
    )

    assert names == ("bad\ufffd",)


@pytest.mark.parametrize(
    "arguments",
    [["--all"], ["--limit", "1"], ["--status", "failed"]],
)
def test_show_reports_list_watch_only_group_options(arguments: list[str]) -> None:
    """Misplaced filters do not recommend invalid show syntax."""
    result = CliRunner().invoke(cli, [*arguments, "show", "run"])

    assert result.exit_code == 2
    assert "only with list or watch" in result.output
    assert "must appear after the subcommand" not in result.output


@pytest.mark.skipif(os.name == "nt", reason="backslash is a Windows separator")
def test_posix_backslash_run_can_be_listed_and_shown(tmp_path: Path) -> None:
    """Every legal POSIX directory listed by the CLI remains addressable."""
    run_id = "bad\\name"
    _write_run(tmp_path, run_id)
    runner = CliRunner()
    environment = {"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"}

    listed = runner.invoke(cli, ["--all", "--json"], env=environment)
    shown = runner.invoke(cli, ["show", run_id, "--json"], env=environment)

    assert listed.exit_code == 0
    assert run_id in {run["runId"] for run in json.loads(listed.output)["runs"]}
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["runId"] == run_id
