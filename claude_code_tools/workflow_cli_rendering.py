"""Responsive Rich renderables for the durable workflow CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from claude_code_tools.workflow_cli_identity_policy import (
    colliding_abbreviations,
    display_run_id,
)
from claude_code_tools.workflow_cli_formatting import (
    bounded_error,
    bounded_text,
    format_age,
    format_duration,
    format_time,
    sanitize,
)
from claude_code_tools.workflow_cli_snapshots import (
    CallbackRecord,
    RunRecord,
    StepRecord,
)

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
WIDE_LIST_WIDTH = 140


@dataclass(frozen=True)
class RunRowView:
    """One bounded, terminal-safe run row shared by every list layout."""

    run_id: str
    abbreviated_id: str
    abbreviation_ambiguous: bool
    workflow_name: str
    project_name: str
    status: str
    agents: str
    agents_with_workers: str
    active_workers: str
    duration: str
    updated: str
    callback_status: str
    activity: str

    @classmethod
    def from_record(cls, run: RunRecord, now: datetime) -> RunRowView:
        """Normalize and bound persisted fields once for list rendering."""
        progress = run.progress
        finished = progress["completed"] + progress["failed"] + progress["canceled"]
        agents = f"{finished}/{progress['total']}"
        active_workers = run.active_workers
        worker_label = "worker" if active_workers == 1 else "workers"
        return cls(
            run_id=sanitize(run.run_id),
            abbreviated_id=sanitize(run.abbreviated_id),
            abbreviation_ambiguous=run.abbreviation_ambiguous,
            workflow_name=bounded_text(
                run.workflow_name,
                maximum=MAX_LIST_NAME_CHARS,
                full=False,
            )[0],
            project_name=bounded_text(
                run.project_name,
                maximum=MAX_LIST_NAME_CHARS,
                full=False,
            )[0],
            status=sanitize(run.status),
            agents=agents,
            agents_with_workers=f"{agents} · {active_workers} {worker_label}",
            active_workers=str(active_workers),
            duration=format_duration(run.duration_seconds(now)),
            updated=format_age(run.updated_at, now),
            callback_status=sanitize(run.callback_status),
            activity=bounded_text(
                run.activity(),
                maximum=200,
                full=False,
            )[0],
        )


def _status_renderable(view: RunRowView, live: bool) -> RenderableType:
    """Build a safe styled status value for one run."""
    style = STATUS_STYLES.get(view.status, "bright_black")
    text = Text(view.status, style=style)
    if live and view.status in {"running", "starting"}:
        return Spinner("dots", text=text, style="cyan")
    return text


def _compact_run_details(
    view: RunRowView,
    *,
    activity_width: int,
    live: bool,
) -> Group:
    """Build compact state, timing, callback, and activity lines."""
    status_line = Table.grid(padding=(0, 1))
    status_line.add_column(no_wrap=True)
    status_line.add_column(overflow="ellipsis")
    status_line.add_row(
        _status_renderable(view, live),
        view.agents_with_workers,
    )
    callback = Text()
    callback.append(
        view.callback_status,
        style=CALLBACK_STYLES.get(view.callback_status, "bright_black"),
    )
    activity = bounded_text(
        view.activity,
        maximum=activity_width,
        full=False,
    )[0]
    callback.append(f" · {activity}")
    return Group(
        status_line,
        Text(f"{view.duration} · {view.updated}"),
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
    views = [RunRowView.from_record(run, now) for run in runs]
    return _build_runs_table_from_views(views, width=width, live=live)


def _build_runs_table_from_views(
    views: list[RunRowView],
    *,
    width: int,
    live: bool,
) -> RenderableType:
    """Arrange already-normalized rows for the available width."""
    collisions = colliding_abbreviations(
        (view.run_id, view.abbreviated_id) for view in views
    )
    display_ids = [
        display_run_id(
            view.run_id,
            view.abbreviated_id,
            ambiguous=(
                view.abbreviation_ambiguous or view.abbreviated_id in collisions
            ),
        )
        for view in views
    ]
    has_collisions = any(
        display_id != view.abbreviated_id
        for view, display_id in zip(views, display_ids, strict=True)
    )
    if width <= ULTRA_NARROW_WIDTH:
        records: list[RenderableType] = []
        for view, display_id in zip(views, display_ids, strict=True):
            records.extend(
                [
                    Text(view.workflow_name, style="bold"),
                    Text(view.project_name, style="cyan"),
                    Text(display_id, style="bright_black"),
                    _compact_run_details(
                        view,
                        activity_width=max(1, width),
                        live=live,
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
        for view, display_id in zip(views, display_ids, strict=True):
            identity = Text()
            identity.append(view.workflow_name, style="bold")
            identity.append(
                f"\n{view.project_name}",
                style="cyan",
            )
            identity.append(
                f"\n{display_id}",
                style="bright_black",
            )
            table.add_row(
                Group(
                    identity,
                    _compact_run_details(
                        view,
                        activity_width=max(12, width - 7),
                        live=live,
                    ),
                )
            )
        return table
    if width < WIDE_LIST_WIDTH:
        table.add_column(
            "Workflow / project / run",
            width=30,
            overflow="ellipsis",
        )
        table.add_column("State / activity", min_width=20, ratio=3)
        for view, display_id in zip(views, display_ids, strict=True):
            identity = Text()
            identity.append(view.workflow_name, style="bold")
            identity.append(
                f"\n{view.project_name}",
                style="cyan",
            )
            identity.append(
                f"\n{display_id}",
                style="bright_black",
            )
            table.add_row(
                identity,
                _compact_run_details(
                    view,
                    activity_width=max(12, width * 3 // 5 - 7),
                    live=live,
                ),
            )
        return table

    table.add_column(
        "Workflow / Project",
        ratio=4,
        max_width=48,
        overflow="ellipsis",
    )
    table.add_column("Run", width=17, no_wrap=True)
    table.add_column("Status", width=12, no_wrap=True)
    table.add_column("Agents", width=8, no_wrap=True)
    table.add_column("Active", width=6, justify="right", no_wrap=True)
    table.add_column("Time", width=9, no_wrap=True)
    table.add_column("Updated", width=12, no_wrap=True)
    table.add_column("Callback", width=10, no_wrap=True)
    table.add_column("Error / activity", ratio=3, overflow="ellipsis")
    for view, display_id in zip(views, display_ids, strict=True):
        identity = Text(view.workflow_name, style="bold")
        identity.append(f"\n{view.project_name}", style="cyan")
        row: list[RenderableType] = [
            identity,
            Text(display_id),
            _status_renderable(view, live),
            Text(view.agents),
            Text(view.active_workers),
            Text(view.duration),
            Text(view.updated),
            Text(
                view.callback_status,
                style=CALLBACK_STYLES.get(
                    view.callback_status,
                    "bright_black",
                ),
            ),
            Text(view.activity),
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
        ("Project", run.project_name),
        ("Working directory", run.cwd or "—"),
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


CALLBACK_DETAIL_FIELDS = (
    ("status", "Status"),
    ("attempts", "Attempts"),
    ("createdAt", "Created"),
    ("updatedAt", "Updated"),
    ("lastAttemptAt", "Last attempt"),
    ("deliveredAt", "Delivered"),
    ("deadlineAt", "Deadline"),
    ("terminalStatus", "Terminal status"),
    ("terminalCompletedAt", "Terminal completed"),
    ("clientUserMessageId", "Client message ID"),
    ("endpoint", "Endpoint"),
    ("threadId", "Thread"),
    ("timeoutMs", "Timeout (ms)"),
    ("turnId", "Turn"),
    ("notifierPid", "Notifier PID"),
    ("notifierStartedAt", "Notifier started"),
    ("notifierProcessStatus", "Notifier process"),
    ("error", "Error"),
)
CALLBACK_ATTRIBUTES = {
    "attempts": "attempts",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "lastAttemptAt": "last_attempt_at",
    "deliveredAt": "delivered_at",
    "deadlineAt": "deadline_at",
    "terminalStatus": "terminal_status",
    "terminalCompletedAt": "terminal_completed_at",
    "clientUserMessageId": "client_user_message_id",
    "endpoint": "endpoint",
    "threadId": "thread_id",
    "timeoutMs": "timeout_ms",
    "turnId": "turn_id",
    "notifierPid": "notifier_pid",
    "notifierStartedAt": "notifier_started_at",
    "error": "error",
}


@dataclass(frozen=True)
class CallbackDetailView:
    """Bounded callback fields for terminal rendering."""

    values: tuple[tuple[str, str], ...]

    @classmethod
    def from_record(cls, run: RunRecord, *, full: bool) -> CallbackDetailView:
        """Build callback details from typed domain data, independent of JSON."""
        callback = run.callback
        if callback is None and run.callback_error is None:
            return cls((("Status", "none"),))
        values = tuple(
            (
                label,
                bounded_text(
                    _callback_field(run, callback, key),
                    maximum=MAX_DETAIL_CHARS,
                    full=full,
                )[0],
            )
            for key, label in CALLBACK_DETAIL_FIELDS
        )
        return cls(values)


def _callback_field(
    run: RunRecord,
    callback: CallbackRecord | None,
    key: str,
) -> str:
    """Render one typed callback field before applying display bounds."""
    if key == "status":
        return "invalid" if run.callback_error else run.callback_status
    if run.callback_error:
        return run.callback_error if key == "error" else "—"
    if key == "notifierProcessStatus":
        value: object = run.callback_process_state
    elif callback is None:
        value = None
    else:
        value = getattr(callback, CALLBACK_ATTRIBUTES[key])
    if key.endswith("At") and isinstance(value, str):
        return format_time(value)
    return "—" if value is None else str(value)


def _callback_grid(run: RunRecord, width: int, full: bool) -> Table:
    """Build the responsive callback detail grid."""
    return _detail_grid(CallbackDetailView.from_record(run, full=full).values, width)


@dataclass(frozen=True)
class StepDetailView:
    """One normalized, bounded agent-step detail row."""

    label: str
    step_id: str
    status: str
    attempt: str
    duration: str
    worker: str
    has_worker: bool
    thread_id: str
    error: str

    @classmethod
    def from_record(
        cls,
        step: StepRecord,
        now: datetime,
        *,
        full: bool,
    ) -> StepDetailView:
        """Normalize a step once before selecting a responsive layout."""

        def text(value: object) -> str:
            return bounded_text(
                value,
                maximum=MAX_STEP_DETAIL_CHARS,
                full=full,
            )[0]

        return cls(
            label=text(step.label),
            step_id=text(step.id),
            status=sanitize(step.status),
            attempt=text("—" if step.attempt is None else step.attempt),
            duration=format_duration(step.duration_seconds(now)),
            worker=text("—" if step.worker_pid is None else step.worker_pid),
            has_worker=step.worker_pid is not None,
            thread_id=text(step.thread_id or ""),
            error=text(step.error or ""),
        )


def _vertical_steps(
    steps: tuple[StepDetailView, ...],
) -> Group:
    """Build vertical step records for a narrow terminal."""
    records: list[RenderableType] = []
    for index, step in enumerate(steps, start=1):
        values = (
            ("ID", step.step_id),
            ("Status", step.status),
            ("Attempt", step.attempt),
            ("Time", step.duration),
            ("Worker", step.worker),
            ("Thread", step.thread_id or "—"),
            ("Error", step.error or "—"),
        )
        records.append(
            Group(
                Text(f"{index}. {step.label}", style="bold"),
                _detail_grid(values, 0),
            )
        )
    return Group(*records)


def _step_table(
    steps: tuple[StepDetailView, ...],
    width: int,
) -> RenderableType:
    """Build responsive, safe step details."""
    has_long_number = any(
        len(value) > MAX_STEP_DETAIL_CHARS
        for step in steps
        for value in (step.attempt, step.worker)
    )
    if width < NARROW_DETAIL_WIDTH or has_long_number:
        return _vertical_steps(steps)
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
        detail_parts: list[str] = []
        if step.has_worker:
            detail_parts.append(f"PID {step.worker}")
        if step.thread_id:
            detail_parts.append(step.thread_id)
        if step.error:
            detail_parts.append(step.error)
        detail = " · ".join(detail_parts) or "—"
        identity = Group(
            Text(step.label),
            Text(step.step_id, style="bright_black"),
        )
        row: list[RenderableType] = [
            identity,
            Text(
                sanitize(step.status),
                style=STATUS_STYLES.get(step.status, "bright_black"),
            ),
            Text(step.attempt),
            Text(step.duration),
        ]
        if width >= 96:
            row.extend(
                [
                    Text(step.worker),
                    Text(
                        " · ".join(
                            part for part in [step.thread_id, step.error] if part
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
    run_steps = run.steps
    shown_steps = run_steps if full else run_steps[:step_limit]
    step_views = tuple(
        StepDetailView.from_record(step, now, full=full) for step in shown_steps
    )
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
    if step_views:
        step_table = _step_table(step_views, width)
        if width <= PLAIN_SHOW_MAX_WIDTH:
            parts.extend([Text("Steps", style="bright_black"), step_table])
        else:
            parts.append(
                Panel(
                    step_table,
                    title=f"Agent steps ({len(step_views)}/{len(run_steps)})",
                    border_style="bright_black",
                )
            )
        if len(step_views) < len(run_steps):
            parts.append(
                Text(
                    f"Showing the first {len(step_views)} of "
                    f"{len(run_steps)} steps; use --full or --json for all steps.",
                    style="yellow",
                )
            )
    else:
        if width <= PLAIN_SHOW_MAX_WIDTH:
            parts.extend([Text("Steps"), Text("No agent steps recorded.")])
        else:
            parts.append(Panel("No agent steps recorded.", title="Agent steps"))
    return Group(*parts)


def fit_live_runs(
    runs: list[RunRecord],
    *,
    console: Console,
    now: datetime,
    footer: RenderableType | None = None,
    empty: RenderableType | None = None,
) -> RenderableType:
    """Fit complete, pre-normalized run rows to the current terminal height."""
    options = console.options.update(height=None)

    def fit_footer() -> RenderableType | None:
        """Return a footer that fits, prioritizing its last warning."""
        if footer is None:
            return None
        if len(console.render_lines(footer, options, pad=False)) <= console.height:
            return footer
        renderables = getattr(footer, "renderables", None)
        if renderables is not None:
            for renderable in reversed(tuple(renderables)):
                if (
                    len(console.render_lines(renderable, options, pad=False))
                    <= console.height
                ):
                    return renderable
        return None

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
        if len(console.render_lines(candidate, options, pad=False)) <= console.height:
            return candidate
        return fit_footer() or empty_renderable

    views = [RunRowView.from_record(run, now) for run in runs]

    def build(visible: int) -> RenderableType:
        omitted = len(views) - visible
        marker = Text(
            f"Showing {visible} of {len(views)} selected runs; "
            f"{omitted} omitted (terminal height).",
            style="bright_black",
            overflow="ellipsis",
            no_wrap=True,
        )
        parts: list[RenderableType] = []
        if visible:
            parts.append(
                _build_runs_table_from_views(
                    views[:visible],
                    width=console.width,
                    live=True,
                )
            )
        if omitted:
            parts.append(marker)
        if footer is not None:
            parts.append(footer)
        return Group(*parts)

    maximum_visible = min(len(views), max(0, console.height))
    largest_candidate = build(maximum_visible)
    if (
        maximum_visible == len(views)
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
    fitted_footer = fit_footer()
    if fitted_footer is not None:
        return fitted_footer
    return smallest_candidate
