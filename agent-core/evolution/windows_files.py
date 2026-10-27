"""Windows-only exclusive file handles for race-safe workspace CAS operations."""
from __future__ import annotations

import ctypes
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from ctypes import wintypes

FILE_SHARE_NONE = 0
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
CREATE_NEW = 1
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)
_MAX_FILE_BYTES = 64 * 1024 * 1024


class WorkspaceFileError(OSError):
    """Stable failure code used by the workspace broker."""

    def __init__(self, code: str, message: str, *, winerror: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.winerror = winerror


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", _FILETIME),
        ("ftLastAccessTime", _FILETIME),
        ("ftLastWriteTime", _FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _win_api():
    if os.name != "nt":
        raise WorkspaceFileError("unsupported_capability", "CreateFileW exclusive handles require Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    kernel32.GetFileSizeEx.restype = wintypes.BOOL
    kernel32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.SetEndOfFile.argtypes = [wintypes.HANDLE]
    kernel32.SetEndOfFile.restype = wintypes.BOOL
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    return kernel32


def _fail_win(operation: str, error: int | None = None) -> WorkspaceFileError:
    code = ctypes.get_last_error() if error is None else error
    if code in (5, 32, 33, 80, 183):
        stable = "scope_conflict"
    elif operation in ("WriteFile", "SetEndOfFile", "FlushFileBuffers"):
        stable = "outcome_unknown"
    else:
        stable = "scope_conflict"
    return WorkspaceFileError(stable, f"{operation} failed (Win32 error {code})", winerror=code)


def _validate_path(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise WorkspaceFileError("scope_conflict", "exclusive file path must be absolute")
    if ".." in path.parts:
        raise WorkspaceFileError("scope_conflict", "path traversal is not allowed")
    for index, part in enumerate(path.parts):
        if index > 0 and ":" in part:
            raise WorkspaceFileError("scope_conflict", "alternate data streams are not allowed")
        if index > 0 and (part.endswith((" ", ".")) or _RESERVED.match(part)):
            raise WorkspaceFileError("scope_conflict", "reserved or ambiguous Windows path component")
    lexical = Path(os.path.abspath(path))
    cursor = Path(lexical.anchor)
    for component in lexical.parts[1:-1]:
        cursor = cursor / component
        try:
            info = os.lstat(cursor)
        except OSError as exc:
            raise WorkspaceFileError("scope_conflict", "parent directory cannot be verified", winerror=getattr(exc, "winerror", None)) from exc
        attrs = getattr(info, "st_file_attributes", 0)
        is_junction = getattr(cursor, "is_junction", lambda: False)()
        if os.path.islink(cursor) or is_junction or attrs & FILE_ATTRIBUTE_REPARSE_POINT or not cursor.is_dir():
            raise WorkspaceFileError("scope_conflict", "reparse point or non-directory parent is not allowed")
    return lexical


class ExclusiveFile:
    """A single non-shared Win32 handle; all reads and writes use this handle."""

    def __init__(self, handle: int, path: Path) -> None:
        self._handle = handle
        self.path = path
        self._closed = False

    def _check(self) -> None:
        if self._closed:
            raise WorkspaceFileError("scope_conflict", "file handle is closed")

    def read_bytes(self, *, max_bytes: int = _MAX_FILE_BYTES) -> bytes:
        self._check()
        kernel32 = _win_api()
        size = ctypes.c_longlong()
        if not kernel32.GetFileSizeEx(self._handle, ctypes.byref(size)):
            raise _fail_win("GetFileSizeEx")
        if size.value < 0 or size.value > max_bytes:
            raise WorkspaceFileError("budget_exceeded", "file exceeds read limit")
        position = ctypes.c_longlong()
        if not kernel32.SetFilePointerEx(self._handle, ctypes.c_longlong(0), ctypes.byref(position), 0):
            raise _fail_win("SetFilePointerEx")
        remaining = size.value
        result = bytearray()
        while remaining:
            amount = min(remaining, 1024 * 1024)
            buffer = ctypes.create_string_buffer(amount)
            read = wintypes.DWORD()
            if not kernel32.ReadFile(self._handle, buffer, amount, ctypes.byref(read), None):
                raise _fail_win("ReadFile")
            if read.value == 0:
                raise WorkspaceFileError("scope_conflict", "file length changed during exclusive read")
            result.extend(buffer.raw[:read.value])
            remaining -= read.value
        return bytes(result)

    def write_bytes(self, data: bytes) -> None:
        self._check()
        if not isinstance(data, bytes):
            raise WorkspaceFileError("invalid_request", "write_bytes requires immutable bytes")
        if len(data) > _MAX_FILE_BYTES:
            raise WorkspaceFileError("budget_exceeded", "file exceeds write limit")
        kernel32 = _win_api()
        position = ctypes.c_longlong()
        if not kernel32.SetFilePointerEx(self._handle, ctypes.c_longlong(0), ctypes.byref(position), 0):
            raise _fail_win("SetFilePointerEx")
        offset = 0
        while offset < len(data):
            piece = data[offset : offset + 1024 * 1024]
            buffer = ctypes.create_string_buffer(piece)
            written = wintypes.DWORD()
            if not kernel32.WriteFile(self._handle, buffer, len(piece), ctypes.byref(written), None):
                raise _fail_win("WriteFile")
            if written.value == 0:
                raise WorkspaceFileError("outcome_unknown", "short write made no progress")
            offset += written.value
        if not kernel32.SetEndOfFile(self._handle):
            raise _fail_win("SetEndOfFile")
        self.flush()

    def flush(self) -> None:
        self._check()
        if not _win_api().FlushFileBuffers(self._handle):
            raise _fail_win("FlushFileBuffers")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not _win_api().CloseHandle(self._handle):
            raise _fail_win("CloseHandle")

    def __enter__(self) -> "ExclusiveFile":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


@contextmanager
def exclusive_file(path: Path, create: bool = False) -> Iterator[ExclusiveFile]:
    """Open an existing file or create a new one with dwShareMode=0."""
    if not isinstance(create, bool):
        raise WorkspaceFileError("invalid_request", "create must be boolean")
    target = _validate_path(path)
    kernel32 = _win_api()
    creation = CREATE_NEW if create else OPEN_EXISTING
    handle = kernel32.CreateFileW(str(target), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_NONE, None, creation,
                                  FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle is None or handle == _INVALID_HANDLE:
        raise _fail_win("CreateFileW")
    try:
        info = _BY_HANDLE_FILE_INFORMATION()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise _fail_win("GetFileInformationByHandle")
        if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise WorkspaceFileError("scope_conflict", "final path is a reparse point")
        yield ExclusiveFile(handle, target)
    finally:
        kernel32.CloseHandle(handle)
