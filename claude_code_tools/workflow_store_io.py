"""Race-safe, read-only access to durable workflow store files."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from claude_code_tools.workflow_cli_projection import (
    CALLBACK_PROJECTION,
    FULL_PROJECTION,
    MAX_JSON_BYTES,
    MAX_PROJECTED_JSON_NODES,
    MAX_STATE_JSON_BYTES,
    ProjectionSpec,
    STATE_PROJECTION,
    project_mapping,
)
from claude_code_tools.workflow_cli_store_backends import (
    NotRegularFileError,
    VerifiedStoreBackend,
    backend_for_platform,
    posix_safe_open_supported,
)

DEFAULT_STATE_READ_BUDGET_BYTES = 128 * 1024 * 1024


class ReadBudgetExceeded(RuntimeError):
    """Raised when a workflow-store scan exhausts its aggregate work budget."""


@dataclass
class ReadWorkBudget:
    """Aggregate raw, structural, and retained limits for one observation."""

    maximum_bytes: int
    consumed_bytes: int = 0
    maximum_nodes: int = MAX_PROJECTED_JSON_NODES
    maximum_retained_bytes: int = 32 * 1024 * 1024
    consumed_nodes: int = 0
    consumed_retained_bytes: int = 0
    limit_exceeded: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Reject nonsensical aggregate limits."""
        if self.maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        if self.consumed_bytes < 0 or self.consumed_bytes > self.maximum_bytes:
            raise ValueError("consumed_bytes must be within the aggregate limit")
        if self.maximum_nodes <= 0:
            raise ValueError("maximum_nodes must be positive")
        if self.maximum_retained_bytes <= 0:
            raise ValueError("maximum_retained_bytes must be positive")

    @property
    def remaining_bytes(self) -> int:
        """Return the unconsumed portion of this budget."""
        return self.maximum_bytes - self.consumed_bytes

    def charge(self, byte_count: int) -> None:
        """Charge bytes processed by a store read.

        Args:
            byte_count: Nonnegative number of newly processed bytes.

        Raises:
            ReadBudgetExceeded: The aggregate limit would be exceeded.
            ValueError: ``byte_count`` is negative.
        """
        if byte_count < 0:
            raise ValueError("byte_count must be nonnegative")
        if byte_count > self.remaining_bytes:
            self.consumed_bytes = self.maximum_bytes
            self.limit_exceeded = True
            raise ReadBudgetExceeded(
                "workflow-store JSON reads exceed the aggregate work limit of "
                f"{self.maximum_bytes} bytes"
            )
        self.consumed_bytes += byte_count

    def charge_nodes(self, node_count: int = 1) -> None:
        """Charge parsed JSON keys and values across the whole observation."""
        if node_count < 0:
            raise ValueError("node_count must be nonnegative")
        self.consumed_nodes += node_count
        if self.consumed_nodes > self.maximum_nodes:
            self.limit_exceeded = True
            raise ReadBudgetExceeded(
                "workflow-store JSON exceeds the aggregate structural limit "
                f"of {self.maximum_nodes} nodes"
            )

    def charge_retained(self, byte_count: int) -> None:
        """Charge bytes preserved by projections across the observation."""
        if byte_count < 0:
            raise ValueError("byte_count must be nonnegative")
        self.consumed_retained_bytes += byte_count
        if self.consumed_retained_bytes > self.maximum_retained_bytes:
            self.limit_exceeded = True
            raise ReadBudgetExceeded(
                "workflow-store JSON exceeds the aggregate retained-data "
                f"limit of {self.maximum_retained_bytes} bytes"
            )


def _safe_directory_open_supported() -> bool:
    """Return whether POSIX descriptor-relative no-follow opens are available."""
    return posix_safe_open_supported()


def _read_stream(
    stream: BinaryIO,
    *,
    projection: ProjectionSpec,
    maximum_input_bytes: int,
    budget: ReadWorkBudget | None,
) -> dict[str, object]:
    """Read one object through the structurally bounded projector."""
    return project_mapping(
        stream,
        maximum_input_bytes=maximum_input_bytes,
        budget=budget,
        spec=projection,
    )


def _read_from_backend(
    backend: VerifiedStoreBackend,
    parent_handle: int,
    name: str,
    *,
    description: str,
    missing_ok: bool,
    budget: ReadWorkBudget | None,
    projection: ProjectionSpec,
    maximum_input_bytes: int,
) -> tuple[dict[str, object] | None, str | None]:
    """Read one verified file and translate expected hostile-state failures."""
    try:
        with backend.open_file(parent_handle, name) as stream:
            return (
                _read_stream(
                    stream,
                    projection=projection,
                    maximum_input_bytes=maximum_input_bytes,
                    budget=budget,
                ),
                None,
            )
    except FileNotFoundError as error:
        return (None, None) if missing_ok else (None, str(error))
    except NotRegularFileError:
        return None, f"expected a regular {description} file"
    except MemoryError:
        return None, "workflow-store JSON exceeds available memory"
    except (
        OSError,
        UnicodeError,
        ValueError,
        RecursionError,
    ) as error:
        return None, str(error)


