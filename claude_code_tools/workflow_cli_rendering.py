"""Responsive Rich renderables for the durable workflow CLI."""

from __future__ import annotations

import re
from datetime import datetime

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from claude_code_tools.workflow_cli_formatting import (
    bounded_error,
    bounded_text,
    format_age,
    format_duration,
    format_time,
    sanitize,
)
from claude_code_tools.workflow_runs import RunRecord, StepRecord

STATUS_STYLES = {
    "canceled": "bright_black",
    "canceling": "yellow",
    "completed": "green",
    "failed": "red",
    "malformed": "red",
    "orphaned": "magenta",
    "paused": "yellow",
    "pausing": "yellow",
    "running": "cyan",
    "stale": "magenta",
    "starting": "cyan",
    "unknown": "bright_black",
    "unverifiable": "yellow",
}
CALLBACK_STYLES = {
    "armed": "cyan",
    "delivered": "green",
    "failed": "red",
    "invalid": "red",
    "none": "bright_black",
    "orphaned": "magenta",
    "sending": "cyan",
    "stale": "magenta",
    "unknown": "yellow",
    "unverifiable": "yellow",
}
MAX_LIST_NAME_CHARS = 80
MAX_DETAIL_CHARS = 500
MAX_STEP_DETAIL_CHARS = 200
MAX_SHOW_STEPS = 50
NARROW_DETAIL_WIDTH = 64
ULTRA_NARROW_WIDTH = 4
PLAIN_SHOW_MAX_WIDTH = 7
ABBREVIATED_RUN_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9._-]{8}~[A-Za-z0-9._-]{8}$"
)


def _status_renderable(run: RunRecord, live: bool) -> RenderableType:
    """Build a safe styled status value for one run."""
    style = STATUS_STYLES.get(run.status, "bright_black")
    text = Text(sanitize(run.status), style=style)
    if live and run.status in {"running", "starting"}:
        return Spinner("dots", text=text, style="cyan")
    return text


def _agents_text(run: RunRecord, *, include_workers: bool) -> str:
    """Return compact agent progress for a run."""
    progress = run.progress
    finished = progress["completed"] + progress["failed"] + progress["canceled"]
    value = f"{finished}/{progress['total']}"
    if include_workers:
        worker_label = "worker" if run.active_workers == 1 else "workers"
        value += f" · {run.active_workers} {worker_label}"
    return value


def _compact_run_details(
    run: RunRecord,
    *,
    activity_width: int,
    live: bool,
    now: datetime,
) -> Group:
    """Build compact state, timing, callback, and activity lines."""
    status_line = Table.grid(padding=(0, 1))
    status_line.add_column(no_wrap=True)
    status_line.add_column(overflow="ellipsis")
    status_line.add_row(
        _status_renderable(run, live),
        _agents_text(run, include_workers=True),
    )
    callback = Text()
    callback.append(
        sanitize(run.callback_status),
        style=CALLBACK_STYLES.get(run.callback_status, "bright_black"),
    )
    activity = bounded_text(
        run.activity(),
        maximum=activity_width,
        full=False,
    )[0]
    callback.append(f" · {activity}")
    return Group(
        status_line,
        Text(
            f"{format_duration(run.duration_seconds(now))} · "
            f"{format_age(run.updated_at, now)}"
        ),
        callback,
    )


