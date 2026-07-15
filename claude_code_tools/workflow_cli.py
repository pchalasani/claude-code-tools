"""Polished read-only CLI for durable dynamic-workflow runs."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable
from datetime import datetime
from typing import TextIO

import click
from click.core import ParameterSource
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.text import Text

from claude_code_tools.workflow_cli_formatting import bounded_text as _bounded_text
from claude_code_tools.workflow_cli_formatting import (
    now as _now,
)
from claude_code_tools.workflow_cli_formatting import (
    sanitize as _sanitize,
)
from claude_code_tools.workflow_cli_rendering import (
    ABBREVIATED_RUN_ID_PATTERN as ABBREVIATED_RUN_ID_PATTERN,
    CALLBACK_STYLES as CALLBACK_STYLES,
    MAX_SHOW_STEPS as MAX_SHOW_STEPS,
    STATUS_STYLES as STATUS_STYLES,
    build_runs_table,
    build_show_renderable,
)
from claude_code_tools.workflow_runs import (
    FILTER_STATUSES,
    ObservationReport,
    RunRecord,
    WorkflowStoreError,
    load_run,
    load_runs,
    workflow_home,
)

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_DIAGNOSTIC_CHARS = 160
MAX_OUTPUT_WIDTH = 240
MAX_OUTPUT_HEIGHT = 1_000


def _terminal_dimension(name: str, fallback: int, maximum: int) -> int:
    """Read one bounded positive terminal dimension from the environment."""
    value = os.environ.get(name)
    if value and len(value) <= 6 and value.isascii() and value.isdigit():
        parsed = int(value)
        if parsed > 0:
            return min(maximum, parsed)
    return min(maximum, max(1, fallback))


def _terminal_size() -> tuple[int, int]:
    """Return bounded terminal dimensions without parsing hostile integers."""
    try:
        detected = os.get_terminal_size()
        fallback = (detected.columns, detected.lines)
    except OSError:
        fallback = (80, 24)
    return (
        _terminal_dimension("COLUMNS", fallback[0], MAX_OUTPUT_WIDTH),
        _terminal_dimension("LINES", fallback[1], MAX_OUTPUT_HEIGHT),
    )


def _configure_output_encoding(stream: TextIO) -> None:
    """Replace characters unsupported by an output stream's encoding."""
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _diagnostic_text(value: object) -> str:
    """Bound text and make it representable in stderr's encoding."""
    rendered = _bounded_text(
        value,
        maximum=MAX_DIAGNOSTIC_CHARS,
        full=False,
    )[0]
    encoding = getattr(sys.stderr, "encoding", None)
    if not encoding:
        return rendered
    try:
        return rendered.encode(encoding, errors="replace").decode(encoding)
    except LookupError:
        return rendered


def _console(no_color: bool, width: int | None = None) -> Console:
    """Create the CLI's stdout console.

    Args:
        no_color: Whether to disable ANSI styling.
        width: Optional explicit output width.

    Returns:
        A configured Rich console.
    """
    color_disabled = no_color or "NO_COLOR" in os.environ
    _configure_output_encoding(sys.stdout)
    inferred_width, inferred_height = _terminal_size()
    if width is None:
        width = inferred_width
    return Console(
        color_system=None if color_disabled else "auto",
        file=sys.stdout,
        highlight=False,
        soft_wrap=False,
        width=width,
        height=inferred_height,
    )


def _stdout_is_tty() -> bool:
    """Return whether stdout supports an interactive live display."""
    return bool(sys.stdout.isatty())


def _emit_json(value: object) -> None:
    """Write stable pretty-printed JSON.

    Args:
        value: JSON-compatible value to emit.
    """
    click.echo(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True))


def _empty_message(
    *,
    has_runs: bool,
    statuses: tuple[str, ...],
    live: bool,
) -> str:
    """Describe an empty store or an empty filtered selection.

    Args:
        has_runs: Whether the store contains any run records.
        statuses: Active status filters.
        live: Whether the message appears in watch mode.

    Returns:
        A precise human-readable empty-state message.
    """
    if has_runs and statuses:
        selected = ", ".join(_sanitize(status) for status in statuses)
        prefix = "Waiting; no workflow runs match" if live else "No workflow runs match"
        return f"{prefix} status filter: {selected}."
    if live:
        return "Waiting for workflow runs…"
    path = _bounded_text(
        workflow_home() / "runs",
        maximum=MAX_DIAGNOSTIC_CHARS,
        full=False,
    )[0]
    return f"No workflow runs found in {path}."


