"""Bounded point-in-time filesystem manifests for Self-Evolution candidates.

This module reports evidence; it is not an OS sandbox, an atomic filesystem
snapshot, a watcher, a merge authorizer, or a claim that Host boundary coverage
is complete. A successful sweep only says that the bounded traversal observed
no consistency violation while it ran.
"""
from __future__ import annotations

import hashlib
import contextlib
import hmac
import json
import os
import re
import secrets
import stat
import unicodedata
from dataclasses import dataclass, field as dataclass_field, replace as dataclass_replace
from pathlib import Path
from typing import Any

SCHEMA = "self_evolution_tree_manifest_v1"
MAX_ENTRIES = 100_000
MAX_PATH_BYTES = 4096
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_DEPTH = 128
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_SEAL_KEY = secrets.token_bytes(32)


class ManifestError(ValueError):
    """A tree cannot be safely or consistently represented by this sweep."""


@dataclass(frozen=True)
class TreeEntry:
    path: str
    kind: str
    mode: int
    size: int
    sha256: str | None
    device: int
    file_id: int
    link_count: int

    def payload(self) -> dict[str, Any]:
        return {
            "path": self.path, "kind": self.kind, "mode": self.mode,
            "size": self.size, "sha256": self.sha256,
            "device": self.device, "file_id": self.file_id,
            "link_count": self.link_count,
        }


@dataclass(frozen=True)
class TreeManifest:
    root: str = dataclass_field(repr=False)
    # Within-sweep handle comparison only; not durable or cross-volume proof.
    root_identity: tuple[int, int]
    entries: tuple[TreeEntry, ...]
    digest: str
    entry_count: int
    file_bytes: int
    excluded_metadata: tuple[dict[str, Any], ...]
    scope: str = "candidate_default_streams_point_in_time_excluding_git_metadata_subtrees"
    identity_scope: str = "held_handle_observation_only_not_durable_or_cross_volume_identity_proof"
    # A completed sweep is not an atomic filesystem snapshot.
    sweep_coverage_complete: bool = False
    snapshot_coverage_complete: bool = False
    full_candidate_coverage: bool = False
    host_coverage_claim_permitted: bool = False
    ignored_item_policy: str = "include_all_present_files_without_git_ignore_filtering"
    ignore_policy_files: tuple[str, ...] = ()
    _capture_seal: str = dataclass_field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class TreeBaseline:
    stable: TreeManifest
    candidate: TreeManifest
    _seal: str


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def validate_relative_path(path: str) -> str:
    if (not isinstance(path, str) or not path or "\\" in path or
            any(unicodedata.category(character).startswith("C") for character in path)):
        raise ManifestError("invalid_relative_path")
    if path.startswith("/") or _DRIVE_PREFIX.match(path):
        raise ManifestError("invalid_relative_path")
    try:
        encoded = path.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ManifestError("invalid_relative_path") from exc
    if len(encoded) > MAX_PATH_BYTES:
        raise ManifestError("path_too_long")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ManifestError("invalid_relative_path")
    for part in parts:
        if unicodedata.normalize("NFC", part) != part:
            raise ManifestError("noncanonical_unicode_path")
        if part.endswith((".", " ")) or ":" in part:
            raise ManifestError("windows_path_alias_forbidden")
        if part.casefold() == ".git":
            raise ManifestError("nested_git_metadata_forbidden")
        stem = part.split(".", 1)[0].casefold()
        if stem in {"con", "prn", "aux", "nul"} or re.fullmatch(r"(?:com|lpt)[1-9¹²³]", stem):
            raise ManifestError("windows_path_alias_forbidden")
    return path


def _identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _safe_root(path: os.PathLike[str] | str) -> tuple[Path, tuple[int, int]]:
    supplied = Path(path).expanduser()
    if not supplied.is_absolute():
        raise ManifestError("root_must_be_absolute")
    if any(part in (".", "..") or part.endswith((".", " ")) for part in supplied.parts[1:]):
        raise ManifestError("root_path_alias_forbidden")
    # Check every component so an ancestor junction/symlink is not silently
    # canonicalized into a different trusted root.
    current = Path(supplied.anchor)
    for part in supplied.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise ManifestError("root_unavailable") from exc
        if _is_reparse(info):
            raise ManifestError("root_link_or_reparse_point")
    try:
        info = supplied.lstat()
    except OSError as exc:
        raise ManifestError("root_unavailable") from exc
    if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise ManifestError("root_not_a_safe_directory")
    return supplied, _identity(info)


def _entry_names(directory: Path) -> tuple[str, ...]:
    try:
        with os.scandir(directory) as iterator:
            names = tuple(sorted((item.name for item in iterator), key=lambda x: (x.casefold(), x)))
    except OSError as exc:
        raise ManifestError("directory_enumeration_failed") from exc
    folded: set[str] = set()
    for name in names:
        if name.casefold() != ".git":
            validate_relative_path(name)
        key = unicodedata.normalize("NFC", name).casefold()
        if key in folded:
            raise ManifestError("windows_path_alias_detected")
        folded.add(key)
    return names


def _file_digest(path: Path, after: os.stat_result, budget: list[int]) -> tuple[int, str, os.stat_result]:
    if after.st_size > MAX_FILE_BYTES or budget[0] + after.st_size > MAX_TOTAL_BYTES:
        raise ManifestError("size_limit_exceeded")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ManifestError("file_open_failed_or_reparse_race") from exc
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if _is_reparse(opened) or not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(after):
            raise ManifestError("file_identity_changed_during_sweep")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES or budget[0] + size > MAX_TOTAL_BYTES:
                raise ManifestError("size_limit_exceeded")
            digest.update(chunk)
        after = os.fstat(handle.fileno())
        if (_identity(after), after.st_size, after.st_mtime_ns) != (_identity(opened), size, opened.st_mtime_ns):
            raise ManifestError("file_changed_during_sweep")
        budget[0] += size
        return size, digest.hexdigest(), after


def _metadata_entry(path: Path, info: os.stat_result, budget: list[int]) -> dict[str, Any]:
    # .git is deliberately not traversed: it is Git metadata, not candidate
    # source. Bind a worktree pointer file's bytes; disclose that directories
    # and their descendants are excluded and must be checked separately.
    metadata: dict[str, Any] = {
        "path": ".git", "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
        "mode": stat.S_IMODE(info.st_mode), "device": int(info.st_dev),
        "file_id": int(info.st_ino), "size": int(info.st_size), "sha256": None,
    }
    if stat.S_ISREG(info.st_mode):
        size, digest, after = _file_digest(path, info, budget)
        metadata.update({"size": size, "sha256": digest, "file_id": int(after.st_ino)})
    elif not stat.S_ISDIR(info.st_mode):
        raise ManifestError("unsupported_git_metadata_entry")
    return metadata


def _manifest_payload(root_identity: tuple[int, int], entries: tuple[TreeEntry, ...],
                      excluded: tuple[dict[str, Any], ...], ignore_files: tuple[str, ...]) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "root_identity": root_identity,
        "identity_scope": "held_handle_observation_only_not_durable_or_cross_volume_identity_proof",
        "coverage_scope": "candidate_default_streams_point_in_time_excluding_git_metadata_subtrees",
        "entries": [entry.payload() for entry in entries],
        "excluded_metadata": sorted((dict(item) for item in excluded), key=lambda item: item["path"]),
        "ignore_policy_files": ignore_files,
    }


