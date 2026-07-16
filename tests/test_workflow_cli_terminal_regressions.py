"""Focused regressions for workflow CLI terminal edge cases."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from rich.console import Console, RenderableType
from rich.text import Text

from claude_code_tools import workflow_cli, workflow_runs
from claude_code_tools import workflow_cli_formatting as formatting
from claude_code_tools import workflow_cli_rendering as rendering
from claude_code_tools.workflow_cli import cli
from claude_code_tools.workflow_validation import parse_run_record

BASE_TIME = "2026-07-14T14:00:00Z"
NOW = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)


def _state(
    run_id: str,
    *,
    workflow: str = "/work/audit.js",
    error: str | None = None,
    steps: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a valid completed workflow state."""
    value: dict[str, object] = {
        "completedAt": BASE_TIME,
        "concurrency": 1,
        "createdAt": BASE_TIME,
        "cwd": "/work",
        "runId": run_id,
        "status": "completed",
        "steps": dict(steps or {}),
        "updatedAt": BASE_TIME,
        "version": 1,
        "workflowHash": "abc123",
        "workflowPath": workflow,
    }
    if error is not None:
        value["error"] = error
    return value


def _write_run(home: Path, state: Mapping[str, object]) -> Path:
    """Write one workflow state beneath an isolated home."""
    directory = home / "runs" / str(state["runId"])
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    return directory


def _invoke(home: Path, arguments: list[str], *, width: int = 80) -> Result:
    """Invoke the CLI with deterministic plain terminal settings."""
    return CliRunner().invoke(
        cli,
        arguments,
        env={
            "CODEX_WORKFLOW_HOME": str(home),
            "COLUMNS": str(width),
            "NO_COLOR": "1",
        },
    )


def test_colliding_abbreviations_display_actionable_full_ids(
    tmp_path: Path,
) -> None:
    """Colliding compact IDs expose full IDs in list and error output."""
    run_ids = (
        "abcdefgh-alpha-12345678",
        "abcdefgh-bravo-12345678",
    )
    for run_id in run_ids:
        _write_run(tmp_path, _state(run_id))

    listed = _invoke(tmp_path, ["--all", "--limit", "1"])
    ambiguous = _invoke(tmp_path, ["show", "abcdefgh~12345678"])

    assert listed.exit_code == 0
    assert ambiguous.exit_code != 0
    assert sum(run_id in listed.output for run_id in run_ids) == 1
    for run_id in run_ids:
        assert run_id in ambiguous.output
        assert _invoke(tmp_path, ["show", run_id]).exit_code == 0


def test_show_resolves_exposed_malformed_directory_name_safely(
    tmp_path: Path,
) -> None:
    """Exact scanned names remain inspectable without allowing traversal."""
    malformed_id = "bad id"
    _write_run(tmp_path, _state(malformed_id))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "state.json").write_text("outside-secret", encoding="utf-8")

    listed = _invoke(tmp_path, ["--all"])
    shown = _invoke(tmp_path, ["show", malformed_id])
    traversal = _invoke(tmp_path, ["show", "../outside"])

    assert malformed_id in listed.output
    assert shown.exit_code == 0
    assert malformed_id in shown.output
    assert traversal.exit_code != 0
    assert "outside-secret" not in traversal.output


def test_long_malformed_run_id_is_listed_in_actionable_full_form(
    tmp_path: Path,
) -> None:
    """A malformed ID is not shortened to an abbreviation show rejects."""
    run_id = "bad idXX-middle-12345678"
    _write_run(tmp_path, _state(run_id))

    listed = _invoke(tmp_path, ["--all"])
    shown = _invoke(tmp_path, ["show", run_id])

    assert listed.exit_code == 0
    assert run_id in listed.output
    assert "bad idXX~12345678" not in listed.output
    assert shown.exit_code == 0