def _observation_warning(report: ObservationReport) -> Text:
    """Explain that process-derived filtering could not inspect every run."""
    noun = "observation" if report.skipped == 1 else "observations"
    return Text(
        f"Incomplete: process-observation budget skipped {report.skipped} "
        f"{noun}; filtered results may omit matching runs.",
        style="yellow",
        overflow="ellipsis",
        no_wrap=True,
    )


def _render_list(
    *,
    statuses: tuple[str, ...],
    limit: int,
    json_output: bool,
    no_color: bool,
    live: bool = False,
    console: Console | None = None,
) -> RenderableType | None:
    """Load, filter, and render a workflow-run list.

    Args:
        statuses: Effective status filters.
        limit: Maximum number of rows.
        json_output: Whether to emit structured JSON.
        no_color: Whether to disable ANSI styles.
        live: Whether the result is for a live dashboard.
        console: Existing live console, when applicable.

    Returns:
        A live renderable, or ``None`` after direct output.
    """
    now = _now()
    observation_report = ObservationReport()
    try:
        load_limit = limit + 1 if json_output else limit
        runs = load_runs(
            statuses=statuses,
            limit=load_limit,
            now=now,
            observation_report=observation_report,
        )
        truncated = len(runs) > limit
        if truncated:
            runs = runs[:limit]
    except WorkflowStoreError as error:
        raise click.ClickException(_diagnostic_text(error)) from error
    if json_output:
        _emit_json(
            {
                "complete": not truncated and observation_report.complete,
                "limit": limit,
                "observationComplete": observation_report.complete,
                "observationSkipped": observation_report.skipped,
                "runs": [run.to_json(now) for run in runs],
                "schemaVersion": 1,
                "truncated": truncated,
            }
        )
        return None
    has_runs = bool(runs)
    if not runs and statuses:
        try:
            has_runs = bool(load_runs(limit=1, observe=False))
        except WorkflowStoreError as error:
            raise click.ClickException(_diagnostic_text(error)) from error
    output = console or _console(no_color)
    if not runs:
        message = _empty_message(
            has_runs=has_runs,
            statuses=statuses,
            live=console is not None,
        )
        if console is not None:
            empty = Text(
                message,
                style="bright_black",
                overflow="ellipsis",
                no_wrap=True,
            )
            warning = (
                None
                if observation_report.complete
                else _observation_warning(observation_report)
            )
            return _fit_live_runs(
                [],
                console=output,
                now=now,
                footer=warning,
                empty=empty,
            )
        output.print(Text(message, style="bright_black"))
        if not observation_report.complete:
            output.print(_observation_warning(observation_report))
        return None
    if console is None:
        table = build_runs_table(runs, width=output.width, live=live, now=now)
        output.print(table)
        if not observation_report.complete:
            output.print(_observation_warning(observation_report))
        return None
    footer = (
        _observation_warning(observation_report)
        if not observation_report.complete
        else None
    )
    return _fit_live_runs(runs, console=output, now=now, footer=footer)


def _fit_live_runs(
    runs: list[RunRecord],
    *,
    console: Console,
    now: datetime,
    footer: RenderableType | None = None,
    empty: RenderableType | None = None,
) -> RenderableType:
    """Fit complete run rows and optional empty/footer content to the height."""
    options = console.options.update(height=None)
    if not runs:
        empty_renderable = empty or Text(
            "Waiting for workflow runs…",
            style="bright_black",
            overflow="ellipsis",
            no_wrap=True,
        )
        if footer is None:
            return empty_renderable
        candidate = Group(empty_renderable, footer)
        if (
            len(console.render_lines(candidate, options, pad=False))
            <= console.height
        ):
            return candidate
        return footer

    def build(visible: int) -> RenderableType:
        omitted = len(runs) - visible
        marker = Text(
            f"Showing {visible} of {len(runs)} selected runs; "
            f"{omitted} omitted (terminal height).",
            style="bright_black",
            overflow="ellipsis",
            no_wrap=True,
        )
        parts: list[RenderableType] = []
        if visible:
            parts.append(
                build_runs_table(
                    runs[:visible],
                    width=console.width,
                    live=True,
                    now=now,
                )
            )
        if omitted:
            parts.append(marker)
        if footer is not None:
            parts.append(footer)
        return Group(*parts)

    maximum_visible = min(len(runs), max(0, console.height))
    largest_candidate = build(maximum_visible)
    if (
        maximum_visible == len(runs)
        and len(console.render_lines(largest_candidate, options, pad=False))
        <= console.height
    ):
        return largest_candidate

    low = 0
    high = maximum_visible
    while low < high:
        middle = (low + high + 1) // 2
        candidate = build(middle)
        if len(console.render_lines(candidate, options, pad=False)) <= console.height:
            low = middle
        else:
            high = middle - 1
    smallest_candidate = build(low)
    if (
        len(console.render_lines(smallest_candidate, options, pad=False))
        <= console.height
    ):
        return smallest_candidate
    if footer is not None:
        return footer
    return smallest_candidate


