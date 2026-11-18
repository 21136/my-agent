"""Run fixed, host-injected checks against an isolated evolution candidate.

This module is a local process runner, not an OS sandbox and not an adoption,
merge, or end-to-end evolution workflow.
"""
from __future__ import annotations

import hashlib
import ctypes
import os
import re
import subprocess
import sys
import stat
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, Sequence

from self_evolution_process import WindowsJobProcess, WindowsJobUnavailable, start_windows_job_process


_MAX_SOURCE_FILES = 256
_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_DEFAULT_OUTPUT_LIMIT = 4_000
_MAX_OUTPUT_LIMIT = 32_000
_TERMINATE_GRACE_SECONDS = 0.5
_CANDIDATE_LOCK = threading.Lock()
_ACTIVE_CANDIDATES: set[str] = set()
_UNRECONCILED_CANDIDATES: set[str] = set()
_QUARANTINE_HANDLES: list[tuple[object, int]] = []
_MAX_GUARDED_CANDIDATES = 256



@dataclass(frozen=True)
class WorkspaceBinding:
    """Host resolver's verified task binding, never a tool-supplied capability.

    The host resolver must authenticate the task mapping, validate the workspace
    against its trust root and authoritative current configuration on *every*
    call, and return None or raise on failure. The resolver and this object are
    host-only dependencies: accepting them from model/tool input is unsafe.
    Identifiers are opaque non-secret revision/identity strings, not credentials.
    """

    task_id: str
    candidate_root: str | os.PathLike[str]
    candidate_id: str
    config_identity: str
    source_paths: tuple[str, ...]


@dataclass(frozen=True)
class VerificationResult:
    """Bounded result; untrusted stdout/stderr are withheld, not pattern-sanitized."""

    outcome: str  # passed | failed | unknown
    error_code: str | None
    runtime: str
    exit_code: int | None
    duration_seconds: float
    candidate_binding_digest: str
    source_digest_before: str | None
    source_digest_after: str | None
    stdout_excerpt: str
    stderr_excerpt: str
    cwd_is_candidate_root: bool
    process_tree_state: str = "not_started"  # stopped is scoped to the assigned Windows Job
    descendants_may_be_alive: bool = False  # descendants outside the Job are not observable here
    requires_reconciliation: bool = False
    automatic_retry_allowed: bool = False
    evidence_scope: str = "declared_source_paths_point_in_time_only"
    job_tree_state: str = "not_started"  # stopped only describes this Job, not broker-created processes

    @property
    def candidate_digest_before(self) -> str | None:
        return self.source_digest_before

    @property
    def candidate_digest_after(self) -> str | None:
        """Digest of host-declared source paths captured after completion, if safe."""
        return self.source_digest_after


