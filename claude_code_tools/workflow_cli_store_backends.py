"""Platform backends for verified, read-only workflow-store access."""

from __future__ import annotations

import ctypes
import errno
import importlib
import ntpath
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import BinaryIO, ContextManager, NoReturn, Protocol, cast

READ_CHUNK_BYTES = 64 * 1024
MAX_POSIX_DESCRIPTOR_PATH_BYTES = 1_024
MAX_WINDOWS_FINAL_PATH_CHARS = 32_768
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_READ_ATTRIBUTES = 0x80
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
FILE_SHARE_DELETE = 4
FILE_ATTRIBUTE_TAG_INFO = 9
FILE_ID_BOTH_DIRECTORY_INFO = 10
FILE_ID_BOTH_DIRECTORY_RESTART_INFO = 11
FILE_LIST_DIRECTORY = 0x1
FILE_TRAVERSE = 0x20
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_OPEN = 1
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
OBJECT_CASE_INSENSITIVE = 0x00000040
SYNCHRONIZE = 0x00100000


class NotRegularFileError(OSError):
    """Raised when a verified child exists but is not a regular file."""


class VerifiedStoreBackend(Protocol):
    """Native operations required by the verified-directory facade."""

    def open_directory(
        self,
        path: Path,
        *,
        parent_handle: int | None = None,
    ) -> int:
        """Open a no-follow directory capability."""
        ...

    def close_directory(self, handle: int) -> None:
        """Close one owned directory capability."""

    def directory_entries(self, handle: int) -> Iterator[tuple[str, bool]]:
        """Enumerate names and no-follow directory classifications."""
        ...

    def directory_name(self, handle: int) -> str:
        """Return the durable basename of an opened directory."""
        ...

    def open_file(
        self,
        parent_handle: int,
        name: str,
    ) -> ContextManager[BinaryIO]:
        """Yield a verified regular file relative to a directory capability."""
        ...


def posix_safe_open_supported() -> bool:
    """Return whether descriptor-relative no-follow opens are available."""
    return (
        os.open in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )


class PosixStoreBackend:
    """Descriptor-relative POSIX implementation of verified store access."""

    def _require_supported(self, path: Path) -> None:
        if not posix_safe_open_supported():
            raise OSError(
                errno.ENOTSUP,
                "race-safe workflow-store inspection is unavailable",
                str(path),
            )

    def open_directory(
        self,
        path: Path,
        *,
        parent_handle: int | None = None,
    ) -> int:
        """Open a directory without following its final component."""
        self._require_supported(path)
        target: str | Path = path.name if parent_handle is not None else path
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if parent_handle is None:
            descriptor = os.open(target, flags)
        else:
            descriptor = os.open(target, flags, dir_fd=parent_handle)
        try:
            metadata = os.fstat(descriptor)
            mode = metadata.st_mode
        except BaseException:
            os.close(descriptor)
            raise
        if not S_ISDIR(mode):
            os.close(descriptor)
            raise NotADirectoryError(path)
        return descriptor

    def close_directory(self, handle: int) -> None:
        """Close one owned directory descriptor."""
        os.close(handle)

    def directory_entries(self, handle: int) -> Iterator[tuple[str, bool]]:
        """Enumerate entries through an already-verified descriptor."""
        with os.scandir(handle) as entries:
            for entry in entries:
                yield entry.name, entry.is_dir(follow_symlinks=False)

    def directory_name(self, handle: int) -> str:
        """Recover one opened directory's basename without enumeration."""
        try:
            fcntl = importlib.import_module("fcntl")
        except ImportError:
            fcntl = None
        command = None if fcntl is None else getattr(fcntl, "F_GETPATH", None)
        if command is not None:
            raw_path = getattr(fcntl, "fcntl")(
                handle,
                command,
                b"\0" * MAX_POSIX_DESCRIPTOR_PATH_BYTES,
            )
            if not isinstance(raw_path, bytes):
                raise OSError(errno.EIO, "invalid descriptor path response")
            if b"\0" not in raw_path:
                raise OSError(errno.EIO, "unterminated descriptor path response")
            path = os.fsdecode(raw_path.split(b"\0", maxsplit=1)[0])
            if path:
                return os.path.basename(path)
            raise OSError(errno.EIO, "empty descriptor path response")
        for descriptor_root in ("/proc/self/fd", "/dev/fd"):
            try:
                path = os.readlink(f"{descriptor_root}/{handle}")
            except OSError:
                continue
            if path:
                return os.path.basename(path)
        raise OSError(
            errno.ENOTSUP,
            "cannot verify exact workflow run directory spelling",
        )

    @contextmanager
    def open_file(
        self,
        parent_handle: int,
        name: str,
    ) -> Iterator[BinaryIO]:
        """Open one no-follow regular file relative to a directory."""
        self._require_supported(Path(name))
        mode = os.stat(
            name,
            dir_fd=parent_handle,
            follow_symlinks=False,
        ).st_mode
        if not S_ISREG(mode):
            raise NotRegularFileError(errno.EINVAL, "expected a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=parent_handle)
        try:
            if not S_ISREG(os.fstat(descriptor).st_mode):
                raise NotRegularFileError(
                    errno.EINVAL,
                    "expected a regular file",
                )
            with os.fdopen(descriptor, "rb") as raw_stream:
                descriptor = -1
                yield cast(BinaryIO, raw_stream)
        finally:
            if descriptor >= 0:
                os.close(descriptor)


class WinFileAttributeTagInfo(ctypes.Structure):
    """Win32 file attributes and reparse tag for an opened handle."""

    _fields_ = [
        ("FileAttributes", ctypes.c_uint32),
        ("ReparseTag", ctypes.c_uint32),
    ]


class WinUnicodeString(ctypes.Structure):
    """Native counted Unicode string used for handle-relative opens."""

    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_wchar_p),
    ]