def _load_named_run(run_id: str, now: datetime) -> RunRecord:
    """Load one validated run ID or raise a user-facing error.

    Args:
        run_id: Directory-safe run identifier.
        now: Shared observation time for classification and rendering.

    Returns:
        The loaded run record.

    Raises:
        click.ClickException: If the ID is invalid or the run is absent.
    """
    rendered_id = _diagnostic_text(run_id)
    if not RUN_ID_PATTERN.fullmatch(run_id):
        try:
            scanned = load_runs(observe=False)
        except WorkflowStoreError as error:
            raise click.ClickException(_diagnostic_text(error)) from error
        exact = next(
            (run for run in scanned if run.directory.name == run_id),
            None,
        )
        if exact is not None:
            return load_run(exact.directory, now=now)
        if not ABBREVIATED_RUN_ID_PATTERN.fullmatch(run_id):
            raise click.ClickException(f"Invalid workflow run ID: {rendered_id}")
        matches = [run for run in scanned if run.abbreviated_id == run_id]
        if len(matches) == 1:
            return load_run(matches[0].directory, now=now)
        if len(matches) > 1:
            choices = ", ".join(
                _sanitize(run.run_id) for run in matches[:5]
            )
            omitted = len(matches) - 5
            suffix = f"; {omitted} more omitted" if omitted > 0 else ""
            raise click.ClickException(
                "Workflow run ID abbreviation is ambiguous: "
                f"{rendered_id}. Use a full run ID: {choices}{suffix}"
            )
        raise click.ClickException(f"Workflow run not found: {rendered_id}")
    directory = workflow_home() / "runs" / run_id
    try:
        is_directory = stat.S_ISDIR(directory.lstat().st_mode)
    except FileNotFoundError:
        raise click.ClickException(f"Workflow run not found: {rendered_id}") from None
    except OSError as error:
        rendered_error = _diagnostic_text(error)
        raise click.ClickException(
            f"Cannot inspect workflow run {rendered_id}: {rendered_error}"
        ) from error
    if not is_directory:
        raise click.ClickException(f"Workflow run not found: {rendered_id}")
    try:
        return load_run(directory, now=now)
    except WorkflowStoreError as error:
        raise click.ClickException(_diagnostic_text(error)) from error


def _status_option(function: Callable[..., object]) -> Callable[..., object]:
    """Decorate a command with the repeatable status filter.

    Args:
        function: Click callback to decorate.

    Returns:
        The decorated callback.
    """
    accepted = ", ".join(FILTER_STATUSES)
    return click.option(
        "--status",
        "statuses",
        type=click.Choice(FILTER_STATUSES, case_sensitive=False),
        multiple=True,
        metavar="STATUS",
        help=(
            "Include only this effective status; repeat for more. "
            f"Accepted values: {accepted}."
        ),
    )(function)


def _limit_option(function: Callable[..., object]) -> Callable[..., object]:
    """Decorate a command with the bounded row limit.

    Args:
        function: Click callback to decorate.

    Returns:
        The decorated callback.
    """
    return click.option(
        "--limit",
        type=click.IntRange(1, 1_000),
        default=50,
        show_default=True,
        help="Maximum number of runs to display.",
    )(function)


