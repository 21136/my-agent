"""Windows-only read-lock worker for a digest-pinned managed toolchain.

This process writes nothing. It accepts one bounded canonical request, opens
all manifest files and their ordinary ancestors with restrictive share modes,
hashes through the held file handles, emits readiness, then waits for a bounded
nonce-bound release frame or EOF. Diagnostics are intentionally suppressed.
"""

import ctypes
import hashlib
import json
import ntpath
import os
import re
import sys

MAX_FRAME = 16 * 1024 * 1024
MAX_ACK_FRAME = 4096
MAX_FILES = 20_000
MAX_DIRECTORIES = 20_000
MAX_TOTAL_BYTES = 448 * 1024 * 1024
MAX_NODE_BYTES = 256 * 1024 * 1024
MAX_PATH_CHARS = 32767

FORMAT = 1
REQUEST_MODE = "managed_toolchain_read_guard_request_v1"
READY_MODE = "managed_toolchain_read_guard_ready_v1"
RELEASE_MODE = "managed_toolchain_read_guard_release_v1"
ACK_MODE = "managed_toolchain_read_guard_release_ack_v1"
PROBE_MODE = "managed_toolchain_read_guard_probe_v1"
RECEIPT_MODE = "managed_toolchain_read_guard_v1"

GENERIC_READ = 0x80000000
FILE_READ_ATTRIBUTES = 0x00000080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_TYPE_DISK = 1
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][\w.-]+)?$")


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTime", FILETIME),
        ("ftLastAccessTime", FILETIME),
        ("ftLastWriteTime", FILETIME),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_int
kernel32.GetFileType.argtypes = [ctypes.c_void_p]
kernel32.GetFileType.restype = ctypes.c_uint32
kernel32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
kernel32.GetFileInformationByHandle.restype = ctypes.c_int
kernel32.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                              ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
kernel32.ReadFile.restype = ctypes.c_int


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def exact(value, keys):
    return type(value) is dict and set(value) == set(keys)


def fail():
    raise ValueError("invalid guard input or filesystem state")


def digest_value(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def safe_relative(value):
    if type(value) is not str or not value or len(value) > 1024 or value.startswith("/"):
        return False
    if any(ch in value for ch in "\\:\x00\r\n\t<>\"|?*"):
        return False
    for part in value.split("/"):
        if not part or part in (".", "..") or part.endswith((".", " ")):
            return False
        if re.match(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", part, re.IGNORECASE):
            return False
    return True


def normalize_final_path(value):
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def final_path(handle):
    if kernel32.GetFileType(handle) != FILE_TYPE_DISK:
        fail()
    buffer = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
    size = kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if size == 0 or size >= len(buffer):
        fail()
    return buffer.value


def information(handle):
    result = BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(result)):
        fail()
    return result


def open_directory(directory):
    handle = kernel32.CreateFileW(directory, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                  None, OPEN_EXISTING,
                                  FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        fail()
    try:
        info = information(handle)
        if not (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) or info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            fail()
        if normalize_final_path(final_path(handle)) != normalize_final_path(directory):
            fail()
        return handle
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def open_file_record(root, record, expected_hash=None, target_override=None, max_bytes=MAX_TOTAL_BYTES):
    relative = record["path"]
    target = ntpath.normpath(target_override or ntpath.join(root, *relative.split("/")))
    if target_override is None:
        try:
            if ntpath.normcase(ntpath.commonpath([root, target])) != ntpath.normcase(root):
                fail()
        except ValueError:
            fail()
    handle = kernel32.CreateFileW(target, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING,
                                  FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle in (None, INVALID_HANDLE_VALUE):
        fail()
    try:
        info = information(handle)
        if info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT) or info.nNumberOfLinks != 1:
            fail()
        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
        expected_size = record.get("byteLength")
        if size > max_bytes or expected_size is not None and size != expected_size:
            fail()
        if normalize_final_path(final_path(handle)) != normalize_final_path(target):
            fail()
        # Windows filesystem mode bits do not preserve the pinned Git executable bit.
        mode = "100644"
        expected_mode = record.get("mode")
        if expected_mode is not None and mode != expected_mode:
            fail()
        content_hash = hashlib.sha256()
        total = 0
        buffer = ctypes.create_string_buffer(1024 * 1024)
        bytes_read = ctypes.c_uint32()
        while True:
            if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(bytes_read), None):
                fail()
            if bytes_read.value == 0:
                break
            total += bytes_read.value
            if total > MAX_TOTAL_BYTES:
                fail()
            content_hash.update(buffer.raw[:bytes_read.value])
        actual_hash = content_hash.hexdigest()
        if total != size or (expected_size is not None and total != expected_size):
            fail()
        if actual_hash != (expected_hash or record["sha256"]):
            fail()
        after = information(handle)
        after_size = (after.nFileSizeHigh << 32) | after.nFileSizeLow
        if after.nNumberOfLinks != 1 or after_size != size or after.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT):
            fail()
        return handle, total
    except BaseException:
        if handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.CloseHandle(handle)
        raise


