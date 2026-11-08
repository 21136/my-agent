"""Fail-closed project write leases and execution through a real ToolExecutor.

This is a host-called building block, not Agent/server wiring or a scheduler.
The caller must supply trusted project identity, a session-bound authorization
check, an already configured ToolExecutor, and the model/tool loop callback.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import logging
import re
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import Condition, Event
from typing import Callable, Iterator, Literal, TypeVar
import os
import re
import stat
import tempfile
import time

from tools.executor import ToolExecutor
from tool_proxies import rewrite_proxy_tool_call
from tools.schema import ToolResult
from project_worker_read import ProjectReadError, ProjectReadTransientError, ProjectTextReader

T = TypeVar("T")
WorkerKind = Literal["project", "independent"]
PROJECT_READ_RETRY_BACKOFF_SECONDS = 0.05


def _log_worker_timing(phase: str, duration_ms: int, **fields: int) -> None:
    """Emit content-free worker timings through the established sidecar logger."""
    metrics = " ".join(f"{key}={value}" for key, value in fields.items())
    logging.getLogger("my_agent.sidecar").info(
        "event=project_worker_timing phase=%s duration_ms=%d%s",
        phase, duration_ms, f" {metrics}" if metrics else "",
    )


def _project_worker_batch_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _log_project_worker_batch_event(
    phase: str, *, batch_id: str, project_id: str, index: int,
    queued_at: str, at: str, status: str = "",
) -> None:
    """Append a content-free batch lifecycle event using a strict field allowlist."""
    if (phase not in {"queued", "authorized", "started", "ended"}
            or type(batch_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", batch_id)
            or type(project_id) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", project_id)
            or type(index) is not int or index < 0
            or type(queued_at) is not str
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", queued_at)
            or type(at) is not str
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", at)):
        return
    fields = [
        "event=project_worker_batch", f"phase={phase}", f"batch_id={batch_id}",
        f"project_id={project_id}", f"index={index}", f"queued_at={queued_at}", f"at={at}",
    ]
    if phase == "authorized":
        fields.append(f"authorized_at={at}")
    elif phase == "started":
        fields.append(f"started_at={at}")
    elif phase == "ended":
        if status not in {"model_turn_finished", "blocked", "failed", "cancelled", "limit_reached"}:
            return
        fields.extend((f"ended_at={at}", f"status={status}"))
    logging.getLogger("my_agent.sidecar").info(" ".join(fields))


def _lock_file_for_identity(identity: tuple[object, ...]) -> Path:
    """Stable per-user lock file keyed by physical root/file identity."""
    payload = json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    digest = hashlib.sha256(payload).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / "my-agent-project-worker-locks-v1"
    lock_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
    try:
        info = lock_dir.lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                or getattr(lock_dir, "is_junction", lambda: False)()):
            raise OSError("worker lock directory is not a plain directory")
        if hasattr(os, "getuid"):
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise OSError("worker lock directory is not private to this user")
    except OSError:
        raise PermissionError("private cross-process lock storage is unavailable") from None
    return lock_dir / f"{digest}.lock"


@contextmanager
def _cross_process_file_locks(identities: frozenset[tuple[object, ...]],
                               cancel_event: Event | None) -> Iterator[None]:
    """Acquire crash-released OS byte-range locks in a stable order (including Windows)."""
    handles: list[tuple[int, Path]] = []
    try:
        for identity in sorted(identities, key=lambda item: json.dumps(item, separators=(",", ":"))):
            if cancel_event is not None and cancel_event.is_set():
                raise ProjectWorkerCancelled("cancelled while waiting for cross-process project lease")
            path = _lock_file_for_identity(identity)
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(fd)
                raise PermissionError("cross-process lock file is unsafe")
            handles.append((fd, path))
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise ProjectWorkerCancelled("cancelled while waiting for cross-process project lease")
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    lock_contention = exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)
                    lock_contention = lock_contention or getattr(exc, "winerror", None) in (33, 36)
                    if not lock_contention:
                        raise
                    time.sleep(0.05)
        yield
    finally:
        for fd, _path in reversed(handles):
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                os.close(fd)


@dataclass(frozen=True, slots=True)
class ProjectWorkerSpec:
    conversation_id: str
    project_id: str
    project_root: Path
    write_targets: tuple[str, ...]
    executor: ToolExecutor = field(repr=False, compare=False)
    host_binding_check: Callable[[], bool] = field(repr=False, compare=False)
    kind: WorkerKind = "project"
    read_targets: tuple[str, ...] = ()
    cancel_event: Event | None = field(default=None, repr=False, compare=False)
    outbound_request_confirm_fn: Callable[[str], str] | None = field(default=None, repr=False, compare=False)
    live_run_id: str = field(default="", repr=False)
    turn_epoch: int = field(default=0, repr=False)


@dataclass(frozen=True, slots=True)
class _Lease:
    conversation_id: str
    project_id: str
    root_identity: tuple[int, int]
    targets: frozenset[tuple[object, ...]]
    path_targets: frozenset[tuple[object, ...]]
    target_snapshots: tuple[tuple[str, tuple[object, ...]], ...]
    kind: WorkerKind
    ticket: int


@dataclass(frozen=True, slots=True)
class ProjectReadAttempt:
    read_id: int
    path: str
    attempt: int
    result: str
    error_code: str = ""


class ProjectWorkerReadFailure(PermissionError):
    """A read failed safely; error_code distinguishes retry exhaustion."""
    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)


class ProjectWorkerCancelled(Exception):
    """Worker was cancelled before a new operation could start."""


class ProjectWorkerCoordinator:
    """In-process per-conversation modification admission and scoped execution."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._next_ticket = 0
        # Physical project/file locks are coordinator-wide; only admission caps
        # are partitioned by conversation_id.
        self._waiting: list[_Lease] = []
        self._active: list[_Lease] = []
        self._admitted: set[int] = set()

    def _prepare_lease(self, conversation_id: str, project_id: str,
                       project_root: Path, write_targets: tuple[str, ...],
                       kind: WorkerKind, *, allow_missing_parents: bool = False) -> _Lease:
        if (type(conversation_id) is not str or not conversation_id.strip()
                or type(project_id) is not str or not project_id.strip()
                or not isinstance(project_root, Path) or kind not in ("project", "independent")
                or type(write_targets) is not tuple or not write_targets
                or type(allow_missing_parents) is not bool):
            raise PermissionError("invalid worker scope")
        try:
            root = project_root.resolve(strict=True)
            st = root.stat()
            if not root.is_dir():
                raise ValueError
            if not st.st_ino:
                raise ValueError
            root_id = (st.st_dev, st.st_ino)
            snapshots = tuple(
                (path, self._target_identity(root, path, allow_missing_parents=allow_missing_parents))
                for path in write_targets
            )
            targets = frozenset(identity for _path, identity in snapshots)
            canonical_targets = tuple(self._target_path_identity(root, path) for path in write_targets)
            if len(set(canonical_targets)) != len(write_targets):
                raise ValueError
            path_targets = frozenset(
                identity
                for path in write_targets
                for identity in (
                    self._target_path_identity(root, path),
                    self._target_parent_path_identity(root, path),
                )
            )
            if len(targets) != len(write_targets):
                raise ValueError
        except Exception:
            raise PermissionError("project scope unavailable or unsafe") from None
        return _Lease(conversation_id, project_id, root_id, targets, path_targets, snapshots, kind, -1)

    @staticmethod
    def _target_path_identity(root: Path, relative: str) -> tuple[object, ...]:
        """Canonical absolute key stable while missing parent directories appear."""
        pure = PurePosixPath(relative)
        target = root.joinpath(*pure.parts)
        return ("absolute-target", os.path.normcase(str(target.absolute())))

    @staticmethod
    def _target_parent_path_identity(root: Path, relative: str) -> tuple[object, ...]:
        """Parent key also serializes rename/alternate-name writes in one directory."""
        pure = PurePosixPath(relative)
        parent = root.joinpath(*pure.parts[:-1])
        return ("absolute-parent", os.path.normcase(str(parent.absolute())))

    @staticmethod
    def _target_identity(
        root: Path, relative: str, *, allow_missing_parents: bool = False,
    ) -> tuple[object, ...]:
        if (type(relative) is not str or not relative or "\\" in relative or ":" in relative
                or any(ord(char) < 32 or char in '<>"|?*~' for char in relative)):
            raise ValueError("invalid relative target")
        pure = PurePosixPath(relative)
        if (pure.is_absolute() or pure.as_posix() != relative
                or any(part in ("", ".", "..") or part.endswith((".", " "))
                       or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\..*)?", part)
                       for part in pure.parts)):
            raise ValueError("invalid relative target")
        target = root.joinpath(*pure.parts)
        current = root
        for index, part in enumerate(pure.parts[:-1]):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not allow_missing_parents:
                    raise ValueError("target parent must exist") from None
                ancestor = current.parent.stat()
                if not ancestor.st_ino:
                    raise ValueError("existing ancestor identity unavailable")
                suffix = tuple(item.casefold() for item in pure.parts[index:])
                return ("planned", ancestor.st_dev, ancestor.st_ino, suffix)
            if (stat.S_ISLNK(info.st_mode)
                    or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                    or getattr(current, "is_junction", lambda: False)()):
                raise ValueError("linked path component")
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("target parent is not a directory")
        parent = target.parent.resolve(strict=True)
        parent.relative_to(root)
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
            raise ValueError("linked target")
        if target.exists():
            resolved = target.resolve(strict=True)
            resolved.relative_to(root)
            info = resolved.stat()
            if not resolved.is_file():
                raise ValueError("target is not a regular file")
            if not info.st_ino:
                raise ValueError("file identity unavailable")
            return ("file", info.st_dev, info.st_ino)
        parent_info = parent.stat()
        if not parent_info.st_ino:
            raise ValueError("parent identity unavailable")
        return ("new", parent_info.st_dev, parent_info.st_ino, target.name.casefold())

    @staticmethod
    def _conflicts(a: _Lease, b: _Lease) -> bool:
        # Projects and physical files are globally exclusive, even across
        # conversations and between project/independent work kinds.
        return (a.root_identity == b.root_identity
                or bool(a.targets.intersection(b.targets))
                or bool(a.path_targets.intersection(b.path_targets)))

    @staticmethod
    def _can_start(lease: _Lease, active: list[_Lease]) -> bool:
        if any(ProjectWorkerCoordinator._conflicts(lease, item) for item in active):
            return False
        in_conversation = [item for item in active if item.conversation_id == lease.conversation_id]
        if lease.kind == "independent":
            return sum(item.kind == "independent" for item in in_conversation) < 1
        active_projects = {item.root_identity for item in in_conversation if item.kind == "project"}
        return len(active_projects) < 2

    def _schedule_locked(self) -> None:
        """Reserve all currently eligible FIFO requests while holding the condition."""
        index = 0
        while index < len(self._waiting):
            lease = self._waiting[index]
            # Do not let later work overtake an earlier waiter for the same
            # physical project/file. Unrelated conversations and targets proceed.
            if any(self._conflicts(lease, earlier) for earlier in self._waiting[:index]):
                index += 1
                continue
            if self._can_start(lease, self._active):
                self._waiting.pop(index)
                self._active.append(lease)
                self._admitted.add(lease.ticket)
                # Keep index: the following queued request may also be eligible.
                continue
            index += 1
        self._condition.notify_all()

    @contextmanager
    def write_lease(self, conversation_id: str, project_id: str, project_root: Path,
                    write_targets: tuple[str, ...], *, kind: WorkerKind = "project",
                    cancel_event: Event | None = None,
                    allow_missing_parents: bool = False) -> Iterator[_Lease]:
        request = self._prepare_lease(
            conversation_id, project_id, project_root, write_targets, kind,
            allow_missing_parents=allow_missing_parents,
        )
        admission_started = time.perf_counter()
        with self._condition:
            ticket = self._next_ticket
            self._next_ticket += 1
            lease = _Lease(request.conversation_id, request.project_id, request.root_identity,
                           request.targets, request.path_targets, request.target_snapshots,
                           request.kind, ticket)
            self._waiting.append(lease)
            self._schedule_locked()
            try:
                while ticket not in self._admitted:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ProjectWorkerCancelled("cancelled while waiting for project write lease")
                    self._condition.wait(timeout=0.05)
                self._admitted.remove(ticket)
            except BaseException:
                if lease in self._waiting:
                    self._waiting.remove(lease)
                if lease in self._active:
                    self._active.remove(lease)
                self._admitted.discard(ticket)
                self._schedule_locked()
                raise
        admission_wait_ms = max(0, int((time.perf_counter() - admission_started) * 1000))
        cross_process_wait_ms = 0
        try:
            # Re-resolve after both local admission and OS-lock waiting. A path key
            # bridges new->file transitions; inode keys serialize hard-link aliases.
            current = lease
            identity_retries = 0
            while True:
                lock_ids = frozenset({current.root_identity, *current.targets, *current.path_targets})
                lock_started = time.perf_counter()
                with _cross_process_file_locks(lock_ids, cancel_event):
                    cross_process_wait_ms += max(0, int((time.perf_counter() - lock_started) * 1000))
                    refreshed = self._prepare_lease(
                        conversation_id, project_id, project_root, write_targets, kind,
                        allow_missing_parents=allow_missing_parents,
                    )
                    if refreshed.root_identity != lease.root_identity:
                        raise PermissionError("project root identity changed while waiting for write lease")
                    if (refreshed.targets != current.targets
                            or refreshed.path_targets != current.path_targets
                            or refreshed.target_snapshots != current.target_snapshots):
                        identity_retries += 1
                        if identity_retries > 8:
                            raise PermissionError("write target kept changing while acquiring its lease")
                        current = _Lease(
                            refreshed.conversation_id, refreshed.project_id, refreshed.root_identity,
                            refreshed.targets, refreshed.path_targets, refreshed.target_snapshots,
                            refreshed.kind, lease.ticket,
                        )
                        continue
                    _log_worker_timing(
                        "lease_wait", admission_wait_ms + cross_process_wait_ms,
                        admission_ms=admission_wait_ms, cross_process_lock_ms=cross_process_wait_ms,
                    )
                    yield current
                    break
        finally:
            with self._condition:
                if lease in self._active:
                    self._active.remove(lease)
                self._schedule_locked()

    def _is_active_lease(self, lease: _Lease) -> bool:
        """Check the stable admission ticket, including refreshed target snapshots."""
        try:
            with self._condition:
                return any(
                    item.ticket == lease.ticket
                    and item.conversation_id == lease.conversation_id
                    and item.root_identity == lease.root_identity
                    and item.path_targets == lease.path_targets
                    for item in self._active
                )
        except Exception:
            return False

    @staticmethod
    def _host_is_current(spec: ProjectWorkerSpec) -> bool:
        try:
            return spec.host_binding_check() is True
        except Exception:
            return False

    @staticmethod
    def _secure_directory_identity(path: Path) -> tuple[int, int, int]:
        try:
            reader = ProjectTextReader(path)
            identity = reader._root_identity
        except ProjectReadError:
            raise PermissionError("project root chain is unsafe") from None
        if identity is None:
            raise PermissionError("secure project-root identity is unavailable")
        return identity

    @staticmethod
    def _directory_stat_identity(path: Path) -> tuple[int, int]:
        """Return the OS file identity used to pin an admitted root across phases."""
        try:
            info = path.stat()
            if not path.is_dir() or not info.st_ino:
                raise ValueError
            return info.st_dev, info.st_ino
        except Exception:
            raise PermissionError("project root identity is unavailable") from None

    @staticmethod
    def _assert_no_reparse_chain(path: Path) -> None:
        """Check the declared path, not only its resolved destination."""
        absolute = Path(path).absolute()
        if not absolute.anchor:
            raise PermissionError("project path must be absolute")
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            if component in (".", ".."):
                raise PermissionError("project path contains traversal")
            current = current / component
            try:
                info = current.lstat()
            except OSError:
                raise PermissionError("project path component is unavailable") from None
            is_junction = getattr(current, "is_junction", lambda: False)()
            is_reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
            if stat.S_ISLNK(info.st_mode) or is_junction or is_reparse:
                raise PermissionError("project path contains a link or reparse point")
            if not current.is_dir():
                raise PermissionError("project path component is not a directory")

    @staticmethod
    def _validate_spec(spec: ProjectWorkerSpec) -> tuple[Path, str]:
        if (type(spec) is not ProjectWorkerSpec or type(spec.executor) is not ToolExecutor
                or not callable(spec.host_binding_check) or type(spec.write_targets) is not tuple
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", spec.project_id) is None
                or spec.project_id in {".", ".."}):
            raise PermissionError("trusted ToolExecutor and host binding are required")
        executor = spec.executor
        session = executor.session
        paths = executor.registry.agent_paths
        try:
            declared_workspace = Path(paths.workspace).absolute()
            declared_project = declared_workspace / spec.project_id
            declared_spec_root = Path(spec.project_root).absolute()
            ProjectWorkerCoordinator._assert_no_reparse_chain(declared_workspace)
            ProjectWorkerCoordinator._secure_directory_identity(declared_workspace)

            try:
                from external_project import validate_external_binding
                from project_mode import project_dir
                external_binding = validate_external_binding(paths, spec.project_id)
            except Exception:
                external_binding = None

            if external_binding is not None:
                # Keep the logical workspace binding validated by the external
                # registry, but pin worker I/O to the canonical target itself.
                # The declared target must remain a non-reparse path and match the
                # registry's current physical identity at every validation point.
                registered_root = Path(external_binding.external_root).resolve(strict=True)
                ProjectWorkerCoordinator._assert_no_reparse_chain(registered_root)
                declared_root = Path(project_dir(paths, spec.project_id)).resolve(strict=True)
                if (registered_root != declared_root
                        or str(registered_root) != external_binding.external_root):
                    raise PermissionError("external project binding changed")
                expected_identity = ProjectWorkerCoordinator._secure_directory_identity(registered_root)
                if declared_spec_root.resolve(strict=True) != registered_root:
                    raise PermissionError("worker root does not match the registered external project")
                project_root = registered_root
                expected = registered_root
                workspace = declared_workspace.resolve(strict=True)
                expected_identity_root = expected_identity
            else:
                ProjectWorkerCoordinator._assert_no_reparse_chain(declared_project)
                ProjectWorkerCoordinator._assert_no_reparse_chain(declared_spec_root)
                expected_identity_root = ProjectWorkerCoordinator._secure_directory_identity(declared_project)
                project_root = declared_spec_root.resolve(strict=True)
                workspace = declared_workspace.resolve(strict=True)
                expected = declared_project.resolve(strict=True)

            spec_identity = ProjectWorkerCoordinator._secure_directory_identity(declared_spec_root)
            if expected_identity_root != spec_identity:
                raise PermissionError("declared project root identity does not match the executor binding")
        except PermissionError:
            raise
        except Exception:
            raise PermissionError("project root is unavailable") from None
        if external_binding is None and (expected.parent != workspace or expected != declared_project):
            raise PermissionError("declared project root is aliased or escapes the workspace")
        if (project_root != expected or not project_root.is_dir()
                or (session.active_shell or "").strip() != "project"
                or (session.project_id or "").strip() != spec.project_id
                or (session.project_root or "").replace("\\", "/").strip("/").casefold()
                   != f"workspace/{spec.project_id}".casefold()
                or not callable(executor.confirm_fn)):
            raise PermissionError("executor is not bound to the requested project")
        if not ProjectWorkerCoordinator._host_is_current(spec):
            raise PermissionError("host project/session binding is not current")
        return project_root, f"workspace/{spec.project_id}"

    def run_project_worker(self, spec: ProjectWorkerSpec, worker: Callable[["ScopedProjectTools"], T]) -> T:
        if not callable(worker):
            raise PermissionError("worker loop is required")
        root, agent_relative_root = self._validate_spec(spec)
        if (type(spec.read_targets) is not tuple or len(set(spec.read_targets)) != len(spec.read_targets)):
            raise PermissionError("invalid declared read targets")
        for target in spec.read_targets:
            try:
                self._target_identity(root, target)
            except Exception:
                raise PermissionError("declared read target is unsafe") from None
        with self.write_lease(spec.conversation_id, spec.project_id, root,
                              spec.write_targets, kind=spec.kind,
                              cancel_event=spec.cancel_event) as acquired:
            scoped = ScopedProjectTools(
                spec, root, agent_relative_root, dict(acquired.target_snapshots),
            )
            return worker(scoped)


