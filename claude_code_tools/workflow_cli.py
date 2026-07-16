"""Polished read-only CLI for durable dynamic-workflow runs."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
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
    CALLBACK_STYLES as CALLBACK_STYLES,
    MAX_SHOW_STEPS as MAX_SHOW_STEPS,
    STATUS_STYLES as STATUS_STYLES,
    build_runs_table,
    build_show_renderable,
    fit_live_runs,
)
from claude_code_tools.workflow_cli_contract import list_payload, show_payload
from claude_code_tools.workflow_runs import (
    ACTIVE_STATUSES,
    FILTER_STATUSES,
    RunLookupResult,
    RunQueryResult,
    RunResolutionKind,
    WorkflowStoreError,
    load_named_run,
    load_runs,
    workflow_home,
)

MAX_DIAGNOSTIC_CHARS = 160
MAX_USAGE_ERROR_CHARS = 360
MAX_OUTPUT_WIDTH = 240
MAX_OUTPUT_HEIGHT = 1_000
MIN_RUN_LIMIT = 1
MAX_RUN_LIMIT = 1_000
MIN_WATCH_REFRESH = 0.2
MAX_WATCH_REFRESH = 60.0
_fit_live_runs = fit_live_runs


@dataclass(frozen=True)
class ListSnapshot:
    """One list query and the clock used by every output adapter."""

    query: RunQueryResult
    observed_at: datetime
    statuses: tuple[str, ...]
    limit: int


def _terminal_dimension(name: str, fallback: int, maximum: int) -> int:
    """Read one bounded positive terminal dimension from the environment."""
    value = os.environ.get(name)
    if value and len(value) <= 6 and value.isascii() and value.isdigit():
        parsed = int(value)
        if parsed > 0:
            return min(maximum, parsed)
    return min(maximum, max(1, fallback))


def _terminal_size() -> tuple[int, int]:
    """Prefer live TTY geometry, using bounded environment fallbacks."""
    try:
        detected = os.get_terminal_size()
        return (
            min(MAX_OUTPUT_WIDTH, max(1, detected.columns)),
            min(MAX_OUTPUT_HEIGHT, max(1, detected.lines)),
        )
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


def _diagnostic_text(
    value: object,
    *,
    maximum: int = MAX_DIAGNOSTIC_CHARS,
) -> str:
    """Bound text and make it representable in stderr's encoding."""
    rendered = _bounded_text(
        value,
        maximum=maximum,
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


def _refresh_console_size(console: Console) -> None:
    """Re-probe bounded terminal dimensions for the next watch frame."""
    console.width, console.height = _terminal_size()


def _stdout_is_tty() -> bool:
    """Return whether stdout supports an interactive live display."""
    return bool(sys.stdout.isatty())


def _emit_json(value: object) -> None:
    """Write stable pretty-printed JSON.

    Args:
        value: JSON-compatible value to emit.
    """
    rendered = json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    )
    output = click.get_binary_stream("stdout")
    output.write(f"{rendered}\n".encode("utf-8"))
    output.flush()


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
    if statuses == ACTIVE_STATUSES and (has_runs or live):
        if live:
            return "Waiting for active workflow runs…"
        return "No active workflow runs. Use --all to show history."
    if has_runs and statuses:
        selected = ", ".join(_sanitize(status) for status in statuses)
        prefix = "Waiting; no workflow runs match" if live else "No workflow runs match"
        return f"{prefix} status filter: {selected}."
    if live:
        return "Waiting for workflow runs…"
    path = _sanitize(workflow_home() / "runs")
    if len(path) > MAX_DIAGNOSTIC_CHARS:
        side = (MAX_DIAGNOSTIC_CHARS - 1) // 2
        path = f"{path[:side]}…{path[-side:]}"
    return f"No workflow runs found in {path}."


def _observation_warning(skipped: int) -> Text:
    """Explain that process-derived filtering could not inspect every run."""
    noun = "observation" if skipped == 1 else "observations"
    return Text(
        f"Incomplete: process-observation budget skipped {skipped} "
        f"{noun}; filtered results may omit matching runs.",
        style="yellow",
        overflow="ellipsis",
        no_wrap=True,
    )


def _limit_warning(limit: int) -> Text:
    """Explain that matching rows were omitted by the requested row limit."""
    return Text(
        f"Showing {limit} matching runs; additional runs omitted (--limit).",
        style="yellow",
        overflow="ellipsis",
        no_wrap=True,
    )