def canonical_absolute_root(value):
    if type(value) is not str or not ntpath.isabs(value) or value.startswith("\\\\") or value.startswith("\\\\?\\"):
        fail()
    normalized = ntpath.normpath(value)
    drive, tail = ntpath.splitdrive(normalized)
    if not drive or not tail.startswith("\\") or normalized != value:
        fail()
    parts = [part for part in tail.split("\\") if part]
    if len(parts) < 3 or parts[-3:] != [".worktrees", "sandbox", "desktop"]:
        fail()
    return normalized


def validate_request(request):
    if not exact(request, ["format", "mode", "nonce", "toolchain"]) or request["format"] != FORMAT or request["mode"] != REQUEST_MODE:
        fail()
    nonce = request["nonce"]
    if type(nonce) is not str or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        fail()
    toolchain = request["toolchain"]
    keys = ["format", "mode", "root", "packageJsonSha256", "packageLockSha256", "node", "files", "toolchainDigest"]
    if not exact(toolchain, keys) or toolchain["format"] != 1 or toolchain["mode"] != "managed_postmerge_toolchain_v1":
        fail()
    root = canonical_absolute_root(toolchain["root"])
    node = toolchain["node"]
    if not exact(node, ["executable", "sha256", "version"]) or type(node["executable"]) is not str or not ntpath.isabs(node["executable"]):
        fail()
    if ntpath.normpath(node["executable"]) != node["executable"] or node["executable"].startswith(("\\\\", "\\\\?\\")):
        fail()
    if not HASH_RE.fullmatch(node["sha256"] or "") or not VERSION_RE.fullmatch(node["version"] or ""):
        fail()
    if not HASH_RE.fullmatch(toolchain["packageJsonSha256"] or "") or not HASH_RE.fullmatch(toolchain["packageLockSha256"] or ""):
        fail()
    if type(toolchain["files"]) is not list or not toolchain["files"] or len(toolchain["files"]) > MAX_FILES - 2:
        fail()
    seen = set()
    total = 0
    for record in toolchain["files"]:
        if not exact(record, ["path", "mode", "sha256", "byteLength"]) or not safe_relative(record["path"]):
            fail()
        if not record["path"].startswith("node_modules/") or record["mode"] not in ("100644", "100755"):
            fail()
        if not HASH_RE.fullmatch(record["sha256"] or "") or type(record["byteLength"]) is not int or record["byteLength"] < 0:
            fail()
        key = record["path"].casefold()
        if key in seen:
            fail()
        seen.add(key)
        total += record["byteLength"]
        if total > MAX_TOTAL_BYTES:
            fail()
    body = dict(toolchain)
    declared_digest = body.pop("toolchainDigest")
    if not HASH_RE.fullmatch(declared_digest or "") or digest_value(body) != declared_digest:
        fail()
    return root, nonce, toolchain


def directory_paths(root, records, node_executable):
    drive, _ = ntpath.splitdrive(root)
    current = drive + "\\"
    paths = {ntpath.normcase(current): current}
    tail = root[len(drive):]
    for part in [segment for segment in tail.split("\\") if segment]:
        current = ntpath.join(current, part)
        paths[ntpath.normcase(current)] = current
    for record in records:
        parts = record["path"].split("/")[:-1]
        current = root
        for part in parts:
            current = ntpath.join(current, part)
            paths[ntpath.normcase(current)] = current
    node_drive, node_tail = ntpath.splitdrive(node_executable)
    if not node_drive or not node_tail.startswith("\\") or node_executable.startswith(("\\\\", "\\\\?\\")):
        fail()
    current = node_drive + "\\"
    paths[ntpath.normcase(current)] = current
    for part in [segment for segment in node_tail.split("\\") if segment][:-1]:
        current = ntpath.join(current, part)
        paths[ntpath.normcase(current)] = current
    if len(paths) > MAX_DIRECTORIES:
        fail()
    return sorted(paths.values(), key=lambda value: (value.count("\\"), len(value), ntpath.normcase(value)))


