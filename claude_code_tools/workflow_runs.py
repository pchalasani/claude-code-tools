"""Read-only repository and models for durable dynamic-workflow runs."""

from __future__ import annotations

import errno
import os
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from time import monotonic

from claude_code_tools.workflow_processes import (
    ObservationContext,
    process_start_identity,
)
from claude_code_tools.workflow_cli_snapshots import (
    CallbackRecord,
    RunLookupResult,
    RunQueryResult,
    RunRecord,
    RunState,
)
from claude_code_tools.workflow_cli_identity_policy import (
    ABBREVIATED_RUN_ID_PATTERN,
    RUN_ID_PATTERN,
    RunResolution,
    RunResolutionKind,
    abbreviate_run_id,
    colliding_abbreviations,
)
from claude_code_tools.workflow_store_io import (
    ReadBudgetExceeded,
    ReadWorkBudget,
    VerifiedDirectory,
)
from claude_code_tools.workflow_validation import (
    NONTERMINAL_STATUSES,
    TERMINAL_STATUSES,
    as_utc,
    parse_run_record,
    validate_callback_observation,
    validate_state_observation,
)

ACTIVE_STATUSES = tuple(sorted(NONTERMINAL_STATUSES | {"unverifiable"}))
FILTER_STATUSES = tuple(
    sorted(
        TERMINAL_STATUSES
        | NONTERMINAL_STATUSES
        | {"malformed", "orphaned", "stale", "unknown", "unverifiable"}
    )
)
STARTUP_GRACE_SECONDS = 5.0
COHERENCE_READ_ATTEMPTS = 3
MAX_RUN_DIRECTORIES = 1_000
MAX_SCAN_JSON_BYTES = 128 * 1024 * 1024
MAX_SINGLE_RUN_JSON_BYTES = MAX_SCAN_JSON_BYTES
MAX_PROCESS_OBSERVATION_SECONDS = 5.0
MAX_POSIX_RUN_NAME_BYTES = 255
MAX_WINDOWS_RUN_NAME_UTF16_BYTES = 510


class WorkflowStoreError(RuntimeError):
    """Raised when the configured workflow store cannot be inspected."""


def workflow_home() -> Path:
    """Return the absolute configured workflow home without creating it."""
    configured = os.environ.get("CODEX_WORKFLOW_HOME")
    if configured is not None:
        return Path(os.path.abspath(configured))
    return Path(os.path.abspath(Path.home() / ".codex" / "workflows"))


def observed_process_state(pid: int, expected: str) -> str | None:
    """Observe one process claim through the shared context policy."""
    return ObservationContext(probe_factory=process_start_identity).classify(
        pid,
        expected,
    )


def _within_startup_grace(state: RunState, now: datetime) -> bool:
    """Return whether identity-less state is in its publication window."""
    updated = as_utc(state.updated_at) or as_utc(state.created_at)
    if updated is None:
        return False
    try:
        age = (now.astimezone(UTC) - updated).total_seconds()
    except (OverflowError, ValueError):
        return False
    return 0.0 <= age <= STARTUP_GRACE_SECONDS


def _supervisor_state(
    state: RunState,
    now: datetime,
    observer: ObservationContext,
) -> str | None:
    """Classify a nonterminal run's supervisor."""
    if state.status not in NONTERMINAL_STATUSES:
        return None
    if state.pid is None and state.pid_started_at is None:
        return None if _within_startup_grace(state, now) else "unverifiable"
    if state.pid is None or state.pid_started_at is None:
        return "unverifiable"
    return observer.classify(state.pid, state.pid_started_at)


class CallbackGeneration(Enum):
    """Relationship between callback metadata and a run generation."""

    UNBOUND = "unbound"
    MATCHES = "matches"
    MISMATCH = "mismatch"


def _timestamps_equal(left: str | None, right: str | None) -> bool:
    """Compare two valid persisted timestamps by instant."""
    normalized_left = as_utc(left)
    normalized_right = as_utc(right)
    return normalized_left is not None and normalized_left == normalized_right