def test_component_over_128_characters_is_listed_and_showable(
    tmp_path: Path,
) -> None:
    """Every safe directory component exposed by list remains actionable."""
    run_id = "bad id " + "x" * 122
    assert len(run_id) == 129
    _write_run(tmp_path, _state(run_id))

    listed = _invoke(tmp_path, ["--all", "--json"])
    shown = _invoke(tmp_path, ["show", run_id, "--json"])

    assert listed.exit_code == 0
    assert shown.exit_code == 0
    assert run_id in listed.output
    assert run_id in shown.output


def test_bounded_error_limits_work_before_sanitizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default error rendering never sanitizes a complete hostile value."""
    observed_lengths: list[int] = []
    real_sanitize = formatting.sanitize

    def recording_sanitize(value: object) -> str:
        """Record the amount of text passed to terminal sanitization."""
        observed_lengths.append(len(str(value)))
        return real_sanitize(value)

    monkeypatch.setattr(formatting, "sanitize", recording_sanitize)

    rendered, truncated = formatting.bounded_error("x" * 8_000_000, full=False)

    assert truncated is True
    assert len(rendered) < 2_100
    assert observed_lengths
    assert max(observed_lengths) <= formatting.MAX_ERROR_CHARS + 1


def test_short_watch_never_builds_rows_beyond_terminal_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Height fitting avoids rendering the many rows it cannot display."""
    runs = [
        parse_run_record(
            directory=Path(f"run-{index}"),
            state=_state(f"run-{index}"),
        )
        for index in range(1_000)
    ]
    observed_counts: list[int] = []
    real_build = rendering._build_runs_table_from_views

    def recording_build(
        selected: list[rendering.RunRowView],
        *,
        width: int,
        live: bool,
    ) -> RenderableType:
        """Record each candidate row count before delegating."""
        observed_counts.append(len(selected))
        return real_build(selected, width=width, live=live)

    monkeypatch.setattr(
        rendering,
        "_build_runs_table_from_views",
        recording_build,
    )
    console = Console(
        color_system=None,
        force_terminal=False,
        height=5,
        width=80,
    )

    renderable = workflow_cli._fit_live_runs(runs, console=console, now=NOW)

    assert len(console.render_lines(renderable, pad=False)) <= console.height
    assert observed_counts
    assert max(observed_counts) <= console.height


def test_short_watch_counts_footer_in_terminal_height() -> None:
    """Height fitting retains a live warning without cropping the frame."""
    runs = [
        parse_run_record(
            directory=Path(f"run-{index}"),
            state=_state(f"run-{index}"),
        )
        for index in range(10)
    ]
    console = Console(
        color_system=None,
        force_terminal=False,
        height=5,
        width=80,
    )
    warning = Text("Observation incomplete", no_wrap=True)

    renderable = workflow_cli._fit_live_runs(
        runs,
        console=console,
        now=NOW,
        footer=warning,
    )
    rendered = console.render_lines(renderable, pad=False)
    text = "\n".join("".join(segment.text for segment in line) for line in rendered)

    assert len(rendered) <= console.height
    assert "Observation incomplete" in text


def test_one_line_empty_watch_prioritizes_footer() -> None:
    """An incomplete empty selection keeps its warning in a one-line frame."""
    console = Console(
        color_system=None,
        force_terminal=False,
        height=1,
        width=80,
    )
    warning = Text("Observation incomplete", no_wrap=True)

    renderable = workflow_cli._fit_live_runs(
        [],
        console=console,
        now=NOW,
        footer=warning,
    )
    rendered = console.render_lines(renderable, pad=False)
    text = "".join(segment.text for segment in rendered[0])

    assert len(rendered) == 1
    assert text == "Observation incomplete"


def test_inferred_output_width_is_clamped(tmp_path: Path) -> None:
    """A hostile COLUMNS value cannot amplify redirected output."""
    _write_run(tmp_path, _state("wide-run", error="x" * 10_000))

    listed = _invoke(tmp_path, ["--all"], width=100_000)
    shown = _invoke(tmp_path, ["show", "wide-run"], width=100_000)

    assert listed.exit_code == 0
    assert shown.exit_code == 0
    assert max(map(len, listed.output.splitlines())) <= workflow_cli.MAX_OUTPUT_WIDTH
    assert max(map(len, shown.output.splitlines())) <= workflow_cli.MAX_OUTPUT_WIDTH