def _computed_manifest_digest(manifest: TreeManifest) -> str:
    payload = _manifest_payload(
        manifest.root_identity, manifest.entries, manifest.excluded_metadata, manifest.ignore_policy_files
    )
    payload.update({
        "entry_count": manifest.entry_count, "file_bytes": manifest.file_bytes,
        "scope": manifest.scope, "identity_scope": manifest.identity_scope,
        "sweep_coverage_complete": manifest.sweep_coverage_complete,
        "snapshot_coverage_complete": manifest.snapshot_coverage_complete,
        "full_candidate_coverage": manifest.full_candidate_coverage,
        "host_coverage_claim_permitted": manifest.host_coverage_claim_permitted,
        "ignored_item_policy": manifest.ignored_item_policy,
    })
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _manifest_capture_seal(manifest: TreeManifest) -> str:
    payload = _canonical({
        "schema": "self_evolution_manifest_capture_seal_v1",
        "root": manifest.root,
        "root_identity": manifest.root_identity,
        "digest": manifest.digest,
        "entry_count": manifest.entry_count,
        "file_bytes": manifest.file_bytes,
        "sweep_coverage_complete": manifest.sweep_coverage_complete,
        "snapshot_coverage_complete": manifest.snapshot_coverage_complete,
        "full_candidate_coverage": manifest.full_candidate_coverage,
        "host_coverage_claim_permitted": manifest.host_coverage_claim_permitted,
    })
    return hmac.new(_SEAL_KEY, b"self-evolution-manifest-capture-v1\0" + payload,
                    hashlib.sha256).hexdigest()


def _require_captured_manifest(manifest: TreeManifest) -> None:
    if (type(manifest.sweep_coverage_complete) is not bool or
            type(manifest.snapshot_coverage_complete) is not bool or
            type(manifest.full_candidate_coverage) is not bool or
            type(manifest.host_coverage_claim_permitted) is not bool or
            manifest.snapshot_coverage_complete or manifest.host_coverage_claim_permitted or
            (manifest.full_candidate_coverage and
             (not manifest.sweep_coverage_complete or bool(manifest.excluded_metadata))) or
            _computed_manifest_digest(manifest) != manifest.digest or
            not isinstance(manifest._capture_seal, str) or
            not hmac.compare_digest(_manifest_capture_seal(manifest), manifest._capture_seal)):
        raise ManifestError("manifest_integrity_check_failed")


def _windows_handle_identity(handle: int) -> tuple[int, bytes]:
    """Return a volume serial and 128-bit file ID for this handle observation.

    The pair is only a within-sweep comparison aid. It is not a durable
    identity, cross-volume proof, local-volume proof, or OS trust anchor.
    """
    if os.name != "nt":
        raise ManifestError("windows_handle_identity_unavailable")
    import ctypes
    from ctypes import wintypes

    class FileIdInfo(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_uint64), ("FileId", ctypes.c_ubyte * 16)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    info = FileIdInfo()
    if not get_info(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
        raise ManifestError("windows_handle_identity_unavailable")
    return int(info.VolumeSerialNumber), bytes(info.FileId)


def _windows_enumerate_directory(directory_handle: int) -> tuple[str, ...]:
    """Enumerate names on a held directory handle, rejecting malformed results."""
    if os.name != "nt":
        raise ManifestError("windows_directory_enumeration_unavailable")
    import ctypes
    from ctypes import wintypes

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    query = ctypes.WinDLL("ntdll").NtQueryDirectoryFile
    query.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                      ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG,
                      ctypes.c_int, wintypes.BOOLEAN, ctypes.c_void_p, wintypes.BOOLEAN]
    query.restype = ctypes.c_long
    output: list[str] = []
    restart = True
    buffer = ctypes.create_string_buffer(65536)
    while True:
        io = IoStatusBlock()
        status = query(directory_handle, None, None, None, ctypes.byref(io), buffer,
                       len(buffer), 12, False, None, restart)
        restart = False
        code = status & 0xffffffff
        if code == 0x80000006:  # STATUS_NO_MORE_FILES
            break
        if status != 0:
            raise ManifestError(f"windows_directory_query_failed_0x{code:08x}")
        used = io.Information
        if used == 0 or used > len(buffer):
            raise ManifestError("windows_directory_query_malformed")
        offset = 0
        while True:
            if offset + 12 > used:
                raise ManifestError("windows_directory_query_malformed")
            next_offset = int.from_bytes(buffer.raw[offset:offset + 4], "little")
            name_length = int.from_bytes(buffer.raw[offset + 8:offset + 12], "little")
            record_limit = offset + next_offset if next_offset else used
            if (name_length % 2 or record_limit > used or record_limit < offset + 12 or
                    offset + 12 + name_length > record_limit or
                    (next_offset and next_offset % 4)):
                raise ManifestError("windows_directory_query_malformed")
            try:
                name = buffer.raw[offset + 12:offset + 12 + name_length].decode("utf-16-le", "strict")
            except UnicodeError as exc:
                raise ManifestError("windows_directory_query_malformed") from exc
            if name not in (".", ".."):
                output.append(name)
                if len(output) > MAX_ENTRIES:
                    raise ManifestError("entry_count_exceeded")
            if not next_offset:
                break
            offset += next_offset
    return tuple(sorted(output, key=lambda value: (value.casefold(), value)))


@__import__("contextlib").contextmanager
def _windows_open_relative(parent_handle: int, name: str, *, directory: bool | None,
                           share_access: int = 7):
    """Open exactly one name relative to a held directory handle.

    FILE_OPEN_REPARSE_POINT prevents following the final component; callers
    must inspect the returned handle and retain the parent for the operation.
    """
    if os.name != "nt":
        raise ManifestError("windows_relative_open_unavailable")
    if (not isinstance(name, str) or not name or
            any(c in name for c in ("/", "\\", "\x00", ":")) or name in (".", "..")):
        raise ManifestError("invalid_relative_component")
    if type(share_access) is not int or share_access < 0 or share_access > 7:
        raise ManifestError("invalid_share_access")
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", wintypes.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    encoded_length = len(name.encode("utf-16-le"))
    if encoded_length > 65532:
        raise ManifestError("invalid_relative_component")
    name_buffer = ctypes.create_unicode_buffer(name)
    name_descriptor = UnicodeString(encoded_length, encoded_length + 2, ctypes.cast(name_buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent_handle, ctypes.pointer(name_descriptor),
                                  0x40, None, None)
    io = IoStatusBlock()
    handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll")
    create = ntdll.NtCreateFile
    create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(ObjectAttributes),
                       ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
                       wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    create.restype = ctypes.c_long
    # FILE_OPEN_REPARSE_POINT applies to this single component, avoiding
    # accidental traversal of a final junction. Hold the resulting handle
    # while inspecting it; caller must hold the parent throughout.
    options = 0x00200000 | 0x20 | (0x1 if directory is True else 0x40 if directory is False else 0)
    status = create(ctypes.byref(handle), 0x80100080, ctypes.byref(attributes), ctypes.byref(io),
                    None, 0, share_access, 1, options, None, 0)
    if status < 0:
        raise ManifestError(f"windows_relative_open_failed_0x{status & 0xffffffff:08x}")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    try:
        info = FileAttributeTagInfo()
        if not get_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ManifestError("windows_handle_attributes_unavailable")
        if info.FileAttributes & _REPARSE_POINT:
            raise ManifestError("link_or_reparse_point_detected")
        if directory is not None and bool(info.FileAttributes & 0x10) != directory:
            raise ManifestError("unexpected_entry_type")
        yield handle.value
    finally:
        kernel.CloseHandle(handle)


# Compatibility aliases for the original NT primitive tests.
_windows_identity_for_research = _windows_handle_identity
_windows_list_directory_for_research = _windows_enumerate_directory
_windows_open_relative_for_research = _windows_open_relative



_WIN32_STREAM_ID_SIZE = 24  # DWORD, DWORD, LARGE_INTEGER, DWORD, WCHAR[1], padded to ABI alignment.
_BACKUP_READ_BUFFER_SIZE = 64 * 1024
if _BACKUP_READ_BUFFER_SIZE <= _WIN32_STREAM_ID_SIZE:
    raise RuntimeError("BackupRead buffer must exceed sizeof(WIN32_STREAM_ID)")


def _windows_backup_read_call(api: Any, handle: int, buffer: Any, capacity: int,
                              transferred: Any, abort: bool, security: bool, context: Any) -> bool:
    """Call BackupRead once; split out so the actual request size is testable."""
    return bool(api(handle, buffer, capacity, transferred, abort, security, context))


def _windows_file_stream_info(handle: int) -> bytes:
    """Query complete FileStreamInformation on a held handle with a fixed cap."""
    import ctypes
    from ctypes import wintypes

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    query = ctypes.WinDLL("ntdll").NtQueryInformationFile
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(IoStatusBlock), ctypes.c_void_p,
                      wintypes.ULONG, ctypes.c_int]
    query.restype = ctypes.c_long
    capacity = 64 * 1024
    buffer = ctypes.create_string_buffer(capacity)
    io = IoStatusBlock()
    status = int(query(handle, ctypes.byref(io), buffer, capacity, 22))  # FileStreamInformation
    if status != 0 or not 0 < io.Information <= capacity:
        raise ManifestError("default_stream_unavailable")
    return buffer.raw[:io.Information]


