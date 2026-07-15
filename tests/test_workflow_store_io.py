"""Regression tests for race-safe workflow-store reads."""

from __future__ import annotations

import errno
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Callable

import pytest

from claude_code_tools import workflow_runs, workflow_store_io

TIME = "2026-07-14T14:00:00Z"


class _FakeCtypesFunction:
    """Callable that accepts ctypes signature attributes in native API tests."""

    def __init__(self, callback: Callable[..., object]) -> None:
        self._callback = callback
        self.argtypes: list[object] = []
        self.restype: object | None = None

    def __call__(self, *args: object) -> object:
        return self._callback(*args)


def test_open_directory_fails_closed_without_safe_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platforms without descriptor no-follow opens reject inspection."""
    directory = tmp_path / "runs"
    directory.mkdir()
    monkeypatch.setattr(
        workflow_store_io,
        "_safe_directory_open_supported",
        lambda: False,
    )

    with pytest.raises(OSError) as raised:
        workflow_store_io.open_directory(directory)

    assert raised.value.errno == errno.ENOTSUP
    assert "race-safe workflow-store inspection is unavailable" in str(raised.value)


def test_mapping_read_fails_closed_without_safe_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct JSON reads cannot fall back to a racy pathname open."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    monkeypatch.setattr(
        workflow_store_io,
        "_safe_directory_open_supported",
        lambda: False,
    )

    value, error = workflow_store_io.read_mapping(path)

    assert value is None
    assert error is not None
    assert "race-safe workflow-store inspection is unavailable" in error


def test_mapping_read_uses_verified_parent_descriptor(tmp_path: Path) -> None:
    """The convenience read path remains usable through a safe parent handle."""
    if not workflow_store_io._safe_directory_open_supported():
        pytest.skip("platform lacks race-safe no-follow opens")
    path = tmp_path / "state.json"
    expected: dict[str, object] = {"status": "completed"}
    path.write_text(json.dumps(expected), encoding="utf-8")

    value, error = workflow_store_io.read_mapping(path)

    assert error is None
    assert value == expected


def test_large_valid_state_discards_unused_result_payloads(
    tmp_path: Path,
) -> None:
    """Near-limit agent results do not make a valid state unreadable."""
    payload = "x" * 1_000_000
    steps: dict[str, object] = {}
    for index in range(9):
        step_id = f"root/step-{index}"
        steps[step_id] = {
            "attempt": 1,
            "completedAt": TIME,
            "fingerprint": f"fingerprint-{index}",
            "id": step_id,
            "label": f"Step {index}",
            "result": payload,
            "startedAt": TIME,
            "status": "completed",
        }
    state: dict[str, object] = {
        "completedAt": TIME,
        "concurrency": 1,
        "createdAt": TIME,
        "cwd": "/work",
        "result": payload,
        "runId": tmp_path.name,
        "status": "completed",
        "steps": steps,
        "updatedAt": TIME,
        "version": 1,
        "workflowHash": "hash",
        "workflowPath": "/work/workflow.js",
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert path.stat().st_size > workflow_store_io.MAX_JSON_BYTES

    value, error = workflow_store_io.read_mapping(
        path,
        description="state",
        omit_result_payloads=True,
    )

    assert error is None
    assert value is not None
    assert value["result"] is None
    projected_steps = value["steps"]
    assert isinstance(projected_steps, dict)
    for step in projected_steps.values():
        assert isinstance(step, dict)
        assert step["result"] is None

    run = workflow_runs.load_run(tmp_path)
    assert run.state_error is None
    assert run.status == "completed"
    assert len(run.steps) == 9


def test_streaming_projection_charges_aggregate_work_budget(
    tmp_path: Path,
) -> None:
    """Discarded payload bytes still consume the bounded scan-work budget."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"result": "x" * 200}), encoding="utf-8")
    budget = workflow_store_io.ReadWorkBudget(maximum_bytes=100)

    value, error = workflow_store_io.read_mapping(
        path,
        omit_result_payloads=True,
        budget=budget,
    )

    assert value is None
    assert error is not None
    assert "aggregate work limit of 100 bytes" in error
    assert budget.consumed_bytes == 100


def test_streaming_projection_accepts_input_exactly_at_work_budget() -> None:
    """The EOF probe does not charge an unread byte after exact consumption."""
    raw = b'{"result":null}'
    budget = workflow_store_io.ReadWorkBudget(maximum_bytes=len(raw))

    value = workflow_store_io._project_json_results(
        io.BytesIO(raw),
        budget=budget,
    )

    assert value == {"result": None}
    assert budget.consumed_bytes == len(raw)
    assert not budget.limit_exceeded