def _query_list(
    *,
    statuses: tuple[str, ...],
    limit: int,
) -> ListSnapshot:
    """Query one immutable list snapshot without producing output."""
    observed_at = _now()
    try:
        query = load_runs(
            statuses=statuses,
            limit=limit,
            now=observed_at,
        )
    except WorkflowStoreError as error:
        raise click.ClickException(_diagnostic_text(error)) from error
    return ListSnapshot(
        query=query,
        observed_at=query.read_completed_at or query.query_at or observed_at,
        statuses=statuses,
        limit=limit,
    )


def _list_renderable(
    snapshot: ListSnapshot,
    *,
    console: Console,
    live: bool,
) -> RenderableType:
    """Adapt one list snapshot to static or live terminal output."""
    query = snapshot.query
    runs = list(query.records)
    warnings: list[RenderableType] = []
    if query.truncated:
        warnings.append(_limit_warning(snapshot.limit))
    if not query.observation_complete:
        warnings.append(_observation_warning(query.observation_skipped))
    footer = Group(*warnings) if warnings else None
    if not runs:
        message = _empty_message(
            has_runs=query.store_has_runs,
            statuses=snapshot.statuses,
            live=live,
        )
        empty = Text(
            message,
            style="bright_black",
            overflow="ellipsis" if live else "fold",
            no_wrap=live,
        )
        if live:
            return fit_live_runs(
                [],
                console=console,
                now=snapshot.observed_at,
                footer=footer,
                empty=empty,
            )
        return Group(empty, *warnings)
    if live:
        return fit_live_runs(
            runs,
            console=console,
            now=snapshot.observed_at,
            footer=footer,
        )
    table = build_runs_table(
        runs,
        width=console.width,
        live=False,
        now=snapshot.observed_at,
    )
    return Group(table, *warnings)


def _emit_list_json(snapshot: ListSnapshot) -> None:
    """Adapt one list snapshot to the stable versioned JSON contract."""
    _emit_json(
        list_payload(
            snapshot.query,
            snapshot.observed_at,
            limit=snapshot.limit,
        )
    )


def _print_list(snapshot: ListSnapshot, *, no_color: bool) -> None:
    """Print one static terminal snapshot."""
    console = _console(no_color)
    console.print(_list_renderable(snapshot, console=console, live=False))


def _load_named_run(run_id: str, now: datetime) -> RunLookupResult:
    """Load one validated run ID or raise a user-facing error.

    Args:
        run_id: Directory-safe run identifier.
        now: Shared observation time for classification and rendering.

    Returns:
        The capability-bound lookup and loaded record.

    Raises:
        click.ClickException: If the ID is invalid or the run is absent.
    """
    rendered_id = _diagnostic_text(run_id)
    try:
        lookup = load_named_run(run_id, now=now)
    except WorkflowStoreError as error:
        raise click.ClickException(_diagnostic_text(error)) from error
    resolution = lookup.resolution
    if resolution.kind is RunResolutionKind.INVALID:
        raise click.ClickException(f"Invalid workflow run ID: {rendered_id}")
    if resolution.kind is RunResolutionKind.NOT_FOUND:
        raise click.ClickException(f"Workflow run not found: {rendered_id}")
    if resolution.kind is RunResolutionKind.AMBIGUOUS:
        choices = ", ".join(
            _sanitize(candidate) for candidate in resolution.candidates[:5]
        )
        omitted = len(resolution.candidates) - 5
        suffix = f"; {omitted} more omitted" if omitted > 0 else ""
        raise click.ClickException(
            "Workflow run ID abbreviation is ambiguous: "
            f"{rendered_id}. Use a full run ID: {choices}{suffix}"
        )
    if resolution.directory is None or lookup.record is None:
        raise click.ClickException(f"Workflow run not found: {rendered_id}")
    return lookup


def _unique_statuses(
    _context: click.Context,
    _parameter: click.Parameter,
    statuses: tuple[str, ...],
) -> tuple[str, ...]:
    """Retain each validated status once, in command-line order."""
    unique = tuple(dict.fromkeys(statuses))
    return unique[: len(FILTER_STATUSES)]


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
        type=_BoundedChoice(FILTER_STATUSES, case_sensitive=False),
        multiple=True,
        callback=_unique_statuses,
        metavar="STATUS",
        help=(
            "Override the active-only default with this effective status; "
            "repeat for more. "
            f"Accepted values: {accepted}."
        ),
    )(function)