def callback_generation_relation(
    state: RunState,
    callback: CallbackRecord,
) -> CallbackGeneration:
    """Correlate callback terminal metadata with one run generation."""
    if callback.terminal_completed_at is None and callback.terminal_status is None:
        return CallbackGeneration.UNBOUND
    base_matches = (
        state.status in TERMINAL_STATUSES
        and _timestamps_equal(
            callback.terminal_completed_at,
            state.completed_at,
        )
        and callback.terminal_status == state.status
    )
    if not base_matches:
        return CallbackGeneration.MISMATCH
    if (
        callback.terminal_fingerprint is not None
        and state.terminal_fingerprint is not None
    ):
        return (
            CallbackGeneration.MATCHES
            if callback.terminal_fingerprint == state.terminal_fingerprint
            else CallbackGeneration.MISMATCH
        )
    return CallbackGeneration.MATCHES


def _callback_observation(
    state: RunState,
    callback: CallbackRecord,
    observer: ObservationContext,
) -> tuple[str | None, str | None]:
    """Correlate callback generation and notifier process status."""
    if callback_generation_relation(state, callback) is CallbackGeneration.MISMATCH:
        return "stale", None
    if callback.status != "sending":
        return None, None
    if callback.notifier_pid is None or callback.notifier_started_at is None:
        process_state = "unverifiable"
    else:
        process_state = (
            observer.classify(
                callback.notifier_pid,
                callback.notifier_started_at,
            )
            or "running"
        )
    delivery_state = (
        "unknown"
        if (callback.attempts or 0) > 0
        else None
        if process_state == "running"
        else process_state
    )
    return delivery_state, process_state


def _observe_record(
    record: RunRecord,
    now: datetime,
    observer: ObservationContext,
) -> RunRecord:
    """Attach process-derived classifications to one immutable record."""
    callback_state: str | None = None
    callback_process_state: str | None = None
    if record.callback_error is None and record.callback is not None:
        if record.state_error is not None:
            callback_state = "unverifiable"
        else:
            callback_state, callback_process_state = _callback_observation(
                record.state,
                record.callback,
                observer,
            )
    supervisor_state = (
        None if record.state_error else _supervisor_state(record.state, now, observer)
    )
    return replace(
        record,
        supervisor_state=supervisor_state,
        callback_state=callback_state,
        callback_process_state=callback_process_state,
    )


@dataclass(frozen=True)
class ValidatedSnapshot:
    """One typed state/callback pair with explicit observation clocks."""

    state: RunState
    callback: CallbackRecord | None
    state_error: str | None
    callback_error: str | None
    query_at: datetime
    read_completed_at: datetime


def read_validated_snapshot_once(
    run_directory: VerifiedDirectory,
    directory_name: str,
    *,
    budget: ReadWorkBudget,
    query_at: datetime,
) -> ValidatedSnapshot:
    """Read each file and validate it against its acquisition completion."""
    raw_state, state_error = run_directory.read_state(budget=budget)
    state_read_completed_at = datetime.now(UTC)
    raw_callback, callback_error = run_directory.read_callback(budget=budget)
    callback_read_completed_at = datetime.now(UTC)

    raw_record = parse_run_record(
        Path(directory_name),
        state=raw_state,
        callback=raw_callback,
    )
    state = raw_record.state
    if raw_state is not None:
        state_error = raw_record.state_error
        if state_error is None:
            state_error = validate_state_observation(
                state,
                state_read_completed_at,
            )

    callback = raw_record.callback
    if raw_callback is not None:
        callback_error = raw_record.callback_error
        if callback_error is None:
            assert callback is not None
            callback_error = validate_callback_observation(
                callback,
                callback_read_completed_at,
            )
    return ValidatedSnapshot(
        state=state,
        callback=callback,
        state_error=state_error,
        callback_error=callback_error,
        query_at=query_at,
        read_completed_at=callback_read_completed_at,
    )


