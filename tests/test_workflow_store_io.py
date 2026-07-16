"""Regression tests for race-safe workflow-store reads."""

from __future__ import annotations

import errno
import io
import json
import os
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

import pytest

from claude_code_tools import (
    workflow_runs,
    workflow_cli_store_backends as workflow_store_backends,
    workflow_store_io,
    workflow_cli_projection as workflow_store_projection,
)
from claude_code_tools.workflow_cli_identity_policy import RunResolutionKind

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
        workflow_store_backends,
        "posix_safe_open_supported",
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
        workflow_store_backends,
        "posix_safe_open_supported",
        lambda: False,
    )

    value, error = workflow_store_io.read_mapping(path)

    assert value is None
    assert error is not None
    assert "race-safe workflow-store inspection is unavailable" in error


def test_posix_directory_open_closes_descriptor_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-open inspection failure cannot leak the directory descriptor."""
    observed_closes: list[int] = []
    original_close = workflow_store_backends.os.close

    def fail_fstat(_descriptor: int) -> os.stat_result:
        raise OSError(errno.EIO, "simulated inspection failure")

    def tracking_close(descriptor: int) -> None:
        observed_closes.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(workflow_store_backends.os, "fstat", fail_fstat)
    monkeypatch.setattr(workflow_store_backends.os, "close", tracking_close)

    with pytest.raises(OSError, match="simulated inspection failure"):
        workflow_store_backends.POSIX_BACKEND.open_directory(tmp_path)

    assert len(observed_closes) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor behavior")
def test_posix_case_alias_uses_canonical_spelling_after_verified_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case-insensitive resolution returns the durable child spelling."""
    run_id = "lower-run"
    directory = tmp_path / "runs" / run_id
    directory.mkdir(parents=True)
    original_open = workflow_store_backends.os.open

    def case_insensitive_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        target: str | os.PathLike[str] = path
        if path == "LOWER-RUN" and dir_fd is not None:
            target = run_id
        if dir_fd is None:
            return original_open(target, flags, mode)
        return original_open(target, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(workflow_store_backends.os, "open", case_insensitive_open)
    monkeypatch.setattr(
        workflow_store_backends,
        "posix_safe_open_supported",
        lambda: True,
    )

    lookup = workflow_runs.load_named_run("LOWER-RUN", home=tmp_path)

    assert lookup.resolution.kind is RunResolutionKind.FOUND
    assert lookup.resolution.directory == directory
    assert lookup.record is not None
    assert lookup.record.run_id == run_id


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor behavior")
def test_posix_exact_child_open_does_not_enumerate_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact spelling is recovered from the child descriptor in constant work."""
    directory = tmp_path / "runs" / "exact-run"
    directory.mkdir(parents=True)

    def reject_enumeration(_handle: int) -> Iterator[os.DirEntry[str]]:
        raise AssertionError("exact child open must not enumerate its parent")

    monkeypatch.setattr(workflow_store_backends.os, "scandir", reject_enumeration)

    with workflow_store_io.VerifiedDirectory.open(directory.parent) as runs:
        with runs.open_child(directory.name) as opened:
            assert opened.path == directory


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor behavior")
def test_posix_descriptor_path_must_be_terminated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed fixed-buffer path recovery fails closed."""

    class FakeFcntl:
        F_GETPATH = 50

        @staticmethod
        def fcntl(_handle: int, _command: int, buffer: bytes) -> bytes:
            return b"x" * len(buffer)

    original_import = workflow_store_backends.importlib.import_module

    def fake_import(name: str) -> object:
        if name == "fcntl":
            return FakeFcntl()
        return original_import(name)

    monkeypatch.setattr(
        workflow_store_backends.importlib,
        "import_module",
        fake_import,
    )

    with pytest.raises(OSError, match="unterminated descriptor path"):
        workflow_store_backends.POSIX_BACKEND.directory_name(101)


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

    with workflow_store_io.VerifiedDirectory.open(tmp_path) as run:
        value, error = run.read_state()

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


def test_direct_state_read_constructs_a_practical_work_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state facade never exposes its 16 GiB ceiling without a work cap."""
    observed: list[workflow_store_io.ReadWorkBudget | None] = []

    def read_mapping(
        _directory: workflow_store_io.VerifiedDirectory,
        _name: str,
        **kwargs: object,
    ) -> tuple[dict[str, object], None]:
        budget = kwargs.get("budget")
        assert budget is None or isinstance(
            budget,
            workflow_store_io.ReadWorkBudget,
        )
        observed.append(budget)
        return {"status": "completed"}, None

    monkeypatch.setattr(
        workflow_store_io.VerifiedDirectory,
        "read_mapping",
        read_mapping,
    )
    with workflow_store_io.VerifiedDirectory.open(tmp_path) as directory:
        value, error = directory.read_state()

    assert error is None
    assert value == {"status": "completed"}
    assert len(observed) == 1
    budget = observed[0]
    assert budget is not None
    assert budget.maximum_bytes == workflow_store_io.DEFAULT_STATE_READ_BUDGET_BYTES


def test_streaming_projection_charges_aggregate_work_budget(
    tmp_path: Path,
) -> None:
    """Discarded payload bytes still consume the bounded scan-work budget."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"result": "x" * 200}), encoding="utf-8")
    budget = workflow_store_io.ReadWorkBudget(maximum_bytes=100)

    with workflow_store_io.VerifiedDirectory.open(tmp_path) as run:
        with pytest.raises(workflow_store_io.ReadBudgetExceeded):
            run.read_state(budget=budget)
    assert budget.consumed_bytes == 100


def test_streaming_projection_accepts_input_exactly_at_work_budget() -> None:
    """The EOF probe does not charge an unread byte after exact consumption."""
    raw = b'{"result":null}'
    budget = workflow_store_io.ReadWorkBudget(maximum_bytes=len(raw))

    value = workflow_store_projection.project_state_mapping(
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
        workflow_store_projection.project_state_mapping(
            io.BytesIO(raw),
            budget=budget,
        )

    assert budget.limit_exceeded


def test_projection_writer_uses_bounded_chunks() -> None:
    """Per-character parser writes retain one entry per bounded chunk."""
    chunk_bytes = workflow_store_projection.PROJECTION_CHUNK_BYTES
    writer = workflow_store_projection.ProjectionWriter(4 * chunk_bytes)

    for _ in range(3 * chunk_bytes + 17):
        writer.append("x")

    assert len(writer._chunks) == 3
    assert len(writer._pending) == 17
    assert writer.value() == "x" * (3 * chunk_bytes + 17)


def test_projection_accepts_retained_input_at_byte_limit() -> None:
    """A near-limit retained string is buffered in bounded chunks."""
    projected_shell = b'{"error":"","result":null}'
    payload_bytes = workflow_store_io.MAX_JSON_BYTES - len(projected_shell)
    raw = b'{"error":"' + b"x" * payload_bytes + b'","result":"discarded"}'

    value = workflow_store_projection.project_state_mapping(
        io.BytesIO(raw),
        budget=None,
    )

    assert value["error"] == "x" * payload_bytes
    assert value["result"] is None


def test_projection_rejects_compact_array_before_materializing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small JSON array cannot fan out into an unbounded Python list."""
    item_count = workflow_store_io.MAX_PROJECTED_JSON_NODES + 1
    raw = b'{"payload":[' + (b"0," * (item_count - 1)) + b"0]}"
    original_loads = workflow_store_projection.json.loads
    decoded_large_projection = False

    def tracking_loads(
        value: str | bytes | bytearray,
        **kwargs: Any,
    ) -> object:
        nonlocal decoded_large_projection
        if len(value) > 4_096:
            decoded_large_projection = True
        return original_loads(value, **kwargs)

    monkeypatch.setattr(
        workflow_store_projection.json,
        "loads",
        tracking_loads,
    )

    with pytest.raises(ValueError, match="structural limit"):
        workflow_store_projection.project_state_mapping(
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
    original_loads = workflow_store_projection.json.loads
    decoded_large_projection = False

    def tracking_loads(
        value: str | bytes | bytearray,
        **kwargs: Any,
    ) -> object:
        nonlocal decoded_large_projection
        if len(value) > 4_096:
            decoded_large_projection = True
        return original_loads(value, **kwargs)

    monkeypatch.setattr(
        workflow_store_projection.json,
        "loads",
        tracking_loads,
    )

    with pytest.raises(ValueError, match="structural limit"):
        workflow_store_projection.project_state_mapping(
            io.BytesIO(raw),
            budget=None,
        )

    assert len(raw) < workflow_store_io.MAX_JSON_BYTES
    assert not decoded_large_projection


def test_projection_discards_schema_irrelevant_fields() -> None:
    """State and callback projections retain only observational schema data."""
    state = workflow_store_projection.project_state_mapping(
        io.BytesIO(b'{"runId":"run-1","unknown":{"secret":"x"}}'),
        budget=None,
    )
    callback = workflow_store_projection.project_callback_mapping(
        io.BytesIO(b'{"status":"armed","unknown":[1,2,3]}'),
        budget=None,
    )

    assert state == {"runId": "run-1"}
    assert callback == {"status": "armed"}


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_projection_rejects_nonstandard_constants_in_discarded_fields(
    constant: bytes,
) -> None:
    """Invalid constants remain errors even when their field is omitted."""
    raw = b'{"runId":"run-1","unknown":' + constant + b"}"

    with pytest.raises(ValueError, match="invalid JSON constant"):
        workflow_store_projection.project_state_mapping(
            io.BytesIO(raw),
            budget=None,
        )


def test_callback_decoding_is_structurally_bounded() -> None:
    """Callback unknown fields cannot fan out into large decoded objects."""
    item_count = workflow_store_io.MAX_PROJECTED_JSON_NODES + 1
    raw = b'{"status":"armed","unknown":[' + (b"{}," * (item_count - 1)) + b"{}]}"

    with pytest.raises(ValueError, match="structural limit"):
        workflow_store_projection.project_callback_mapping(
            io.BytesIO(raw),
            budget=None,
        )


def test_structural_budget_is_shared_across_files(tmp_path: Path) -> None:
    """Many individually small files consume one observation node budget."""
    del tmp_path
    budget = workflow_store_io.ReadWorkBudget(
        maximum_bytes=1_000,
        maximum_nodes=5,
    )

    value = workflow_store_projection.project_callback_mapping(
        io.BytesIO(b'{"status":"armed"}'),
        budget=budget,
    )
    assert value == {"status": "armed"}

    with pytest.raises(
        workflow_store_io.ReadBudgetExceeded,
        match="aggregate structural limit of 5 nodes",
    ):
        workflow_store_projection.project_callback_mapping(
            io.BytesIO(b'{"status":"armed"}'),
            budget=budget,
        )


def test_discarded_string_scans_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large omitted strings do not call the character parser per byte."""
    original_take = workflow_store_projection.CharacterStream.take
    calls = 0

    def counting_take(
        stream: workflow_store_projection.CharacterStream,
    ) -> str:
        nonlocal calls
        calls += 1
        return original_take(stream)

    monkeypatch.setattr(
        workflow_store_projection.CharacterStream,
        "take",
        counting_take,
    )
    raw = b'{"unknown":"' + (b"x" * 1_000_000) + b'","runId":"run-1"}'

    value = workflow_store_projection.project_state_mapping(
        io.BytesIO(raw),
        budget=None,
    )

    assert value == {"runId": "run-1"}
    assert calls < 100


def test_json_whitespace_scans_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large legal whitespace regions avoid per-character parser calls."""
    original_take = workflow_store_projection.CharacterStream.take
    calls = 0

    def counting_take(
        stream: workflow_store_projection.CharacterStream,
    ) -> str:
        nonlocal calls
        calls += 1
        return original_take(stream)

    monkeypatch.setattr(
        workflow_store_projection.CharacterStream,
        "take",
        counting_take,
    )
    spaces = b" " * 1_000_000
    raw = spaces + b'{"runId":"run-1"}' + spaces

    value = workflow_store_projection.project_state_mapping(
        io.BytesIO(raw),
        budget=None,
    )

    assert value == {"runId": "run-1"}
    assert calls < 100


def test_verified_directory_owns_lifetime_and_relative_reads(
    tmp_path: Path,
) -> None:
    """The facade hides handle ownership and rejects reads after close."""
    child = tmp_path / "run-1"
    child.mkdir()
    (child / "state.json").write_text(
        '{"runId":"run-1","unknown":"discarded"}',
        encoding="utf-8",
    )

    with workflow_store_io.VerifiedDirectory.open(tmp_path) as root:
        assert ("run-1", True) in root.entries()
        with root.open_child("run-1") as run:
            value, error = run.read_state()
            assert value == {"runId": "run-1"}
            assert error is None

        with pytest.raises(ValueError, match="closed"):
            run.read_mapping("state.json")


def test_posix_observation_fails_closed_without_anchored_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX cannot fall back to pathname checks followed by pathname use."""
    monkeypatch.setattr(
        workflow_store_backends,
        "posix_safe_open_supported",
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
    monkeypatch.setattr(
        workflow_store_backends,
        "windows_kernel32",
        lambda: kernel32,
    )

    observed_kernel32, handle = workflow_store_backends.create_verified_windows_handle(
        tmp_path / "state.json",
        expect_directory=False,
    )

    assert observed_kernel32 is kernel32
    assert handle == 101
    assert share_modes == [
        workflow_store_backends.FILE_SHARE_READ
        | workflow_store_backends.FILE_SHARE_WRITE
        | workflow_store_backends.FILE_SHARE_DELETE
    ]


def test_mapping_read_delegates_to_selected_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mapping reads use one backend and an already-verified parent handle."""
    path = tmp_path / "state.json"
    observed: list[tuple[int, str]] = []

    class FakeBackend:
        def open_directory(
            self,
            _path: Path,
            *,
            parent_handle: int | None = None,
        ) -> int:
            raise AssertionError(parent_handle)

        def close_directory(self, _handle: int) -> None:
            raise AssertionError("borrowed parent must not be closed")

        def directory_entries(
            self,
            _handle: int,
        ) -> Iterator[tuple[str, bool]]:
            return iter(())

        def directory_name(self, _handle: int) -> str:
            raise AssertionError("no child directory open expected")

        def open_file(
            self,
            parent_handle: int,
            name: str,
        ) -> nullcontext[io.BytesIO]:
            observed.append((parent_handle, name))
            return nullcontext(io.BytesIO(b'{"status":"completed"}'))

    monkeypatch.setattr(
        workflow_store_io,
        "backend_for_platform",
        lambda: FakeBackend(),
    )

    value, error = workflow_store_io.read_mapping(path, directory_fd=101)

    assert error is None
    assert value == {"status": "completed"}
    assert observed == [(101, "state.json")]


def test_verified_directory_enumeration_uses_its_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run discovery stays on the backend that opened the capability."""
    observed: list[int] = []

    class FakeBackend:
        def open_directory(
            self,
            _path: Path,
            *,
            parent_handle: int | None = None,
        ) -> int:
            assert parent_handle is None
            return 303

        def close_directory(self, handle: int) -> None:
            observed.append(-handle)

        def directory_entries(
            self,
            handle: int,
        ) -> Iterator[tuple[str, bool]]:
            observed.append(handle)
            return iter([("run-1", True), ("state.json", False)])

        def directory_name(self, _handle: int) -> str:
            raise AssertionError("no child directory open expected")

        def open_file(
            self,
            _parent_handle: int,
            _name: str,
        ) -> nullcontext[io.BytesIO]:
            raise AssertionError("no file read expected")

    monkeypatch.setattr(
        workflow_store_io,
        "backend_for_platform",
        lambda: FakeBackend(),
    )

    with workflow_store_io.VerifiedDirectory.open(Path("unused")) as directory:
        entries = list(directory.entries())

    assert entries == [("run-1", True), ("state.json", False)]
    assert observed == [303, -303]


def test_enumerated_child_does_not_require_descriptor_path_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capability-bound names open where descriptor paths are unavailable."""
    observed: list[tuple[str, int]] = []

    class FakeBackend:
        def open_directory(
            self,
            path: Path,
            *,
            parent_handle: int | None = None,
        ) -> int:
            if parent_handle is None:
                return 101
            assert path.name == "run-1"
            assert parent_handle == 101
            return 202

        def close_directory(self, handle: int) -> None:
            observed.append(("close", handle))

        def directory_entries(
            self,
            handle: int,
        ) -> Iterator[tuple[str, bool]]:
            assert handle == 101
            return iter([("run-1", True)])

        def directory_name(self, handle: int) -> str:
            observed.append(("recover", handle))
            raise OSError(
                errno.ENOTSUP,
                "cannot verify exact workflow run directory spelling",
            )

        def open_file(
            self,
            _parent_handle: int,
            _name: str,
        ) -> nullcontext[io.BytesIO]:
            raise AssertionError("no file read expected")

    monkeypatch.setattr(
        workflow_store_io,
        "backend_for_platform",
        lambda: FakeBackend(),
    )

    with workflow_store_io.VerifiedDirectory.open(Path("runs")) as runs:
        with pytest.raises(OSError, match="cannot verify exact"):
            runs.open_child("run-1")

        assert list(runs.entries()) == [("run-1", True)]
        with runs.open_child("run-1") as run:
            assert run.path == Path("runs/run-1")

    assert observed == [
        ("recover", 202),
        ("close", 202),
        ("close", 202),
        ("close", 101),
    ]


def test_windows_access_denied_uses_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Win32 access failures retain permission-denied semantics."""
    monkeypatch.setattr(
        workflow_store_backends,
        "windows_last_error",
        lambda: 5,
    )

    with pytest.raises(PermissionError, match="Win32 error 5"):
        workflow_store_backends.raise_windows_os_error("cannot open state")


def test_windows_inspection_captures_error_before_closing_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloseHandle cannot overwrite a failed inspection's Win32 error."""
    current_error = 5

    def get_info(*_args: object) -> int:
        return 0

    def close_handle(_handle: object) -> int:
        nonlocal current_error
        current_error = 0
        return 1

    kernel32 = type(
        "FakeKernel32",
        (),
        {
            "CreateFileW": _FakeCtypesFunction(lambda *_args: 101),
            "GetFileInformationByHandleEx": _FakeCtypesFunction(get_info),
            "CloseHandle": _FakeCtypesFunction(close_handle),
        },
    )()
    monkeypatch.setattr(
        workflow_store_backends,
        "windows_kernel32",
        lambda: kernel32,
    )
    monkeypatch.setattr(
        workflow_store_backends,
        "windows_last_error",
        lambda: current_error,
    )

    with pytest.raises(PermissionError, match="Win32 error 5"):
        workflow_store_backends.create_verified_windows_handle(
            tmp_path / "state.json",
            expect_directory=False,
        )

    assert current_error == 0


def test_windows_relative_inspection_captures_error_before_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle-relative inspection also preserves the original Win32 error."""
    current_error = 5

    def nt_create_file(*args: object) -> int:
        handle_pointer = args[0]
        getattr(handle_pointer, "_obj").value = 202
        return 0

    def close_handle(_handle: object) -> int:
        nonlocal current_error
        current_error = 0
        return 1

    kernel32 = type(
        "FakeKernel32",
        (),
        {
            "GetFileInformationByHandleEx": _FakeCtypesFunction(lambda *_args: 0),
            "CloseHandle": _FakeCtypesFunction(close_handle),
        },
    )()
    ntdll = type(
        "FakeNtdll",
        (),
        {"NtCreateFile": _FakeCtypesFunction(nt_create_file)},
    )()
    monkeypatch.setattr(
        workflow_store_backends,
        "windows_kernel32",
        lambda: kernel32,
    )
    monkeypatch.setattr(
        workflow_store_backends,
        "windows_last_error",
        lambda: current_error,
    )
    monkeypatch.setattr(
        workflow_store_backends.ctypes,
        "WinDLL",
        lambda name, **_kwargs: ntdll if name == "ntdll" else kernel32,
        raising=False,
    )

    with pytest.raises(PermissionError, match="Win32 error 5"):
        workflow_store_backends.create_verified_windows_relative_handle(
            101,
            "state.json",
            expect_directory=False,
        )

    assert current_error == 0


def test_windows_directory_name_recovers_stored_spelling_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows exact open gets its durable spelling without enumeration."""
    final_path = r"\\?\C:\store\runs\lower-run"

    def get_final_path(
        _handle: object,
        buffer: object,
        capacity: int,
        _flags: int,
    ) -> int:
        if capacity <= len(final_path):
            return len(final_path) + 1
        setattr(buffer, "value", final_path)
        return len(final_path)

    kernel32 = type(
        "FakeKernel32",
        (),
        {
            "GetFinalPathNameByHandleW": _FakeCtypesFunction(get_final_path),
        },
    )()
    monkeypatch.setattr(
        workflow_store_backends,
        "windows_kernel32",
        lambda: kernel32,
    )

    assert workflow_store_backends.windows_directory_name(101) == "lower-run"


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
    kernel32, handle = workflow_store_backends.create_verified_windows_handle(
        state,
        expect_directory=False,
    )

    try:
        os.replace(replacement, state)
    finally:
        workflow_store_backends.close_windows_handle(kernel32, handle)

    assert state.read_text(encoding="utf-8") == "new"


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows APIs")
def test_windows_verified_directory_facade_native(tmp_path: Path) -> None:
    """The facade performs anchored discovery and reads on native Windows."""
    run_path = tmp_path / "run-1"
    run_path.mkdir()
    (run_path / "state.json").write_text(
        '{"runId":"run-1","status":"completed"}',
        encoding="utf-8",
    )

    with workflow_store_io.VerifiedDirectory.open(tmp_path) as root:
        assert ("run-1", True) in root.entries()
        with root.open_child("run-1") as run:
            value, error = run.read_state()

    assert error is None
    assert value == {"runId": "run-1", "status": "completed"}
