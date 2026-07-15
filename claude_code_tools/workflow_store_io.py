"""Race-safe, read-only access to durable workflow store files."""

from __future__ import annotations

import codecs
import ctypes
import errno
import importlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Any, BinaryIO, NoReturn, cast

MAX_JSON_BYTES = 8 * 1024 * 1024
# A run can contain 1,000 one-megabyte results, duplicate them in its final
# result, and expand control characters sixfold when JSON-escaped. The retained
# projection remains capped at MAX_JSON_BYTES; this is only a raw-work ceiling.
MAX_STATE_JSON_BYTES = 16 * 1024 * 1024 * 1024
MAX_PROJECTED_JSON_NODES = 250_000
_READ_CHUNK_BYTES = 64 * 1024
_PROJECTION_CHUNK_BYTES = 64 * 1024
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_READ_ATTRIBUTES = 0x80
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_FILE_SHARE_READ = 1
_FILE_SHARE_WRITE = 2
_FILE_SHARE_DELETE = 4
_FILE_ATTRIBUTE_TAG_INFO = 9
_FILE_ID_BOTH_DIRECTORY_INFO = 10
_FILE_ID_BOTH_DIRECTORY_RESTART_INFO = 11
_FILE_LIST_DIRECTORY = 0x1
_FILE_TRAVERSE = 0x20
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_NON_DIRECTORY_FILE = 0x00000040
_FILE_OPEN = 1
_FILE_OPEN_REPARSE_POINT = 0x00200000
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_OBJECT_CASE_INSENSITIVE = 0x00000040
_SYNCHRONIZE = 0x00100000


class ReadBudgetExceeded(RuntimeError):
    """Raised when a workflow-store scan exhausts its aggregate work budget."""


@dataclass
class ReadWorkBudget:
    """Aggregate byte budget shared by all JSON reads in one observation."""

    maximum_bytes: int
    consumed_bytes: int = 0
    limit_exceeded: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Reject nonsensical aggregate limits."""
        if self.maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        if self.consumed_bytes < 0 or self.consumed_bytes > self.maximum_bytes:
            raise ValueError("consumed_bytes must be within the aggregate limit")

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


class _WinFileAttributeTagInfo(ctypes.Structure):
    """Win32 file attributes and reparse tag for an opened handle."""

    _fields_ = [
        ("FileAttributes", ctypes.c_uint32),
        ("ReparseTag", ctypes.c_uint32),
    ]


class _WinUnicodeString(ctypes.Structure):
    """Native counted Unicode string used for handle-relative opens."""

    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_wchar_p),
    ]


class _WinObjectAttributes(ctypes.Structure):
    """Native object attributes with an anchoring root-directory handle."""

    _fields_ = [
        ("Length", ctypes.c_ulong),
        ("RootDirectory", ctypes.c_void_p),
        ("ObjectName", ctypes.POINTER(_WinUnicodeString)),
        ("Attributes", ctypes.c_ulong),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _WinIoStatusBlock(ctypes.Structure):
    """Native I/O result storage required by ``NtCreateFile``."""

    _fields_ = [
        ("Status", ctypes.c_void_p),
        ("Information", ctypes.c_size_t),
    ]


class _WinFileIdBothDirectoryInfo(ctypes.Structure):
    """Fixed header for one handle-relative Windows directory entry."""

    _fields_ = [
        ("NextEntryOffset", ctypes.c_uint32),
        ("FileIndex", ctypes.c_uint32),
        ("CreationTime", ctypes.c_int64),
        ("LastAccessTime", ctypes.c_int64),
        ("LastWriteTime", ctypes.c_int64),
        ("ChangeTime", ctypes.c_int64),
        ("EndOfFile", ctypes.c_int64),
        ("AllocationSize", ctypes.c_int64),
        ("FileAttributes", ctypes.c_uint32),
        ("FileNameLength", ctypes.c_uint32),
        ("EaSize", ctypes.c_uint32),
        ("ShortNameLength", ctypes.c_byte),
        ("ShortName", ctypes.c_wchar * 12),
        ("FileId", ctypes.c_int64),
    ]


class _CharacterStream:
    """Incrementally decode UTF-8 while accounting for raw bytes read."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        maximum_bytes: int,
        budget: ReadWorkBudget | None,
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._budget = budget
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._buffer = ""
        self._position = 0
        self._raw_bytes = 0
        self._finished = False

    def _fill(self) -> bool:
        """Decode another nonempty character chunk, if one remains."""
        while self._position >= len(self._buffer) and not self._finished:
            read_size = _READ_CHUNK_BYTES
            if self._budget is not None:
                read_size = min(
                    read_size,
                    max(1, self._budget.remaining_bytes + 1),
                )
            raw = self._stream.read(read_size)
            if raw:
                self._raw_bytes += len(raw)
                if self._raw_bytes > self._maximum_bytes:
                    raise ValueError(
                        f"JSON file exceeds {self._maximum_bytes} bytes"
                    )
                if self._budget is not None:
                    self._budget.charge(len(raw))
                self._buffer = self._decoder.decode(raw, final=False)
                self._position = 0
            else:
                self._buffer = self._decoder.decode(b"", final=True)
                self._position = 0
                self._finished = True
        return self._position < len(self._buffer)

    def peek(self) -> str | None:
        """Return the next decoded character without consuming it."""
        return self._buffer[self._position] if self._fill() else None

    def take(self) -> str:
        """Consume and return one decoded character."""
        value = self.peek()
        if value is None:
            raise ValueError("unexpected end of JSON input")
        self._position += 1
        return value