class WinObjectAttributes(ctypes.Structure):
    """Native object attributes with an anchoring root-directory handle."""

    _fields_ = [
        ("Length", ctypes.c_ulong),
        ("RootDirectory", ctypes.c_void_p),
        ("ObjectName", ctypes.POINTER(WinUnicodeString)),
        ("Attributes", ctypes.c_ulong),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class WinIoStatusBlock(ctypes.Structure):
    """Native I/O result storage required by ``NtCreateFile``."""

    _fields_ = [
        ("Status", ctypes.c_void_p),
        ("Information", ctypes.c_size_t),
    ]


class WinFileIdBothDirectoryInfo(ctypes.Structure):
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


def windows_last_error() -> int:
    """Return the calling thread's last Win32 error code."""
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def raise_windows_os_error(
    message: str,
    *,
    error_code: int | None = None,
) -> NoReturn:
    """Raise an OSError containing a captured Win32 failure code."""
    code = windows_last_error() if error_code is None else error_code
    detail = f"{message} (Win32 error {code})"
    if code in {2, 3}:
        raise FileNotFoundError(errno.ENOENT, detail)
    if code == 5:
        raise PermissionError(errno.EACCES, detail)
    raise OSError(errno.EIO, detail)


def windows_kernel32() -> object:
    """Load trusted Win32 file APIs."""
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Win32 APIs are unavailable")
    return loader("kernel32", use_last_error=True)


def close_windows_handle(kernel32: object, handle: int) -> None:
    """Close one verified native handle."""
    close_handle = getattr(kernel32, "CloseHandle")
    close_handle(handle)


def create_verified_windows_handle(
    path: Path,
    *,
    expect_directory: bool,
) -> tuple[object, int]:
    """Open one path without following its final reparse point."""
    kernel32 = windows_kernel32()
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
        FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | FILE_TRAVERSE
        if expect_directory
        else GENERIC_READ
    )
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if expect_directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(
        str(path),
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if handle in {None, 0, INVALID_HANDLE_VALUE}:
        raise_windows_os_error(f"cannot open {path}")
    info = WinFileAttributeTagInfo()
    if not get_info(
        handle,
        FILE_ATTRIBUTE_TAG_INFO,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error_code = windows_last_error()
        close_handle(handle)
        raise_windows_os_error(
            f"cannot inspect {path}",
            error_code=error_code,
        )
    if info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        close_handle(handle)
        raise OSError(errno.ELOOP, "workflow-store reparse point rejected", path)
    is_directory = bool(info.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != expect_directory:
        close_handle(handle)
        if expect_directory:
            raise NotADirectoryError(path)
        raise NotRegularFileError(errno.EINVAL, "expected a regular file", path)
    return kernel32, cast(int, handle)


def _ntstatus_error(target: str, status: int) -> NoReturn:
    unsigned_status = status & 0xFFFFFFFF
    detail = f"cannot open {target} (NTSTATUS {unsigned_status:#010x})"
    if unsigned_status in {0xC0000034, 0xC000003A}:
        raise FileNotFoundError(errno.ENOENT, detail)
    if unsigned_status == 0xC0000022:
        raise PermissionError(errno.EACCES, detail)
    raise OSError(errno.EIO, detail)


def create_verified_windows_relative_handle(
    parent_handle: int,
    target: str,
    *,
    expect_directory: bool,
) -> tuple[object, int]:
    """Open a child object relative to a verified directory handle."""
    if not target or target in {".", ".."} or "\\" in target or "/" in target:
        raise OSError(errno.EINVAL, "expected one relative path component")
    kernel32 = windows_kernel32()
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Win32 native APIs are unavailable")
    ntdll = loader("ntdll", use_last_error=True)
    nt_create_file = getattr(ntdll, "NtCreateFile")
    nt_create_file.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(WinObjectAttributes),
        ctypes.POINTER(WinIoStatusBlock),
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
    object_name = WinUnicodeString(
        Length=target_bytes,
        MaximumLength=target_bytes + 2,
        Buffer=ctypes.cast(target_buffer, ctypes.c_wchar_p),
    )
    attributes = WinObjectAttributes(
        Length=ctypes.sizeof(WinObjectAttributes),
        RootDirectory=parent_handle,
        ObjectName=ctypes.pointer(object_name),
        Attributes=OBJECT_CASE_INSENSITIVE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    io_status = WinIoStatusBlock()
    handle = ctypes.c_void_p()
    options = FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT
    options |= FILE_DIRECTORY_FILE if expect_directory else FILE_NON_DIRECTORY_FILE
    access = FILE_READ_ATTRIBUTES | SYNCHRONIZE
    if expect_directory:
        access |= FILE_LIST_DIRECTORY | FILE_TRAVERSE
    else:
        access |= GENERIC_READ
    status = int(
        nt_create_file(
            ctypes.byref(handle),
            access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            FILE_OPEN,
            options,
            None,
            0,
        )
    )
    if status < 0 or handle.value in {None, 0, INVALID_HANDLE_VALUE}:
        _ntstatus_error(target, status)
    native_handle = cast(int, handle.value)
    get_info = getattr(kernel32, "GetFileInformationByHandleEx")
    get_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_info.restype = ctypes.c_int
    info = WinFileAttributeTagInfo()
    if not get_info(
        native_handle,
        FILE_ATTRIBUTE_TAG_INFO,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error_code = windows_last_error()
        close_windows_handle(kernel32, native_handle)
        raise_windows_os_error(
            f"cannot inspect {target}",
            error_code=error_code,
        )
    if info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        close_windows_handle(kernel32, native_handle)
        raise OSError(
            errno.ELOOP,
            "workflow-store reparse point rejected",
            target,
        )
    is_directory = bool(info.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != expect_directory:
        close_windows_handle(kernel32, native_handle)
        if expect_directory:
            raise NotADirectoryError(target)
        raise NotRegularFileError(
            errno.EINVAL,
            "expected a regular file",
            target,
        )
    return kernel32, native_handle


def windows_stream_for_handle(kernel32: object, handle: int) -> BinaryIO:
    """Transfer an owned native file handle into a binary Python stream."""
    descriptor = -1
    try:
        msvcrt = importlib.import_module("msvcrt")
        open_osfhandle = getattr(msvcrt, "open_osfhandle")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        descriptor = int(open_osfhandle(handle, flags))
    except (ImportError, OSError, TypeError, ValueError):
        close_windows_handle(kernel32, handle)
        raise
    try:
        return cast(BinaryIO, os.fdopen(descriptor, "rb"))
    except (OSError, TypeError, ValueError):
        if descriptor >= 0:
            os.close(descriptor)
        raise


def windows_directory_name(handle: int) -> str:
    """Return an opened directory's stored basename without enumeration."""
    kernel32 = windows_kernel32()
    get_final_path = getattr(kernel32, "GetFinalPathNameByHandleW")
    get_final_path.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    get_final_path.restype = ctypes.c_uint32
    capacity = 1_024
    while capacity <= MAX_WINDOWS_FINAL_PATH_CHARS:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = int(get_final_path(handle, buffer, capacity, 0))
        if length == 0:
            raise_windows_os_error("cannot identify workflow run directory")
        if length < capacity:
            name = ntpath.basename(buffer.value.rstrip("\\"))
            if name and name not in {".", ".."}:
                return name
            raise OSError(errno.EIO, "invalid workflow run directory name")
        capacity = length + 1
    raise OSError(
        errno.ENAMETOOLONG,
        "workflow run directory path exceeds the safety limit",
    )


def windows_directory_entries(
    descriptor: int,
) -> Iterator[tuple[str, bool]]:
    """Enumerate directory names and types through a verified native handle."""
    kernel32 = windows_kernel32()
    get_info = getattr(kernel32, "GetFileInformationByHandleEx")
    get_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_info.restype = ctypes.c_int
    info_class = FILE_ID_BOTH_DIRECTORY_RESTART_INFO
    header_bytes = ctypes.sizeof(WinFileIdBothDirectoryInfo)
    buffer_bytes = READ_CHUNK_BYTES
    while True:
        buffer = ctypes.create_string_buffer(buffer_bytes)
        if not get_info(descriptor, info_class, buffer, buffer_bytes):
            if windows_last_error() == 18:
                return
            raise_windows_os_error("cannot enumerate workflow-store directory")
        info_class = FILE_ID_BOTH_DIRECTORY_INFO
        offset = 0
        while True:
            if offset + header_bytes > buffer_bytes:
                raise OSError(errno.EIO, "invalid Windows directory entry")
            address = ctypes.addressof(buffer) + offset
            info = ctypes.cast(
                address,
                ctypes.POINTER(WinFileIdBothDirectoryInfo),
            ).contents
            name_bytes = int(info.FileNameLength)
            name_end = offset + header_bytes + name_bytes
            if name_bytes % 2 != 0 or name_end > buffer_bytes:
                raise OSError(errno.EIO, "invalid Windows directory name")
            name = ctypes.wstring_at(address + header_bytes, name_bytes // 2)
            attributes = int(info.FileAttributes)
            is_directory = bool(attributes & FILE_ATTRIBUTE_DIRECTORY)
            is_reparse = bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
            if name not in {".", ".."}:
                yield name, is_directory and not is_reparse
            next_offset = int(info.NextEntryOffset)
            if next_offset == 0:
                break
            if next_offset < header_bytes or offset + next_offset >= buffer_bytes:
                raise OSError(errno.EIO, "invalid Windows directory offset")
            offset += next_offset


class WindowsStoreBackend:
    """Handle-relative Win32 implementation of verified store access."""

    def open_directory(
        self,
        path: Path,
        *,
        parent_handle: int | None = None,
    ) -> int:
        """Open a directory without following its final component."""
        if parent_handle is None:
            _, handle = create_verified_windows_handle(
                path,
                expect_directory=True,
            )
        else:
            _, handle = create_verified_windows_relative_handle(
                parent_handle,
                path.name,
                expect_directory=True,
            )
        return handle

    def close_directory(self, handle: int) -> None:
        """Close one owned native directory handle."""
        close_windows_handle(windows_kernel32(), handle)

    def directory_entries(self, handle: int) -> Iterator[tuple[str, bool]]:
        """Enumerate entries through an already-verified handle."""
        yield from windows_directory_entries(handle)

    def directory_name(self, handle: int) -> str:
        """Return an opened directory's stored basename."""
        return windows_directory_name(handle)

    @contextmanager
    def open_file(
        self,
        parent_handle: int,
        name: str,
    ) -> Iterator[BinaryIO]:
        """Open one verified regular file relative to a directory."""
        kernel32, handle = create_verified_windows_relative_handle(
            parent_handle,
            name,
            expect_directory=False,
        )
        with windows_stream_for_handle(kernel32, handle) as stream:
            yield stream


POSIX_BACKEND = PosixStoreBackend()
WINDOWS_BACKEND = WindowsStoreBackend()


def backend_for_platform() -> VerifiedStoreBackend:
    """Return the native backend for the observer host."""
    return WINDOWS_BACKEND if os.name == "nt" else POSIX_BACKEND