def test_streaming_projection_rejects_data_past_exact_work_budget() -> None:
    """The EOF probe still detects one actual byte beyond the work budget."""
    raw = b'{"result":null} '
    budget = workflow_store_io.ReadWorkBudget(maximum_bytes=len(raw) - 1)

    with pytest.raises(workflow_store_io.ReadBudgetExceeded):
        workflow_store_io._project_json_results(
            io.BytesIO(raw),
            budget=budget,
        )

    assert budget.limit_exceeded


def test_projection_writer_uses_bounded_chunks() -> None:
    """Per-character parser writes retain one entry per bounded chunk."""
    chunk_bytes = workflow_store_io._PROJECTION_CHUNK_BYTES
    writer = workflow_store_io._ProjectionWriter(4 * chunk_bytes)

    for _ in range(3 * chunk_bytes + 17):
        writer.append("x")

    assert len(writer._chunks) == 3
    assert len(writer._pending) == 17
    assert writer.value() == "x" * (3 * chunk_bytes + 17)


def test_projection_accepts_retained_input_at_byte_limit() -> None:
    """A near-limit retained string is buffered in bounded chunks."""
    projected_shell = b'{"payload":"","result":null}'
    payload_bytes = workflow_store_io.MAX_JSON_BYTES - len(projected_shell)
    raw = (
        b'{"payload":"'
        + b"x" * payload_bytes
        + b'","result":"discarded"}'
    )

    value = workflow_store_io._project_json_results(
        io.BytesIO(raw),
        budget=None,
    )

    assert value["payload"] == "x" * payload_bytes
    assert value["result"] is None


def test_projection_rejects_compact_array_before_materializing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small JSON array cannot fan out into an unbounded Python list."""
    item_count = workflow_store_io.MAX_PROJECTED_JSON_NODES + 1
    raw = b'{"payload":[' + (b"0," * (item_count - 1)) + b"0]}"
    original_loads = workflow_store_io.json.loads
    decoded_large_projection = False

    def tracking_loads(value: str | bytes | bytearray) -> object:
        nonlocal decoded_large_projection
        if len(value) > 4_096:
            decoded_large_projection = True
        return original_loads(value)

    monkeypatch.setattr(workflow_store_io.json, "loads", tracking_loads)

    with pytest.raises(ValueError, match="structural limit"):
        workflow_store_io._project_json_results(
            io.BytesIO(raw),
            budget=None,
        )

    assert len(raw) < 1024 * 1024
    assert not decoded_large_projection


def test_projection_rejects_compact_object_before_materializing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compact object members cannot create an unbounded Python mapping."""
    member_count = workflow_store_io.MAX_PROJECTED_JSON_NODES // 2 + 1
    members = (f'"{index}":0'.encode() for index in range(member_count))
    raw = b'{"payload":{' + b",".join(members) + b"}}"
    original_loads = workflow_store_io.json.loads
    decoded_large_projection = False

    def tracking_loads(value: str | bytes | bytearray) -> object:
        nonlocal decoded_large_projection
        if len(value) > 4_096:
            decoded_large_projection = True
        return original_loads(value)

    monkeypatch.setattr(workflow_store_io.json, "loads", tracking_loads)

    with pytest.raises(ValueError, match="structural limit"):
        workflow_store_io._project_json_results(
            io.BytesIO(raw),
            budget=None,
        )

    assert len(raw) < workflow_store_io.MAX_JSON_BYTES
    assert not decoded_large_projection