class _ProjectionWriter:
    """Collect only the bounded JSON projection retained by the dashboard."""

    def __init__(self, maximum_bytes: int) -> None:
        self._maximum_bytes = maximum_bytes
        self._byte_count = 0
        self._chunks: list[bytes] = []
        self._pending = bytearray()

    def _flush(self) -> None:
        """Move one bounded pending chunk into the completed chunk list."""
        if self._pending:
            self._chunks.append(bytes(self._pending))
            self._pending.clear()

    def append(self, value: str) -> None:
        """Append text unless doing so would exceed the retained-data cap."""
        encoded = value.encode("utf-8")
        next_byte_count = self._byte_count + len(encoded)
        if next_byte_count > self._maximum_bytes:
            raise ValueError(
                "projected JSON object exceeds the retained-data limit of "
                f"{self._maximum_bytes} bytes"
            )
        self._byte_count = next_byte_count
        position = 0
        while position < len(encoded):
            available = _PROJECTION_CHUNK_BYTES - len(self._pending)
            end = min(position + available, len(encoded))
            self._pending.extend(encoded[position:end])
            position = end
            if len(self._pending) == _PROJECTION_CHUNK_BYTES:
                self._flush()

    def value(self) -> str:
        """Return the completed projected JSON text."""
        self._flush()
        return b"".join(self._chunks).decode("utf-8")


@dataclass
class _ProjectionNodeBudget:
    """Bound objects created when the retained projection is decoded."""

    maximum_nodes: int
    consumed_nodes: int = 0

    def charge(self) -> None:
        """Account for one retained JSON key or value."""
        self.consumed_nodes += 1
        if self.consumed_nodes > self.maximum_nodes:
            raise ValueError(
                "projected JSON object exceeds the structural limit of "
                f"{self.maximum_nodes} nodes"
            )


def _copy_whitespace(
    source: _CharacterStream,
    destination: _ProjectionWriter | None,
) -> None:
    """Consume JSON whitespace and optionally retain it."""
    while (value := source.peek()) is not None and value in " \t\r\n":
        value = source.take()
        if destination is not None:
            destination.append(value)


def _expect(source: _CharacterStream, expected: str) -> None:
    """Consume one required JSON punctuation character."""
    observed = source.take()
    if observed != expected:
        raise ValueError(f"expected {expected!r}, got {observed!r}")


def _parse_string(
    source: _CharacterStream,
    destination: _ProjectionWriter | None,
    *,
    capture: bool = False,
) -> str | None:
    """Parse one JSON string without retaining omitted payload contents."""
    _expect(source, '"')
    captured = _ProjectionWriter(MAX_JSON_BYTES) if capture else None
    if captured is not None:
        captured.append('"')
    if destination is not None:
        destination.append('"')
    while True:
        value = source.take()
        if ord(value) < 0x20:
            raise ValueError("unescaped control character in JSON string")
        if destination is not None:
            destination.append(value)
        if captured is not None:
            captured.append(value)
        if value == '"':
            break
        if value != "\\":
            continue
        escaped = source.take()
        if escaped not in '"\\/bfnrtu':
            raise ValueError(f"invalid JSON escape {escaped!r}")
        if destination is not None:
            destination.append(escaped)
        if captured is not None:
            captured.append(escaped)
        if escaped == "u":
            for _ in range(4):
                digit = source.take()
                if digit not in "0123456789abcdefABCDEF":
                    raise ValueError("invalid Unicode escape in JSON string")
                if destination is not None:
                    destination.append(digit)
                if captured is not None:
                    captured.append(digit)
    if captured is None:
        return None
    decoded: Any = json.loads(captured.value())
    return cast(str, decoded)