def read_mapping(
    path: Path,
    *,
    description: str = "JSON",
    directory_fd: int | None = None,
    missing_ok: bool = False,
    budget: ReadWorkBudget | None = None,
    _projection: ProjectionSpec = FULL_PROJECTION,
    _maximum_input_bytes: int = MAX_JSON_BYTES,
) -> tuple[dict[str, object] | None, str | None]:
    """Read a bounded JSON object with per-file diagnostics.

    ``directory_fd`` is a compatibility path for callers that already own a
    native parent capability. Repository code should use ``VerifiedDirectory``.
    """
    backend = backend_for_platform()
    owned_parent: int | None = None
    try:
        parent_handle = directory_fd
        if parent_handle is None:
            owned_parent = backend.open_directory(path.parent)
            parent_handle = owned_parent
        return _read_from_backend(
            backend,
            parent_handle,
            path.name,
            description=description,
            missing_ok=missing_ok,
            budget=budget,
            projection=_projection,
            maximum_input_bytes=_maximum_input_bytes,
        )
    except FileNotFoundError as error:
        return (None, None) if missing_ok else (None, str(error))
    except OSError as error:
        return None, str(error)
    finally:
        if owned_parent is not None:
            backend.close_directory(owned_parent)


def open_directory(path: Path, *, parent_fd: int | None = None) -> int:
    """Compatibility wrapper returning a verified native directory handle."""
    return backend_for_platform().open_directory(path, parent_handle=parent_fd)


def close_directory(descriptor: int) -> None:
    """Compatibility wrapper closing a native directory handle."""
    backend_for_platform().close_directory(descriptor)


def directory_entries(descriptor: int) -> Iterator[tuple[str, bool]]:
    """Compatibility wrapper enumerating a native directory handle."""
    yield from backend_for_platform().directory_entries(descriptor)


def _require_relative_name(name: str) -> None:
    """Reject a name that could escape a verified directory capability."""
    separators = {"/", "\\"} if os.name == "nt" else {"/"}
    if (
        not name
        or name in {".", ".."}
        or any(separator in name for separator in separators)
    ):
        raise ValueError("expected one relative path component")


@dataclass
class VerifiedDirectory:
    """Owned, no-follow directory capability for observational reads.

    Native descriptors and handles remain private. Children are anchored to
    the parent's already-verified capability and retain the same backend.
    """

    path: Path
    _handle: int | None
    _backend: VerifiedStoreBackend
    _enumerated_names: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        parent: VerifiedDirectory | None = None,
    ) -> VerifiedDirectory:
        """Open one directory without following its final component."""
        backend = backend_for_platform() if parent is None else parent._backend
        parent_handle = None if parent is None else parent._active_handle()
        handle = backend.open_directory(path, parent_handle=parent_handle)
        try:
            stored_path = path
            if parent is not None:
                stored_path = path.with_name(backend.directory_name(handle))
            return cls(path=stored_path, _handle=handle, _backend=backend)
        except BaseException:
            backend.close_directory(handle)
            raise

    def __enter__(self) -> VerifiedDirectory:
        """Return this active capability."""
        self._active_handle()
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close the owned native directory capability."""
        self.close()

    def _active_handle(self) -> int:
        """Return the private handle or reject use after close."""
        if self._handle is None:
            raise ValueError("verified directory is closed")
        return self._handle

    def close(self) -> None:
        """Idempotently close the owned directory capability."""
        handle = self._handle
        self._handle = None
        if handle is not None:
            self._backend.close_directory(handle)

    def open_child(self, name: str) -> VerifiedDirectory:
        """Open one child directory relative to this directory."""
        _require_relative_name(name)
        path = self.path / name
        handle = self._backend.open_directory(
            path,
            parent_handle=self._active_handle(),
        )
        try:
            stored_path = path
            if name not in self._enumerated_names:
                stored_path = path.with_name(self._backend.directory_name(handle))
            return type(self)(stored_path, handle, self._backend)
        except BaseException:
            self._backend.close_directory(handle)
            raise

    def entries(self) -> Iterator[tuple[str, bool]]:
        """Enumerate names and no-follow directory classifications."""
        for name, is_directory in self._backend.directory_entries(
            self._active_handle()
        ):
            self._enumerated_names.add(name)
            yield name, is_directory

    def read_mapping(
        self,
        name: str,
        *,
        description: str = "JSON",
        missing_ok: bool = False,
        budget: ReadWorkBudget | None = None,
        _projection: ProjectionSpec = FULL_PROJECTION,
        _maximum_input_bytes: int = MAX_JSON_BYTES,
    ) -> tuple[dict[str, object] | None, str | None]:
        """Read one bounded JSON object relative to this directory."""
        _require_relative_name(name)
        return _read_from_backend(
            self._backend,
            self._active_handle(),
            name,
            description=description,
            missing_ok=missing_ok,
            budget=budget,
            projection=_projection,
            maximum_input_bytes=_maximum_input_bytes,
        )

    def read_state(
        self,
        *,
        budget: ReadWorkBudget | None = None,
    ) -> tuple[dict[str, object] | None, str | None]:
        """Read projected run state under the explicit state policy."""
        active_budget = (
            budget
            if budget is not None
            else ReadWorkBudget(DEFAULT_STATE_READ_BUDGET_BYTES)
        )
        return self.read_mapping(
            "state.json",
            description="state",
            budget=active_budget,
            _projection=STATE_PROJECTION,
            _maximum_input_bytes=MAX_STATE_JSON_BYTES,
        )

    def read_callback(
        self,
        *,
        budget: ReadWorkBudget | None = None,
    ) -> tuple[dict[str, object] | None, str | None]:
        """Read projected callback metadata under its explicit policy."""
        return self.read_mapping(
            "completion-notification.json",
            description="callback metadata",
            missing_ok=True,
            budget=budget,
            _projection=CALLBACK_PROJECTION,
            _maximum_input_bytes=MAX_JSON_BYTES,
        )
