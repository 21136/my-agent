"""Connection-scoped authorization for independent project Markdown viewing."""
from __future__ import annotations

import math
import os
import stat
from contextlib import contextmanager
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from paths import AgentPaths
from project_mode import (
    PROJECT_ARTIFACTS, ProjectModeError, _DOC_LIST_SKIP_DIRS,
    _catalog_skips_rel, _doc_content_hash, _is_template_document,
    _resolve_project_doc_path, list_projects, project_dir,
)


# Process-wide serialization prevents independent websocket connections from
# racing optimistic version checks against the same on-disk document.
_DOCUMENT_IO_LOCK = threading.RLock()
_FORCE_UNSET = object()


@contextmanager
def _open_secure_document(root: Path, rel: str, expected_root: tuple[str, int, int], *, writable: bool = False):
    """Open one regular Markdown file without re-resolving attacker-controlled links.

    POSIX uses directory-relative O_NOFOLLOW opens. Windows pins every directory
    handle (no FILE_SHARE_DELETE), opens the leaf with OPEN_REPARSE_POINT, and
    verifies the final path and file identity from the handles before use.
    Unsupported primitives fail closed.
    """
    parts = rel.split("/")
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_READ_ATTRIBUTES = 0x0080
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        FILE_ATTRIBUTE_DIRECTORY = 0x10
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        FILE_NAME_NORMALIZED = 0
        VOLUME_NAME_DOS = 0
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", FILETIME),
                ("ftLastAccessTime", FILETIME), ("ftLastWriteTime", FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                       wintypes.DWORD, wintypes.DWORD]
        kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handles: list[int] = []
        fd: int | None = None

        def open_handle(path: str, access: int, share: int, flags: int) -> int:
            handle = kernel32.CreateFileW(path, access, share, None, OPEN_EXISTING, flags, None)
            value = ctypes.cast(handle, ctypes.c_void_p).value
            if value == INVALID_HANDLE_VALUE or value is None:
                raise OSError(ctypes.get_last_error(), "safe document open failed", path)
            handles.append(value)
            return value

        def info(handle: int) -> BY_HANDLE_FILE_INFORMATION:
            result = BY_HANDLE_FILE_INFORMATION()
            if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(result)):
                raise OSError(ctypes.get_last_error(), "cannot inspect document handle")
            return result

        def final_path(handle: int) -> str:
            buffer = ctypes.create_unicode_buffer(32768)
            length = kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer),
                                                        FILE_NAME_NORMALIZED | VOLUME_NAME_DOS)
            if not length or length >= len(buffer):
                raise OSError(ctypes.get_last_error(), "cannot resolve document handle path")
            return buffer.value.rstrip("\\/")

        def comparable_path(value: str) -> str:
            value = value.replace("/", "\\")
            if value.startswith("\\\\?\\UNC\\"):
                value = "\\\\" + value[8:]
            elif value.startswith("\\\\?\\"):
                value = value[4:]
            return os.path.normcase(os.path.normpath(value).rstrip("\\/"))

        def under_root(candidate: str, root_path: str) -> bool:
            candidate = comparable_path(candidate)
            root_path = comparable_path(root_path)
            return candidate == root_path or candidate.startswith(root_path + "\\")

        try:
            root_handle = open_handle(str(root), FILE_READ_ATTRIBUTES,
                                      FILE_SHARE_READ | FILE_SHARE_WRITE,
                                      FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            root_info = info(root_handle)
            root_file_id = (root_info.nFileIndexHigh << 32) | root_info.nFileIndexLow
            if (root_info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
                    != FILE_ATTRIBUTE_DIRECTORY
                    or root_file_id != expected_root[2]
                    or comparable_path(final_path(root_handle)) != comparable_path(expected_root[0])):
                raise ProjectDocViewError("项目根目录身份无法安全确认", code="safe_io_unavailable")
            root_final = final_path(root_handle)
            parent = root
            for component in parts[:-1]:
                parent = parent / component
                directory = open_handle(str(parent), FILE_READ_ATTRIBUTES,
                                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                                        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
                directory_info = info(directory)
                directory_final = final_path(directory)
                if (directory_info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
                        != FILE_ATTRIBUTE_DIRECTORY or not under_root(directory_final, root_final)):
                    raise ProjectDocViewError("文档路径含重解析点或越出项目根目录", code="invalid_path")
            leaf = parent / parts[-1]
            access = GENERIC_READ | (GENERIC_WRITE if writable else 0)
            # No write/delete sharing: the compared inode cannot be replaced or
            # concurrently modified by another Windows process until close.
            file_handle = open_handle(str(leaf), access, FILE_SHARE_READ, FILE_FLAG_OPEN_REPARSE_POINT)
            file_info = info(file_handle)
            if (file_info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
                    or file_info.nNumberOfLinks != 1
                    or not under_root(final_path(file_handle), root_final)):
                raise ProjectDocViewError("文档不是项目内的普通独占文件", code="invalid_path")
            fd_flags = os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY)
            fd = msvcrt.open_osfhandle(file_handle, fd_flags)
            handles.remove(file_handle)  # CRT fd owns the native file handle now.
            yield fd, os.fstat(fd)
        finally:
            if fd is not None:
                os.close(fd)
            for handle in reversed(handles):
                kernel32.CloseHandle(handle)
        return

    if (os.name != "posix" or os.open not in os.supports_dir_fd
            or not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY"))):
        raise ProjectDocViewError("当前平台缺少安全的句柄相对打开能力", code="safe_io_unavailable")
    directory_fds: list[int] = []
    fd: int | None = None
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory_fds.append(root_fd)
        root_stat = os.fstat(root_fd)
        if (int(root_stat.st_dev), int(root_stat.st_ino)) != (expected_root[1], expected_root[2]):
            raise ProjectDocViewError("项目根目录身份已变化", code="permission_denied")
        parent_fd = root_fd
        for component in parts[:-1]:
            parent_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=parent_fd)
            directory_fds.append(parent_fd)
        flags = os.O_RDWR if writable else os.O_RDONLY
        flags |= os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(parts[-1], flags, dir_fd=parent_fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ProjectDocViewError("文档不是项目内的普通独占文件", code="invalid_path")
        yield fd, st
    finally:
        if fd is not None:
            os.close(fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _read_open_document(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _write_open_document(fd: int, content: str) -> tuple[str, os.stat_result]:
    written = content.replace("\r\n", "\n").replace("\r", "\n")
    data = written.encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:offset + 1024 * 1024])
    os.fsync(fd)
    return written, os.fstat(fd)


class ProjectDocViewError(Exception):
    def __init__(self, message: str, *, code: str = "invalid_request", current: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.current = current


@dataclass(frozen=True)
class _ViewLease:
    project_id: str
    session: Any
    session_id: str
    root_identity: tuple[str, int, int]


class ProjectDocumentViewStore:
    """Store belongs to one websocket connection; view IDs are never portable."""

    def __init__(self, paths: AgentPaths):
        self._paths = paths
        self._lock = threading.RLock()
        self._views: dict[str, _ViewLease] = {}
        self._epoch = 0

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    def _root_identity(self, project_id: str) -> tuple[str, int, int]:
        try:
            root = project_dir(self._paths, project_id).resolve(strict=True)
            st = root.stat()
            if not root.is_dir():
                raise OSError("not a directory")
            return str(root), int(st.st_dev), int(st.st_ino)
        except Exception as exc:
            raise ProjectDocViewError("项目不可用", code="project_unavailable") from exc

    def check_project(self, project_id: Any) -> tuple[str, tuple[str, int, int]]:
        from project_mode import normalize_project_id
        if type(project_id) is not str:
            raise ProjectDocViewError("项目不可用", code="project_unavailable")
        try:
            pid = normalize_project_id(project_id)
            catalog = {normalize_project_id(item) for item in list_projects(self._paths)}
            if pid not in catalog:
                raise ValueError("not registered")
            return pid, self._root_identity(pid)
        except Exception as exc:
            if isinstance(exc, ProjectDocViewError):
                raise
            raise ProjectDocViewError("项目不可用", code="project_unavailable") from exc

    def authorize(self, session: Any, project_id: Any, *, approved: bool,
                  expected_identity: tuple[str, int, int] | None = None,
                  expected_epoch: int | None = None) -> str | None:
        pid, identity = self.check_project(project_id)
        if expected_identity is not None and identity != expected_identity:
            raise ProjectDocViewError("项目身份已变化，请重新授权", code="permission_denied")
        if approved is not True:
            return None
        session_id = getattr(session, "conversation_id", None)
        if type(session_id) is not str or not session_id:
            raise ProjectDocViewError("当前会话不可用", code="identity_mismatch")
        with self._lock:
            if expected_epoch is not None and self._epoch != expected_epoch:
                raise ProjectDocViewError("连接或会话已变化，请重新授权", code="identity_mismatch")
            # Session transitions revoke every older lease, including leases which may
            # otherwise become valid again if a caller switches back to an old session.
            self._views = {key: value for key, value in self._views.items() if value.session is session}
            view_id = secrets.token_urlsafe(32)
            self._views[view_id] = _ViewLease(pid, session, session_id, identity)
            return view_id

    def _lease(self, view_id: Any, session: Any, project_id: Any) -> tuple[str, _ViewLease]:
        if type(view_id) is not str or not view_id or type(project_id) is not str:
            raise ProjectDocViewError("查看授权无效", code="permission_denied")
        try:
            pid = self.check_project(project_id)[0]
        except ProjectDocViewError:
            raise ProjectDocViewError("查看授权无效", code="permission_denied")
        with self._lock:
            lease = self._views.get(view_id)
            if lease is None:
                raise ProjectDocViewError("查看授权已失效", code="permission_denied")
            if lease.session is not session or lease.session_id != getattr(session, "conversation_id", None):
                self._views.clear()
                raise ProjectDocViewError("查看授权已失效", code="identity_mismatch")
            identity = self._root_identity(pid)
            if lease.project_id != pid or lease.root_identity != identity:
                self._views.pop(view_id, None)
                raise ProjectDocViewError("查看授权已失效", code="permission_denied")
            return pid, lease

    @staticmethod
    def _validate_path(root: Path, path: Any) -> str:
        if type(path) is not str or not path or len(path) > 1024:
            raise ProjectDocViewError("文档路径无效", code="invalid_path")
        # Reject rather than normalize rooted, drive-qualified, traversal, or symlink paths.
        normalized = path.replace("\\", "/")
        pure = PurePosixPath(normalized)
        parts = normalized.split("/")
        if (pure.is_absolute() or re.match(r"^[A-Za-z]:", normalized)
                or any(part in {"", ".", ".."} for part in parts)):
            raise ProjectDocViewError("文档路径无效", code="invalid_path")
        # Reject Win32 path syntax even when running on another platform. This
        # blocks ADS, device namespaces, reserved DOS devices, and names that
        # Win32 silently aliases by trimming trailing dots/spaces.
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10)), "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
        for part in parts:
            stem = part.split(".", 1)[0].rstrip(" .").upper()
            if (any(ord(char) < 32 or char in '<>:\"|?*' for char in part)
                    or part.endswith((".", " ")) or stem in reserved):
                raise ProjectDocViewError("文档路径无效", code="invalid_path")
        if pure.suffix.lower() != ".md":
            raise ProjectDocViewError("只能操作 Markdown 文档", code="invalid_path")
        cursor = root
        for part in pure.parts:
            cursor = cursor / part
            try:
                is_junction = getattr(cursor, "is_junction", lambda: False)
                if cursor.is_symlink() or is_junction():
                    raise ProjectDocViewError("文档路径不允许符号链接", code="invalid_path")
            except OSError as exc:
                raise ProjectDocViewError("文档路径无效", code="invalid_path") from exc
        try:
            rel, full = _resolve_project_doc_path(root, normalized)
            resolved = full.resolve(strict=True)
            if not resolved.is_relative_to(root.resolve(strict=True)):
                raise ProjectDocViewError("文档路径无效", code="invalid_path")
        except ProjectDocViewError:
            raise
        except Exception as exc:
            raise ProjectDocViewError("文档路径无效", code="invalid_path") from exc
        return rel

    def _authorized_root(self, view_id: Any, session: Any, project_id: Any) -> tuple[str, _ViewLease, Path]:
        pid, lease = self._lease(view_id, session, project_id)
        try:
            root = project_dir(self._paths, pid).resolve(strict=True)
            st = root.stat()
            identity = (str(root), int(st.st_dev), int(st.st_ino))
        except Exception as exc:
            raise ProjectDocViewError("项目不可用", code="permission_denied") from exc
        if identity != lease.root_identity:
            with self._lock:
                self._views.pop(view_id, None)
            raise ProjectDocViewError("查看授权已失效", code="permission_denied")
        return pid, lease, root

    def validate(self, view_id: Any, session: Any, project_id: Any) -> None:
        self._authorized_root(view_id, session, project_id)

    def list(self, view_id: Any, session: Any, project_id: Any, *, request_id: str = "") -> dict[str, Any]:
        pid, lease, root = self._authorized_root(view_id, session, project_id)
        docs: list[dict[str, Any]] = []
        # Do not call project_mode.list_project_docs here: its generic walker
        # follows directory symlinks before this layer can filter their results.
        with _DOCUMENT_IO_LOCK:
            stack = [root]
            candidates: list[Path] = []
            while stack:
                current = stack.pop()
                try:
                    entries = list(os.scandir(current))
                except OSError:
                    continue
                for entry in entries:
                    path = Path(entry.path)
                    try:
                        if entry.is_symlink() or getattr(path, "is_junction", lambda: False)():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in _DOC_LIST_SKIP_DIRS:
                                stack.append(path)
                            continue
                        if entry.is_file(follow_symlinks=False) and path.suffix.lower() == ".md":
                            candidates.append(path)
                    except OSError:
                        continue
            for full in sorted(candidates, key=lambda item: item.as_posix().lower()):
                try:
                    rel = full.relative_to(root).as_posix()
                    if _catalog_skips_rel(rel):
                        continue
                    rel = self._validate_path(root, rel)
                    with _open_secure_document(root, rel, lease.root_identity) as (fd, st):
                        if "/" not in rel and _is_template_document(full.name, pid, _read_open_document(fd)):
                            continue
                        docs.append({"path": rel, "name": full.name, "size": st.st_size,
                                     "mtime": float(st.st_mtime), "is_standard": rel in PROJECT_ARTIFACTS})
                except (OSError, ProjectDocViewError, ValueError):
                    continue
        return {"type": "project.doc.view.list.done", "request_id": request_id, "project_id": pid, "docs": docs}

    def read(self, view_id: Any, session: Any, project_id: Any, path: Any, *, request_id: str = "") -> dict[str, Any]:
        pid, lease, root = self._authorized_root(view_id, session, project_id)
        rel = self._validate_path(root, path)
        try:
            with _DOCUMENT_IO_LOCK:
                with _open_secure_document(root, rel, lease.root_identity) as (fd, st):
                    content = _read_open_document(fd)
                    result = {"path": rel, "content": content, "size": len(content),
                              "mtime": float(st.st_mtime), "content_hash": _doc_content_hash(content)}
        except (ProjectModeError, OSError) as exc:
            raise ProjectDocViewError("文档读取失败", code="document_unavailable") from exc
        return {**result, "type": "project.doc.view.read.done", "request_id": request_id, "project_id": pid}

    def write(self, view_id: Any, session: Any, project_id: Any, path: Any, content: Any, *,
              base_mtime: Any = None, base_hash: Any = None, request_id: str = "", force: Any = _FORCE_UNSET) -> dict[str, Any]:
        pid, _lease, root = self._authorized_root(view_id, session, project_id)
        rel = self._validate_path(root, path)
        if force is not _FORCE_UNSET:
            raise ProjectDocViewError("不支持强制覆盖", code="force_not_supported")
        if type(content) is not str:
            raise ProjectDocViewError("content 必须是字符串", code="invalid_request")
        if (type(base_mtime) not in (int, float) or type(base_mtime) is bool
                or not math.isfinite(base_mtime)):
            raise ProjectDocViewError("保存必须包含有效版本", code="invalid_version")
        if type(base_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", base_hash) is None:
            raise ProjectDocViewError("保存必须包含有效版本", code="invalid_version")
        try:
            # Keep revocation and commit on one lease-lock boundary, and
            # serialize version-check + write process-wide across connections.
            with self._lock:
                with _DOCUMENT_IO_LOCK:
                    _pid, lease, root = self._authorized_root(view_id, session, pid)
                    rel = self._validate_path(root, rel)
                    with _open_secure_document(root, rel, lease.root_identity, writable=True) as (fd, st):
                        current_content = _read_open_document(fd)
                        current = {"path": rel, "mtime": float(st.st_mtime),
                                   "content_hash": _doc_content_hash(current_content),
                                   "size": len(current_content)}
                        if (current["content_hash"] != base_hash
                                or abs(current["mtime"] - float(base_mtime)) > 0.001):
                            raise ProjectModeError("文档已被修改", code="doc_conflict", details=current)
                        written, new_stat = _write_open_document(fd, content)
                        result = {"path": rel, "name": Path(rel).name, "size": len(written),
                                  "mtime": float(new_stat.st_mtime),
                                  "content_hash": _doc_content_hash(written)}
        except ProjectModeError as exc:
            raise ProjectDocViewError(str(exc), code=(getattr(exc, "code", "") or "document_unavailable"),
                                      current=getattr(exc, "details", None)) from exc
        except OSError as exc:
            raise ProjectDocViewError("文档写入失败", code="document_unavailable") from exc
        return {**result, "type": "project.doc.view.write.done", "request_id": request_id, "project_id": pid}

    def revoke_all(self) -> None:
        with self._lock:
            self._views.clear()
            self._epoch += 1

    def close(self, view_id: Any, session: Any, project_id: Any, *, request_id: str = "") -> dict[str, Any]:
        pid, _ = self._lease(view_id, session, project_id)
        with self._lock:
            self._views.pop(view_id, None)
        return {"type": "project.doc.view.close.done", "request_id": request_id, "project_id": pid}