def _windows_empty_default_stream_by_handle(handle: int) -> None:
    """Verify exactly one empty unnamed $DATA stream using the same held handle.

    BackupRead may omit a zero-length BACKUP_DATA record. A bounded
    FileStreamInfo query must positively identify the default stream and reject
    every named stream; unsupported, truncated or inconsistent results fail
    closed. This is only a point-in-time observation, not an atomic snapshot.
    """
    before_id, before = _windows_handle_info(handle)
    if before["directory"] or before["delete_pending"] or before["size"] != 0:
        raise ManifestError("default_stream_unavailable")
    raw = _windows_file_stream_info(handle)
    capacity = len(raw)
    offset = 0
    found_default = False
    while True:
        if offset + 24 > capacity:
            raise ManifestError("windows_stream_enumeration_malformed")
        next_offset = int.from_bytes(raw[offset:offset + 4], "little")
        name_size = int.from_bytes(raw[offset + 4:offset + 8], "little")
        stream_size = int.from_bytes(raw[offset + 8:offset + 16], "little", signed=True)
        end = offset + next_offset if next_offset else capacity
        if (name_size % 2 or name_size > MAX_PATH_BYTES or stream_size < 0 or
                end > capacity or end < offset + 24 or offset + 24 + name_size > end or
                (next_offset and next_offset % 8)):
            raise ManifestError("windows_stream_enumeration_malformed")
        try:
            name = raw[offset + 24:offset + 24 + name_size].decode("utf-16-le", "strict")
        except UnicodeError as exc:
            raise ManifestError("windows_stream_enumeration_malformed") from exc
        if name not in ("", "::$DATA"):
            raise ManifestError("non_default_stream_detected")
        if found_default or stream_size != 0:
            raise ManifestError("default_stream_unavailable")
        found_default = True
        if not next_offset:
            break
        offset = end
    after_id, after = _windows_handle_info(handle)
    if (not found_default or before_id != after_id or after["directory"] or
            after["delete_pending"] or after["size"] != 0 or
            before["write_time"] != after["write_time"] or
            before["change_time"] != after["change_time"]):
        raise ManifestError("default_stream_unavailable")


def _windows_default_streams(handle: int, *, directory: bool) -> tuple[str, ...]:
    """Enumerate backup streams on this held file/directory handle.

    Only the unnamed default $DATA stream is accepted. BackupRead is used
    because GetFileInformationByHandleEx(FileStreamInfo) does not enumerate
    directory streams on supported Windows versions. Any unsupported stream
    type, malformed response, or API error is a hard failure.
    """
    if os.name != "nt":
        raise ManifestError("windows_stream_enumeration_unavailable")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    backup_read = kernel.BackupRead
    backup_read.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.BOOL,
                            ctypes.POINTER(ctypes.c_void_p)]
    backup_read.restype = wintypes.BOOL
    backup_seek = kernel.BackupSeek
    backup_seek.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                            ctypes.POINTER(ctypes.c_void_p)]
    backup_seek.restype = wintypes.BOOL
    context = ctypes.c_void_p()
    pending = bytearray()
    eof = False

    def fill() -> None:
        nonlocal eof
        if eof:
            return
        buffer = ctypes.create_string_buffer(_BACKUP_READ_BUFFER_SIZE)
        transferred = wintypes.DWORD()
        if not _windows_backup_read_call(backup_read, handle, buffer, _BACKUP_READ_BUFFER_SIZE,
                                         ctypes.byref(transferred), False, False, ctypes.byref(context)):
            raise ManifestError("windows_stream_enumeration_failed")
        if transferred.value > _BACKUP_READ_BUFFER_SIZE:
            raise ManifestError("windows_stream_enumeration_malformed")
        if transferred.value == 0:
            eof = True
            return
        pending.extend(buffer.raw[:transferred.value])

    def read_exact(size: int, *, allow_eof: bool = False) -> bytes | None:
        if size < 0 or size > _BACKUP_READ_BUFFER_SIZE:
            raise ManifestError("windows_stream_enumeration_malformed")
        while len(pending) < size and not eof:
            fill()
        if len(pending) < size:
            if allow_eof and not pending:
                return None
            raise ManifestError("windows_stream_enumeration_malformed")
        result = bytes(pending[:size])
        del pending[:size]
        return result

    def skip_stream_data(size: int) -> None:
        # BackupRead may have returned a whole stream (or the next stream
        # header) in its first large buffer. Consume only this stream's data;
        # preserve any over-read bytes for the following header.
        buffered_data = min(size, len(pending))
        if buffered_data:
            del pending[:buffered_data]
        remaining = size - buffered_data
        if not remaining:
            return
        low = wintypes.DWORD()
        high = wintypes.DWORD()
        if not backup_seek(handle, remaining & 0xFFFFFFFF, (remaining >> 32) & 0xFFFFFFFF,
                           ctypes.byref(low), ctypes.byref(high), ctypes.byref(context)):
            raise ManifestError("windows_stream_enumeration_failed")
        skipped = (int(high.value) << 32) | int(low.value)
        if skipped != remaining:
            raise ManifestError("windows_stream_enumeration_malformed")

    accepted: list[str] = []
    try:
        while True:
            header = read_exact(20, allow_eof=True)
            if header is None:
                break
            stream_id = int.from_bytes(header[0:4], "little")
            stream_size = int.from_bytes(header[8:16], "little", signed=True)
            name_size = int.from_bytes(header[16:20], "little")
            if stream_size < 0 or name_size > MAX_PATH_BYTES or name_size % 2:
                raise ManifestError("windows_stream_enumeration_malformed")
            name_bytes = read_exact(name_size) if name_size else b""
            try:
                stream_name = (name_bytes or b"").decode("utf-16-le", "strict")
            except UnicodeError as exc:
                raise ManifestError("windows_stream_enumeration_malformed") from exc
            # WIN32_STREAM_ID: BACKUP_DATA=1, BACKUP_ALTERNATE_DATA=4.
            if stream_id == 1 and stream_name == "":
                if accepted:
                    raise ManifestError("windows_stream_enumeration_malformed")
                accepted.append("::$DATA")
            elif stream_id == 4:
                raise ManifestError("non_default_stream_detected")
            else:
                raise ManifestError("unsupported_backup_stream_type")
            skip_stream_data(stream_size)
    finally:
        transferred = wintypes.DWORD()
        cleanup_failed = False
        if context.value and not _windows_backup_read_call(
                backup_read, handle, None, 0, ctypes.byref(transferred), True, False, ctypes.byref(context)):
            cleanup_failed = True
        if not directory:
            set_pointer = kernel.SetFilePointerEx
            set_pointer.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                    ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
            set_pointer.restype = wintypes.BOOL
            if not set_pointer(handle, ctypes.c_longlong(0), None, 0):  # FILE_BEGIN
                cleanup_failed = True
        if cleanup_failed:
            raise ManifestError("windows_stream_enumeration_cleanup_failed")
    if not directory and not accepted:
        _windows_empty_default_stream_by_handle(handle)
        accepted.append("::$DATA")
    if not directory and accepted != ["::$DATA"]:
        raise ManifestError("default_stream_unavailable")
    return tuple(accepted)