def _read_coherent_snapshot(
    run_directory: VerifiedDirectory,
    directory_name: str,
    *,
    budget: ReadWorkBudget,
    query_at: datetime,
) -> ValidatedSnapshot:
    """Retry changing mismatches without amplifying identical snapshots."""
    snapshot = read_validated_snapshot_once(
        run_directory,
        directory_name,
        budget=budget,
        query_at=query_at,
    )
    for _ in range(COHERENCE_READ_ATTEMPTS - 1):
        if (
            snapshot.state_error is not None
            or snapshot.callback_error is not None
            or snapshot.callback is None
            or callback_generation_relation(
                snapshot.state,
                snapshot.callback,
            )
            is not CallbackGeneration.MISMATCH
        ):
            break
        try:
            candidate = read_validated_snapshot_once(
                run_directory,
                directory_name,
                budget=budget,
                query_at=query_at,
            )
        except ReadBudgetExceeded:
            return snapshot
        if (
            candidate.state == snapshot.state
            and candidate.callback == snapshot.callback
            and candidate.state_error == snapshot.state_error
            and candidate.callback_error == snapshot.callback_error
        ):
            break
        snapshot = candidate
    return snapshot


def _record_from_open_directory(
    directory: Path,
    run_directory: VerifiedDirectory,
    *,
    query_at: datetime,
    observe: bool,
    budget: ReadWorkBudget,
    observer: ObservationContext | None,
) -> RunRecord:
    """Build one record from an already verified run capability."""
    snapshot = _read_coherent_snapshot(
        run_directory,
        directory.name,
        budget=budget,
        query_at=query_at,
    )
    record = RunRecord(
        directory=directory,
        state=snapshot.state,
        callback=snapshot.callback,
        state_error=snapshot.state_error,
        callback_error=snapshot.callback_error,
    )
    if not observe:
        return record
    active_observer = observer or ObservationContext(
        probe_factory=process_start_identity
    )
    return _observe_record(
        record,
        snapshot.read_completed_at,
        active_observer,
    )


def load_run(
    directory: Path,
    *,
    now: datetime | None = None,
    observe: bool = True,
    budget: ReadWorkBudget | None = None,
    observer: ObservationContext | None = None,
    _parent_directory: VerifiedDirectory | None = None,
) -> RunRecord:
    """Load one run directory without modifying it."""
    observed_at = now or datetime.now(UTC)
    owns_budget = budget is None
    active_budget = budget or ReadWorkBudget(MAX_SINGLE_RUN_JSON_BYTES)
    try:
        with ExitStack() as stack:
            parent = _parent_directory
            if parent is None:
                parent = stack.enter_context(VerifiedDirectory.open(directory.parent))
            run_directory = stack.enter_context(parent.open_child(directory.name))
            return _record_from_open_directory(
                directory,
                run_directory,
                query_at=observed_at,
                observe=observe,
                budget=active_budget,
                observer=observer,
            )
    except ReadBudgetExceeded as error:
        if not owns_budget:
            raise
        return RunRecord(directory=directory, state_error=str(error))
    except (OSError, ValueError) as error:
        return RunRecord(directory=directory, state_error=str(error))


def _has_surrogate(value: str) -> bool:
    """Return whether text contains a non-reversible surrogate code point."""
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _directory_names(
    verified_runs: VerifiedDirectory,
    runs_directory: Path,
) -> tuple[str, ...]:
    """Enumerate a bounded catalog of reversible Unicode identities."""
    names: list[str] = []
    for entry_count, (name, is_directory) in enumerate(
        verified_runs.entries(),
        start=1,
    ):
        if entry_count > MAX_RUN_DIRECTORIES:
            raise WorkflowStoreError(
                "Workflow run scan exceeds the safety limit of "
                f"{MAX_RUN_DIRECTORIES} directory entries in "
                f"{runs_directory}"
            )
        if is_directory and not _has_surrogate(name):
            names.append(name)
    return tuple(names)