def test_hostile_columns_integer_cannot_crash_human_output(
    tmp_path: Path,
) -> None:
    """Rich never reparses an unbounded COLUMNS environment value."""
    result = CliRunner().invoke(
        cli,
        [],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "COLUMNS": "9" * 5_000,
            "NO_COLOR": "1",
        },
    )

    assert result.exit_code == 0
    assert "No workflow runs found" in result.output


@pytest.mark.parametrize("width", range(4, 9))
@pytest.mark.parametrize("height", range(1, 4))
@pytest.mark.parametrize("incomplete", [False, True])
def test_empty_watch_frame_fits_and_prioritizes_warning(
    width: int,
    height: int,
    incomplete: bool,
) -> None:
    """Empty live frames fit, with incomplete observation shown first."""
    console = Console(
        color_system=None,
        force_terminal=False,
        height=height,
        width=width,
    )
    warning = (
        Text("Incomplete observation", overflow="ellipsis", no_wrap=True)
        if incomplete
        else None
    )
    renderable = workflow_cli._fit_live_runs(
        [],
        console=console,
        now=NOW,
        footer=warning,
        empty=Text(
            "Waiting for workflow runs",
            overflow="ellipsis",
            no_wrap=True,
        ),
    )

    rendered = console.render_lines(renderable, pad=False)
    text = "\n".join("".join(segment.text for segment in line) for line in rendered)

    assert len(rendered) <= height
    if incomplete:
        assert "Inc" in text
        if height == 1:
            assert text.startswith("Inc")


def test_filtered_empty_json_list_loads_store_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Machine-readable empty filtering does not scan for human copy."""
    calls: list[dict[str, object]] = []

    def recording_load_runs(**kwargs: object) -> workflow_runs.RunQueryResult:
        """Record one simulated empty store scan."""
        calls.append(kwargs)
        return workflow_runs.RunQueryResult((), False, False)

    monkeypatch.setattr(workflow_cli, "load_runs", recording_load_runs)
    snapshot = workflow_cli._query_list(
        statuses=("failed",),
        limit=50,
    )
    monkeypatch.setattr(workflow_cli, "_emit_json", lambda _value: None)
    workflow_cli._emit_list_json(snapshot)

    assert len(calls) == 1
    assert calls[0]["limit"] == 50


def test_json_list_reports_when_matching_runs_are_truncated(
    tmp_path: Path,
) -> None:
    """Machine-readable lists expose the requested limit and truncation."""
    _write_run(tmp_path, _state("older"))
    newer = _state("newer")
    newer["createdAt"] = "2026-07-14T15:00:00Z"
    newer["completedAt"] = "2026-07-14T15:00:00Z"
    newer["updatedAt"] = "2026-07-14T15:00:00Z"
    _write_run(tmp_path, newer)

    result = _invoke(tmp_path, ["--all", "--limit", "1", "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["complete"] is False
    assert payload["limit"] == 1
    assert payload["observationComplete"] is True
    assert payload["observationSkipped"] == 0
    assert payload["schemaVersion"] == 1
    assert payload["truncated"] is True
    assert [run["runId"] for run in payload["runs"]] == ["newer"]


@pytest.mark.parametrize("width", [4, 5, 6, 7])
def test_ultra_narrow_show_uses_content_bearing_plain_text(
    tmp_path: Path,
    width: int,
) -> None:
    """Ultra-narrow output retains data instead of empty panel borders."""
    steps = {
        "s1": {
            "attempt": 1,
            "completedAt": BASE_TIME,
            "fingerprint": "step-one",
            "id": "s1",
            "label": "work",
            "startedAt": BASE_TIME,
            "status": "completed",
        }
    }
    _write_run(tmp_path, _state("tiny", steps=steps))

    result = _invoke(tmp_path, ["show", "tiny"], width=width)

    assert result.exit_code == 0
    assert "tiny" in result.output
    assert "work" in result.output
    assert "╭" not in result.output
    assert max(len(line) for line in result.output.splitlines()) <= width


def test_malformed_workflow_home_diagnostic_is_bounded() -> None:
    """An unusable huge workflow home cannot flood stderr."""
    workflow_home = Path("/") / ("x" * 20_000)

    result = CliRunner().invoke(
        cli,
        [],
        env={"CODEX_WORKFLOW_HOME": str(workflow_home), "NO_COLOR": "1"},
    )

    assert result.exit_code != 0
    assert "Cannot read workflow runs" in result.output
    assert "…" in result.output
    assert len(result.output) < 500


@pytest.mark.parametrize(
    "arguments",
    [
        ["--" + "x" * 20_000],
        ["command-" + "x" * 20_000],
        ["show", "run", "x" * 20_000],
        ["show", "--" + "x" * 20_000, "run"],
    ],
)
def test_click_parser_diagnostics_are_bounded(arguments: list[str]) -> None:
    """Hostile options, commands, and extra arguments cannot flood stderr."""
    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 2
    assert "…" in result.output
    assert len(result.output) < 500


def test_repeated_status_filters_are_deduplicated_before_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated finite filters cannot amplify filtered-empty human output."""
    observed: list[tuple[str, ...]] = []

    def recording_load_runs(**kwargs: object) -> workflow_runs.RunQueryResult:
        """Record the normalized statuses and return a filtered-empty query."""
        statuses = kwargs["statuses"]
        assert isinstance(statuses, tuple)
        observed.append(statuses)
        return workflow_runs.RunQueryResult((), False, True)

    monkeypatch.setattr(workflow_cli, "load_runs", recording_load_runs)
    arguments = ["--status", "completed"] * 5_000

    result = _invoke(tmp_path, arguments)

    assert result.exit_code == 0
    assert observed == [("completed",)]
    assert result.output.count("completed") == 1
    assert len(result.output) < 500