@click.group(
    invoke_without_command=True,
    help="Observe local durable dynamic-workflow runs without changing them.",
)
@_status_option
@_limit_option
@click.option("--json", "json_output", is_flag=True, help="Emit stable JSON.")
@click.option(
    "--no-color",
    is_flag=True,
    help="Disable ANSI color (also honored through NO_COLOR).",
)
@click.pass_context
def cli(
    context: click.Context,
    statuses: tuple[str, ...],
    limit: int,
    json_output: bool,
    no_color: bool,
) -> None:
    """Observe local durable dynamic-workflow runs without changing them.

    Args:
        context: Active Click context.
        statuses: Effective status filters for the default list.
        limit: Maximum rows for the default list.
        json_output: Whether the default list is JSON.
        no_color: Whether ANSI styling is disabled.

    Raises:
        click.UsageError: If list-only options precede a subcommand.
    """
    context.ensure_object(dict)
    context.obj["no_color"] = no_color
    if context.invoked_subcommand is not None:
        misplaced = [
            option
            for option in ("statuses", "limit", "json_output")
            if context.get_parameter_source(option) is ParameterSource.COMMANDLINE
        ]
        if misplaced:
            rendered = ", ".join(
                "--status"
                if option == "statuses"
                else "--json"
                if option == "json_output"
                else "--limit"
                for option in misplaced
            )
            raise click.UsageError(f"{rendered} must appear after the subcommand.")
    if context.invoked_subcommand is None:
        _render_list(
            statuses=statuses,
            limit=limit,
            json_output=json_output,
            no_color=no_color,
        )


@cli.command(help="Watch a live dashboard until Ctrl-C.")
@_status_option
@_limit_option
@click.option(
    "--refresh",
    type=click.FloatRange(min=0.2, max=60.0),
    default=1.0,
    show_default=True,
    help="Seconds between state-file reads.",
)
@click.pass_context
def watch(
    context: click.Context,
    statuses: tuple[str, ...],
    limit: int,
    refresh: float,
) -> None:
    """Watch a live dashboard until Ctrl-C.

    Args:
        context: Active Click context.
        statuses: Effective statuses to include.
        limit: Maximum rows to show.
        refresh: Seconds between state reads.

    Raises:
        click.ClickException: If stdout is not an interactive terminal.
        click.exceptions.Exit: When the user interrupts the dashboard.
    """
    no_color = bool(context.obj.get("no_color"))
    console = _console(no_color)
    term = os.environ.get("TERM", "").strip().lower()
    if not _stdout_is_tty() or term in {"dumb", "unknown"}:
        raise click.ClickException(
            "watch requires an interactive terminal with live-display support; "
            "use the default command or --json instead."
        )
    try:
        initial = _render_list(
            statuses=statuses,
            limit=limit,
            json_output=False,
            no_color=no_color,
            live=True,
            console=console,
        ) or Text("Waiting for workflow runs…", style="bright_black")
        with Live(
            initial,
            console=console,
            auto_refresh=False,
            screen=True,
            transient=True,
        ) as live_display:
            while True:
                time.sleep(refresh)
                rendered = _render_list(
                    statuses=statuses,
                    limit=limit,
                    json_output=False,
                    no_color=no_color,
                    live=True,
                    console=console,
                )
                live_display.update(
                    rendered
                    or Text("Waiting for workflow runs…", style="bright_black"),
                    refresh=True,
                )
    except KeyboardInterrupt:
        console.print("Stopped watching workflow runs.")
        raise click.exceptions.Exit(130) from None


@cli.command(help="Show detailed state for RUN_ID.")
@click.argument("run_id")
@click.option("--json", "json_output", is_flag=True, help="Emit stable JSON.")
@click.option(
    "--full",
    is_flag=True,
    help="Show all steps and complete human-readable field values.",
)
@click.pass_context
def show(
    context: click.Context,
    run_id: str,
    json_output: bool,
    full: bool,
) -> None:
    """Show detailed state for RUN_ID.

    Args:
        context: Active Click context.
        run_id: Run identifier to load.
        json_output: Whether to emit complete structured JSON.
        full: Whether to disable human-output limits.
    """
    now = _now()
    run = _load_named_run(run_id, now)
    if json_output:
        _emit_json(run.to_json(now, include_steps=True))
        return
    console = _console(bool(context.obj.get("no_color")))
    console.print(build_show_renderable(run, width=console.width, now=now, full=full))


def main() -> None:
    """Run the ``codex-workflows`` console entry point."""
    _configure_output_encoding(sys.stdout)
    _configure_output_encoding(sys.stderr)
    cli(prog_name="codex-workflows")


if __name__ == "__main__":
    main()