class VerificationRunner:
    """Execute only host-selected argv entries in a candidate root.

    `commands` and `workspace_resolver` are trusted host configuration, not
    model/tool arguments. The resolver verifies identity/configuration itself;
    this runner cannot turn an untrusted resolver into an authority. Public run
    accepts only a task id, command id, timeout, and host cancellation event.
    No user environment is inherited except a small platform/runtime allowlist.

    Source snapshots include bytes and last-write identity, so ordinary changes
    restored to the same bytes are still detected; they remain point-in-time
    evidence, not proof against a process deliberately restoring timestamps.
    A successful Windows check passes only after the private Job reports zero
    active members. That scope excludes broker-created processes outside the Job.
    Cancellation, timeout, changed-source, and uncertain cleanup remain unknown.
    Windows named guards block other local processes while the owner is alive;
    they are not durable across host restart. Non-Windows launches fail closed
    because no cross-process guard is implemented.
    """

    def __init__(
        self,
        commands: Mapping[str, Sequence[str]],
        *,
        workspace_resolver: Callable[[str], WorkspaceBinding | None],
        output_limit_chars: int = _DEFAULT_OUTPUT_LIMIT,
        max_source_files: int = _MAX_SOURCE_FILES,
        max_source_bytes: int = _MAX_SOURCE_BYTES,
    ) -> None:
        if not callable(workspace_resolver):
            raise ValueError("trusted workspace_resolver is required")
        if not isinstance(commands, Mapping):
            raise ValueError("commands must be a host mapping")
        copied: dict[str, tuple[str, ...]] = {}
        for key, argv in commands.items():
            if (not isinstance(key, str) or not key or len(key) > 100
                    or not isinstance(argv, (tuple, list)) or not argv
                    or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)):
                raise ValueError("invalid host command mapping")
            copied[key] = tuple(argv)
        if not 1 <= output_limit_chars <= _MAX_OUTPUT_LIMIT:
            raise ValueError("output_limit_chars out of range")
        if max_source_files < 1 or max_source_bytes < 1:
            raise ValueError("source limits must be positive")
        self._commands = copied
        self._workspace_resolver = workspace_resolver
        self._state_lock = threading.Lock()
        self._active_tasks: set[str] = set()
        self._unreconciled_tasks: set[str] = set()
        self._output_limit = output_limit_chars
        self._max_source_files = max_source_files
        self._max_source_bytes = max_source_bytes

    def run(
        self,
        *,
        task_id: str,
        command_id: str,
        timeout_seconds: float,
        cancel_event: threading.Event | None = None,
    ) -> VerificationResult:
        # This prevents concurrent dispatches through this runner and preserves
        # uncertainty after a started run. Host-side durable gating is still required.
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 256:
            return self._run_once(task_id=task_id, command_id=command_id,
                                  timeout_seconds=timeout_seconds, cancel_event=cancel_event)
        with self._state_lock:
            refusal = ("task_not_reconciled" if task_id in self._unreconciled_tasks
                       else "task_in_progress" if task_id in self._active_tasks else None)
            if refusal is None:
                self._active_tasks.add(task_id)
        if refusal is not None:
            return VerificationResult(
                outcome="failed", error_code=refusal, runtime=_runtime_label(sys.executable),
                exit_code=None, duration_seconds=0.0,
                candidate_binding_digest=_digest_parts("unbound", task_id, str(command_id)),
                source_digest_before=None, source_digest_after=None,
                stdout_excerpt="", stderr_excerpt="", cwd_is_candidate_root=False,
                process_tree_state="unknown", descendants_may_be_alive=True,
                requires_reconciliation=True,
            )
        try:
            result = self._run_once(task_id=task_id, command_id=command_id,
                                    timeout_seconds=timeout_seconds, cancel_event=cancel_event)
            if result.requires_reconciliation:
                with self._state_lock:
                    self._unreconciled_tasks.add(task_id)
            return result
        except BaseException as exc:
            # We may have spawned after the exception. Preserve the block even
            # on interruption; do not leak exception text or infer tree status.
            with self._state_lock:
                self._unreconciled_tasks.add(task_id)
            if not isinstance(exc, Exception):
                raise
            return VerificationResult(
                outcome="unknown", error_code="runner_error", runtime=_runtime_label(sys.executable),
                exit_code=None, duration_seconds=0.0,
                candidate_binding_digest=_digest_parts("unbound", task_id, str(command_id)),
                source_digest_before=None, source_digest_after=None,
                stdout_excerpt="", stderr_excerpt="", cwd_is_candidate_root=False,
                process_tree_state="unknown", descendants_may_be_alive=True,
                requires_reconciliation=True,
            )
        finally:
            with self._state_lock:
                self._active_tasks.discard(task_id)

    def _run_once(
        self, *, task_id: str, command_id: str, timeout_seconds: float,
        cancel_event: threading.Event | None,
    ) -> VerificationResult:
        started = time.monotonic()
        after: str | None = None
        binding = _digest_parts("unbound", str(task_id), str(command_id))
        stdout = bytearray()
        stderr = bytearray()
        process: subprocess.Popen[bytes] | WindowsJobProcess | None = None
        argv: tuple[str, ...] | None = None
        spawned = False
        tree_stopped_proven = False
        candidate_keys: tuple[str, str] | None = None
        candidate_mutexes = []
        candidate_markers = []

        def result(outcome: str, error: str | None, exit_code: int | None = None,
                   after: str | None = None) -> VerificationResult:
            effective = _digest_parts(binding, after or "", after or "")
            return VerificationResult(
                outcome=outcome,
                error_code=error,
                runtime=_runtime_label(argv[0] if argv is not None else sys.executable),
                exit_code=exit_code,
                duration_seconds=max(0.0, time.monotonic() - started),
                candidate_binding_digest=effective,
                source_digest_before=after, source_digest_after=after,
                stdout_excerpt=_safe_excerpt(bytes(stdout), self._output_limit),
                stderr_excerpt=_safe_excerpt(bytes(stderr), self._output_limit),
                cwd_is_candidate_root=spawned,
                process_tree_state="unknown" if spawned else "not_started",
                descendants_may_be_alive=spawned,
                requires_reconciliation=spawned,
                evidence_scope=("declared_source_paths_and_windows_job_tree" if tree_stopped_proven or (isinstance(process, WindowsJobProcess) and process.tree_stopped) else "declared_source_paths_point_in_time_only"),
                job_tree_state=("stopped" if tree_stopped_proven or (isinstance(process, WindowsJobProcess) and process.tree_stopped) else "unknown" if spawned and os.name == "nt" else "not_started"),
            )

        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 256:
            return result("failed", "workspace_unavailable")
        argv = self._commands.get(command_id) if isinstance(command_id, str) else None
        if argv is None:
            return result("failed", "command_not_allowed")
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 86_400:
            return result("failed", "timeout_invalid")
        if cancel_event is not None and cancel_event.is_set():
            return result("unknown", "cancelled")

        verified = _resolve_workspace(self._workspace_resolver, task_id)
        if verified is None:
            return result("failed", "workspace_unavailable")
        workspace, root, root_identity = verified
        candidate_keys = ("path:" + os.path.normcase(str(root)),
                          "identity:" + str(root_identity))
        with _CANDIDATE_LOCK:
            if any(key in _ACTIVE_CANDIDATES for key in candidate_keys):
                return result("failed", "candidate_in_progress")
            if any(key in _UNRECONCILED_CANDIDATES for key in candidate_keys):
                return result("failed", "candidate_not_reconciled")
            if len(_QUARANTINE_HANDLES) + len(_ACTIVE_CANDIDATES) >= 2 * _MAX_GUARDED_CANDIDATES:
                return result("failed", "candidate_guard_capacity_exceeded")
            _ACTIVE_CANDIDATES.update(candidate_keys)
        if os.name != "nt":
            with _CANDIDATE_LOCK:
                _ACTIVE_CANDIDATES.difference_update(candidate_keys)
            return result("failed", "candidate_guard_unavailable")
        try:
            candidate_mutexes = _acquire_candidate_mutexes(candidate_keys)
        except OSError:
            with _CANDIDATE_LOCK:
                _ACTIVE_CANDIDATES.difference_update(candidate_keys)
            return result("failed", "candidate_in_progress")
        retain = True
        try:
            try:
                candidate_markers = _acquire_candidate_markers(candidate_keys)
            except OSError:
                return result("failed", "candidate_not_reconciled")
            bound_result = self._run_bound(task_id, command_id, timeout_seconds, cancel_event,
                                           started, verified, argv, binding, stdout, stderr)
            retain = bound_result.requires_reconciliation
            return bound_result
        finally:
            with _CANDIDATE_LOCK:
                if retain and candidate_markers:
                    _QUARANTINE_HANDLES.extend(candidate_markers)
                    _UNRECONCILED_CANDIDATES.update(candidate_keys)
                else:
                    for kernel, handle in candidate_markers:
                        kernel.CloseHandle(handle)
                    # The reservation is also placed in the unreconciled set before
                    # launch to close races. Release it only after the run has
                    # produced no reconciliation requirement.
                    _UNRECONCILED_CANDIDATES.difference_update(candidate_keys)
                _ACTIVE_CANDIDATES.difference_update(candidate_keys)
            _release_candidate_mutexes(candidate_mutexes)

    def _run_bound(self, task_id, command_id, timeout_seconds, cancel_event,
                   started, verified, argv, binding, stdout, stderr):
        workspace, root, root_identity = verified
        source_paths = workspace.source_paths
        before = None
        spawned = False
        process = None
        tree_stopped_proven = False
        def result(outcome, error, exit_code=None, after=None):
            return VerificationResult(
                outcome=outcome, error_code=error,
                runtime=_runtime_label(argv[0] if argv is not None else sys.executable),
                exit_code=exit_code, duration_seconds=max(0.0, time.monotonic() - started),
                candidate_binding_digest=_digest_parts(binding, before or "", after or ""),
                source_digest_before=before, source_digest_after=after,
                stdout_excerpt=_safe_excerpt(bytes(stdout), self._output_limit),
                stderr_excerpt=_safe_excerpt(bytes(stderr), self._output_limit),
                cwd_is_candidate_root=spawned,
                process_tree_state="unknown" if spawned else "not_started",
                descendants_may_be_alive=spawned, requires_reconciliation=spawned,
                evidence_scope=("declared_source_paths_and_windows_job_tree" if tree_stopped_proven or (isinstance(process, WindowsJobProcess) and process.tree_stopped) else "declared_source_paths_point_in_time_only"),
                job_tree_state=("stopped" if tree_stopped_proven or (isinstance(process, WindowsJobProcess) and process.tree_stopped) else "unknown" if spawned and os.name == "nt" else "not_started"),
            )
        try:
            before = _source_snapshot(root, source_paths, self._max_source_files, self._max_source_bytes)
        except (OSError, ValueError, TypeError, RuntimeError):
            return result("failed", "source_path_invalid")

        binding = _digest_parts(
            "self-evolution-verification-v2", task_id, workspace.candidate_id,
            workspace.config_identity, os.path.normcase(str(root)),
            str(root_identity), command_id, _digest_parts(*argv), before,
        )
        # Revalidate after snapshotting immediately after starting the process.
        # A trusted resolver must independently check its authority every time.
        if _resolve_workspace(self._workspace_resolver, task_id) != verified:
            return result("failed", "workspace_changed")
        if _snapshot_or_none(root, source_paths, self._max_source_files, self._max_source_bytes) != before:
            return result("failed", "source_changed_before_start")
        if cancel_event is not None and cancel_event.is_set():
            return result("unknown", "cancelled")

        env = _safe_child_environment()
        popen_kwargs: dict[str, object] = {
            "cwd": str(root), "env": env, "shell": False,
            "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
            "bufsize": 0,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        with _CANDIDATE_LOCK:
            _UNRECONCILED_CANDIDATES.update(("path:" + os.path.normcase(str(root)), "identity:" + str(root_identity)))
        try:
            if os.name == "nt":
                process = start_windows_job_process(argv, cwd=str(root), env=env)
            else:
                process = subprocess.Popen(argv, **popen_kwargs)  # type: ignore[arg-type]
            spawned = True
        except WindowsJobUnavailable as exc:
            after = _snapshot_or_none(root, source_paths, self._max_source_files, self._max_source_bytes)
            if exc.process_started:
                spawned = True
                with _CANDIDATE_LOCK:
                    _UNRECONCILED_CANDIDATES.update(("path:" + os.path.normcase(str(root)), "identity:" + str(root_identity)))
                tree_stopped_proven = exc.cleanup_confirmed
                return result("unknown", "process_start_cleanup_unconfirmed" if exc.started_uncertain else "process_start_failed", after=after)
            with _CANDIDATE_LOCK:
                _UNRECONCILED_CANDIDATES.difference_update(("path:" + os.path.normcase(str(root)), "identity:" + str(root_identity)))
            return result("failed", "process_start_failed", after=after)
        except (OSError, ValueError):
            with _CANDIDATE_LOCK:
                _UNRECONCILED_CANDIDATES.difference_update(("path:" + os.path.normcase(str(root)), "identity:" + str(root_identity)))
            after = _snapshot_or_none(root, source_paths, self._max_source_files, self._max_source_bytes)
            return result("failed", "process_start_failed", after=after)

        streams = ((process.stdout, stdout), (process.stderr, stderr))
        try:
            if os.name != "nt":
                for stream, _ in streams:
                    if stream is not None:
                        os.set_blocking(stream.fileno(), False)
            deadline = started + float(timeout_seconds)
            error_code: str | None = None
            if isinstance(process, WindowsJobProcess):
                while True:
                    for stream, target in streams:
                        _drain_available(stream, target, self._output_limit * 8)
                    try:
                        process.poll()
                        tree_stopped = process.active_processes() == 0
                    except OSError:
                        tree_stopped = False
                        error_code = "process_tree_query_failed"
                        break
                    if tree_stopped:
                        process.mark_tree_stopped()
                        break
                    if cancel_event is not None and cancel_event.is_set():
                        error_code = "cancelled"
                        break
                    if time.monotonic() >= deadline:
                        error_code = "timeout"
                        break
                    time.sleep(0.025)
                if error_code:
                    try:
                        process.terminate_job()
                        if process.wait_tree_stopped(_TERMINATE_GRACE_SECONDS + 1.0):
                            process.mark_tree_stopped()
                        else:
                            error_code = "process_stop_unconfirmed"
                    except OSError:
                        error_code = "process_stop_unconfirmed"
                try:
                    process.wait(timeout=_TERMINATE_GRACE_SECONDS + 1.0)
                except subprocess.TimeoutExpired:
                    error_code = error_code or "process_stop_unconfirmed"
            else:
                while process.poll() is None:
                    for stream, target in streams:
                        _drain_available(stream, target, self._output_limit * 8)
                    if cancel_event is not None and cancel_event.is_set():
                        error_code = "cancelled"
                        _stop_process(process)
                        break
                    if time.monotonic() >= deadline:
                        error_code = "timeout"
                        _stop_process(process)
                        break
                    time.sleep(min(0.025, max(0.001, deadline - time.monotonic())))
                try:
                    process.wait(timeout=_TERMINATE_GRACE_SECONDS + 1.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
                    except (OSError, subprocess.TimeoutExpired):
                        error_code = error_code or "process_stop_unconfirmed"
            for stream, target in streams:
                _drain_available(stream, target, self._output_limit * 8)
        finally:
            for stream, _ in streams:
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            if isinstance(process, WindowsJobProcess):
                process.close()
            elif process.poll() is None:
                _stop_process(process)
        final_workspace = _resolve_workspace(self._workspace_resolver, task_id)
        if final_workspace != verified:
            return result("unknown", "workspace_changed", None if error_code else process.returncode)
        after = _snapshot_or_none(root, source_paths, self._max_source_files, self._max_source_bytes)
        if error_code:
            return result("unknown", error_code, after=after)
        if after is None or after != before:
            return result("unknown", "source_changed", process.returncode, after)
        code = process.returncode
        if isinstance(process, WindowsJobProcess):
            if not process.tree_stopped:
                return result("unknown", "process_tree_unconfirmed", code, after)
        elif code == 0:
            return result("unknown", "process_tree_unconfirmed", code, after)
        if code == 0:
            # A successful check is authoritative only when the actual private
            # Job Object reported zero active members. This is deliberately
            # scoped to the registered Job, not broker-created processes outside it.
            passed = result("passed", None, code, after)
            return replace(
                passed,
                process_tree_state="stopped",
                descendants_may_be_alive=False,
                requires_reconciliation=False,
                job_tree_state="stopped",
            )
        return result("failed", "nonzero_exit", code, after)


def _acquire_candidate_mutexes(keys):
    """Guard concurrent candidate use across Windows processes; no durable recovery."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    from ctypes import wintypes
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    try:
        for key in sorted(keys):
            name = "Global\\MyAgentSelfEvolution-" + hashlib.sha256(key.encode("utf8")).hexdigest()
            handle = kernel.CreateMutexW(None, False, name)
            if not handle:
                raise OSError("candidate mutex unavailable")
            wait = kernel.WaitForSingleObject(handle, 0)
            if wait != 0:
                if wait == 0x80:
                    kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)
                raise OSError("candidate mutex busy or abandoned")
            handles.append((kernel, handle))
        return handles
    except BaseException:
        _release_candidate_mutexes(handles)
        raise


def _acquire_candidate_markers(keys):
    """Named events persist while this process lives; host must persist recovery."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    try:
        for key in sorted(keys):
            name = "Global\\MyAgentSelfEvolutionUnreconciled-" + hashlib.sha256(key.encode("utf8")).hexdigest()
            ctypes.set_last_error(0)
            handle = kernel.CreateEventW(None, True, True, name)
            error = ctypes.get_last_error()
            if not handle:
                raise OSError("candidate quarantine unavailable")
            if error == 183:  # ERROR_ALREADY_EXISTS: another runner owns unresolved evidence.
                kernel.CloseHandle(handle)
                raise OSError("candidate unresolved")
            handles.append((kernel, handle))
        return handles
    except BaseException:
        for kernel, handle in handles:
            kernel.CloseHandle(handle)
        raise


def _release_candidate_mutexes(handles):
    for kernel, handle in reversed(handles):
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _drain_available(stream, target, limit):
    """Read only bytes already available; never block on an external writer."""
    if stream is None or stream.closed:
        return
    try:
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                            wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
            kernel.PeekNamedPipe.restype = wintypes.BOOL
            available = wintypes.DWORD()
            if not kernel.PeekNamedPipe(msvcrt.get_osfhandle(stream.fileno()), None, 0, None, ctypes.byref(available), None):
                return
            size = min(8192, available.value)
            if not size:
                return
        else:
            size = 8192
        chunk = os.read(stream.fileno(), size)
        if len(target) < limit:
            target.extend(chunk[:limit - len(target)])
    except (OSError, ValueError):
        return


def _resolve_workspace(
    resolver: Callable[[str], WorkspaceBinding | None], task_id: str,
) -> tuple[WorkspaceBinding, Path, tuple[int, int]] | None:
    """Call the host authority; suppress its potentially sensitive exceptions."""
    try:
        binding = resolver(task_id)
        if (not isinstance(binding, WorkspaceBinding) or binding.task_id != task_id
                or not isinstance(binding.candidate_id, str) or not binding.candidate_id.strip()
                or not isinstance(binding.config_identity, str) or not binding.config_identity.strip()
                or len(binding.candidate_id) > 1024 or len(binding.config_identity) > 1024
                or not isinstance(binding.source_paths, tuple) or not binding.source_paths
                or any(not isinstance(item, str) for item in binding.source_paths)):
            return None
        root = Path(binding.candidate_root).resolve(strict=True)
        if not root.is_dir():
            return None
        stat_result = root.stat()
        return binding, root, (stat_result.st_dev, stat_result.st_ino)
    except Exception:
        return None


def _source_snapshot(root: Path, source_paths: Sequence[str | os.PathLike[str]],
                     max_files: int, max_bytes: int) -> str:
    if isinstance(source_paths, (str, bytes)) or not source_paths:
        raise ValueError("source_paths must be a non-empty sequence")
    if len(source_paths) > max_files:
        raise ValueError("too many source paths")
    normalized: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for raw in source_paths:
        try:
            value = os.fspath(raw)
        except TypeError as exc:
            raise ValueError("invalid source path") from exc
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise ValueError("invalid source path")
        parts = tuple(value.split("/"))
        if (value.startswith("/") or re.match(r"^[A-Za-z]:", value)
                or any(part in ("", ".", "..") for part in parts)
                or any(part.casefold() == ".git" for part in parts)
                or any(_is_windows_path_alias(part) for part in parts)):
            raise ValueError("invalid source path")
        key = "/".join(parts).casefold()
        if key in seen:
            raise ValueError("duplicate or aliased source path")
        seen.add(key)
        normalized.append(("/".join(parts), parts))
    records: list[tuple[str, str, int]] = []
    total = 0
    for rel, parts in normalized:
        path = root
        parent_chain: list[tuple[int, int]] = []
        for index, part in enumerate(parts):
            path = path / part
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode)
                    or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))):
                raise ValueError("source path contains a link or reparse point")
            if index < len(parts) - 1:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("source parent is not a directory")
                parent_chain.append((int(info.st_dev), int(info.st_ino)))
            elif not stat.S_ISREG(info.st_mode):
                raise ValueError("source is not a regular file")
        info = path.lstat()
        total += info.st_size
        if total > max_bytes:
            raise ValueError("source snapshot exceeds configured limit")
        digest = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            info_identity = (info.st_dev, info.st_ino, info.st_size, getattr(info, "st_mtime_ns", 0))
            opened_identity = (opened.st_dev, opened.st_ino, opened.st_size,
                               getattr(opened, "st_mtime_ns", 0))
            if not stat.S_ISREG(opened.st_mode) or opened_identity != info_identity:
                raise ValueError("source identity changed during open")
            size = 0
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > max_bytes or total - info.st_size + size > max_bytes:
                    raise ValueError("source snapshot exceeds configured limit")
                digest.update(block)
            after = os.fstat(stream.fileno())
            after_identity = (after.st_dev, after.st_ino, after.st_size,
                              getattr(after, "st_mtime_ns", 0))
            if after_identity != (opened.st_dev, opened.st_ino, size,
                                  getattr(opened, "st_mtime_ns", 0)):
                raise ValueError("source changed during read")
        # Confirm the lexical path still resolves through the same non-link
        # parents and leaf after reading. This narrows races but is not an OS
        # sandbox or an atomic filesystem snapshot.
        verify = root
        expected_chain: list[tuple[int, int]] = []
        for index, part in enumerate(parts):
            verify = verify / part
            current = verify.lstat()
            if (stat.S_ISLNK(current.st_mode)
                    or bool(getattr(current, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))):
                raise ValueError("source path changed to a link or reparse point")
            if index < len(parts) - 1:
                if not stat.S_ISDIR(current.st_mode):
                    raise ValueError("source parent changed type")
                expected_chain.append((int(current.st_dev), int(current.st_ino)))
            elif ((current.st_dev, current.st_ino, current.st_size,
                   getattr(current, "st_mtime_ns", 0))
                  != (opened.st_dev, opened.st_ino, size,
                      getattr(opened, "st_mtime_ns", 0))):
                raise ValueError("source path identity changed during read")
        if tuple(expected_chain) != tuple(parent_chain):
            raise ValueError("source parent identity changed during read")
        # Include last-write time as well as bytes so a check that writes and then
        # restores identical content is detected by the before/after snapshots.
        records.append((rel, digest.hexdigest(), int(getattr(opened, "st_mtime_ns", 0))))
    return _digest_parts(*(f"{path}\0{digest}\0{mtime_ns}" for path, digest, mtime_ns in sorted(records)))



def _is_windows_path_alias(part: str) -> bool:
    if ":" in part or part.endswith((".", " ")):
        return True
    stem = part.split(".", 1)[0].casefold()
    return stem in {"con", "prn", "aux", "nul"} or bool(re.fullmatch(r"(?:com|lpt)[1-9]", stem))

def _snapshot_or_none(root: Path, source_paths: Sequence[str | os.PathLike[str]],
                      max_files: int, max_bytes: int) -> str | None:
    try:
        return _source_snapshot(root, source_paths, max_files, max_bytes)
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def _runtime_label(executable: str) -> str:
    name = Path(executable).name[:80] or "unknown"
    version = f"python-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return f"{name}; host-{version}"


def _digest_parts(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        payload = part.encode("utf-8", errors="replace")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _safe_child_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _safe_excerpt(raw: bytes, limit: int) -> str:
    """Withhold untrusted output; pattern matching cannot establish secrecy."""
    return "[OUTPUT REDACTED]"[:limit] if raw else ""


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, 15)
        else:
            process.terminate()
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