class ScopedProjectTools:
    """Only declared flat file writes; every call passes through ToolExecutor."""

    __slots__ = ("_spec", "_root", "_root_identity", "_agent_relative_root", "_declared", "_read_declared",
                 "_leased_targets", "_write_attempted", "_read_attempts", "_read_sequence")

    def __init__(self, spec: ProjectWorkerSpec, root: Path, agent_relative_root: str,
                 leased_targets: dict[str, tuple[object, ...]]) -> None:
        self._spec = spec
        self._root = root
        self._root_identity = ProjectWorkerCoordinator._directory_stat_identity(root)
        self._agent_relative_root = agent_relative_root
        self._declared = frozenset(spec.write_targets)
        self._read_declared = frozenset(spec.read_targets)
        self._leased_targets = dict(leased_targets)
        self._write_attempted: set[str] = set()
        self._read_attempts: list[ProjectReadAttempt] = []
        self._read_sequence = 0

    def _assert_current_binding(self) -> None:
        root, _ = ProjectWorkerCoordinator._validate_spec(self._spec)
        try:
            identity_matches = (
                root == self._root
                and ProjectWorkerCoordinator._directory_stat_identity(root) == self._root_identity
            )
        except Exception:
            identity_matches = False
        if not identity_matches or not ProjectWorkerCoordinator._host_is_current(self._spec):
            raise PermissionError("host project/session binding changed")

    def assert_current_binding(self) -> None:
        if self._spec.cancel_event is not None and self._spec.cancel_event.is_set():
            raise ProjectWorkerCancelled("cancelled before project operation")
        self._assert_current_binding()

    @property
    def read_attempts(self) -> tuple[ProjectReadAttempt, ...]:
        """Content-free per-read attempt evidence, suitable for outcome reporting."""
        return tuple(self._read_attempts)

    def read_snapshot(self, relative_path: str) -> tuple[str, tuple[object, ...]]:
        """Read one authorized file; retry only typed transient Windows lock errors."""
        if type(relative_path) is not str or relative_path not in self._read_declared:
            raise PermissionError("read target was not declared by host")
        self._read_sequence += 1
        read_id = self._read_sequence
        original_identity: tuple[object, ...] | None = None
        for attempt in range(1, 7):
            try:
                # Every attempt, including retries, revalidates cancellation and host scope.
                self.assert_current_binding()
                before = ProjectWorkerCoordinator._target_identity(self._root, relative_path)
                if original_identity is None:
                    original_identity = before
                elif before != original_identity:
                    raise ProjectReadError("file identity changed between read attempts")
                content = ProjectTextReader(self._root).read_texts([relative_path])[relative_path]
                after = ProjectWorkerCoordinator._target_identity(self._root, relative_path)
                if before != after or after != original_identity:
                    raise ProjectReadError("file identity changed during snapshot")
                self._read_attempts.append(ProjectReadAttempt(read_id, relative_path, attempt, "success"))
                return content, after
            except ProjectWorkerCancelled:
                self._read_attempts.append(ProjectReadAttempt(read_id, relative_path, attempt, "cancelled"))
                raise
            except ProjectReadTransientError as exc:
                self._read_attempts.append(ProjectReadAttempt(read_id, relative_path, attempt, "retrying" if attempt < 6 else "failed",
                                                              exc.error_code))
                if attempt == 6:
                    raise ProjectWorkerReadFailure("read_retry_exhausted") from None
                # Recheck all authority and physical identity before another read syscall.
                try:
                    self.assert_current_binding()
                except ProjectWorkerCancelled:
                    self._read_attempts.append(ProjectReadAttempt(read_id, relative_path, attempt, "cancelled"))
                    raise
                try:
                    current = ProjectWorkerCoordinator._target_identity(self._root, relative_path)
                except Exception:
                    raise PermissionError("declared project read target changed") from None
                if current != original_identity:
                    raise PermissionError("declared project read target changed")
                cancel_event = self._spec.cancel_event
                if not isinstance(cancel_event, Event):
                    raise ProjectWorkerReadFailure("retry_cancel_event_unavailable") from None
                if cancel_event.wait(PROJECT_READ_RETRY_BACKOFF_SECONDS):
                    self._read_attempts.append(ProjectReadAttempt(read_id, relative_path, attempt, "cancelled"))
                    raise ProjectWorkerCancelled("cancelled during project read retry backoff")
            except Exception:
                self._read_attempts.append(ProjectReadAttempt(read_id, relative_path, attempt, "failed", "non_retryable"))
                raise PermissionError("declared project read failed safely") from None
        raise PermissionError("declared project read failed safely")

    def read_text(self, relative_path: str) -> str:
        return self.read_snapshot(relative_path)[0]

    def verify_written_snapshot(self, relative_path: str) -> tuple[str, tuple[object, ...]]:
        """Secure read-back and identity for an already declared write target."""
        self.assert_current_binding()
        if relative_path not in self._declared:
            raise PermissionError("verification target was not declared by host")
        try:
            before = ProjectWorkerCoordinator._target_identity(self._root, relative_path)
            content = ProjectTextReader(self._root).read_texts([relative_path])[relative_path]
            after = ProjectWorkerCoordinator._target_identity(self._root, relative_path)
            if before != after:
                raise ProjectReadError("write identity changed during read-back")
            return content, after
        except Exception:
            raise PermissionError("write read-back failed safely") from None

    def verify_written_text(self, relative_path: str) -> str:
        return self.verify_written_snapshot(relative_path)[0]

    def has_write_attempted(self, relative_path: str) -> bool:
        return relative_path in self._write_attempted

    def run(self, tool_name: str, arguments: dict[str, object]) -> ToolResult:
        if self._spec.cancel_event is not None and self._spec.cancel_event.is_set():
            raise ProjectWorkerCancelled("cancelled before project write")
        self._assert_current_binding()
        if tool_name not in {"write_text", "patch_file"} or type(arguments) is not dict:
            raise PermissionError("worker tool is not exposed")
        relative = arguments.get("path")
        if type(relative) is not str or relative not in self._declared:
            raise PermissionError("write target was not declared by host")
        if relative in self._write_attempted:
            raise PermissionError("write_already_attempted")
        current_identity = ProjectWorkerCoordinator._target_identity(self._root, relative)
        if self._leased_targets.get(relative) != current_identity:
            raise PermissionError("write target identity changed after lease acquisition")
        self._write_attempted.add(relative)
        name, args = rewrite_proxy_tool_call(tool_name, dict(arguments))
        nested = args.get("arguments")
        if type(nested) is not dict:
            raise PermissionError("tool arguments are invalid")
        nested["path"] = f"{self._agent_relative_root}/{relative}"
        result = self._spec.executor.run(name, args)
        if not (not result.ok and result.error is not None and result.error.code in {
            "confirm_rejected", "permission_denied", "path_denied", "path_out_of_bounds",
        }):
            self._assert_current_binding()
        return result


PROJECT_WORKER_COORDINATOR = ProjectWorkerCoordinator()


def run_project_worker(spec: ProjectWorkerSpec, worker: Callable[[ScopedProjectTools], T]) -> T:
    """Host entrypoint using the shared process-local conversation coordinator."""
    return PROJECT_WORKER_COORDINATOR.run_project_worker(spec, worker)