def read_frame(limit):
    raw = sys.stdin.buffer.readline(limit + 1)
    if not raw or len(raw) > limit or not raw.endswith(b"\n"):
        fail()
    try:
        text = raw[:-1].decode("utf-8", "strict")
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=lambda _: fail())
    except BaseException:
        fail()
    if canonical(value) + "\n" != raw.decode("utf-8", "strict"):
        fail()
    return value


def write_frame(value):
    sys.stdout.buffer.write((canonical(value) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def wait_release(nonce, toolchain_digest):
    raw = sys.stdin.buffer.readline(MAX_ACK_FRAME + 1)
    if not raw or len(raw) > MAX_ACK_FRAME or not raw.endswith(b"\n"):
        fail()
    try:
        text = raw[:-1].decode("utf-8", "strict")
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=lambda _: fail())
    except BaseException:
        fail()
    expected = {"format": FORMAT, "mode": RELEASE_MODE, "nonce": nonce, "toolchainDigest": toolchain_digest}
    if not exact(value, expected.keys()) or value != expected or canonical(value) + "\n" != raw.decode("utf-8", "strict"):
        fail()
    if sys.stdin.buffer.read(1):
        fail()


def probe():
    # Import the full stdlib surface needed by guard mode before validation is enabled.
    import ntpath as _ntpath
    payload = {"format": FORMAT, "mode": PROBE_MODE, "python": sys.executable, "imports": True}
    write_frame(payload)


def run_guard():
    request = read_frame(MAX_FRAME)
    root, nonce, toolchain = validate_request(request)
    directories = directory_paths(root, toolchain["files"], toolchain["node"]["executable"])
    directory_handles = []
    file_handles = []
    total_bytes = 0
    try:
        for directory in directories:
            directory_handles.append(open_directory(directory))
        file_records = list(toolchain["files"])
        file_records.extend([
            {"path": "package.json", "mode": None, "byteLength": None, "sha256": toolchain["packageJsonSha256"]},
            {"path": "package-lock.json", "mode": None, "byteLength": None, "sha256": toolchain["packageLockSha256"]},
        ])
        for record in file_records:
            handle, byte_count = open_file_record(root, record)
            file_handles.append(handle)
            total_bytes += byte_count
            if total_bytes > MAX_TOTAL_BYTES:
                fail()
        node_record = {"path": "pinned-node-runtime", "mode": None, "byteLength": None, "sha256": toolchain["node"]["sha256"]}
        node_handle, _ = open_file_record(root, node_record, target_override=toolchain["node"]["executable"], max_bytes=MAX_NODE_BYTES)
        file_handles.append(node_handle)
        digest = toolchain["toolchainDigest"]
        ready = {"format": FORMAT, "mode": READY_MODE, "nonce": nonce,
                 "toolchainDigest": digest, "fileCount": len(file_handles) - 1,
                 "runtimeFileCount": 1, "directoryCount": len(directory_handles)}
        write_frame(ready)
        wait_release(nonce, digest)
        for handle in reversed(file_handles):
            kernel32.CloseHandle(handle)
        file_handles.clear()
        for handle in reversed(directory_handles):
            kernel32.CloseHandle(handle)
        directory_handles.clear()
        write_frame({"format": FORMAT, "mode": ACK_MODE, "nonce": nonce, "toolchainDigest": digest})
        return 0
    except BaseException:
        return 2
    finally:
        for handle in reversed(file_handles):
            try:
                kernel32.CloseHandle(handle)
            except BaseException:
                pass
        for handle in reversed(directory_handles):
            try:
                kernel32.CloseHandle(handle)
            except BaseException:
                pass


def main():
    if os.name != "nt" or len(sys.argv) != 2:
        return 2
    if sys.argv[1] == "--probe":
        probe()
        return 0
    if sys.argv[1] != "--guard":
        return 2
    try:
        return run_guard()
    except BaseException:
        return 2


if __name__ == "__main__":
    try:
        result = main()
    except BaseException:
        result = 2
    raise SystemExit(result)
