"""Regression tests for durable-workflow terminal behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from claude_code_tools import workflow_cli
from claude_code_tools.workflow_cli import (
    MAX_SHOW_STEPS,
    build_runs_table,
    build_show_renderable,
    cli,
)
from claude_code_tools.workflow_runs import ObservationReport, RunRecord

NOW = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)
BASE_TIME = "2026-07-14T14:00:00Z"
SUBPROCESS_TIMEOUT_SECONDS = 10


def _symlink_or_skip(link: Path, target: Path) -> None:
    """Create a directory symlink or skip on a restricted Windows host."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if os.name != "nt":
            raise
        pytest.skip(f"Windows host cannot create test symlinks: {error}")


def _state(
    run_id: str,
    *,
    status: str = "completed",
    workflow: str = "/work/audit.js",
    error: str | None = None,
    steps: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a complete version-1 state fixture.

    Args:
        run_id: Durable run identifier.
        status: Durable workflow status.
        workflow: Persisted workflow path.
        error: Optional run-level error.
        steps: Optional durable step mapping.

    Returns:
        A JSON-compatible workflow state.
    """
    normalized_steps: dict[str, object] = {}
    for key, raw_step in (steps or {}).items():
        if not isinstance(raw_step, Mapping):
            normalized_steps[key] = raw_step
            continue
        step = dict(raw_step)
        if step.get("status") in {"canceled", "completed", "failed"}:
            step.setdefault("completedAt", BASE_TIME)
        normalized_steps[key] = step
    value: dict[str, object] = {
        "concurrency": 2,
        "createdAt": BASE_TIME,
        "cwd": "/work",
        "runId": run_id,
        "status": status,
        "steps": normalized_steps,
        "updatedAt": BASE_TIME,
        "version": 1,
        "workflowHash": "abc123",
        "workflowPath": workflow,
    }
    if status in {"canceled", "completed", "failed"}:
        value["completedAt"] = BASE_TIME
    if error is not None:
        value["error"] = error
    return value


def _write_run(home: Path, state: Mapping[str, object]) -> Path:
    """Write one real workflow state file.

    Args:
        home: Isolated workflow home.
        state: State object to persist.

    Returns:
        The created run directory.
    """
    directory = home / "runs" / str(state["runId"])
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return directory


def _render(renderable: object, *, width: int) -> str:
    """Render a Rich object without terminal styling.

    Args:
        renderable: Rich-compatible object.
        width: Target terminal width.

    Returns:
        Plain rendered text.
    """
    console = Console(
        color_system=None,
        force_terminal=False,
        record=True,
        width=width,
    )
    console.print(renderable)
    return console.export_text()


@pytest.mark.parametrize("width", [20, 32])
def test_narrow_show_stacks_values_and_steps(
    width: int,
    tmp_path: Path,
) -> None:
    """Narrow detail output retains summary, callback, and step identities."""
    state = _state(
        "narrow",
        steps={
            "root/x": {
                "attempt": 1,
                "fingerprint": "narrow-step-fingerprint",
                "id": "root/x",
                "label": "Do work",
                "startedAt": BASE_TIME,
                "status": "failed",
                "threadId": "thread-x",
            }
        },
    )
    callback = {
        "attempts": 1,
        "createdAt": BASE_TIME,
        "endpoint": "ipc",
        "lastAttemptAt": BASE_TIME,
        "runId": "narrow",
        "status": "failed",
        "threadId": "main",
        "timeoutMs": 1000,
        "updatedAt": BASE_TIME,
        "version": 1,
    }
    run = RunRecord(directory=Path("narrow"), state=state, callback=callback)

    rendered = _render(
        build_show_renderable(run, width=width, now=NOW),
        width=width,
    )

    assert "narrow" in rendered
    assert "audit" in rendered
    assert "Do work" in rendered
    assert "root/x" in rendered
    assert "failed" in rendered
    assert max(len(line) for line in rendered.splitlines()) <= width

    _write_run(tmp_path, state)
    invoked = CliRunner().invoke(
        cli,
        ["show", "narrow"],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "COLUMNS": str(width),
            "NO_COLOR": "1",
        },
    )
    assert invoked.exit_code == 0
    assert "narrow" in invoked.output
    assert "Do work" in invoked.output
    assert max(len(line) for line in invoked.output.splitlines()) <= width


def test_non_tty_watch_is_rejected_with_alternative(tmp_path: Path) -> None:
    """Redirected watch output fails promptly instead of buffering forever."""
    result = CliRunner().invoke(
        cli,
        ["watch"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code != 0
    assert "requires an interactive terminal" in result.output
    assert "--json" in result.output


def test_dumb_tty_watch_is_rejected_with_static_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TTY without live-display support never appears to hang."""
    monkeypatch.setattr(workflow_cli, "_stdout_is_tty", lambda: True)

    result = CliRunner().invoke(
        cli,
        ["watch", "--limit", "1", "--refresh", "0.2"],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "NO_COLOR": "1",
            "TERM": "dumb",
        },
    )

    assert result.exit_code != 0
    assert "live-display support" in result.output
    assert "default command" in result.output
    assert "--json" in result.output