def _windows_handle_info(handle: int) -> tuple[tuple[int, bytes], dict[str, int | bool]]:
    """Read identity, attributes, sizes and timestamps from one held handle."""
    if os.name != "nt":
        raise ManifestError("safe_relative_handle_traversal_unavailable")
    import ctypes
    from ctypes import wintypes

    class BasicInfo(ctypes.Structure):
        _fields_ = [("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong),
                    ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong),
                    ("FileAttributes", wintypes.DWORD)]

    class StandardInfo(ctypes.Structure):
        _fields_ = [("AllocationSize", ctypes.c_longlong), ("EndOfFile", ctypes.c_longlong),
                    ("NumberOfLinks", wintypes.DWORD), ("DeletePending", wintypes.BOOLEAN),
                    ("Directory", wintypes.BOOLEAN)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    basic, standard = BasicInfo(), StandardInfo()
    if not query(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)) or not query(
            handle, 1, ctypes.byref(standard), ctypes.sizeof(standard)):
        raise ManifestError("windows_handle_metadata_unavailable")
    identity = _windows_handle_identity(handle)
    return identity, {
        "attributes": int(basic.FileAttributes), "write_time": int(basic.LastWriteTime),
        "change_time": int(basic.ChangeTime), "size": int(standard.EndOfFile),
        "links": int(standard.NumberOfLinks), "directory": bool(standard.Directory),
        "delete_pending": bool(standard.DeletePending),
    }


