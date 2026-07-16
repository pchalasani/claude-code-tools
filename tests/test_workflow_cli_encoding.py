"""Encoding regressions for durable-workflow terminal output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from claude_code_tools import workflow_cli

BASE_TIME = "2026-07-14T14:00:00Z"
REPOSITORY = Path(__file__).parents[1]
SUBPROCESS_TIMEOUT_SECONDS = 10


def test_json_emitter_only_encodes_the_supplied_payload() -> None:
    """The JSON output adapter does not rewrite versioned payload values."""
    payload = {"hostile": "before\ud800after"}

    @click.command()
    def emit() -> None:
        """Emit the test payload through Click's isolated stdout."""
        workflow_cli._emit_json(payload)

    result = CliRunner().invoke(emit)

    assert result.exit_code == 0
    assert json.loads(result.output) == payload


def _write_run(home: Path, run_id: str) -> None:
    """Write one valid completed run to an isolated workflow store."""
    state = {
        "completedAt": BASE_TIME,
        "concurrency": 1,
        "createdAt": BASE_TIME,
        "cwd": "/work",
        "runId": run_id,
        "status": "completed",
        "steps": {},
        "updatedAt": BASE_TIME,
        "version": 1,
        "workflowHash": "abc123",
        "workflowPath": "/work/audit.js",
    }
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _environment(home: Path, encoding: str) -> dict[str, str]:
    """Return a subprocess environment with a legacy output encoding."""
    return {
        **os.environ,
        "CODEX_WORKFLOW_HOME": str(home),
        "NO_COLOR": "1",
        "PYTHONIOENCODING": encoding,
        "PYTHONPATH": str(REPOSITORY),
    }


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_legacy_encoded_abbreviation_remains_actionable(
    encoding: str,
    tmp_path: Path,
) -> None:
    """A redirected compact run ID can be copied exactly into show."""
    run_id = "20260714T140000Z-1234abcd"
    _write_run(tmp_path, run_id)
    environment = _environment(tmp_path, encoding)
    command = [sys.executable, "-m", "claude_code_tools.workflow_cli"]

    listed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        cwd=REPOSITORY,
        env=environment,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    abbreviation = f"{run_id[:8]}~{run_id[-8:]}"
    assert abbreviation in listed.stdout.decode(encoding)
    shown = subprocess.run(
        [*command, "show", abbreviation],
        check=False,
        capture_output=True,
        cwd=REPOSITORY,
        env=environment,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert shown.returncode == 0, shown.stderr.decode(encoding)
    assert run_id in shown.stdout.decode(encoding)


def test_ascii_invalid_id_diagnostic_uses_stderr_encoding(
    tmp_path: Path,
) -> None:
    """Click diagnostics honor a configured non-UTF-8 stderr encoding."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "claude_code_tools.workflow_cli",
            "show",
            "!" + "x" * 20_000,
        ],
        check=False,
        capture_output=True,
        cwd=REPOSITORY,
        env=_environment(tmp_path, "ascii"),
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert completed.returncode == 1
    assert "Invalid workflow run ID" in completed.stderr.decode("ascii")