def load_runs(
    home: Path | None = None,
    *,
    statuses: tuple[str, ...] = (),
    now: datetime | None = None,
    observe: bool = True,
    limit: int | None = None,
) -> RunQueryResult:
    """Load, sort, filter, and bound immutable run snapshots."""
    observed_at = now or datetime.now(UTC)
    runs_directory = (home or workflow_home()) / "runs"
    records: list[RunRecord] = []
    budget = ReadWorkBudget(MAX_SCAN_JSON_BYTES)
    try:
        with VerifiedDirectory.open(runs_directory) as verified_runs:
            names = _directory_names(verified_runs, runs_directory)
            for name in names:
                directory = runs_directory / name
                try:
                    with verified_runs.open_child(name) as run_directory:
                        record = _record_from_open_directory(
                            directory,
                            run_directory,
                            query_at=observed_at,
                            observe=False,
                            budget=budget,
                            observer=None,
                        )
                except (OSError, ValueError) as error:
                    record = RunRecord(
                        directory=directory,
                        state_error=str(error),
                    )
                records.append(record)
    except FileNotFoundError:
        read_completed_at = datetime.now(UTC)
        return RunQueryResult(
            (),
            False,
            False,
            query_at=observed_at,
            read_completed_at=read_completed_at,
        )
    except ReadBudgetExceeded as error:
        raise WorkflowStoreError(
            f"Workflow run JSON scan exceeds its aggregate work limit in "
            f"{runs_directory}: {error}"
        ) from error
    except OSError as error:
        raise WorkflowStoreError(
            f"Cannot read workflow runs from {runs_directory}: {error}"
        ) from error
    read_completed_at = datetime.now(UTC)
    if not names:
        return RunQueryResult(
            (),
            False,
            False,
            query_at=observed_at,
            read_completed_at=read_completed_at,
        )

    oldest = datetime.min.replace(tzinfo=UTC)

    def sort_key(run: RunRecord) -> tuple[bool, datetime, str]:
        normalized = as_utc(run.created_at) or as_utc(run.updated_at) or oldest
        return run.state_error is None, normalized, run.run_id

    sorted_records = sorted(records, key=sort_key, reverse=True)
    collisions = colliding_abbreviations(
        [(record.run_id, record.abbreviated_id) for record in sorted_records]
    )
    sorted_records = [
        replace(
            record,
            abbreviation_ambiguous=record.abbreviated_id in collisions,
        )
        for record in sorted_records
    ]

    wanted = set(statuses)
    if not observe:
        matching = [
            record for record in sorted_records if not wanted or record.status in wanted
        ]
        truncated = limit is not None and len(matching) > limit
        selected = matching if limit is None else matching[:limit]
        return RunQueryResult(
            tuple(selected),
            truncated,
            True,
            query_at=observed_at,
            read_completed_at=read_completed_at,
        )

    observer = ObservationContext(
        deadline=monotonic() + MAX_PROCESS_OBSERVATION_SECONDS,
        probe_factory=process_start_identity,
        clock=monotonic,
    )
    selected: list[RunRecord] = []
    selection_cap = None if limit is None else limit + 1
    for record in sorted_records:
        if not _could_match_without_observation(record, wanted):
            continue
        observed = _observe_record(record, read_completed_at, observer)
        if wanted and observed.status not in wanted:
            continue
        selected.append(observed)
        if selection_cap is not None and len(selected) >= selection_cap:
            break
    truncated = limit is not None and len(selected) > limit
    if truncated:
        selected = selected[:limit]
    return RunQueryResult(
        tuple(selected),
        truncated,
        True,
        observation_complete=observer.complete,
        observation_skipped=observer.skipped,
        query_at=observed_at,
        read_completed_at=read_completed_at,
    )


def _could_match_without_observation(
    record: RunRecord,
    wanted: set[str],
) -> bool:
    """Reject records whose effective status cannot match without probing."""
    if not wanted:
        return True
    if record.state_error:
        return "malformed" in wanted
    recorded = record.recorded_status
    if recorded not in NONTERMINAL_STATUSES:
        effective = recorded if recorded in TERMINAL_STATUSES else "unknown"
        return effective in wanted
    possible = {recorded, "orphaned", "stale", "unverifiable"}
    return not possible.isdisjoint(wanted)


def _safe_exact_name(run_id: str) -> bool:
    """Return whether exact lookup cannot escape the runs directory."""
    if _has_surrogate(run_id):
        return False
    separators = {os.sep}
    if os.altsep is not None:
        separators.add(os.altsep)
    try:
        encoded_name = (
            run_id.encode("utf-16-le") if os.name == "nt" else os.fsencode(run_id)
        )
    except UnicodeError:
        return False
    maximum_bytes = (
        MAX_WINDOWS_RUN_NAME_UTF16_BYTES
        if os.name == "nt"
        else MAX_POSIX_RUN_NAME_BYTES
    )
    return (
        bool(run_id)
        and run_id not in {".", ".."}
        and "\0" not in run_id
        and len(encoded_name) <= maximum_bytes
        and not any(separator in run_id for separator in separators)
    )