def test_watch_height_probe_bounds_text_before_sanitizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Height fitting never sanitizes complete huge persisted fields."""
    observed_lengths: list[int] = []
    real_sanitize = formatting.sanitize

    def recording_sanitize(value: object) -> str:
        """Record the amount of text passed to terminal sanitization."""
        observed_lengths.append(len(str(value)))
        return real_sanitize(value)

    monkeypatch.setattr(formatting, "sanitize", recording_sanitize)
    monkeypatch.setattr(rendering, "sanitize", recording_sanitize)
    huge = "x" * 1_000_000
    runs = [
        parse_run_record(
            directory=Path(f"run-{index}"),
            state=_state(
                f"run-{index}",
                workflow=f"/work/{huge}.js",
                error=huge,
            ),
        )
        for index in range(12)
    ]
    console = Console(
        color_system=None,
        force_terminal=False,
        height=5,
        width=80,
    )

    renderable = workflow_cli._fit_live_runs(runs, console=console, now=NOW)
    lines = console.render_lines(renderable, pad=False)

    assert len(lines) <= console.height
    assert observed_lengths
    assert max(observed_lengths) <= 201


def test_interactive_watch_is_hermetic_with_dumb_parent_term(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interactive watch test supplies its own capable TERM."""
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(workflow_cli, "_stdout_is_tty", lambda: True)
    monkeypatch.setattr(
        workflow_cli.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    class RecordingLive:
        """Avoid terminal control while exercising the interactive path."""

        def __init__(self, _initial: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> RecordingLive:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def update(self, _rendered: object, *, refresh: bool) -> None:
            del refresh

    monkeypatch.setattr(workflow_cli, "Live", RecordingLive)

    result = CliRunner().invoke(
        cli,
        ["watch", "--refresh", "0.2"],
        env={
            "CODEX_WORKFLOW_HOME": str(tmp_path),
            "NO_COLOR": "1",
            "TERM": "xterm-256color",
        },
    )

    assert result.exit_code == 130
    assert "Stopped watching workflow runs." in result.output
