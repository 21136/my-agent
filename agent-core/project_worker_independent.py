"""Single-target, non-project writes through the shared worker coordinator and ToolExecutor.

This is an explicit host-callable interface. It is intentionally not wired into
Agent/server chat dispatch yet. Callers must provide current-session binding and
separate target-scope confirmation callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
import re
from threading import Event
from typing import Callable

from project_worker_execution import (
    PROJECT_WORKER_COORDINATOR,
    ProjectWorkerCancelled,
    ProjectWorkerCoordinator,
)
from tools.executor import ToolExecutor
from tools.schema import ToolResult, tool_fail


@dataclass(frozen=True, slots=True)
class IndependentWriteSpec:
    """Host-owned authorization for exactly one agent-root-relative file."""

    conversation_id: str
    operation_id: str
    target_path: str
    executor: ToolExecutor = field(repr=False, compare=False)
    host_binding_check: Callable[[], bool] = field(repr=False, compare=False)
    scope_confirm_fn: Callable[[str], str] = field(repr=False, compare=False)
    cancel_event: Event | None = field(default=None, repr=False, compare=False)


def _binding_is_current(spec: IndependentWriteSpec) -> bool:
    try:
        return spec.host_binding_check() is True
    except Exception:
        return False


def _validate_target(
    spec: IndependentWriteSpec,
) -> tuple[Path, str, Path, str, tuple[int, int, int], tuple[object, ...]]:
    if (
        type(spec) is not IndependentWriteSpec
        or type(spec.executor) is not ToolExecutor
        or type(spec.conversation_id) is not str
        or not spec.conversation_id.strip()
        or type(spec.operation_id) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", spec.operation_id) is None
        or type(spec.target_path) is not str
        or not callable(spec.host_binding_check)
        or not callable(spec.scope_confirm_fn)
        or spec.scope_confirm_fn is spec.executor.confirm_fn
        or not callable(spec.executor.confirm_fn)
        or (spec.cancel_event is not None and not isinstance(spec.cancel_event, Event))
    ):
        raise PermissionError("invalid independent-write authorization")

    session = spec.executor.session
    if (
        (session.active_shell or "").strip()
        or (session.project_root or "").strip()
        or (session.project_id or "").strip()
    ):
        raise PermissionError("independent writes require an unbound, non-project executor")

    raw = spec.target_path
    pure = PurePosixPath(raw)
    if (
        not raw
        or raw != raw.strip()
        or "\\" in raw
        or ":" in raw
        or raw.lower().startswith("host:")
        or pure.is_absolute()
        or pure.as_posix() != raw
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise PermissionError("independent target must be a supported agent-root-relative path")

    paths = spec.executor.registry.agent_paths
    try:
        declared_agent_root = Path(paths.agent_root).absolute()
        ProjectWorkerCoordinator._assert_no_reparse_chain(declared_agent_root)
        agent_root = declared_agent_root.resolve(strict=True)
        resolved = paths.resolve_under_agent_for_write(raw, must_exist=False)
        resolved_abs = Path(resolved).absolute()
        resolved_abs.relative_to(agent_root)

        # Reject reparse/aliasing before canonicalization can hide it.
        declared = agent_root.joinpath(*pure.parts)
        parent = declared.parent
        ProjectWorkerCoordinator._assert_no_reparse_chain(parent)
        parent_resolved = parent.resolve(strict=True)
        if parent_resolved != resolved_abs.parent.resolve(strict=True):
            raise PermissionError("independent target parent is aliased")
        relative = paths.to_agent_relative(resolved)
        if relative != raw:
            raise PermissionError("independent target is not a canonical agent-root path")

        # Reuse current path deny-list and session write-scope rules, even
        # before ToolExecutor enters its context-local write-scope guard.
        paths.resolve_under_agent_for_write(raw, must_exist=False)
        paths.assert_within_session_write_scope(
            resolved_abs, raw=raw, scope_root=spec.executor.session.write_scope_root,
        )
        ProjectWorkerCoordinator._target_identity(parent_resolved, pure.name)
        parent_identity = ProjectWorkerCoordinator._secure_directory_identity(parent_resolved)
        target_identity = ProjectWorkerCoordinator._target_identity(parent_resolved, pure.name)
    except PermissionError:
        raise
    except Exception:
        raise PermissionError("independent target is unavailable or outside supported agent paths") from None

    if not _binding_is_current(spec):
        raise PermissionError("host/session binding is not current")
    return agent_root, relative, parent_resolved, pure.name, parent_identity, target_identity




def _nearest_existing_anchor(agent_root: Path, target_parent: Path) -> Path:
    """Return the deepest existing plain directory between agent_root and parent."""
    try:
        relative = target_parent.relative_to(agent_root)
    except ValueError:
        raise PermissionError("write target is outside agent root") from None
    ProjectWorkerCoordinator._assert_no_reparse_chain(agent_root)
    anchor = agent_root
    for part in relative.parts:
        candidate = anchor / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            break
        if (candidate.is_symlink()
                or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                or getattr(candidate, "is_junction", lambda: False)()
                or not candidate.is_dir()):
            raise PermissionError("write target path contains an unsafe parent")
        anchor = candidate
    return anchor.resolve(strict=True)


def _host_executor_write_target(
    executor: ToolExecutor, operation: str, raw_path: str,
    *, conversation_id_override: str | None = None,
) -> tuple[Path, Path, Path, str, str, tuple[object, ...], str] | None:
    """Resolve one local AgentPaths target and its trusted lease anchor.

    Host/external targets are left to the established executor and remain
    explicitly outside this coordinator guarantee.
    """
    from paths import PathOutOfBoundsError
    from tools.logging import conversation_id_from_session

    paths = executor.registry.agent_paths
    raw = raw_path.strip()
    if not raw or raw.lower().startswith("host:"):
        return None
    try:
        agent_root = Path(paths.agent_root).resolve(strict=True)
        if operation == "write_text":
            resolved = paths.resolve_under_agent_for_write(raw, must_exist=False)
        else:
            resolved = paths.resolve_under_agent(raw, must_exist=False)
        target = Path(resolved).resolve(strict=False)
        agent_relative = target.relative_to(agent_root).as_posix()
    except (PathOutOfBoundsError, ValueError, OSError):
        return None
    except Exception:
        return None
    if not agent_relative or agent_relative == ".":
        return None
    pure_agent = PurePosixPath(agent_relative)
    if any(part in ("", ".", "..") for part in pure_agent.parts):
        return None

    try:
        paths.assert_within_session_write_scope(
            target, raw=raw, scope_root=executor.session.write_scope_root,
        )
        lease_kind = "independent"
        lease_root = _nearest_existing_anchor(agent_root, target.parent)
        relative = target.relative_to(lease_root).as_posix()
        if (executor.session.active_shell or "").strip() == "project":
            session_project_id = (executor.session.project_id or "").strip()
            project_root_raw = (executor.session.project_root or "").strip()
            if not session_project_id or not project_root_raw:
                raise PermissionError("project write lease lacks a trusted project binding")
            workspace_declared = Path(paths.workspace).absolute()
            ProjectWorkerCoordinator._assert_no_reparse_chain(workspace_declared)
            expected_project = workspace_declared / session_project_id
            ProjectWorkerCoordinator._assert_no_reparse_chain(expected_project)
            workspace = workspace_declared.resolve(strict=True)
            project_root = paths.resolve_under_agent(project_root_raw, must_exist=True).resolve(strict=True)
            if project_root != expected_project.resolve(strict=True) or project_root.parent != workspace:
                raise PermissionError("project write lease root does not match the session project")
            try:
                project_relative = target.relative_to(project_root).as_posix()
            except ValueError:
                project_relative = ""
            if project_relative:
                lease_kind = "project"
                lease_root = project_root
                relative = project_relative
        ProjectWorkerCoordinator._secure_directory_identity(lease_root)
        target_identity = ProjectWorkerCoordinator._target_identity(
            lease_root, relative, allow_missing_parents=True,
        )
        session_conversation_id = conversation_id_from_session(executor.session.session_dir)
        if conversation_id_override is not None:
            if (type(conversation_id_override) is not str or not conversation_id_override.strip()
                    or (session_conversation_id
                        and session_conversation_id != conversation_id_override)):
                raise PermissionError("conversation override does not match the executor session")
            conversation_id = conversation_id_override
        else:
            conversation_id = session_conversation_id
        if not conversation_id:
            raise PermissionError("shared write lease requires a host-bound conversation")
    except PermissionError:
        raise
    except Exception:
        raise PermissionError("shared write target or trusted ancestor is unsafe") from None
    return agent_root, target, lease_root, relative, lease_kind, target_identity, conversation_id


def run_tool_with_shared_write_lease(
    executor: ToolExecutor, tool_name: str, arguments: dict[str, object], *,
    conversation_id_override: str | None = None,
) -> ToolResult | None:
    """Run Agent-hosted write_text/patch_file under the shared path lease."""
    from tools.executor import _trusted_host_write_lease_context
    from tool_proxies import rewrite_proxy_tool_call

    if type(executor) is not ToolExecutor or type(arguments) is not dict:
        return None
    normalized_name, normalized_arguments = rewrite_proxy_tool_call(tool_name, arguments)
    action = executor._shared_write_action(normalized_name, normalized_arguments)
    if action is None:
        return None
    operation, raw_path = action
    try:
        info = _host_executor_write_target(
            executor, operation, raw_path,
            conversation_id_override=conversation_id_override,
        )
    except PermissionError as exc:
        return tool_fail(tool_name, "shared_write_lease_denied", str(exc))
    if info is None:
        return None
    agent_root, target, lease_root, relative, lease_kind, _initial_identity, conversation_id = info
    coordinator = PROJECT_WORKER_COORDINATOR
    label = ((executor.session.project_id or "").strip()
             if lease_kind == "project" else "independent") or "independent"
    target_key = coordinator._target_path_identity(lease_root, relative)
    parent_key = coordinator._target_parent_path_identity(lease_root, relative)
    coordinator._secure_directory_identity(lease_root)
    root_stat = lease_root.stat()
    root_identity = (root_stat.st_dev, root_stat.st_ino)
    session_binding = (
        (executor.session.active_shell or "").strip(),
        (executor.session.project_id or "").strip(),
        (executor.session.project_root or "").strip(),
    )
    agent_relative = target.relative_to(agent_root).as_posix()
    paths = executor.registry.agent_paths

    try:
        with coordinator.write_lease(
            conversation_id,
            label,
            lease_root,
            (relative,),
            kind=lease_kind,
            cancel_event=executor.cancel_event,
            allow_missing_parents=True,
        ) as lease:
            leased_identity = dict(lease.target_snapshots).get(relative)
            if (leased_identity not in lease.targets
                    or lease.root_identity != root_identity
                    or target_key not in lease.path_targets
                    or parent_key not in lease.path_targets):
                raise PermissionError("leased target identity changed or is not canonical")

            def lease_is_current() -> bool:
                try:
                    if executor.cancel_event is not None and executor.cancel_event.is_set():
                        return False
                    ProjectWorkerCoordinator._secure_directory_identity(lease_root)
                    current_root_stat = lease_root.stat()
                    if (current_root_stat.st_dev, current_root_stat.st_ino) != root_identity:
                        return False
                    current_binding = (
                        (executor.session.active_shell or "").strip(),
                        (executor.session.project_id or "").strip(),
                        (executor.session.project_root or "").strip(),
                    )
                    ProjectWorkerCoordinator._target_identity(
                        lease_root, relative, allow_missing_parents=True,
                    )
                    if operation == "write_text":
                        current_target = paths.resolve_under_agent_for_write(raw_path, must_exist=False)
                    else:
                        current_target = paths.resolve_under_agent(raw_path, must_exist=False)
                    if Path(current_target).resolve(strict=False) != target:
                        return False
                    paths.assert_within_session_write_scope(
                        target, raw=raw_path, scope_root=executor.session.write_scope_root,
                    )
                    with coordinator._condition:
                        return (
                            current_binding == session_binding
                            and agent_relative == target.relative_to(agent_root).as_posix()
                            and target_key in lease.path_targets
                            and parent_key in lease.path_targets
                            and coordinator._is_active_lease(lease)
                        )
                except Exception:
                    return False

            def prepare_target() -> None:
                if operation != "write_text":
                    return
                if executor.cancel_event is not None and executor.cancel_event.is_set():
                    raise ProjectWorkerCancelled("cancelled before creating target parents")
                parts = PurePosixPath(relative).parts
                current = lease_root
                for part in parts[:-1]:
                    if executor.cancel_event is not None and executor.cancel_event.is_set():
                        raise ProjectWorkerCancelled("cancelled while creating target parents")
                    current = current / part
                    try:
                        info = current.lstat()
                    except FileNotFoundError:
                        current.mkdir()
                        info = current.lstat()
                    if (current.is_symlink()
                            or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                            or getattr(current, "is_junction", lambda: False)()
                            or not current.is_dir()):
                        raise PermissionError("target parent became a link or non-directory")
                # Re-run the coordinator's complete target/alias checks after
                # materialization; write_text's own mkdir then becomes a no-op.
                ProjectWorkerCoordinator._target_identity(lease_root, relative)
                if not lease_is_current():
                    raise PermissionError("target or ancestor changed while creating parents")

            with _trusted_host_write_lease_context(
                executor,
                operation=operation,
                target_path=target,
                target_identity=leased_identity,
                coordinator=coordinator,
                lease=lease,
                lease_is_current=lease_is_current,
                prepare_target=prepare_target,
            ):
                return executor.run(tool_name, arguments)
    except ProjectWorkerCancelled:
        return tool_fail(tool_name, "cancelled", "file write was cancelled while waiting for its shared lease")
    except PermissionError as exc:
        return tool_fail(tool_name, "shared_write_lease_denied", str(exc))
    except Exception as exc:
        return tool_fail(
            tool_name,
            "shared_write_lease_failed",
            f"shared file-write lease failed safely ({type(exc).__name__})",
        )


def run_independent_write_from_host(
    parent_executor: ToolExecutor,
    *,
    worker_host: object,
    target_path: str,
    content: str,
    host_binding_check: Callable[[], bool],
    cancel_event: Event,
) -> ToolResult:
    """Host-callable one-file independent writer for an active main Agent turn.

    It never accepts or synthesizes a project ID. A fresh unbound ExecutorSession
    reuses the parent's real registry, write-scope fence, event/log callbacks and
    confirm bridge. The host's confirmation serializer is shared with its
    project workers so parallel confirmations cannot race the WsBridge slot.
    """
    from project_worker_host import ProjectWorkerHost
    from tools.executor import ExecutorSession
    from tools.logging import conversation_id_from_session
    from uuid import uuid4

    if (
        type(parent_executor) is not ToolExecutor
        or type(worker_host) is not ProjectWorkerHost
        or not callable(parent_executor.confirm_fn)
        or not callable(parent_executor.project_worker_host_dispatch)
        or type(target_path) is not str
        or type(content) is not str
        or not callable(host_binding_check)
        or not isinstance(cancel_event, Event)
    ):
        return tool_fail(
            "write_text", "independent_host_context_invalid",
            "a live Agent executor, worker host, explicit target and cancellation context are required",
        )

    source = parent_executor.session
    if (
        source.turn_mode != "agent"
        or source.ordinary_work_intent != "execute"
        or source.harness != "desktop"
        or source.evolution_id
        or "run_evolved" in (source.blocked_tools or set())
        or (source.allowed_evolved is not None and "write_text" not in source.allowed_evolved)
    ):
        return tool_fail(
            "write_text", "independent_host_turn_not_authorized",
            "independent writes require an executing Desktop Agent turn with write_text permission",
        )
    conversation_id = conversation_id_from_session(source.session_dir)
    if not conversation_id:
        return tool_fail(
            "write_text", "independent_conversation_unavailable",
            "host Agent conversation identity is unavailable",
        )
    if cancel_event.is_set():
        return tool_fail("write_text", "cancelled", "independent write was cancelled")

    def live_current() -> bool:
        if cancel_event.is_set():
            return False
        try:
            return host_binding_check() is True
        except Exception:
            return False

    original_confirm = parent_executor.confirm_fn
    assert callable(original_confirm)

    def serialized_confirm(label: str, preview: str) -> str:
        if not live_current():
            return "n"
        choice = worker_host._serialized_confirm(
            original_confirm, cancel_event, f"{label}\n{preview}", False,
        )
        return choice if live_current() else "n"

    def confirm_scope(preview: str) -> str:
        return serialized_confirm("独立事项目标范围确认：", preview)

    def confirm_write(preview: str, _allow_all: bool) -> str:
        return serialized_confirm("独立事项文件写入确认：", preview)

    # session_dir=None is intentional: refresh_bound_project_meta must not
    # reload the parent's project binding into this independent tool session.
    independent_executor = ToolExecutor(
        registry=parent_executor.registry,
        session=ExecutorSession(
            allowed_evolved={"write_text"},
            turn_mode="agent",
            ordinary_work_intent="execute",
            ordinary_task_scope="independent_file_write",
            write_scope_root=source.write_scope_root,
            harness="desktop",
        ),
        confirm_fn=confirm_write,
        on_event=parent_executor.on_event,
        evolve_log=parent_executor.evolve_log,
        cancel_event=cancel_event,
    )

    try:
        initial_target = _host_executor_write_target(
            independent_executor, "write_text", target_path,
            conversation_id_override=conversation_id,
        )
    except PermissionError as exc:
        return tool_fail("write_text", "permission_denied", str(exc))
    if initial_target is None:
        return tool_fail(
            "write_text", "independent_target_unsupported",
            "independent target must resolve inside the supported agent root",
        )
    canonical_target = initial_target[1]

    # Scope preview is host-authored and bound to the resolved target;
    # write_tool later performs its own normal permission + content confirmation.
    scope = {
        "action": "write_one_independent_file",
        "conversation_id": conversation_id,
        "operation_id": uuid4().hex,
        "target_path": canonical_target.relative_to(parent_executor.registry.agent_paths.agent_root).as_posix(),
        "characters": len(content),
        "write_count": 1,
    }
    import json
    try:
        scope_choice = confirm_scope(json.dumps(scope, ensure_ascii=False, sort_keys=True))
    except Exception:
        scope_choice = "n"
    if type(scope_choice) is not str or scope_choice != "y":
        return tool_fail("write_text", "confirm_rejected", "independent target scope was not approved")
    if not live_current():
        return tool_fail("write_text", "live_binding_changed", "host turn changed after scope confirmation")
    try:
        current_target = _host_executor_write_target(
            independent_executor, "write_text", target_path,
            conversation_id_override=conversation_id,
        )
    except PermissionError as exc:
        return tool_fail("write_text", "permission_denied", str(exc))
    if current_target is None or current_target[1] != canonical_target:
        return tool_fail("write_text", "target_changed", "independent target changed after scope confirmation")

    arguments = {
        "path": target_path,
        "content": content,
        "on_conflict": "overwrite",
    }
    result = run_tool_with_shared_write_lease(
        independent_executor,
        "write_text",
        arguments,
        conversation_id_override=conversation_id,
    )
    return result or tool_fail(
        "write_text", "independent_target_unsupported",
        "independent target is not a supported agent-root file path",
    )

def run_independent_write(spec: IndependentWriteSpec, content: str) -> ToolResult:
    """Confirm and write exactly one file through the existing ToolExecutor.

    Target-scope confirmation and normal ToolExecutor write confirmation are
    separate gates. Project-bound executor state is rejected rather than reused.
    """
    if type(content) is not str or len(content) > 1_000_000:
        raise PermissionError("independent write content is invalid or too large")
    (
        agent_root,
        relative,
        lease_root,
        leaf,
        parent_identity,
        target_identity,
    ) = _validate_target(spec)
    if spec.cancel_event is not None and spec.cancel_event.is_set():
        raise ProjectWorkerCancelled("cancelled before independent scope confirmation")

    preview = (
        "Independent file write (no project binding)\n"
        f"Operation: {spec.operation_id}\n"
        f"Target: {relative}\n"
        "Write count: 1"
    )
    try:
        choice = spec.scope_confirm_fn(preview)
    except Exception:
        choice = "n"
    if type(choice) is not str or choice != "y":
        return tool_fail(
            "write_text", "confirm_rejected", "independent target scope was not approved"
        )

    # Re-check after the potentially blocking user confirmation. Bind the
    # lease to the exact parent and leaf identity that were previewed.
    current = _validate_target(spec)
    if (
        current[0] != agent_root
        or current[1] != relative
        or current[2] != lease_root
        or current[3] != leaf
        or current[4] != parent_identity
        or current[5] != target_identity
    ):
        raise PermissionError("independent target changed during scope confirmation")
    if spec.cancel_event is not None and spec.cancel_event.is_set():
        raise ProjectWorkerCancelled("cancelled before independent write lease")

    # The project_id argument is a required coordinator label only; it does not
    # bind this operation to a project. Physical root/file identities arbitrate.
    with PROJECT_WORKER_COORDINATOR.write_lease(
        spec.conversation_id,
        "independent",
        lease_root,
        (leaf,),
        kind="independent",
        cancel_event=spec.cancel_event,
    ) as lease:
        leased = _validate_target(spec)
        if (
            leased[4] != parent_identity
            or leased[5] != target_identity
            or dict(lease.target_snapshots).get(leaf) != target_identity
        ):
            raise PermissionError("independent target identity changed while acquiring its lease")
        if spec.cancel_event is not None and spec.cancel_event.is_set():
            raise ProjectWorkerCancelled("cancelled before independent write confirmation")

        original_confirm = spec.executor.confirm_fn
        assert callable(original_confirm)

        def guarded_confirm(preview: str, allow_all: bool) -> str:
            if spec.cancel_event is not None and spec.cancel_event.is_set():
                return "n"
            if not _binding_is_current(spec):
                return "n"
            try:
                choice = original_confirm(preview, allow_all)
            except Exception:
                return "n"
            if spec.cancel_event is not None and spec.cancel_event.is_set():
                return "n"
            return choice if _binding_is_current(spec) else "n"

        # Keep the original executor unmodified while preserving its registry,
        # session, logging, validation, permission checks and real confirm path.
        executor = replace(
            spec.executor,
            confirm_fn=guarded_confirm,
            cancel_event=spec.cancel_event or spec.executor.cancel_event,
        )
        from tools.executor import _trusted_host_write_lease_context

        arguments = {"path": relative, "content": content, "on_conflict": "overwrite"}

        def lease_is_current() -> bool:
            try:
                current = ProjectWorkerCoordinator._target_identity(lease_root, leaf)
                with PROJECT_WORKER_COORDINATOR._condition:
                    return (
                        current == target_identity
                        and _binding_is_current(spec)
                        and PROJECT_WORKER_COORDINATOR._is_active_lease(lease)
                    )
            except Exception:
                return False

        target_path = agent_root.joinpath(*PurePosixPath(relative).parts)
        with _trusted_host_write_lease_context(
            executor,
            operation="write_text",
            target_path=target_path,
            target_identity=target_identity,
            coordinator=PROJECT_WORKER_COORDINATOR,
            lease=lease,
            lease_is_current=lease_is_current,
            prepare_target=lambda: None,
        ):
            return executor.run("write_text", arguments)