def load_named_run(
    run_id: str,
    *,
    home: Path | None = None,
    now: datetime | None = None,
) -> RunLookupResult:
    """Resolve and load one run under the same verified store capability."""
    query_at = now or datetime.now(UTC)
    runs_directory = (home or workflow_home()) / "runs"
    directory = runs_directory / run_id
    valid_full = RUN_ID_PATTERN.fullmatch(run_id) is not None
    valid_abbreviation = ABBREVIATED_RUN_ID_PATTERN.fullmatch(run_id) is not None
    exact_candidate = _safe_exact_name(run_id)
    if not exact_candidate and not valid_abbreviation:
        resolution = RunResolution(RunResolutionKind.INVALID, run_id)
        return RunLookupResult(
            resolution,
            None,
            query_at,
            datetime.now(UTC),
        )
    try:
        with VerifiedDirectory.open(runs_directory) as verified_runs:
            selected_name = run_id
            run_directory: VerifiedDirectory | None = None
            names: tuple[str, ...] | None = None
            if exact_candidate:
                try:
                    run_directory = verified_runs.open_child(selected_name)
                    selected_name = run_directory.path.name
                except OSError as error:
                    if error.errno not in {
                        errno.ENOENT,
                        errno.ENOTDIR,
                        errno.ELOOP,
                    }:
                        raise
            if run_directory is None:
                if not valid_abbreviation:
                    kind = (
                        RunResolutionKind.NOT_FOUND
                        if valid_full
                        else RunResolutionKind.INVALID
                    )
                    resolution = RunResolution(kind, run_id)
                    return RunLookupResult(
                        resolution,
                        None,
                        query_at,
                        datetime.now(UTC),
                    )
                if names is None:
                    names = _directory_names(verified_runs, runs_directory)
                matches = tuple(
                    name for name in names if abbreviate_run_id(name) == run_id
                )
                if len(matches) != 1:
                    kind = (
                        RunResolutionKind.AMBIGUOUS
                        if matches
                        else RunResolutionKind.NOT_FOUND
                    )
                    resolution = RunResolution(
                        kind,
                        run_id,
                        candidates=tuple(sorted(matches)),
                    )
                    return RunLookupResult(
                        resolution,
                        None,
                        query_at,
                        datetime.now(UTC),
                    )
                selected_name = matches[0]
                try:
                    run_directory = verified_runs.open_child(selected_name)
                except (OSError, ValueError) as open_error:
                    raise WorkflowStoreError(
                        "Cannot inspect resolved workflow run "
                        f"{selected_name!r}: {open_error}"
                    ) from open_error
            directory = runs_directory / selected_name
            with run_directory:
                record = _record_from_open_directory(
                    directory,
                    run_directory,
                    query_at=query_at,
                    observe=True,
                    budget=ReadWorkBudget(MAX_SINGLE_RUN_JSON_BYTES),
                    observer=None,
                )
    except FileNotFoundError:
        kind = (
            RunResolutionKind.NOT_FOUND
            if valid_full or valid_abbreviation
            else RunResolutionKind.INVALID
        )
        resolution = RunResolution(kind, run_id)
        return RunLookupResult(
            resolution,
            None,
            query_at,
            datetime.now(UTC),
        )
    except ReadBudgetExceeded as error:
        record = RunRecord(directory=directory, state_error=str(error))
    except WorkflowStoreError:
        raise
    except (OSError, ValueError) as error:
        raise WorkflowStoreError(
            f"Cannot inspect workflow run in {runs_directory}: {error}"
        ) from error
    resolution = RunResolution(
        RunResolutionKind.FOUND,
        run_id,
        directory=directory,
    )
    return RunLookupResult(
        resolution,
        record,
        query_at,
        datetime.now(UTC),
    )