def _all_option(function: Callable[..., object]) -> Callable[..., object]:
    """Decorate a list command with the explicit history switch."""
    return click.option(
        "--all",
        "show_all",
        is_flag=True,
        help=(
            "Include terminal and diagnostic workflow history; cannot be "
            "combined with --status."
        ),
    )(function)


def _effective_statuses(
    statuses: tuple[str, ...],
    *,
    show_all: bool,
) -> tuple[str, ...]:
    """Resolve explicit filters against the active-only default."""
    if show_all and statuses:
        raise click.UsageError("--all cannot be combined with --status.")
    if show_all:
        return ()
    return statuses or ACTIVE_STATUSES


def _limit_option(function: Callable[..., object]) -> Callable[..., object]:
    """Decorate a command with the bounded row limit.

    Args:
        function: Click callback to decorate.

    Returns:
        The decorated callback.
    """
    return click.option(
        "--limit",
        type=_BoundedIntRange(MIN_RUN_LIMIT, MAX_RUN_LIMIT),
        default=50,
        show_default=True,
        help="Maximum number of runs to display.",
    )(function)


class _BoundedChoice(click.Choice):
    """A Click choice whose invalid-value diagnostic has a fixed size."""

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> object:
        """Convert a choice while bounding hostile values in failures."""
        try:
            return super().convert(value, param, ctx)
        except click.BadParameter:
            rendered = _diagnostic_text(value)
            choices = ", ".join(str(choice) for choice in self.choices)
            self.fail(
                f"{rendered!r} is not one of: {choices}.",
                param=param,
                ctx=ctx,
            )


class _BoundedIntRange(click.IntRange):
    """A Click integer range with bounded invalid-value diagnostics."""

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> int:
        """Convert a bounded integer while safely reporting failures."""
        try:
            converted = super().convert(value, param, ctx)
        except click.BadParameter:
            rendered = _diagnostic_text(value)
            self.fail(
                f"{rendered!r} is not an integer from {MIN_RUN_LIMIT} "
                f"through {MAX_RUN_LIMIT}.",
                param=param,
                ctx=ctx,
            )
        return int(converted)


class _BoundedFloatRange(click.FloatRange):
    """A Click float range with bounded invalid-value diagnostics."""

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> float:
        """Convert a bounded float while safely reporting failures."""
        try:
            converted = super().convert(value, param, ctx)
        except click.BadParameter:
            rendered = _diagnostic_text(value)
            self.fail(
                f"{rendered!r} is not a number from {MIN_WATCH_REFRESH:g} "
                f"through {MAX_WATCH_REFRESH:g}.",
                param=param,
                ctx=ctx,
            )
        return float(converted)


def _bounded_usage_error(error: click.UsageError) -> click.UsageError:
    """Replace a parser error with a bounded, context-preserving error."""
    return click.UsageError(
        _diagnostic_text(
            error.format_message(),
            maximum=MAX_USAGE_ERROR_CHARS,
        ),
        ctx=error.ctx,
    )


class _BoundedCommand(click.Command):
    """A Click command whose parser diagnostics have a fixed size."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Parse arguments while bounding every usage-error diagnostic."""
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as error:
            raise _bounded_usage_error(error) from None


class _BoundedGroup(click.Group):
    """A Click group with bounded root and command-resolution errors."""

    command_class = _BoundedCommand

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Parse group arguments while bounding usage-error diagnostics."""
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as error:
            raise _bounded_usage_error(error) from None

    def resolve_command(
        self,
        ctx: click.Context,
        args: list[str],
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """Resolve a subcommand while bounding unknown-command input."""
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as error:
            raise _bounded_usage_error(error) from None


def _finite_refresh(
    _context: click.Context,
    parameter: click.Parameter,
    value: float,
) -> float:
    """Reject non-finite refresh values Click's range comparison admits."""
    if not math.isfinite(value):
        raise click.BadParameter("must be finite", param=parameter)
    return value