@contextlib.contextmanager
def _windows_open_volume_root(drive: str):
    """Open a local volume root; it is the trust anchor for relative traversal."""
    if os.name != "nt" or not re.fullmatch(r"[A-Za-z]:", drive):
        raise ManifestError("trusted_local_drive_root_required")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    handle = create("\\\\?\\" + drive + "\\", 0x80000000, 7, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ManifestError("trusted_volume_root_open_failed")
    try:
        # A drive letter alone is not proof of a local volume root: mapped
        # drives and SUBST aliases can also be opened through this spelling.
        drive_type = kernel.GetDriveTypeW(drive + "\\")
        query_dos_device = kernel.QueryDosDeviceW
        query_dos_device.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        query_dos_device.restype = wintypes.DWORD
        device_target = ctypes.create_unicode_buffer(4096)
        if (drive_type != 3 or not query_dos_device(drive, device_target, len(device_target)) or
                not re.fullmatch(r"\\Device\\HarddiskVolume[0-9]+", device_target.value)):
            # Reject mapped network and SUBST drive targets; the drive type by
            # itself does not establish a local volume-root trust anchor.
            raise ManifestError("local_fixed_volume_not_proven")
        get_final = kernel.GetFinalPathNameByHandleW
        get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final.restype = wintypes.DWORD
        final_name = ctypes.create_unicode_buffer(32768)
        final_length = get_final(handle, final_name, len(final_name), 1)  # VOLUME_NAME_GUID
        volume_prefix = "\\\\?\\Volume{"
        final_value = final_name.value
        volume_guid = final_value[len(volume_prefix):-2] if final_value.startswith(volume_prefix) and final_value.endswith("}\\") else ""
        if (not final_length or final_length >= len(final_name) or
                not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", volume_guid)):
            raise ManifestError("drive_is_not_a_proven_volume_root")
        identity, info = _windows_handle_info(handle)
        if not info["directory"] or info["attributes"] & _REPARSE_POINT or info["delete_pending"]:
            raise ManifestError("root_not_a_safe_directory")
        yield handle, identity
    finally:
        close(handle)


@contextlib.contextmanager
def _windows_open_trusted_root(root: os.PathLike[str] | str):
    """Bind a local absolute root by opening every component relative to its held parent."""
    supplied = Path(root).expanduser()
    raw = os.fspath(supplied)
    if not supplied.is_absolute() or not re.match(r"^[A-Za-z]:\\", raw):
        raise ManifestError("trusted_local_absolute_root_required")
    if any(part in (".", "..") or part.endswith((".", " ")) or ":" in part
           for part in supplied.parts[1:]):
        raise ManifestError("root_path_alias_forbidden")
    # UNC, device namespaces, and alternate stream paths are intentionally unsupported.
    with _windows_open_volume_root(supplied.drive) as (volume, _):
        stack: list[Any] = []
        current = volume
        try:
            components = [part for part in supplied.parts[1:] if part not in ("\\", "/")]
            if not components:
                raise ManifestError("volume_root_is_not_a_candidate")
            for component in components:
                if (component in (".", "..") or component.endswith((".", " ")) or
                        any(c in component for c in ("/", "\\", "\x00", ":")) or
                        unicodedata.normalize("NFC", component) != component):
                    raise ManifestError("root_path_alias_forbidden")
                cm = _windows_open_relative(current, component, directory=True)
                child = cm.__enter__()
                stack.append(cm)
                current = child
            root_handle = current
            identity, info = _windows_handle_info(root_handle)
            if not info["directory"] or info["attributes"] & _REPARSE_POINT or info["delete_pending"]:
                raise ManifestError("root_not_a_safe_directory")
            yield supplied, root_handle, identity, stack
        finally:
            # Each relative open context owns exactly its child handle.
            for cm in reversed(stack):
                cm.__exit__(None, None, None)


def _windows_digest_handle(handle: int, expected: dict[str, int | bool], budget: list[int], *,
                           max_file_bytes: int | None = None,
                           max_total_bytes: int | None = None) -> tuple[int, str, dict[str, int | bool]]:
    if max_file_bytes is None:
        max_file_bytes = MAX_FILE_BYTES
    if max_total_bytes is None:
        max_total_bytes = MAX_TOTAL_BYTES
    if not (0 <= max_file_bytes <= MAX_FILE_BYTES and 0 <= max_total_bytes <= MAX_TOTAL_BYTES):
        raise ManifestError("invalid_size_limit")
    if int(expected["size"]) < 0:
        raise ManifestError("invalid_file_size")
    if int(expected["size"]) > max_file_bytes or budget[0] + int(expected["size"]) > max_total_bytes:
        raise ManifestError("size_limit_exceeded")
    import ctypes
    from ctypes import wintypes
    import msvcrt
    # Bind the read to the identity observed on this held handle immediately
    # before duplicating it for streaming. The post-read observation below must
    # compare against this value, not against a second post-read query.
    before_id, _before = _windows_handle_info(handle)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    duplicate = kernel.DuplicateHandle
    duplicate.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                          ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    duplicate.restype = wintypes.BOOL
    get_current = kernel.GetCurrentProcess
    get_current.restype = wintypes.HANDLE
    process = get_current()
    copy = wintypes.HANDLE()
    if not duplicate(process, handle, process, ctypes.byref(copy), 0, False, 2):
        raise ManifestError("windows_file_handle_duplicate_failed")
    try:
        fd = msvcrt.open_osfhandle(copy.value, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        kernel.CloseHandle(copy)
        raise ManifestError("windows_file_handle_stream_unavailable") from exc
    digest, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "rb", closefd=True) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_file_bytes or budget[0] + size > max_total_bytes:
                    raise ManifestError("size_limit_exceeded")
                digest.update(chunk)
        after_id, after = _windows_handle_info(handle)
    except OSError as exc:
        raise ManifestError("file_read_failed") from exc
    if (size != expected["size"] or after["size"] != size or after_id != before_id or
            after["write_time"] != expected["write_time"] or after["change_time"] != expected["change_time"] or
            after["links"] != 1 or after["delete_pending"]):
        raise ManifestError("file_changed_during_sweep")
    budget[0] += size
    return size, digest.hexdigest(), after


def capture_tree_manifest(root: os.PathLike[str] | str) -> TreeManifest:
    """Capture a bounded point-in-time sweep using Windows relative handles.

    This is deliberately not an atomic snapshot or OS sandbox. It accepts only
    a fixed local drive whose device target and final volume-GUID path prove
    that the opened handle is the volume root (rejecting mapped drives/SUBST);
    every descendant is opened
    relative to its held parent with FILE_OPEN_REPARSE_POINT and inspected by
    handle after use. Unsupported platforms and path forms fail closed.
    """
    if os.name != "nt":
        raise ManifestError("safe_relative_handle_traversal_unavailable")
    entries: list[TreeEntry] = []
    excluded: list[dict[str, Any]] = []
    budget = [0]
    seen_ids: dict[tuple[int, bytes], str] = {}
    seen_dirs: set[tuple[int, bytes]] = set()

    def directory_stamp(handle: int) -> tuple[tuple[int, bytes], int, int]:
        identity, info = _windows_handle_info(handle)
        if not info["directory"] or info["attributes"] & _REPARSE_POINT or info["delete_pending"]:
            raise ManifestError("link_or_reparse_point_detected")
        return identity, int(info["write_time"]), int(info["change_time"])

    def visit(handle: int, relative_dir: str, depth: int) -> None:
        if depth > _MAX_DEPTH:
            raise ManifestError("tree_depth_exceeded")
        before_streams = _windows_default_streams(handle, directory=True)
        before_stamp = directory_stamp(handle)
        before_names = _windows_enumerate_directory(handle)
        folded: set[str] = set()
        for name in before_names:
            if name in (".", ".."):
                raise ManifestError("directory_enumeration_malformed")
            if name.casefold() != ".git":
                validate_relative_path(name)
            key = unicodedata.normalize("NFC", name).casefold()
            if key in folded:
                raise ManifestError("windows_path_alias_detected")
            folded.add(key)
        for name in before_names:
            if (not name or name in (".", "..") or "\x00" in name or
                    unicodedata.normalize("NFC", name) != name or name.endswith((".", " ")) or
                    any(character in name for character in ("/", "\\", ":"))):
                raise ManifestError("windows_path_alias_forbidden")
            relative = f"{relative_dir}/{name}" if relative_dir else name
            if relative_dir or name.casefold() != ".git":
                validate_relative_path(relative)
            is_metadata = not relative_dir and name.casefold() == ".git"
            # Open one exact component under the still-held parent. ADS, paths,
            # case aliases, and reparse traversal are rejected by the helper.
            with _windows_open_relative(handle, name, directory=None) as child:
                identity, info = _windows_handle_info(child)
                if info["attributes"] & _REPARSE_POINT or info["delete_pending"]:
                    raise ManifestError("link_or_reparse_point_detected")
                child_is_directory = bool(info["directory"])
                child_streams_before = _windows_default_streams(child, directory=child_is_directory)
                if info["directory"]:
                    if is_metadata:
                        if _windows_default_streams(child, directory=True) != child_streams_before:
                            raise ManifestError("directory_changed_during_sweep")
                        excluded.append({"path": ".git", "kind": "directory", "mode": int(info["attributes"]),
                                         "device": identity[0], "file_id": int.from_bytes(identity[1], "little"),
                                         "size": 0, "sha256": None, "streams_checked": "default_only",
                                         "subtree_excluded": True})
                        continue
                    if identity in seen_dirs:
                        raise ManifestError("directory_identity_alias_detected")
                    seen_dirs.add(identity)
                    seen_ids[identity] = relative
                    entries.append(TreeEntry(relative, "directory", int(info["attributes"]), 0, None, identity[0],
                                             int.from_bytes(identity[1], "little"), int(info["links"])))
                    if len(entries) + len(excluded) > MAX_ENTRIES:
                        raise ManifestError("entry_count_exceeded")
                    visit(child, relative, depth + 1)
                    continue
                if info["directory"] or int(info["attributes"]) & 0x40:  # FILE_ATTRIBUTE_DEVICE
                    raise ManifestError("unsupported_filesystem_entry")
                if info["links"] != 1:
                    raise ManifestError("hard_link_alias_detected")
                if identity in seen_ids:
                    raise ManifestError("file_identity_alias_detected")
                seen_ids[identity] = relative
                size, digest, after = _windows_digest_handle(child, info, budget)
                child_streams_after = _windows_default_streams(child, directory=child_is_directory)
                if child_streams_after != child_streams_before:
                    raise ManifestError("file_changed_during_sweep")
                file_id = int.from_bytes(identity[1], "little")
                if is_metadata:
                    excluded.append({"path": ".git", "kind": "file", "mode": int(info["attributes"]),
                                     "device": identity[0], "file_id": file_id,
                                     "size": size, "sha256": digest, "streams_checked": "default_only",
                                     "subtree_excluded": False})
                    continue
                entries.append(TreeEntry(relative, "file", int(info["attributes"]), size, digest, identity[0], file_id,
                                         int(after["links"])))
                if len(entries) + len(excluded) > MAX_ENTRIES:
                    raise ManifestError("entry_count_exceeded")
        after_streams = _windows_default_streams(handle, directory=True)
        after_stamp = directory_stamp(handle)
        if (after_streams != before_streams or after_stamp != before_stamp or
                _windows_enumerate_directory(handle) != before_names):
            raise ManifestError("directory_changed_during_sweep")

    with _windows_open_trusted_root(root) as (root_path, root_handle, root_identity, _):
        visit(root_handle, "", 0)
        # Reopen the supplied name from the volume anchor: the manifest is only
        # accepted if the final name still resolves to the identity held above.
        with _windows_open_trusted_root(root_path) as (_, rebound, rebound_id, _):
            if rebound_id != root_identity:
                raise ManifestError("root_changed_during_sweep")
    ordered = tuple(sorted(entries, key=lambda entry: (entry.path.casefold(), entry.path)))
    excluded_ordered = tuple(sorted(excluded, key=lambda item: item["path"]))
    ignore_files = tuple(entry.path for entry in ordered
                         if entry.kind == "file" and
                         entry.path.rsplit("/", 1)[-1].casefold() in {".gitignore", ".ignore"})
    portable_root_identity = (root_identity[0], int.from_bytes(root_identity[1], "little"))
    captured = TreeManifest(str(root_path), portable_root_identity,
                            ordered, "", len(ordered), budget[0], excluded_ordered,
                            sweep_coverage_complete=True,
                            # The traversal covers every default-stream entry only
                            # when it did not omit a .git subtree. This is a
                            # point-in-time tree claim, never an atomic snapshot
                            # or a Host/actor-coverage claim.
                            full_candidate_coverage=not excluded_ordered,
                            ignore_policy_files=ignore_files)
    captured = dataclass_replace(captured, digest=_computed_manifest_digest(captured))
    return dataclass_replace(captured, _capture_seal=_manifest_capture_seal(captured))


def _baseline_payload(stable: TreeManifest, candidate: TreeManifest) -> bytes:
    return _canonical({"stable_root": stable.root, "stable_digest": stable.digest,
                       "stable_identity": stable.root_identity, "candidate_root": candidate.root,
                       "candidate_digest": candidate.digest, "candidate_identity": candidate.root_identity})


def capture_tree_baseline(stable_root: os.PathLike[str] | str,
                          candidate_root: os.PathLike[str] | str) -> TreeBaseline:
    stable = capture_tree_manifest(stable_root)
    candidate = capture_tree_manifest(candidate_root)
    stable_path, candidate_path = Path(stable.root), Path(candidate.root)
    if stable_path == candidate_path or stable_path in candidate_path.parents or candidate_path in stable_path.parents:
        raise ManifestError("stable_and_candidate_roots_must_not_overlap")
    seal = hmac.new(_SEAL_KEY, _baseline_payload(stable, candidate), hashlib.sha256).hexdigest()
    return TreeBaseline(stable, candidate, seal)


def _entry_map(manifest: TreeManifest) -> dict[str, TreeEntry]:
    return {entry.path: entry for entry in manifest.entries}


def _same_observed(left: TreeEntry | None, right: TreeEntry | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.payload() == right.payload()


def _content(entry: TreeEntry | None) -> tuple[Any, ...] | None:
    if entry is None:
        return None
    return (entry.kind, entry.mode, entry.size, entry.sha256)


def attribute_tree_changes(baseline: TreeBaseline) -> dict[str, Any]:
    """Attribute whole-path add/modify/delete deltas since a two-tree baseline.

    This is point-in-time temporal attribution, not proof of which process or
    person authored a change. Volume-serial/file-ID equality is not a durable
    root identity proof, so results are always blocked from safe consideration.
    Even a clear result is never merge authority.
    """
    if not isinstance(baseline, TreeBaseline):
        raise ManifestError("baseline_must_be_captured_in_this_process")
    if (_computed_manifest_digest(baseline.stable) != baseline.stable.digest or
            _computed_manifest_digest(baseline.candidate) != baseline.candidate.digest):
        raise ManifestError("manifest_integrity_check_failed")
    expected = hmac.new(_SEAL_KEY, _baseline_payload(baseline.stable, baseline.candidate), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, baseline._seal):
        raise ManifestError("baseline_integrity_check_failed")
    current_stable = capture_tree_manifest(baseline.stable.root)
    current_candidate = capture_tree_manifest(baseline.candidate.root)
    if current_stable.root_identity != baseline.stable.root_identity or current_candidate.root_identity != baseline.candidate.root_identity:
        raise ManifestError("root_identity_changed_since_baseline")
    old_stable, old_candidate = _entry_map(baseline.stable), _entry_map(baseline.candidate)
    now_stable, now_candidate = _entry_map(current_stable), _entry_map(current_candidate)
    paths = sorted(set(old_stable) | set(old_candidate) | set(now_stable) | set(now_candidate), key=lambda p: (p.casefold(), p))
    preexisting = {path for path in set(old_stable) | set(old_candidate)
                   if _content(old_stable.get(path)) != _content(old_candidate.get(path))}
    changes: list[dict[str, Any]] = []
    # Volume serial plus file ID is not a durable identity proof across scans;
    # report deltas for inspection but never call their attribution safe.
    blockers: set[str] = {"root_identity_is_not_durable_proof"}
    if current_stable.digest != baseline.stable.digest:
        blockers.add("stable_tree_changed_since_baseline")
    for path in paths:
        before, after = old_candidate.get(path), now_candidate.get(path)
        if _same_observed(before, after):
            continue
        action = "add" if before is None else "delete" if after is None else "modify"
        changes.append({"path": path, "action": action,
                        "before": before.payload() if before else None,
                        "after": after.payload() if after else None,
                        "preexisting_divergence": path in preexisting})
        if path in preexisting:
            blockers.add("candidate_path_was_preexisting_and_changed")
        if _content(now_stable.get(path)) != _content(old_stable.get(path)):
            blockers.add("stable_path_changed_since_baseline")
    result: dict[str, Any] = {
        "schema": "self_evolution_tree_attribution_v1",
        "evidence_scope": "candidate_default_streams_point_in_time_excluding_git_metadata_subtrees",
        "snapshot_coverage_complete": False,
        "full_candidate_coverage": False,
        "host_coverage_claim_permitted": False,
        "actor_identity_proven": False,
        "attribution_kind": "filesystem_delta_since_baseline_not_writer_identity",
        "ignored_item_policy": "all_present_files_included_gitignore_not_applied",
        "ignore_policy_files": list(current_candidate.ignore_policy_files),
        "excluded_git_metadata": list(current_candidate.excluded_metadata),
        "safe_to_consider": not blockers,
        "merge_authorized": False,
        "baseline_stable_digest": baseline.stable.digest,
        "baseline_candidate_digest": baseline.candidate.digest,
        "current_stable_digest": current_stable.digest,
        "current_candidate_digest": current_candidate.digest,
        "preexisting_paths": sorted(preexisting, key=lambda p: (p.casefold(), p)),
        "changes": changes,
        "blockers": sorted(blockers),
    }
    result["summary_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def validate_relative_paths(paths: Any) -> tuple[str, ...]:
    """Validate declared paths for duplicates and portable Windows aliases."""
    if isinstance(paths, (str, bytes)):
        raise ManifestError("paths_must_be_a_sequence")
    try:
        values = tuple(validate_relative_path(value) for value in paths)
    except TypeError as exc:
        raise ManifestError("paths_must_be_a_sequence") from exc
    if len(values) != len(set(values)):
        raise ManifestError("duplicate_paths")
    folded = [unicodedata.normalize("NFC", value).casefold() for value in values]
    if len(folded) != len(set(folded)):
        raise ManifestError("windows_path_alias_detected")
    return tuple(sorted(values, key=lambda value: (value.casefold(), value)))

# Review manifests are short-lived, backend-only read capabilities. The current
# Host does not issue them; this primitive must not be wired to renderer input.
@dataclass(frozen=True)
class ReviewManifestBinding:
    """Process-local binding for re-reading one candidate review manifest.

    ``_candidate_root`` is intentionally omitted from repr and all projections.
    This is not a durable identity or an authorization to start/merge work.
    """

    session_id: str
    task_id: str
    attempt_id: str
    candidate_id: str
    candidate_digest: str
    candidate_identity: tuple[int, int]
    _candidate_root: str = dataclass_field(repr=False)
    _seal: str = dataclass_field(repr=False)


def _review_binding_payload(session_id: str, task_id: str, attempt_id: str,
                            candidate_id: str, manifest: TreeManifest) -> bytes:
    return _canonical({
        "schema": "self_evolution_review_binding_v1",
        "session_id": session_id, "task_id": task_id, "attempt_id": attempt_id,
        "candidate_id": candidate_id, "candidate_digest": manifest.digest,
        "candidate_identity": manifest.root_identity, "candidate_root": manifest.root,
        "entry_count": manifest.entry_count, "file_bytes": manifest.file_bytes,
        "scope": manifest.scope, "identity_scope": manifest.identity_scope,
        "sweep_coverage_complete": manifest.sweep_coverage_complete,
        "snapshot_coverage_complete": manifest.snapshot_coverage_complete,
        "full_candidate_coverage": manifest.full_candidate_coverage,
        "host_coverage_claim_permitted": manifest.host_coverage_claim_permitted,
    })


def _validate_review_identity(value: Any, field: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)):
        raise ManifestError(f"invalid_review_{field}")
    return value


def create_review_manifest_binding(*, session_id: str, task_id: str, attempt_id: str,
                                   candidate_id: str, manifest: TreeManifest) -> ReviewManifestBinding:
    """Bind a backend-captured manifest to trusted host identity values.

    Callers must obtain every identity value and ``manifest`` from trusted Host
    state. This function is deliberately not an API for renderer-supplied data.
    The current Host does not issue this binding, and the process-local seal is
    not a production key or restart-safe authorization.
    """
    identities = {
        "session": _validate_review_identity(session_id, "session_id"),
        "task": _validate_review_identity(task_id, "task_id"),
        "attempt": _validate_review_identity(attempt_id, "attempt_id"),
        "candidate": _validate_review_identity(candidate_id, "candidate_id"),
    }
    if type(manifest) is not TreeManifest:
        raise ManifestError("review_manifest_required")
    _require_captured_manifest(manifest)
    payload = _review_binding_payload(
        identities["session"], identities["task"], identities["attempt"],
        identities["candidate"], manifest,
    )
    seal = hmac.new(_SEAL_KEY, b"self-evolution-review-binding-v1\0" + payload,
                    hashlib.sha256).hexdigest()
    return ReviewManifestBinding(
        identities["session"], identities["task"], identities["attempt"],
        identities["candidate"], manifest.digest, manifest.root_identity,
        str(manifest.root), seal,
    )


_SENSITIVE_PATH_PART = re.compile(
    r"(?:^|[._-])(?:secret|secrets|credential|credentials|private|token|password|passwd|apikey|api-key|keyring)(?:$|[._-])",
    re.IGNORECASE,
)
_SENSITIVE_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".p7b", ".p7c", ".env"}
_SENSITIVE_FILENAMES = {
    ".env", ".env.local", ".env.production", ".npmrc", ".pypirc", ".netrc",
    "credentials", "credentials.json", "secrets", "secrets.json", "id_rsa",
    "id_ed25519", "known_hosts",
}
# Deny known sensitive directories conservatively; names cannot establish
# whether arbitrary paths or file contents are safe to disclose.
_SENSITIVE_DIRECTORY_NAMES = {
    "config", "configs", "settings", ".config", ".aws", ".ssh",
    "key", "keys", ".keys", "signing", "keystore", "keystores",
    "key-store", "key-stores", "key_store", "key_stores", "keyring", "keyrings",
    "cert", "certs", "certificate", "certificates", "pki", ".pki",
    "tls", "ssl", "gpg", ".gpg", "gnupg", ".gnupg",
}


def _review_path_is_sensitive(path: str) -> bool:
    parts = path.split("/")
    for part in parts:
        folded = part.casefold()
        suffix = Path(folded).suffix
        if (folded in _SENSITIVE_FILENAMES or folded in _SENSITIVE_DIRECTORY_NAMES or
                folded.startswith(".env") or suffix in _SENSITIVE_EXTENSIONS or
                suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".properties"} or
                _SENSITIVE_PATH_PART.search(part)):
            return True
    return False


def _verify_review_recapture(binding: ReviewManifestBinding, captured: TreeManifest,
                              current: TreeManifest) -> None:
    """Validate provenance and identity/content stability across two sweeps."""
    _require_captured_manifest(current)
    if (current.root_identity != binding.candidate_identity or
            current.digest != binding.candidate_digest or
            str(Path(current.root)) != str(Path(binding._candidate_root)) or
            current.digest != captured.digest or current.root_identity != captured.root_identity):
        raise ManifestError("review_candidate_changed_since_capture")
    if not (current.sweep_coverage_complete and current.full_candidate_coverage and
            not current.snapshot_coverage_complete and not current.host_coverage_claim_permitted):
        raise ManifestError("review_manifest_coverage_incomplete")


_REVIEW_MAX_FILE_CONTENT_BYTES = 256 * 1024
_REVIEW_MAX_TOTAL_CONTENT_BYTES = 1024 * 1024


def _read_review_file_from_bound_root(root: str, expected_root_identity: tuple[int, int],
                                      entry: TreeEntry, max_bytes: int) -> bytes:
    """Read one captured file through held Windows directory/file handles."""
    if os.name != "nt":
        raise ManifestError("safe_relative_handle_traversal_unavailable")
    parts = validate_relative_path(entry.path).split("/")
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    with _windows_open_trusted_root(root) as (_, root_handle, root_identity, _):
        if (root_identity[0], int.from_bytes(root_identity[1], "little")) != expected_root_identity:
            raise ManifestError("review_candidate_changed_since_capture")
        with contextlib.ExitStack() as stack:
            parent = root_handle
            for component in parts[:-1]:
                parent = stack.enter_context(_windows_open_relative(parent, component, directory=True))
            file_handle = stack.enter_context(_windows_open_relative(
                parent, parts[-1], directory=False, share_access=0))
            identity, after = _windows_handle_info(file_handle)
            if (after["directory"] or after["delete_pending"] or after["links"] != 1 or
                    after["size"] != entry.size or (identity[0], int.from_bytes(identity[1], "little")) != (entry.device, entry.file_id)):
                raise ManifestError("review_candidate_changed_since_capture")
            streams = _windows_default_streams(file_handle, directory=False)
            if streams != ("::$DATA",):
                raise ManifestError("review_file_streams_unsupported")
            duplicate = kernel.DuplicateHandle
            duplicate.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                  ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            duplicate.restype = wintypes.BOOL
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            process = kernel.GetCurrentProcess()
            copy = wintypes.HANDLE()
            if not duplicate(process, file_handle, process, ctypes.byref(copy), 0, False, 2):
                raise ManifestError("review_file_handle_duplicate_failed")
            try:
                fd = msvcrt.open_osfhandle(copy.value, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            except OSError as exc:
                close(copy)
                raise ManifestError("review_file_handle_stream_unavailable") from exc
            try:
                with os.fdopen(fd, "rb", closefd=True) as stream:
                    content = stream.read(max_bytes + 1)
                    if len(content) > max_bytes or stream.read(1):
                        raise ManifestError("review_content_size_limit_exceeded")
            except OSError as exc:
                raise ManifestError("review_file_read_failed") from exc
            after_id, after = _windows_handle_info(file_handle)
            if (after_id != identity or after["size"] != after["size"] or
                    after["write_time"] != after["write_time"] or
                    after["change_time"] != after["change_time"] or after["delete_pending"]):
                raise ManifestError("review_candidate_changed_during_read")
            if len(content) != entry.size or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), entry.sha256):
                raise ManifestError("review_candidate_changed_since_capture")
            return content