@pytest.mark.parametrize(
    ("arguments", "option"),
    [
        (["--limit", "1", "watch"], "--limit"),
        (["--status", "failed", "watch"], "--status"),
        (["--json", "show", "run"], "--json"),
    ],
)
def test_misplaced_group_options_are_rejected(
    arguments: list[str],
    option: str,
) -> None:
    """List options before a subcommand are never silently discarded."""
    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code != 0
    assert option in result.output
    assert "must appear after the subcommand" in result.output


@pytest.mark.parametrize("no_color", [False, True])
def test_human_output_sanitizes_persisted_controls(
    tmp_path: Path,
    no_color: bool,
) -> None:
    """Persisted terminal controls cannot reach list or detailed output."""
    directory = _write_run(
        tmp_path,
        _state(
            "unsafe",
            workflow="/work/evil\x1b[2J.js",
            error="failure\x1b]0;forged-title\x07",
            steps={
                "root/unsafe\x07": {
                    "attempt": 1,
                    "error": "step\x1b[2Jerror",
                    "fingerprint": "unsafe",
                    "id": "root/unsafe\x07",
                    "label": "label\x1b]0;step-title\x07",
                    "startedAt": BASE_TIME,
                    "status": "failed",
                    "threadId": "thread\x1b[2J",
                }
            },
        ),
    )
    callback = {
        "attempts": 1,
        "createdAt": BASE_TIME,
        "endpoint": "unix://unsafe\x1b[2J",
        "error": "callback\x07error",
        "lastAttemptAt": BASE_TIME,
        "runId": "unsafe",
        "status": "failed",
        "threadId": "main\x1b]0;callback-title\x07",
        "timeoutMs": 1000,
        "updatedAt": BASE_TIME,
        "version": 1,
    }
    (directory / "completion-notification.json").write_text(
        json.dumps(callback),
        encoding="utf-8",
    )
    runner = CliRunner()
    environment = {"CODEX_WORKFLOW_HOME": str(tmp_path)}
    if no_color:
        environment["NO_COLOR"] = "1"

    listed = runner.invoke(cli, [], env=environment)
    arguments = ["--no-color", "show", "unsafe"] if no_color else ["show", "unsafe"]
    shown = runner.invoke(cli, arguments, env=environment)

    assert listed.exit_code == 0
    assert shown.exit_code == 0
    for output in (listed.output, shown.output):
        assert "\x1b" not in output
        assert "\x07" not in output
        assert "failed" in output
        assert "forged-title" in output


def test_output_safely_handles_persisted_lone_surrogates(tmp_path: Path) -> None:
    """Malformed Unicode scalars remain visible without crashing output."""
    _write_run(
        tmp_path,
        _state(
            "surrogate",
            workflow="/work/unsafe-\ud800.js",
            error="failure-\udfff",
        ),
    )
    runner = CliRunner()
    environment = {"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"}

    listed = runner.invoke(cli, [], env=environment)
    shown = runner.invoke(cli, ["show", "surrogate"], env=environment)
    json_result = runner.invoke(cli, ["--json"], env=environment)

    assert listed.exit_code == 0
    assert shown.exit_code == 0
    assert "�" in listed.output
    assert "�" in shown.output
    assert json_result.exit_code == 0
    assert "\\ud800" in json_result.output
    assert "\\udfff" in json_result.output