def test_windows_observation_fails_closed_without_anchored_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows cannot fall back to pathname checks followed by pathname use."""
    monkeypatch.setattr(
        workflow_store_io,
        "_safe_directory_open_supported",
        lambda: False,
    )
    directory = tmp_path / "runs"

    with pytest.raises(OSError) as raised:
        workflow_store_io.open_directory(directory)
    value, error = workflow_store_io.read_mapping(
        directory / "completion-notification.json",
        missing_ok=True,
    )

    assert raised.value.errno == errno.ENOTSUP
    assert value is None
    assert error is not None
    assert "race-safe workflow-store inspection is unavailable" in error


def test_windows_observer_handle_shares_delete_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native observation handles permit atomic replacement by producers."""
    share_modes: list[int] = []

    def create_file(*args: object) -> int:
        share_mode = args[2]
        assert isinstance(share_mode, int)
        share_modes.append(share_mode)
        return 101

    def get_info(*args: object) -> int:
        info_pointer = args[2]
        info = getattr(info_pointer, "_obj")
        info.FileAttributes = 0
        return 1

    kernel32 = type(
        "FakeKernel32",
        (),
        {
            "CreateFileW": _FakeCtypesFunction(create_file),
            "GetFileInformationByHandleEx": _FakeCtypesFunction(get_info),
            "CloseHandle": _FakeCtypesFunction(lambda _handle: 1),
        },
    )()
    monkeypatch.setattr(workflow_store_io, "_windows_kernel32", lambda: kernel32)

    observed_kernel32, handle = (
        workflow_store_io._windows_create_verified_handle(
            tmp_path / "state.json",
            expect_directory=False,
        )
    )

    assert observed_kernel32 is kernel32
    assert handle == 101
    assert share_modes == [
        workflow_store_io._FILE_SHARE_READ
        | workflow_store_io._FILE_SHARE_WRITE
        | workflow_store_io._FILE_SHARE_DELETE
    ]


def test_windows_mapping_read_uses_verified_relative_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows reads bypass the POSIX prerequisite through an anchored handle."""
    path = tmp_path / "state.json"
    observed: list[tuple[int, str, bool]] = []

    def open_relative(
        parent_handle: int,
        target: str,
        *,
        expect_directory: bool,
    ) -> tuple[object, int]:
        observed.append((parent_handle, target, expect_directory))
        return object(), 202

    monkeypatch.setattr(workflow_store_io.os, "name", "nt")
    monkeypatch.setattr(
        workflow_store_io,
        "_safe_directory_open_supported",
        lambda: False,
    )
    monkeypatch.setattr(
        workflow_store_io,
        "_windows_create_verified_relative_handle",
        open_relative,
    )
    monkeypatch.setattr(
        workflow_store_io,
        "_windows_stream_for_handle",
        lambda _kernel32, _handle: io.BytesIO(b'{"status":"completed"}'),
    )

    value, error = workflow_store_io.read_mapping(path, directory_fd=101)

    assert error is None
    assert value == {"status": "completed"}
    assert observed == [(101, "state.json", False)]


def test_windows_directory_enumeration_uses_verified_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows run discovery enumerates the already-open directory handle."""
    observed: list[int] = []

    def native_entries(descriptor: int) -> Iterator[tuple[str, bool]]:
        observed.append(descriptor)
        return iter([("run-1", True), ("state.json", False)])

    monkeypatch.setattr(workflow_store_io.os, "name", "nt")
    monkeypatch.setattr(
        workflow_store_io,
        "_windows_directory_entries",
        native_entries,
    )

    entries = list(workflow_store_io.directory_entries(303))

    assert entries == [("run-1", True), ("state.json", False)]
    assert observed == [303]


def test_windows_access_denied_uses_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Win32 access failures retain permission-denied semantics."""
    monkeypatch.setattr(workflow_store_io, "_windows_last_error", lambda: 5)

    with pytest.raises(PermissionError, match="Win32 error 5"):
        workflow_store_io._windows_os_error("cannot open state")


def test_load_runs_surfaces_aggregate_budget_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded scan fails explicitly instead of silently omitting valid runs."""
    directory = tmp_path / "runs" / "completed"
    directory.mkdir(parents=True)
    state = {
        "completedAt": TIME,
        "concurrency": 1,
        "createdAt": TIME,
        "cwd": "/work",
        "runId": directory.name,
        "status": "completed",
        "steps": {},
        "updatedAt": TIME,
        "version": 1,
        "workflowHash": "hash",
        "workflowPath": "/work/workflow.js",
    }
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(workflow_runs, "MAX_SCAN_JSON_BYTES", 100)

    with pytest.raises(
        workflow_runs.WorkflowStoreError,
        match="aggregate work limit of 100 bytes",
    ):
        workflow_runs.load_runs(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows reparse APIs")
def test_windows_observer_handle_does_not_block_replace(tmp_path: Path) -> None:
    """A live native observation handle permits producer publication."""
    state = tmp_path / "state.json"
    replacement = tmp_path / "state.next.json"
    state.write_text("old", encoding="utf-8")
    replacement.write_text("new", encoding="utf-8")
    kernel32, handle = workflow_store_io._windows_create_verified_handle(
        state,
        expect_directory=False,
    )

    try:
        os.replace(replacement, state)
    finally:
        workflow_store_io._close_windows_handle(kernel32, handle)

    assert state.read_text(encoding="utf-8") == "new"