def project_review_file_contents(binding: ReviewManifestBinding, *, session_id: str,
                                 task_id: str, attempt_id: str, candidate_id: str,
                                 manifest: TreeManifest, host_selected_paths: Any,
                                 user_requested: bool) -> dict[str, Any]:
    """Content disclosure is unavailable, regardless of caller-supplied flags.

    A boolean, relative path list, and process-local capture seal do not prove
    user authorization, path entitlement, or safe disclosure of file bytes.
    The existing low-level handle reader is not an exposed review capability.
    """
    raise ManifestError("review_content_disclosure_unavailable")


def project_review_content_diff(binding: ReviewManifestBinding, *, session_id: str,
                                 task_id: str, attempt_id: str, candidate_id: str,
                                 manifest: TreeManifest) -> dict[str, Any]:
    """Explicitly refuse a content diff until content-safety authority exists.

    Current review state supplies neither a trusted authorization to disclose
    file contents nor a reliable way to prove arbitrary source bytes contain no
    secrets. This endpoint validates the process-local capture binding and the
    complete-coverage marker, then refuses without reopening or reading files.
    A point-in-time sweep is not an atomic snapshot, production authorization,
    or permission to display candidate content.
    """
    if type(binding) is not ReviewManifestBinding or type(manifest) is not TreeManifest:
        raise ManifestError("review_binding_or_manifest_invalid")
    values = (
        _validate_review_identity(session_id, "session_id"),
        _validate_review_identity(task_id, "task_id"),
        _validate_review_identity(attempt_id, "attempt_id"),
        _validate_review_identity(candidate_id, "candidate_id"),
    )
    if values != (binding.session_id, binding.task_id, binding.attempt_id, binding.candidate_id):
        raise ManifestError("review_binding_mismatch")
    _require_captured_manifest(manifest)
    payload = _review_binding_payload(*values, manifest)
    expected_seal = hmac.new(_SEAL_KEY, b"self-evolution-review-binding-v1\0" + payload,
                             hashlib.sha256).hexdigest()
    if (not hmac.compare_digest(expected_seal, binding._seal) or
            not hmac.compare_digest(binding.candidate_digest, manifest.digest) or
            binding.candidate_identity != manifest.root_identity or
            str(Path(binding._candidate_root)) != str(Path(manifest.root))):
        raise ManifestError("review_binding_integrity_check_failed")
    if not (manifest.sweep_coverage_complete and manifest.full_candidate_coverage and
            not manifest.snapshot_coverage_complete and not manifest.host_coverage_claim_permitted):
        raise ManifestError("review_manifest_coverage_incomplete")
    # Deliberately no diff, text, or byte projection until a separate trusted
    # content-disclosure grant and sensitivity policy can be enforced.
    raise ManifestError("review_content_diff_not_supported")