@click.group(
    cls=_BoundedGroup,
    invoke_without_command=True,
    help=(
        "Observe durable dynamic-workflow runs in the global cross-project "
        "store without changing them. With no subcommand, list active runs, "
        "including their launch projects and unverifiable nonterminal runs."
    ),
)
@_status_option
@_all_option
@_limit_option
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit versioned list JSON, including each launch directory.",
)
@click.option(
    "--no-color",
    is_flag=True,
    help="Disable ANSI color (also honored through NO_COLOR).",
)
@click.pass_context
def cli(
    context: click.Context,
    statuses: tuple[str, ...],
    show_all: bool,
    limit: int,
    json_output: bool,
    no_color: bool,
) -> None:
    """Observe local durable dynamic-workflow runs without changing them.

    Args:
        context: Active Click context.
        statuses: Effective status filters for the default list.
        show_all: Whether terminal and diagnostic history is included.
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
            for option in ("statuses", "show_all", "limit", "json_output")
            if context.get_parameter_source(option) is ParameterSource.COMMANDLINE
        ]
        if misplaced:
            if "json_output" in misplaced and context.invoked_subcommand == "watch":
                raise click.UsageError(
                    "--json is not supported by watch; use "
                    "codex-workflows --json (without watch)."
                )
            show_only_list_options = [
                option
                for option in misplaced
                if option in {"statuses", "show_all", "limit"}
            ]
            if show_only_list_options and context.invoked_subcommand == "show":
                rendered = ", ".join(
                    "--status"
                    if option == "statuses"
                    else "--all"
                    if option == "show_all"
                    else "--limit"
                    for option in show_only_list_options
                )
                raise click.UsageError(
                    f"{rendered} may be used only with list or watch."
                )
            rendered = ", ".join(
                "--status"
                if option == "statuses"
                else "--all"
                if option == "show_all"
                else "--json"
                if option == "json_output"
                else "--limit"
                for option in misplaced
            )
            raise click.UsageError(f"{rendered} must appear after the subcommand.")
    if context.invoked_subcommand is None:
        selected_statuses = _effective_statuses(statuses, show_all=show_all)
        snapshot = _query_list(
            statuses=selected_statuses,
            limit=limit,
        )
        if json_output:
            _emit_list_json(snapshot)
        else:
            _print_list(snapshot, no_color=no_color)


@cli.command(
    help=(
        "Watch active runs from the global cross-project store until Ctrl-C. "
        "JSON is not supported; put --no-color before watch when needed."
    )
)
@_status_option
@_all_option
@_limit_option
@click.option(
    "--refresh",
    type=_BoundedFloatRange(
        min=MIN_WATCH_REFRESH,
        max=MAX_WATCH_REFRESH,
    ),
    callback=_finite_refresh,
    default=1.0,
    show_default=True,
    help="Seconds between state-file reads.",
)
@click.pass_context
def watch(
    context: click.Context,
    statuses: tuple[str, ...],
    show_all: bool,
    limit: int,
    refresh: float,
) -> None:
    """Watch a live dashboard until Ctrl-C.

    Args:
        context: Active Click context.
        statuses: Effective statuses to include.
        show_all: Whether terminal and diagnostic history is included.
        limit: Maximum rows to show.
        refresh: Seconds between state reads.

    Raises:
        click.ClickException: If stdout is not an interactive terminal.
        click.exceptions.Exit: When the user interrupts the dashboard.
    """
    no_color = bool(context.obj.get("no_color"))
    selected_statuses = _effective_statuses(statuses, show_all=show_all)
    console = _console(no_color)
    term = os.environ.get("TERM", "").strip().lower()
    if not _stdout_is_tty() or console.is_dumb_terminal or term in {"dumb", "unknown"}:
        raise click.ClickException(
            "watch requires an interactive terminal with live-display support; "
            "use codex-workflows --json (without watch) instead."
        )
    try:
        _refresh_console_size(console)
        initial_snapshot = _query_list(
            statuses=selected_statuses,
            limit=limit,
        )
        initial = _list_renderable(
            initial_snapshot,
            console=console,
            live=True,
        )
        with Live(
            initial,
            console=console,
            auto_refresh=False,
            screen=True,
            transient=True,
        ) as live_display:
            while True:
                time.sleep(refresh)
                _refresh_console_size(console)
                snapshot = _query_list(
                    statuses=selected_statuses,
                    limit=limit,
                )
                rendered = _list_renderable(
                    snapshot,
                    console=console,
                    live=True,
                )
                live_display.update(
                    rendered,
                    refresh=True,
                )
    except KeyboardInterrupt:
        console.print("Stopped watching workflow runs.")
        raise click.exceptions.Exit(130) from None


@cli.command(
    help=(
        "Show detailed state and the full launch directory for RUN_ID from "
        "the global store. Put --no-color before show when needed."
    )
)
@click.argument("run_id")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit complete versioned JSON, including the launch directory.",
)
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
    lookup = _load_named_run(run_id, _now())
    run = lookup.record
    if run is None:
        raise click.ClickException(
            f"Workflow run not found: {_diagnostic_text(run_id)}"
        )
    now = lookup.read_completed_at
    if json_output:
        _emit_json(show_payload(run, now))
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