def test_human_output_sanitizes_unicode_format_and_separators(
    tmp_path: Path,
) -> None:
    """Bidi and invisible separator controls cannot spoof adjacent fields."""
    unsafe = "\u202e\u2066forged\u2069\u2028line\u2029end"
    _write_run(
        tmp_path,
        _state(
            "unicode-controls",
            status="failed",
            workflow=f"/work/{unsafe}.js",
            error=f"failure {unsafe}",
            steps={
                "root/safe": {
                    "attempt": 1,
                    "error": unsafe,
                    "fingerprint": "safe",
                    "id": "root/safe",
                    "label": f"Deploy {unsafe}",
                    "startedAt": BASE_TIME,
                    "status": "failed",
                }
            },
        ),
    )
    environment = {"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"}

    listed = CliRunner().invoke(cli, [], env=environment)
    shown = CliRunner().invoke(
        cli,
        ["show", "unicode-controls"],
        env=environment,
    )

    assert listed.exit_code == 0
    assert shown.exit_code == 0
    for output in (listed.output, shown.output):
        for control in ("\u202e", "\u2066", "\u2069", "\u2028", "\u2029"):
            assert control not in output
        assert "forged" in output
        assert "�" in output


def test_show_marks_unrepresentable_local_timestamp_invalid(
    tmp_path: Path,
) -> None:
    """A boundary timestamp cannot crash local-time detail rendering."""
    state = _state("boundary-time")
    state["createdAt"] = "0001-01-01T00:00:00+23:59"
    _write_run(tmp_path, state)

    result = CliRunner().invoke(
        cli,
        ["show", "boundary-time"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 0
    assert "0001-01-01T00:00:00+23:59 (invalid local time)" in result.output


def test_filtered_empty_message_distinguishes_existing_runs(tmp_path: Path) -> None:
    """An empty filtered selection does not claim the store is empty."""
    _write_run(tmp_path, _state("complete"))

    result = CliRunner().invoke(
        cli,
        ["--status", "failed"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 0
    assert "No workflow runs match status filter: failed" in result.output
    assert "No workflow runs found" not in result.output


def test_show_bounds_errors_and_step_rows_by_default() -> None:
    """Detailed human output visibly bounds errors and large step collections."""
    steps = {
        f"root/{index:03d}": {
            "attempt": 1,
            "fingerprint": f"step-{index:03d}-fingerprint",
            "id": f"root/{index:03d}",
            "label": f"step-{index:03d}",
            "startedAt": BASE_TIME,
            "status": "failed",
        }
        for index in range(MAX_SHOW_STEPS + 5)
    }
    run = RunRecord(
        directory=Path("bounded"),
        state=_state(
            "bounded",
            status="failed",
            error="\n".join(f"error-{index:03d}" for index in range(100)),
            steps=steps,
        ),
    )

    bounded = _render(
        build_show_renderable(run, width=88, now=NOW),
        width=88,
    )
    complete = _render(
        build_show_renderable(run, width=88, now=NOW, full=True),
        width=88,
    )

    assert "output truncated" in bounded
    assert "Showing the first 11 of 55 steps" in bounded
    assert "step-054" not in bounded
    assert "step-054" in complete
    assert "error-099" not in bounded
    assert "error-099" in complete
    assert {step.status for step in run.steps} == {"failed"}


def test_default_narrow_show_has_a_width_aware_step_budget() -> None:
    """Narrow default details stay bounded while full output stays complete."""
    steps = {
        f"root/{index:03d}": {
            "attempt": 1,
            "error": "failure " * 60,
            "fingerprint": f"step-{index:03d}",
            "id": f"root/{index:03d}",
            "label": f"step-{index:03d}",
            "startedAt": BASE_TIME,
            "status": "failed",
        }
        for index in range(MAX_SHOW_STEPS)
    }
    run = RunRecord(
        directory=Path("narrow-budget"),
        state=_state("narrow-budget", status="failed", steps=steps),
    )

    bounded = _render(
        build_show_renderable(run, width=20, now=NOW),
        width=20,
    )
    complete = _render(
        build_show_renderable(run, width=20, now=NOW, full=True),
        width=20,
    )

    assert len(bounded.splitlines()) < 160
    assert "use --full or --json" in " ".join(bounded.split())
    assert "step-049" not in bounded
    assert "step-049" in complete


def test_detail_step_budget_is_stable_across_layout_threshold() -> None:
    """A one-column change cannot expand detail output by thousands of lines."""
    steps = {
        f"root/{index:03d}": {
            "attempt": 1,
            "error": "a long persisted failure " * 80,
            "fingerprint": f"step-{index:03d}",
            "id": f"root/{index:03d}",
            "label": f"step-{index:03d}",
            "startedAt": BASE_TIME,
            "status": "failed",
        }
        for index in range(100)
    }
    run = RunRecord(
        directory=Path("threshold-budget"),
        state=_state("threshold-budget", status="failed", steps=steps),
    )

    line_counts = [
        len(
            _render(
                build_show_renderable(run, width=width, now=NOW),
                width=width,
            ).splitlines()
        )
        for width in (47, 48)
    ]

    assert max(line_counts) < 300
    assert abs(line_counts[1] - line_counts[0]) < 100


def test_wide_steps_include_ids_when_labels_are_duplicated() -> None:
    """Ordinary-width detail rows preserve durable step identity."""
    steps = {
        step_id: {
            "attempt": 1,
            "fingerprint": step_id,
            "id": step_id,
            "label": "Deploy",
            "startedAt": BASE_TIME,
            "status": "completed",
        }
        for step_id in ("root/backend", "root/frontend")
    }
    run = RunRecord(
        directory=Path("duplicate-labels"),
        state=_state("duplicate-labels", steps=steps),
    )

    rendered = _render(
        build_show_renderable(run, width=88, now=NOW),
        width=88,
    )

    assert "root/backend" in rendered
    assert "root/frontend" in rendered


@pytest.mark.parametrize("width", [20, 88])
def test_show_bounds_persisted_step_metadata(width: int) -> None:
    """Huge step labels, IDs, and thread IDs obey default detail limits."""
    huge = "x" * 10_000
    run = RunRecord(
        directory=Path("huge-step"),
        state=_state(
            "huge-step",
            steps={
                "root/huge": {
                    "attempt": 1,
                    "fingerprint": "huge",
                    "id": huge,
                    "label": huge,
                    "startedAt": BASE_TIME,
                    "status": "failed",
                    "threadId": huge,
                }
            },
        ),
    )

    bounded = _render(
        build_show_renderable(run, width=width, now=NOW),
        width=width,
    )

    assert "…" in bounded
    assert len(bounded) < 5_000
    assert len(bounded.splitlines()) < 250


@pytest.mark.parametrize("width", [20, 88, 100])
def test_show_bounds_numeric_step_metadata(width: int) -> None:
    """Huge attempt and worker integers are bounded unless full is requested."""
    huge = int("9" * 4_001)
    run = RunRecord(
        directory=Path("huge-numbers"),
        state=_state(
            "huge-numbers",
            status="running",
            steps={
                "root/numeric": {
                    "attempt": huge,
                    "fingerprint": "numeric",
                    "id": "root/numeric",
                    "label": "Numeric metadata",
                    "startedAt": BASE_TIME,
                    "status": "running",
                    "workerPid": huge,
                }
            },
        ),
    )

    bounded = _render(
        build_show_renderable(run, width=width, now=NOW),
        width=width,
    )
    complete = _render(
        build_show_renderable(run, width=width, now=NOW, full=True),
        width=width,
    )

    assert "…" in bounded
    assert len(bounded) < 5_000
    assert len(complete) > len(bounded) * 2


def test_narrow_list_bounds_malformed_workflow_name() -> None:
    """A huge workflow name cannot expand a narrow row without bound."""
    run = RunRecord(
        directory=Path("long-name"),
        state=_state("long-name", workflow=f"/work/{'x' * 10_000}.js"),
    )

    rendered = _render(
        build_runs_table([run], width=24, live=False, now=NOW),
        width=24,
    )

    assert "…" in rendered
    assert len(rendered.splitlines()) < 20


@pytest.mark.parametrize("width", [20, 32, 40, 80])
def test_status_help_uses_short_wrapping_metavar(width: int) -> None:
    """Status help avoids embedding every accepted value in the option name."""
    result = CliRunner().invoke(cli, ["--help"], terminal_width=width)

    assert result.exit_code == 0
    assert "--status STATUS" in result.output
    assert "--status {" not in result.output
    assert max(len(line) for line in result.output.splitlines()) <= max(width, 40)


def test_watch_refreshes_filtered_state_and_exits_130(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive watch rereads state and gives Ctrl-C its conventional code."""
    _write_run(tmp_path, _state("complete"))
    reads = 0
    real_load_runs = workflow_cli.load_runs

    def counting_load_runs(
        *,
        statuses: tuple[str, ...] = (),
        limit: int | None = None,
        now: datetime | None = None,
        observe: bool = True,
        observation_report: ObservationReport | None = None,
    ) -> list[RunRecord]:
        """Count real durable-state reads."""
        nonlocal reads
        reads += 1
        return real_load_runs(
            statuses=statuses,
            limit=limit,
            now=now,
            observe=observe,
            observation_report=observation_report,
        )

    sleeps = 0

    def interrupt_second_sleep(_: float) -> None:
        """Allow one refresh, then emulate Ctrl-C."""
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(workflow_cli, "load_runs", counting_load_runs)
    monkeypatch.setattr(workflow_cli, "_stdout_is_tty", lambda: True)
    monkeypatch.setattr(workflow_cli.time, "sleep", interrupt_second_sleep)

    result = CliRunner().invoke(
        cli,
        ["watch", "--status", "failed", "--limit", "1", "--refresh", "0.2"],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "NO_COLOR": "1",
            "TERM": "xterm-256color",
        },
    )

    assert result.exit_code == 130
    assert reads >= 2
    assert result.output.splitlines() == ["Stopped watching workflow runs."]


def test_watch_interrupt_during_initial_load_exits_130(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C during the first state scan follows the normal stop path."""
    monkeypatch.setattr(workflow_cli, "_stdout_is_tty", lambda: True)

    def interrupt_load(**_kwargs: object) -> list[RunRecord]:
        """Interrupt the initial durable-state scan."""
        raise KeyboardInterrupt

    monkeypatch.setattr(workflow_cli, "load_runs", interrupt_load)
    result = CliRunner().invoke(
        cli,
        ["watch"],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "NO_COLOR": "1",
            "TERM": "xterm-256color",
        },
    )

    assert result.exit_code == 130
    assert "Stopped watching workflow runs." in result.output
    assert "Aborted" not in result.output


def test_watch_uses_alternate_screen_to_avoid_retained_frame_flood(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping a large dashboard does not retain its live frame."""
    options: dict[str, object] = {}

    class RecordingLive:
        """Record Live construction without performing terminal control."""

        def __init__(self, _initial: object, **kwargs: object) -> None:
            options.update(kwargs)

        def __enter__(self) -> RecordingLive:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def update(self, _rendered: object, *, refresh: bool) -> None:
            del refresh

    monkeypatch.setattr(workflow_cli, "Live", RecordingLive)
    monkeypatch.setattr(workflow_cli, "_stdout_is_tty", lambda: True)
    monkeypatch.setattr(
        workflow_cli.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    result = CliRunner().invoke(
        cli,
        ["watch", "--limit", "1000"],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "NO_COLOR": "1",
            "TERM": "xterm-256color",
        },
    )

    assert result.exit_code == 130
    assert options["screen"] is True
    assert options["transient"] is True
    assert options["auto_refresh"] is False
    assert "refresh_per_second" not in options


@pytest.mark.parametrize("height", [5, 10])
def test_watch_omits_complete_rows_beyond_terminal_height(
    height: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short watch frame reports omitted runs and never gets cropped."""
    monkeypatch.setenv("CODEX_WORKFLOW_HOME", str(tmp_path))
    for index in range(10):
        _write_run(
            tmp_path,
            _state(f"run-{index:02d}", workflow=f"/work/job-{index:02d}.js"),
        )
    console = Console(
        color_system=None,
        force_terminal=False,
        height=height,
        width=80,
    )

    renderable = workflow_cli._render_list(
        statuses=(),
        limit=10,
        json_output=False,
        no_color=True,
        live=True,
        console=console,
    )
    assert renderable is not None
    rendered = _render(renderable, width=80)

    assert len(rendered.splitlines()) <= height
    assert "of 10 selected runs" in rendered
    assert "omitted (terminal height)" in rendered


def test_warning_statuses_have_explicit_non_neutral_styles() -> None:
    """Ownership and generation warnings remain visually distinct."""
    assert workflow_cli.STATUS_STYLES["unverifiable"] != "bright_black"
    assert workflow_cli.CALLBACK_STYLES["stale"] != "bright_black"


@pytest.mark.parametrize("arguments", [[], ["show", "ascii-run"]])
@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_redirected_legacy_encoded_human_output_is_graceful(
    arguments: list[str],
    encoding: str,
    tmp_path: Path,
) -> None:
    """Non-UTF-8 redirected human output never raises an encoding error."""
    _write_run(tmp_path, _state("ascii-run"))
    environment = {
        **os.environ,
        "CODEX_WORKFLOW_HOME": str(tmp_path),
        "NO_COLOR": "1",
        "PYTHONIOENCODING": encoding,
        "PYTHONPATH": str(Path(__file__).parents[1]),
    }

    completed = subprocess.run(
        [sys.executable, "-m", "claude_code_tools.workflow_cli", *arguments],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert completed.returncode == 0, completed.stderr.decode(encoding)
    output = completed.stdout.decode(encoding)
    assert "ascii-run" in output
    assert b"UnicodeEncodeError" not in completed.stderr


@pytest.mark.parametrize("encoding", ["ascii", "cp1252"])
def test_legacy_encoding_empty_path_is_safe_and_width_bounded(
    encoding: str,
    tmp_path: Path,
) -> None:
    """Unsupported path characters are replaced before narrow Rich layout."""
    workflow_home = tmp_path / ("missing-" + "😀" * 5)
    environment = {
        **os.environ,
        "CODEX_WORKFLOW_HOME": str(workflow_home),
        "COLUMNS": "20",
        "NO_COLOR": "1",
        "PYTHONIOENCODING": encoding,
        "PYTHONPATH": str(Path(__file__).parents[1]),
    }

    completed = subprocess.run(
        [sys.executable, "-m", "claude_code_tools.workflow_cli"],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert completed.returncode == 0, completed.stderr.decode(encoding)
    output = completed.stdout.decode(encoding)
    assert max(len(line) for line in output.splitlines()) <= 20
    assert b"UnicodeEncodeError" not in completed.stderr


def test_empty_store_path_is_not_interpreted_as_rich_markup(
    tmp_path: Path,
) -> None:
    """Legal bracketed workflow-home paths render as literal text."""
    workflow_home = tmp_path / "[" / "]"

    result = CliRunner().invoke(
        cli,
        [],
        env={"CODEX_WORKFLOW_HOME": str(workflow_home), "NO_COLOR": "1"},
    )

    assert result.exit_code == 0
    assert "[/]" in result.output
    assert "MarkupError" not in result.output


@pytest.mark.parametrize("width", [40, 41, 42, 43])
def test_show_accepts_the_listed_run_id_abbreviation(
    width: int,
    tmp_path: Path,
) -> None:
    """A compact ID printed by list can be passed directly to show."""
    run_id = "20260714T140000Z-1234abcd"
    _write_run(tmp_path, _state(run_id))
    abbreviation = f"{run_id[:8]}~{run_id[-8:]}"

    listed = CliRunner().invoke(
        cli,
        [],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "COLUMNS": str(width),
            "NO_COLOR": "1",
        },
    )
    shown = CliRunner().invoke(
        cli,
        ["show", abbreviation],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert abbreviation in listed.output
    assert shown.exit_code == 0
    assert run_id in shown.output


def test_show_distinguishes_store_access_failure_from_absence(
    tmp_path: Path,
) -> None:
    """An invalid workflow-home parent is not reported as an absent run."""
    workflow_home = tmp_path / "not-a-directory"
    workflow_home.write_text("not a store", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["show", "valid-run"],
        env={"CODEX_WORKFLOW_HOME": str(workflow_home), "NO_COLOR": "1"},
    )

    assert result.exit_code != 0
    assert "Cannot inspect workflow run" in result.output
    assert "Workflow run not found" not in result.output


def test_invalid_run_id_diagnostic_is_bounded() -> None:
    """A hostile invalid positional value cannot flood terminal diagnostics."""
    run_id = "!" + "x" * 20_000

    result = CliRunner().invoke(cli, ["show", run_id])

    assert result.exit_code != 0
    assert "Invalid workflow run ID" in result.output
    assert "…" in result.output
    assert len(result.output) < 500


@pytest.mark.parametrize("state_kind", ["missing", "directory"])
def test_show_exposes_existing_run_with_non_regular_state(
    state_kind: str,
    tmp_path: Path,
) -> None:
    """Show surfaces malformed-state diagnostics for an existing run."""
    run_directory = tmp_path / "runs" / "broken-state"
    run_directory.mkdir(parents=True)
    if state_kind == "directory":
        (run_directory / "state.json").mkdir()

    result = CliRunner().invoke(
        cli,
        ["show", "broken-state"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 0
    assert "malformed" in result.output
    assert "state.json" in result.output or "regular state" in result.output
    assert "Workflow run not found" not in result.output


def test_show_rejects_a_symlinked_named_run(
    tmp_path: Path,
) -> None:
    """Named lookup cannot follow a run-directory symlink out of the store."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "state.json").write_text(
        json.dumps(_state("linked-run", error="outside-secret")),
        encoding="utf-8",
    )
    runs_directory = tmp_path / "runs"
    runs_directory.mkdir()
    _symlink_or_skip(runs_directory / "linked-run", outside)

    result = CliRunner().invoke(
        cli,
        ["show", "linked-run"],
        env={"CODEX_WORKFLOW_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code != 0
    assert "Workflow run not found" in result.output
    assert "outside-secret" not in result.output