def build_runs_table(
    runs: list[RunRecord],
    *,
    width: int,
    live: bool,
    now: datetime,
) -> RenderableType:
    """Build a workflow table appropriate for the available width."""
    abbreviation_counts: dict[str, int] = {}
    for run in runs:
        abbreviation_counts[run.abbreviated_id] = (
            abbreviation_counts.get(run.abbreviated_id, 0) + 1
        )
    display_ids = [
        run.run_id
        if (
            run.abbreviation_ambiguous
            or abbreviation_counts[run.abbreviated_id] > 1
            or not ABBREVIATED_RUN_ID_PATTERN.fullmatch(run.abbreviated_id)
        )
        else run.abbreviated_id
        for run in runs
    ]
    has_collisions = any(
        display_id != run.abbreviated_id
        for run, display_id in zip(runs, display_ids, strict=True)
    )
    if width <= ULTRA_NARROW_WIDTH:
        records: list[RenderableType] = []
        for run, display_id in zip(runs, display_ids, strict=True):
            name = bounded_text(
                run.workflow_name,
                maximum=MAX_LIST_NAME_CHARS,
                full=False,
            )[0]
            records.extend(
                [
                    Text(name, style="bold"),
                    Text(sanitize(display_id), style="bright_black"),
                    _compact_run_details(
                        run,
                        activity_width=max(1, width),
                        live=live,
                        now=now,
                    ),
                ]
            )
        return Group(*records)
    table = Table(
        box=box.SIMPLE_HEAD,
        collapse_padding=True,
        expand=True,
        pad_edge=False,
        show_edge=False,
    )
    if width < 40 or has_collisions:
        table.add_column("Workflow runs", overflow="fold")
        for run, display_id in zip(runs, display_ids, strict=True):
            identity = Text()
            name = bounded_text(
                run.workflow_name,
                maximum=MAX_LIST_NAME_CHARS,
                full=False,
            )[0]
            identity.append(name, style="bold")
            identity.append(
                f" · {sanitize(display_id)}",
                style="bright_black",
            )
            table.add_row(
                Group(
                    identity,
                    _compact_run_details(
                        run,
                        activity_width=max(12, width - 7),
                        live=live,
                        now=now,
                    ),
                )
            )
        return table
    if width < 118:
        table.add_column(
            "Workflow / run",
            width=17,
            overflow="ellipsis",
        )
        table.add_column("State / activity", min_width=20, ratio=3)
        for run, display_id in zip(runs, display_ids, strict=True):
            identity = Text()
            name = bounded_text(
                run.workflow_name,
                maximum=MAX_LIST_NAME_CHARS,
                full=False,
            )[0]
            identity.append(name, style="bold")
            identity.append(
                f"\n{sanitize(display_id)}",
                style="bright_black",
            )
            table.add_row(
                identity,
                _compact_run_details(
                    run,
                    activity_width=max(12, width * 3 // 5 - 7),
                    live=live,
                    now=now,
                ),
            )
        return table

    table.add_column("Workflow", ratio=2, max_width=26, overflow="ellipsis")
    table.add_column("Run", width=17, no_wrap=True)
    table.add_column("Status", width=12, no_wrap=True)
    table.add_column("Agents", width=8, no_wrap=True)
    table.add_column("Active", width=6, justify="right", no_wrap=True)
    table.add_column("Time", width=9, no_wrap=True)
    table.add_column("Updated", width=12, no_wrap=True)
    table.add_column("Callback", width=10, no_wrap=True)
    table.add_column("Error / activity", ratio=3, overflow="ellipsis")
    for run, display_id in zip(runs, display_ids, strict=True):
        row: list[RenderableType] = [
            Text(
                bounded_text(
                    run.workflow_name,
                    maximum=MAX_LIST_NAME_CHARS,
                    full=False,
                )[0],
                style="bold",
            ),
            Text(sanitize(display_id)),
            _status_renderable(run, live),
            Text(_agents_text(run, include_workers=False)),
            Text(str(run.active_workers)),
            Text(format_duration(run.duration_seconds(now))),
            Text(format_age(run.updated_at, now)),
            Text(
                sanitize(run.callback_status),
                style=CALLBACK_STYLES.get(
                    run.callback_status,
                    "bright_black",
                ),
            ),
            Text(
                bounded_text(
                    run.activity(),
                    maximum=200,
                    full=False,
                )[0],
            ),
        ]
        table.add_row(*row)
    return table


def _detail_grid(values: tuple[tuple[str, object], ...], width: int) -> Table:
    """Build a stacked narrow grid or a two-column wide grid."""
    grid = Table.grid(expand=True, padding=(0, 2))
    if width < NARROW_DETAIL_WIDTH:
        grid.add_column(overflow="fold")
        for label, value in values:
            grid.add_row(Text(label, style="bold"))
            grid.add_row(Text(sanitize(value), overflow="fold"))
        return grid
    grid.add_column(style="bold", no_wrap=True)
    grid.add_column(overflow="fold", ratio=1)
    for label, value in values:
        grid.add_row(label, Text(sanitize(value), overflow="fold"))
    return grid


def _summary_grid(run: RunRecord, now: datetime, width: int, full: bool) -> Table:
    """Build the responsive run-summary grid."""
    progress = run.progress
    finished = progress["completed"] + progress["failed"] + progress["canceled"]
    activity, _ = bounded_text(
        run.activity(),
        maximum=MAX_DETAIL_CHARS,
        full=full,
    )
    values = (
        ("Run ID", run.run_id),
        ("Workflow", run.workflow_name),
        ("Workflow path", run.workflow_path or "—"),
        ("Status", run.status),
        ("Recorded status", run.recorded_status),
        ("Agent progress", f"{finished}/{progress['total']}"),
        ("Active workers", str(run.active_workers)),
        ("Created", format_time(run.created_at)),
        ("Started", format_time(run.started_at)),
        ("Completed", format_time(run.completed_at)),
        ("Last state update", format_time(run.updated_at)),
        ("Elapsed / total", format_duration(run.duration_seconds(now))),
        ("Callback", run.callback_status),
        ("Activity", activity),
    )
    bounded = tuple(
        (
            label,
            bounded_text(value, maximum=MAX_DETAIL_CHARS, full=full)[0],
        )
        for label, value in values
    )
    return _detail_grid(bounded, width)


def _callback_grid(run: RunRecord, width: int, full: bool) -> Table:
    """Build the responsive callback detail grid."""
    callback = run.callback_json()
    if callback is None:
        return _detail_grid((("Status", "none"),), width)
    labels = {
        "status": "Status",
        "attempts": "Attempts",
        "createdAt": "Created",
        "updatedAt": "Updated",
        "lastAttemptAt": "Last attempt",
        "deliveredAt": "Delivered",
        "deadlineAt": "Deadline",
        "terminalStatus": "Terminal status",
        "terminalCompletedAt": "Terminal completed",
        "clientUserMessageId": "Client message ID",
        "endpoint": "Endpoint",
        "threadId": "Thread",
        "timeoutMs": "Timeout (ms)",
        "turnId": "Turn",
        "notifierPid": "Notifier PID",
        "notifierStartedAt": "Notifier started",
        "notifierProcessStatus": "Notifier process",
        "error": "Error",
    }
    values: list[tuple[str, object]] = []
    for key, label in labels.items():
        value = callback[key]
        if key.endswith("At") and isinstance(value, str):
            rendered = format_time(value)
        else:
            rendered = "—" if value is None else str(value)
        bounded, _ = bounded_text(
            rendered,
            maximum=MAX_DETAIL_CHARS,
            full=full,
        )
        values.append((label, bounded))
    return _detail_grid(tuple(values), width)


def _bounded_number(value: int | None, *, full: bool) -> str:
    """Render optional numeric metadata with the standard detail bound."""
    rendered = "—" if value is None else str(value)
    return bounded_text(
        rendered,
        maximum=MAX_STEP_DETAIL_CHARS,
        full=full,
    )[0]


def _vertical_steps(
    steps: tuple[StepRecord, ...],
    now: datetime,
    *,
    full: bool,
) -> Group:
    """Build vertical step records for a narrow terminal."""
    records: list[RenderableType] = []
    for index, step in enumerate(steps, start=1):
        label, _ = bounded_text(
            step.label,
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        step_id, _ = bounded_text(
            step.id,
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        thread_id, _ = bounded_text(
            step.thread_id or "—",
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        detail, _ = bounded_text(
            step.error or "—",
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        values = (
            ("ID", step_id),
            ("Status", step.status),
            ("Attempt", _bounded_number(step.attempt, full=full)),
            ("Time", format_duration(step.duration_seconds(now))),
            ("Worker", _bounded_number(step.worker_pid, full=full)),
            ("Thread", thread_id),
            ("Error", detail),
        )
        records.append(
            Group(
                Text(f"{index}. {label}", style="bold"),
                _detail_grid(values, 0),
            )
        )
    return Group(*records)


def _step_table(
    steps: tuple[StepRecord, ...],
    now: datetime,
    width: int,
    *,
    full: bool,
) -> RenderableType:
    """Build responsive, safe step details."""
    has_long_number = any(
        len(str(value)) > MAX_STEP_DETAIL_CHARS
        for step in steps
        for value in (step.attempt, step.worker_pid)
        if value is not None
    )
    if width < NARROW_DETAIL_WIDTH or (full and has_long_number):
        return _vertical_steps(steps, now, full=full)
    table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
    table.add_column("Step / ID", ratio=2, overflow="fold")
    table.add_column("Status", width=10, no_wrap=True)
    table.add_column("Try", width=3, justify="right", no_wrap=True)
    table.add_column("Time", width=8, no_wrap=True)
    if width >= 96:
        table.add_column("Worker", width=8, no_wrap=True)
        table.add_column("Thread / error", ratio=3, overflow="fold")
    else:
        table.add_column("Worker / thread / error", ratio=3, overflow="fold")
    for step in steps:
        label, _ = bounded_text(
            step.label,
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        step_id, _ = bounded_text(
            step.id,
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        thread_id, _ = bounded_text(
            step.thread_id or "",
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        error, _ = bounded_text(
            step.error or "",
            maximum=MAX_STEP_DETAIL_CHARS,
            full=full,
        )
        worker = _bounded_number(step.worker_pid, full=full)
        detail_parts: list[str] = []
        if step.worker_pid is not None:
            detail_parts.append(f"PID {worker}")
        if step.thread_id:
            detail_parts.append(thread_id)
        if step.error:
            detail_parts.append(error)
        detail = " · ".join(detail_parts) or "—"
        identity = Group(
            Text(label),
            Text(step_id, style="bright_black"),
        )
        row: list[RenderableType] = [
            identity,
            Text(
                sanitize(step.status),
                style=STATUS_STYLES.get(step.status, "bright_black"),
            ),
            Text(_bounded_number(step.attempt, full=full)),
            Text(format_duration(step.duration_seconds(now))),
        ]
        if width >= 96:
            row.extend(
                [
                    Text(worker),
                    Text(
                        " · ".join(
                            bounded_text(
                                part,
                                maximum=MAX_STEP_DETAIL_CHARS,
                                full=full,
                            )[0]
                            for part in [thread_id, error]
                            if part
                        )
                        or "—"
                    ),
                ]
            )
        else:
            row.append(Text(detail))
        table.add_row(*row)
    return table


def _default_step_limit(width: int) -> int:
    """Return a stable default row budget for the available terminal width."""
    return max(1, min(MAX_SHOW_STEPS, width // 8))


def build_show_renderable(
    run: RunRecord,
    *,
    width: int,
    now: datetime,
    full: bool = False,
) -> Group:
    """Build detailed run, callback, error, and step state."""
    step_limit = MAX_SHOW_STEPS if full else _default_step_limit(width)
    shown_steps = run.steps if full else run.steps[:step_limit]
    summary = _summary_grid(run, now, width, full)
    callback = _callback_grid(run, width, full)
    if width <= PLAIN_SHOW_MAX_WIDTH:
        parts: list[RenderableType] = [
            Text("Run", style="cyan"),
            summary,
            Text("Callback", style="blue"),
            callback,
        ]
    else:
        parts = [
            Panel(
                summary,
                title="Workflow run",
                border_style="cyan",
            ),
            Panel(
                callback,
                title="Completion callback",
                border_style="blue",
            ),
        ]
    if run.error:
        error, _ = bounded_error(run.error, full=full)
        if width <= PLAIN_SHOW_MAX_WIDTH:
            parts.extend([Text("Error", style="red"), Text(error)])
        else:
            parts.append(Panel(Text(error), title="Error", border_style="red"))
    if shown_steps:
        step_table = _step_table(shown_steps, now, width, full=full)
        if width <= PLAIN_SHOW_MAX_WIDTH:
            parts.extend([Text("Steps", style="bright_black"), step_table])
        else:
            parts.append(
                Panel(
                    step_table,
                    title=f"Agent steps ({len(shown_steps)}/{len(run.steps)})",
                    border_style="bright_black",
                )
            )
        if len(shown_steps) < len(run.steps):
            parts.append(
                Text(
                    f"Showing the first {len(shown_steps)} of "
                    f"{len(run.steps)} steps; use --full or --json for all steps.",
                    style="yellow",
                )
            )
    else:
        if width <= PLAIN_SHOW_MAX_WIDTH:
            parts.extend([Text("Steps"), Text("No agent steps recorded.")])
        else:
            parts.append(Panel("No agent steps recorded.", title="Agent steps"))
    return Group(*parts)