def _parse_scalar(
    source: _CharacterStream,
    destination: _ProjectionWriter | None,
) -> None:
    """Parse a JSON number, boolean, or null token."""
    pieces: list[str] = []
    while (value := source.peek()) is not None and value not in " \t\r\n,]}":
        pieces.append(source.take())
        if len(pieces) > 4_096:
            raise ValueError("JSON scalar token exceeds 4096 characters")
    token = "".join(pieces)
    if not token:
        raise ValueError("expected a JSON value")
    json.loads(token)
    if destination is not None:
        destination.append(token)


def _parse_array(
    source: _CharacterStream,
    destination: _ProjectionWriter | None,
    nodes: _ProjectionNodeBudget,
) -> None:
    """Parse a JSON array, retaining it only when requested."""
    _expect(source, "[")
    if destination is not None:
        destination.append("[")
    _copy_whitespace(source, destination)
    if source.peek() == "]":
        source.take()
        if destination is not None:
            destination.append("]")
        return
    while True:
        _parse_value(source, destination, nodes)
        _copy_whitespace(source, destination)
        separator = source.take()
        if separator == "]":
            if destination is not None:
                destination.append("]")
            return
        if separator != ",":
            raise ValueError(f"expected ',' or ']', got {separator!r}")
        if destination is not None:
            destination.append(",")
        _copy_whitespace(source, destination)


def _parse_object(
    source: _CharacterStream,
    destination: _ProjectionWriter | None,
    nodes: _ProjectionNodeBudget,
) -> None:
    """Parse an object and replace retained ``result`` values with null."""
    _expect(source, "{")
    if destination is not None:
        destination.append("{")
    _copy_whitespace(source, destination)
    if source.peek() == "}":
        source.take()
        if destination is not None:
            destination.append("}")
        return
    while True:
        if destination is not None:
            nodes.charge()
        key = _parse_string(
            source,
            destination,
            capture=destination is not None,
        )
        _copy_whitespace(source, destination)
        _expect(source, ":")
        if destination is not None:
            destination.append(":")
        _copy_whitespace(source, destination)
        if destination is not None and key == "result":
            nodes.charge()
            _parse_value(source, None, nodes)
            destination.append("null")
        else:
            _parse_value(source, destination, nodes)
        _copy_whitespace(source, destination)
        separator = source.take()
        if separator == "}":
            if destination is not None:
                destination.append("}")
            return
        if separator != ",":
            raise ValueError(f"expected ',' or '}}', got {separator!r}")
        if destination is not None:
            destination.append(",")
        _copy_whitespace(source, destination)


def _parse_value(
    source: _CharacterStream,
    destination: _ProjectionWriter | None,
    nodes: _ProjectionNodeBudget,
) -> None:
    """Parse one complete JSON value."""
    if destination is not None:
        nodes.charge()
    value = source.peek()
    if value == "{":
        _parse_object(source, destination, nodes)
    elif value == "[":
        _parse_array(source, destination, nodes)
    elif value == '"':
        _parse_string(source, destination)
    else:
        _parse_scalar(source, destination)


def _project_json_results(
    stream: BinaryIO,
    *,
    budget: ReadWorkBudget | None,
) -> dict[str, object]:
    """Decode a state object while discarding every unused result payload."""
    source = _CharacterStream(
        stream,
        maximum_bytes=MAX_STATE_JSON_BYTES,
        budget=budget,
    )
    destination = _ProjectionWriter(MAX_JSON_BYTES)
    nodes = _ProjectionNodeBudget(MAX_PROJECTED_JSON_NODES)
    _copy_whitespace(source, destination)
    _parse_value(source, destination, nodes)
    _copy_whitespace(source, destination)
    if source.peek() is not None:
        raise ValueError("extra data after JSON object")
    value: Any = json.loads(destination.value())
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _safe_directory_open_supported() -> bool:
    """Return whether descriptor-relative no-follow opens are available."""
    return (
        os.open in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )


def _windows_last_error() -> int:
    """Return the calling thread's last Win32 error code."""
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def _windows_os_error(message: str) -> NoReturn:
    """Raise an OSError containing the current Win32 failure code."""
    error_code = _windows_last_error()
    detail = f"{message} (Win32 error {error_code})"
    if error_code in {2, 3}:
        raise FileNotFoundError(errno.ENOENT, detail)
    if error_code == 5:
        raise PermissionError(errno.EACCES, detail)
    raise OSError(errno.EIO, detail)


def _windows_kernel32() -> object:
    """Load trusted Win32 file APIs."""
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Win32 APIs are unavailable")
    return loader("kernel32", use_last_error=True)


def _windows_create_verified_handle(
    path: Path,
    *,
    expect_directory: bool,
) -> tuple[object, int]:
    """Open one path without following its final reparse point."""
    kernel32 = _windows_kernel32()
    create_file = getattr(kernel32, "CreateFileW")
    get_info = getattr(kernel32, "GetFileInformationByHandleEx")
    close_handle = getattr(kernel32, "CloseHandle")
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    get_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_info.restype = ctypes.c_int
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    access = (
        _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _FILE_TRAVERSE
        if expect_directory
        else _GENERIC_READ
    )
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if expect_directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(
        str(path),
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle in {None, 0, _INVALID_HANDLE_VALUE}:
        _windows_os_error(f"cannot open {path}")
    info = _WinFileAttributeTagInfo()
    if not get_info(
        handle,
        _FILE_ATTRIBUTE_TAG_INFO,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        close_handle(handle)
        _windows_os_error(f"cannot inspect {path}")
    if info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        close_handle(handle)
        raise OSError(errno.ELOOP, "workflow-store reparse point rejected", path)
    is_directory = bool(info.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != expect_directory:
        close_handle(handle)
        expected = "directory" if expect_directory else "regular file"
        raise OSError(errno.EINVAL, f"expected a {expected}", path)
    return kernel32, cast(int, handle)


def _windows_create_verified_relative_handle(
    parent_handle: int,
    target: str,
    *,
    expect_directory: bool,
) -> tuple[object, int]:
    """Open a child object relative to a verified directory handle."""
    if not target or target in {".", ".."} or "\\" in target or "/" in target:
        raise OSError(errno.EINVAL, "expected one relative path component")
    kernel32 = _windows_kernel32()
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Win32 native APIs are unavailable")
    ntdll = loader("ntdll", use_last_error=True)
    nt_create_file = getattr(ntdll, "NtCreateFile")
    nt_create_file.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(_WinObjectAttributes),
        ctypes.POINTER(_WinIoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    nt_create_file.restype = ctypes.c_long
    target_buffer = ctypes.create_unicode_buffer(target)
    target_bytes = len(target.encode("utf-16-le"))
    object_name = _WinUnicodeString(
        Length=target_bytes,
        MaximumLength=target_bytes + 2,
        Buffer=ctypes.cast(target_buffer, ctypes.c_wchar_p),
    )
    attributes = _WinObjectAttributes(
        Length=ctypes.sizeof(_WinObjectAttributes),
        RootDirectory=parent_handle,
        ObjectName=ctypes.pointer(object_name),
        Attributes=_OBJECT_CASE_INSENSITIVE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    io_status = _WinIoStatusBlock()
    handle = ctypes.c_void_p()
    options = _FILE_OPEN_REPARSE_POINT | _FILE_SYNCHRONOUS_IO_NONALERT
    options |= _FILE_DIRECTORY_FILE if expect_directory else _FILE_NON_DIRECTORY_FILE
    access = _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
    if expect_directory:
        access |= _FILE_LIST_DIRECTORY | _FILE_TRAVERSE
    else:
        access |= _GENERIC_READ
    status = int(
        nt_create_file(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            _FILE_OPEN,
            options,
            None,
            0,
        )
    )
    if status < 0 or handle.value in {None, 0, _INVALID_HANDLE_VALUE}:
        unsigned_status = status & 0xFFFFFFFF
        if unsigned_status in {0xC0000034, 0xC000003A}:
            raise FileNotFoundError(
                errno.ENOENT,
                f"cannot open {target} (NTSTATUS {unsigned_status:#010x})",
            )
        if unsigned_status == 0xC0000022:
            raise PermissionError(
                errno.EACCES,
                f"cannot open {target} (NTSTATUS {unsigned_status:#010x})",
            )
        raise OSError(
            errno.EIO,
            f"cannot open {target} (NTSTATUS {unsigned_status:#010x})",
        )
    native_handle = cast(int, handle.value)
    get_info = getattr(kernel32, "GetFileInformationByHandleEx")
    get_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_info.restype = ctypes.c_int
    info = _WinFileAttributeTagInfo()
    if not get_info(
        native_handle,
        _FILE_ATTRIBUTE_TAG_INFO,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        _close_windows_handle(kernel32, native_handle)
        _windows_os_error(f"cannot inspect {target}")
    if info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        _close_windows_handle(kernel32, native_handle)
        raise OSError(
            errno.ELOOP,
            "workflow-store reparse point rejected",
            target,
        )
    is_directory = bool(info.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != expect_directory:
        _close_windows_handle(kernel32, native_handle)
        expected = "directory" if expect_directory else "regular file"
        raise OSError(errno.EINVAL, f"expected a {expected}", target)
    return kernel32, native_handle


def _close_windows_handle(kernel32: object, handle: int) -> None:
    """Close one verified native handle."""
    close_handle = getattr(kernel32, "CloseHandle")
    close_handle(handle)


def _windows_stream_for_handle(kernel32: object, handle: int) -> BinaryIO:
    """Transfer an owned native file handle into a binary Python stream."""
    descriptor = -1
    try:
        msvcrt = importlib.import_module("msvcrt")
        open_osfhandle = getattr(msvcrt, "open_osfhandle")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        descriptor = int(open_osfhandle(handle, flags))
    except (ImportError, OSError, TypeError, ValueError):
        _close_windows_handle(kernel32, handle)
        raise
    try:
        return cast(BinaryIO, os.fdopen(descriptor, "rb"))
    except (OSError, TypeError, ValueError):
        if descriptor >= 0:
            os.close(descriptor)
        raise


def close_directory(descriptor: int) -> None:
    """Close a directory descriptor or native Windows directory handle."""
    if os.name == "nt":
        _close_windows_handle(_windows_kernel32(), descriptor)
    else:
        os.close(descriptor)


def _windows_directory_entries(
    descriptor: int,
) -> Iterator[tuple[str, bool]]:
    """Enumerate directory names and types through a verified native handle."""
    kernel32 = _windows_kernel32()
    get_info = getattr(kernel32, "GetFileInformationByHandleEx")
    get_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_info.restype = ctypes.c_int
    info_class = _FILE_ID_BOTH_DIRECTORY_RESTART_INFO
    header_bytes = ctypes.sizeof(_WinFileIdBothDirectoryInfo)
    buffer_bytes = _READ_CHUNK_BYTES
    while True:
        buffer = ctypes.create_string_buffer(buffer_bytes)
        if not get_info(descriptor, info_class, buffer, buffer_bytes):
            if _windows_last_error() == 18:
                return
            _windows_os_error("cannot enumerate workflow-store directory")
        info_class = _FILE_ID_BOTH_DIRECTORY_INFO
        offset = 0
        while True:
            if offset + header_bytes > buffer_bytes:
                raise OSError(errno.EIO, "invalid Windows directory entry")
            address = ctypes.addressof(buffer) + offset
            info = ctypes.cast(
                address,
                ctypes.POINTER(_WinFileIdBothDirectoryInfo),
            ).contents
            name_bytes = int(info.FileNameLength)
            name_end = offset + header_bytes + name_bytes
            if name_bytes % 2 != 0 or name_end > buffer_bytes:
                raise OSError(errno.EIO, "invalid Windows directory name")
            name = ctypes.wstring_at(address + header_bytes, name_bytes // 2)
            attributes = int(info.FileAttributes)
            is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
            is_reparse = bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
            if name not in {".", ".."}:
                yield name, is_directory and not is_reparse
            next_offset = int(info.NextEntryOffset)
            if next_offset == 0:
                break
            if next_offset < header_bytes or offset + next_offset >= buffer_bytes:
                raise OSError(errno.EIO, "invalid Windows directory offset")
            offset += next_offset


def directory_entries(descriptor: int) -> Iterator[tuple[str, bool]]:
    """Enumerate entries through the platform's verified directory handle."""
    if os.name == "nt":
        yield from _windows_directory_entries(descriptor)
        return
    with os.scandir(descriptor) as entries:
        for entry in entries:
            yield entry.name, entry.is_dir(follow_symlinks=False)


def _read_stream(
    stream: BinaryIO,
    *,
    omit_result_payloads: bool,
    budget: ReadWorkBudget | None,
) -> dict[str, object]:
    """Read one object using either the bounded projection or normal decoder."""
    if omit_result_payloads:
        return _project_json_results(stream, budget=budget)
    read_size = MAX_JSON_BYTES + 1
    if budget is not None:
        read_size = min(read_size, max(1, budget.remaining_bytes + 1))
    raw = stream.read(read_size)
    if budget is not None:
        budget.charge(len(raw))
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError(f"JSON file exceeds {MAX_JSON_BYTES} bytes")
    value: Any = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def read_mapping(
    path: Path,
    *,
    description: str = "JSON",
    directory_fd: int | None = None,
    missing_ok: bool = False,
    omit_result_payloads: bool = False,
    budget: ReadWorkBudget | None = None,
) -> tuple[dict[str, object] | None, str | None]:
    """Read a bounded JSON object with per-file diagnostics.

    Args:
        path: JSON file to read.
        description: Human-readable kind of JSON file for diagnostics.
        directory_fd: Verified POSIX parent-directory descriptor.
        missing_ok: Whether an absent optional file is valid.
        omit_result_payloads: Stream state while replacing unused ``result``
            fields with null, bounding retained memory for valid large states.
        budget: Optional aggregate byte budget shared across a store scan.

    Returns:
        The decoded mapping and no error, or no mapping and an error message.
    """
    descriptor = -1
    owned_directory_fd: int | None = None
    try:
        if os.name == "nt":
            if directory_fd is None:
                owned_directory_fd = open_directory(path.parent)
                directory_fd = owned_directory_fd
            if directory_fd is None:
                raise OSError(
                    errno.ENOTSUP,
                    "missing verified directory handle",
                )
            kernel32, handle = _windows_create_verified_relative_handle(
                directory_fd,
                path.name,
                expect_directory=False,
            )
            with _windows_stream_for_handle(kernel32, handle) as stream:
                return (
                    _read_stream(
                        stream,
                        omit_result_payloads=omit_result_payloads,
                        budget=budget,
                    ),
                    None,
                )
        if not _safe_directory_open_supported():
            raise OSError(
                errno.ENOTSUP,
                "race-safe workflow-store inspection is unavailable",
                str(path),
            )
        if directory_fd is None:
            owned_directory_fd = open_directory(path.parent)
            directory_fd = owned_directory_fd
        if directory_fd is None:
            raise OSError(errno.ENOTSUP, "missing verified directory descriptor")
        target = path.name
        mode = os.stat(
            target,
            dir_fd=directory_fd,
            follow_symlinks=False,
        ).st_mode
        if not S_ISREG(mode):
            return None, f"expected a regular {description} file"
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | os.O_NOFOLLOW
        descriptor = os.open(target, flags, dir_fd=directory_fd)
        if not S_ISREG(os.fstat(descriptor).st_mode):
            return None, f"expected a regular {description} file"
        with os.fdopen(descriptor, "rb") as raw_stream:
            descriptor = -1
            stream = cast(BinaryIO, raw_stream)
            return (
                _read_stream(
                    stream,
                    omit_result_payloads=omit_result_payloads,
                    budget=budget,
                ),
                None,
            )
    except FileNotFoundError as error:
        return (None, None) if missing_ok else (None, str(error))
    except (
        OSError,
        UnicodeError,
        ValueError,
        RecursionError,
        ReadBudgetExceeded,
    ) as error:
        return None, str(error)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if owned_directory_fd is not None:
            close_directory(owned_directory_fd)


def open_directory(path: Path, *, parent_fd: int | None = None) -> int | None:
    """Open a directory without following its final path component.

    Args:
        path: Directory path, or child path when ``parent_fd`` is supplied.
        parent_fd: Verified POSIX parent-directory descriptor.

    Returns:
        An open POSIX directory descriptor.

    Raises:
        OSError: The directory cannot be opened safely or does not exist.
    """
    if os.name == "nt":
        if parent_fd is None:
            _, handle = _windows_create_verified_handle(
                path,
                expect_directory=True,
            )
        else:
            _, handle = _windows_create_verified_relative_handle(
                parent_fd,
                path.name,
                expect_directory=True,
            )
        return handle
    if not _safe_directory_open_supported():
        raise OSError(
            errno.ENOTSUP,
            "race-safe workflow-store inspection is unavailable",
            str(path),
        )
    target: str | Path = path.name if parent_fd is not None else path
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if parent_fd is None:
        descriptor = os.open(target, flags)
    else:
        descriptor = os.open(target, flags, dir_fd=parent_fd)
    if not S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise NotADirectoryError(path)
    return descriptor