def project_review_file_manifest(binding: ReviewManifestBinding, *, session_id: str,
                                 task_id: str, attempt_id: str, candidate_id: str,
                                 manifest: TreeManifest) -> dict[str, Any]:
    """Re-read and project safe file metadata, or fail closed.

    Returned data contains relative file paths and hashes only, never root
    paths or file bytes. A completed sweep is distinguished from an atomic
    snapshot; the current scanner still cannot claim full candidate coverage.
    """
    if type(binding) is not ReviewManifestBinding or type(manifest) is not TreeManifest:
        raise ManifestError("review_binding_or_manifest_invalid")
    values = (
        _validate_review_identity(session_id, "session_id"),
        _validate_review_identity(task_id, "task_id"),
        _validate_review_identity(attempt_id, "attempt_id"),
        _validate_review_identity(candidate_id, "candidate_id"),
    )
    if values != (binding.session_id, binding.task_id, binding.attempt_id, binding.candidate_id):
        raise ManifestError("review_binding_mismatch")
    _require_captured_manifest(manifest)
    payload = _review_binding_payload(*values, manifest)
    expected_seal = hmac.new(_SEAL_KEY, b"self-evolution-review-binding-v1\0" + payload,
                             hashlib.sha256).hexdigest()
    if (not hmac.compare_digest(expected_seal, binding._seal) or
            not hmac.compare_digest(binding.candidate_digest, manifest.digest) or
            binding.candidate_identity != manifest.root_identity or
            str(Path(binding._candidate_root)) != str(Path(manifest.root))):
        raise ManifestError("review_binding_integrity_check_failed")
    try:
        validate_relative_paths(entry.path for entry in manifest.entries)
    except ManifestError as exc:
        raise ManifestError("review_manifest_entry_invalid") from exc
    for entry in manifest.entries:
        if type(entry) is not TreeEntry:
            raise ManifestError("review_manifest_entry_invalid")
        validate_relative_path(entry.path)
        if entry.kind not in {"file", "directory"}:
            raise ManifestError("review_manifest_entry_invalid")
        if entry.kind == "file":
            if (type(entry.size) is not int or entry.size < 0 or
                    not isinstance(entry.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", entry.sha256)):
                raise ManifestError("review_manifest_entry_invalid")
            if _review_path_is_sensitive(entry.path):
                raise ManifestError("review_manifest_sensitive_path_detected")
        elif entry.size != 0 or entry.sha256 is not None:
            raise ManifestError("review_manifest_entry_invalid")
    if (type(manifest.entry_count) is not int or manifest.entry_count != len(manifest.entries) or
            manifest.entry_count > MAX_ENTRIES or type(manifest.file_bytes) is not int or
            manifest.file_bytes < 0 or manifest.file_bytes > MAX_TOTAL_BYTES):
        raise ManifestError("review_manifest_bounds_invalid")
    observed_file_bytes = 0
    for entry in manifest.entries:
        if entry.kind == "file":
            if entry.size > MAX_FILE_BYTES:
                raise ManifestError("review_manifest_bounds_invalid")
            observed_file_bytes += entry.size
            if observed_file_bytes > MAX_TOTAL_BYTES:
                raise ManifestError("review_manifest_bounds_invalid")
    if observed_file_bytes != manifest.file_bytes:
        raise ManifestError("review_manifest_bounds_invalid")

    # Re-open the trusted root through the scanner. This is a second point-in-
    # time sweep, not an atomic snapshot; any observed identity/digest drift is
    # a hard denial. The scanner itself rejects unsupported platforms/races.
    current = capture_tree_manifest(binding._candidate_root)
    _verify_review_recapture(binding, manifest, current)
    files = [
        {"path": entry.path, "kind": "file", "size": entry.size, "sha256": entry.sha256}
        for entry in current.entries if entry.kind == "file"
    ]
    return {
        "schema": "self_evolution_review_file_manifest_v1",
        "task_id": binding.task_id,
        "attempt_id": binding.attempt_id,
        "candidate_id": binding.candidate_id,
        "candidate_digest": binding.candidate_digest,
        "files": files,
        "sweep_coverage_complete": current.sweep_coverage_complete,
        "snapshot_coverage_complete": False,
        "full_candidate_coverage": current.full_candidate_coverage,
        "host_coverage_claim_permitted": False,
        "merge_authorized": False,
    }
