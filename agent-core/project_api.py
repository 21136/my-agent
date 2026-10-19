"""WebSocket / desktop API for workspace project mode (PROJECT-MODE T-1109)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, Callable

_AGENT_CORE = Path(__file__).resolve().parent
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from paths import AgentPaths
from project_cli import (
    confirm_direct_implement,
    confirm_project_design,
)
from project_mode import (
    ProjectModeError,
    acceptance_script_exists,
    acceptance_workspace_path,
    count_untouched_template_docs,
    create_project_doc,
    compute_execution_stage,
    delete_project_doc,
    detect_potential_project,
    list_project_docs,
    list_projects,
    normalize_project_id,
    parse_acceptance_spec,
    project_dir,
    read_project_artifacts,
    read_project_doc,
    read_project_template,
    rename_project_doc,
    restore_project_doc_from_trash,
    run_acceptance_check,
    write_project_doc,
)
from project_release import load_release_acceptance, save_release_acceptance
from session import Session, corruption_notice_events, session_banner_event, utc_now_iso

EmitFn = Callable[[dict[str, Any]], None]

_PLAN_PREVIEW_MAX = 4000
_PROJECT_SUMMARY_MAX = 1200
class ProjectApiError(Exception):
    """Invalid project WS message."""



def _project_summary(project_md: str) -> str:
    text = project_md.strip()
    if not text:
        return "（PROJECT.md 为空）"
    if len(text) <= _PROJECT_SUMMARY_MAX:
        return text
    return text[:_PROJECT_SUMMARY_MAX] + "\n…(truncated)"



def _git_branch(project_root: Path) -> str:
    """Best-effort current branch (or short detached sha) from .git/HEAD; never raises."""
    try:
        git = project_root / ".git"
        if git.is_file():  # worktree / submodule: "gitdir: <path>"
            text = git.read_text(encoding="utf-8", errors="replace").strip()
            if text.startswith("gitdir:"):
                gitdir = Path(text[7:].strip())
                git = gitdir if gitdir.is_absolute() else (project_root / gitdir)
        head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        ref = head[4:].strip()
        return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    return head[:7]


def project_state_payload(session: Session, paths: AgentPaths) -> dict[str, Any]:
    from project_mode import classify_stage_documents, get_delivery_profile
    from external_project import validate_external_binding
    from project_manifest import (
        manifest_blocks_on_l2_stale,
        manifest_payload,
        project_template_of,
        refresh_project_manifest,
    )

    pid = (session.meta.project_id or "").strip()
    root = (session.meta.project_root or "").strip()
    plan_status = session.meta.project_plan_status or "draft"
    workflow_stage = getattr(session.meta, "project_workflow_stage", "requirements") or "requirements"
    artifacts = read_project_artifacts(paths, pid) if pid else {}
    manifest = None
    manifest_error = None
    if pid:
        try:
            manifest = refresh_project_manifest(paths, pid)
        except Exception as exc:
            manifest_error = str(exc)
    map_md = artifacts.get("MAP.md", "")
    review_verdict = getattr(session, "last_review_verdict", None) or None
    review_blockers_count = int(getattr(session, "last_review_blockers_count", 0) or 0)
    stage = compute_execution_stage(
        project_id=pid,
        plan_status=plan_status,
        manifest=manifest,
        project_root=project_dir(paths, pid) if pid else None,
        review_verdict=review_verdict,
        review_blockers_count=review_blockers_count,
        workflow_stage=workflow_stage,
    )
    stage_documents = classify_stage_documents(
        manifest,
        stage=str(stage["stage"]),
        blockers=list(stage.get("blockers", [])),
    )
    release_artifact = next(
        (item for item in (manifest or {}).get("artifacts", [])
         if isinstance(item, dict) and item.get("path") == "RELEASE.md"),
        None,
    )
    release_acceptance = load_release_acceptance(
        project_dir(paths, pid),
        pid,
        release_revision=(
            str(release_artifact.get("revision"))
            if release_artifact and release_artifact.get("status") == "current"
            else None
        ),
    ) if pid else {"accepted": False, "accepted_at": None, "release_revision": None, "checklist": {}}
    acceptance = parse_acceptance_spec(artifacts.get("PROJECT.md", "")) if pid else None
    can_verify = bool(
        pid
        and acceptance is not None
        and acceptance_script_exists(paths, pid, acceptance)
    )
    display_directory = project_dir(paths, pid) if pid else None
    payload = {
        "type": "project.state",
        "project_id": pid or None,
        "project_root": root or None,
        # Human-facing metadata only. Keep the logical ID/root for routing and tools.
        "project_display_name": display_directory.name if display_directory else None,
        "project_directory": str(display_directory) if display_directory else None,
        "git_branch": _git_branch(project_dir(paths, pid)) if pid else "",
        "external_project": (
            {
                "adopted": True,
                "healthy": bool(validate_external_binding(paths, pid)),
            }
            if pid and validate_external_binding(paths, pid) is not None
            else {"adopted": False, "healthy": True}
        ),
        "plan_status": plan_status,
        "workflow_stage": workflow_stage,
        "project_entry": getattr(session.meta, "project_entry", "") or "",
        "design_confirmed_at": getattr(session.meta, "project_design_confirmed_at", "") or None,
        "map_markdown": map_md,
        "project_summary": _project_summary(artifacts.get("PROJECT.md", "")),
        # Plan/design confirmation gates were removed with the task subsystem.
        "needs_design_confirm": False,
        "needs_documentation": False,
        "ordinary_permission_source": "request_intent_path_risk",
        "ordinary_stage_gates_blocking": False,
        "acceptance_command": acceptance.display if acceptance else None,
        "acceptance_expected_exit": acceptance.expected_exit_code if acceptance else None,
        "can_verify": can_verify,
        "scope_confirmed_at": getattr(session.meta, "project_scope_confirmed_at", "") or None,
        "delivery_profile": get_delivery_profile(session.meta),
        "review_verdict": review_verdict,
        "review_blockers_count": review_blockers_count,
        "review_progress_blocked": False,
        "manifest": manifest_payload(manifest) if manifest is not None else None,
        "manifest_stale": manifest_blocks_on_l2_stale(manifest) if manifest is not None else False,
        "project_template": project_template_of(manifest) if manifest is not None else "standard",
        "manifest_error": manifest_error,
        "execution_stage": stage["stage"],
        "execution_stage_status": stage["status"],
        "execution_stage_reason": stage["reason"],
        "execution_stage_blockers": list(stage["blockers"]),
        "execution_stage_missing": list(stage.get("missing", stage["blockers"])),
        "execution_stage_warnings": list(stage.get("warnings", [])),
        "execution_stage_affected": stage_documents["affected"],
        "execution_stage_deferred": stage_documents["deferred"],
        "content_lint": stage.get("content_lint"),
        "release_acceptance": release_acceptance,
        "execution_stage_artifacts": [
            {
                "path": item.get("path"),
                "role": item.get("role"),
                "revision": item.get("revision"),
                "status": item.get("status"),
                "completeness": item.get("completeness"),
                "ids": list(item.get("ids") or []),
            }
            for item in (manifest or {}).get("artifacts", [])
            if isinstance(item, dict)
        ],
    }
    return payload


def project_list_payload(paths: AgentPaths, session: Session | None = None) -> dict[str, Any]:
    from project_switch import lookup_project_session
    from external_project import validate_external_binding

    items: list[dict[str, Any]] = []
    current_pid = (session.meta.project_id or "").strip() if session else ""
    for project_id in list_projects(paths):
        sid = lookup_project_session(paths, project_id)
        items.append(
            {
                "id": project_id,
                "root": f"workspace/{project_id}",
                "session_id": sid,
                "is_current": project_id == current_pid,
                "external_adopted": validate_external_binding(paths, project_id) is not None,
                "external_healthy": (
                    validate_external_binding(paths, project_id).healthy
                    if validate_external_binding(paths, project_id) is not None
                    else True
                ),
            }
        )
    return {"type": "project.list", "projects": items}



def emit_project_session_bundle(session: Session, paths: AgentPaths, emit: EmitFn) -> None:
    """Emit banner + project.state when a project is bound."""
    emit(session_banner_event(session))
    if session.meta.project_id and session.meta.active_shell == "project":
        emit(project_state_payload(session, paths))



def after_turn_project_hooks(session: Session, paths: AgentPaths, emit: EmitFn) -> None:
    if session.meta.active_shell != "project" or not session.meta.project_id:
        # Not in project mode: detect potential workspace projects
        maybe_emit_project_detect(session, paths, emit)
        return
    emit(project_state_payload(session, paths))
    emit(session_banner_event(session))


# Track which sessions have already received a project.detect notice
_detected_sessions: set[str] = set()


def maybe_emit_project_detect(session: Session, paths: AgentPaths, emit: EmitFn) -> None:
    """Detect workspace dirs that look like projects but aren't bound."""
    if session.conversation_id in _detected_sessions:
        return
    current_pid = (session.meta.project_id or "").strip()
    detected = detect_potential_project(paths, current_pid)
    if detected is not None:
        _detected_sessions.add(session.conversation_id)
        emit(detected)


def dispatch_project_message(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
) -> dict[str, Any]:
    msg_type = message.get("type")

    if msg_type == "project.list":
        return project_list_payload(paths, session)

    if msg_type == "project.state":
        return project_state_payload(session, paths)

    if msg_type == "project.scope.confirm":
        _project_pid(session)
        session.meta.project_scope_confirmed_at = utc_now_iso()
        session.save()
        return project_state_payload(session, paths)

    if msg_type == "project.release.accept":
        pid = _project_pid(session)
        state = project_state_payload(session, paths)
        if state.get("execution_stage") != "release":
            raise ProjectApiError("release acceptance requires the authoritative release stage")
        artifacts = {
            str(item.get("path")): item
            for item in state.get("execution_stage_artifacts", [])
            if isinstance(item, dict) and item.get("path")
        }
        checklist = {
            "evidence_fresh": all(
                artifacts.get(path, {}).get("status") == "current"
                for path in ("VERIFY.md", "RELEASE.md")
            ),
            "blockers_clear": (
                state.get("review_verdict") == "pass"
                and int(state.get("review_blockers_count") or 0) == 0
            ),
            "release_current": artifacts.get("RELEASE.md", {}).get("status") == "current",
            "human_acceptance": True,
        }
        if not all(checklist.values()):
            missing = [key for key, value in checklist.items() if not value and key != "human_acceptance"]
            raise ProjectApiError(f"release checklist incomplete: {', '.join(missing)}")
        release_revision = str(artifacts.get("RELEASE.md", {}).get("revision") or "")
        if not release_revision:
            raise ProjectApiError("RELEASE.md revision unavailable")
        record = save_release_acceptance(
            project_dir(paths, pid),
            pid,
            release_revision=release_revision,
            checklist=checklist,
        )
        return {
            "_events": [
                {"type": "project.release.accepted", "release_acceptance": record},
                project_state_payload(session, paths),
            ]
        }

    if msg_type == "project.import.scan":
        from external_project import scan_external_project

        raw_path = message.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ProjectApiError("project.import.scan requires path")
        project_id = message.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise ProjectApiError("project.import.scan project_id must be string")
        try:
            scan = scan_external_project(paths, raw_path, project_id=project_id)
        except Exception as exc:
            raise ProjectApiError(str(exc)) from exc
        return {"type": "project.import.scan", "scan": scan.to_dict()}

    if msg_type == "project.import.cancel":
        return {
            "type": "project.import.cancelled",
            "project_id": str(message.get("project_id") or "").strip() or None,
        }

    if msg_type == "project.import.apply":
        if message.get("confirm") is not True:
            raise ProjectApiError("project.import.apply requires confirm=true")
        raw_path = message.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ProjectApiError("project.import.apply requires path")
        project_id = message.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            raise ProjectApiError("project.import.apply project_id must be string")
        try:
            from project_cli import import_external_project

            notice = import_external_project(session, paths, raw_path, project_id=project_id)
        except Exception as exc:
            raise ProjectApiError(str(exc)) from exc
        pid = (session.meta.project_id or "").strip()
        return {
            "_events": [
                {"type": "project.import.applied", "project_id": pid, "message": notice},
                project_state_payload(session, paths),
            ]
        }

    if msg_type == "project.open":
        project_id = message.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProjectApiError("project.open requires project_id")
        updated, events = perform_project_open(session, paths, project_id.strip())
        return {"_session": updated, "_events": events}

    if msg_type == "project.switch":
        updated, events = perform_project_switch(session, paths, message)
        return {"_session": updated, "_events": events}

    if msg_type == "project.thread.new":
        updated, events = perform_project_thread_new(session, paths, message)
        return {"_session": updated, "_events": events}

    if msg_type == "project.threads":
        from project_switch import build_project_threads_payload

        project_id = message.get("project_id")
        pid = project_id.strip() if isinstance(project_id, str) and project_id.strip() else None
        return build_project_threads_payload(paths, session, project_id=pid)

    if msg_type == "project.design.confirm":
        try:
            message = confirm_project_design(session, paths)
        except ProjectModeError as exc:
            raise ProjectApiError(str(exc)) from exc
        return {
            "_events": [
                {"type": "notice", "text": message},
                project_state_payload(session, paths),
            ]
        }

    if msg_type == "project.direct_implement":
        try:
            result = confirm_direct_implement(session)
        except ProjectModeError as exc:
            raise ProjectApiError(str(exc)) from exc
        session.save()
        return {
            "_events": [
                {"type": "notice", "text": result},
                project_state_payload(session, paths),
            ]
        }

    if msg_type == "project.verify":
        pid = (session.meta.project_id or "").strip()
        if not pid or session.meta.active_shell != "project":
            raise ProjectApiError("no active project session")
        artifacts = read_project_artifacts(paths, pid)
        acceptance = parse_acceptance_spec(artifacts.get("PROJECT.md", ""))
        if acceptance is None:
            raise ProjectApiError("PROJECT.md 未定义验收命令（命令：`python …`）")
        if not acceptance_script_exists(paths, pid, acceptance):
            rel = acceptance_workspace_path(pid, acceptance)
            raise ProjectApiError(f"验收脚本不存在：workspace/{rel}")
        result = run_acceptance_check(paths, pid, acceptance)
        return {"type": "project.verify.done", **result}

    raise ProjectApiError(f"unknown project message type: {msg_type}")


def _project_pid(session: Session) -> str:
    pid = (session.meta.project_id or "").strip()
    if not pid or session.meta.active_shell != "project":
        raise ProjectApiError("no active project session")
    return pid



def _doc_list_event(paths: AgentPaths, pid: str) -> dict[str, Any]:
    return {
        "type": "project.doc.list.done",
        "docs": list_project_docs(paths, pid),
        "placeholder_count": count_untouched_template_docs(paths, pid),
    }


def dispatch_doc_message(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Handle project.doc.* messages."""
    msg_type = message.get("type")
    _project_pid(session)
    pid = session.meta.project_id.strip()

    try:
        if msg_type == "project.doc.list":
            return _doc_list_event(paths, pid)

        if msg_type == "project.doc.read":
            doc_path = str(message.get("path", ""))
            if not doc_path:
                raise ProjectApiError("project.doc.read requires path")
            return read_project_doc(paths, pid, doc_path)

        if msg_type == "project.doc.create":
            if getattr(session.meta, "project_workflow_stage", "requirements") == "requirements":
                raise ProjectApiError("请先明确「开始整理文档」；需求阶段不写项目文档")
            doc_path = str(message.get("path", ""))
            content = str(message.get("content", ""))
            if not doc_path:
                raise ProjectApiError("project.doc.create requires path")
            result = create_project_doc(paths, pid, doc_path, content)
            return {"_events": [result, _doc_list_event(paths, pid)]}

        if msg_type in {"project.doc.write", "project.doc.save"}:
            doc_path = str(message.get("path", ""))
            if not doc_path:
                raise ProjectApiError("project.doc.write requires path")
            content = message.get("content")
            if content is None:
                raise ProjectApiError("project.doc.write requires content")
            base_mtime = message.get("base_mtime")
            base_hash = message.get("base_hash")
            force = bool(message.get("force"))
            try:
                base_mtime_f = float(base_mtime) if base_mtime is not None else None
            except (TypeError, ValueError):
                base_mtime_f = None
            base_hash_s = str(base_hash).strip() if isinstance(base_hash, str) else None
            result = write_project_doc(
                paths,
                pid,
                doc_path,
                str(content),
                base_mtime=base_mtime_f,
                base_hash=base_hash_s,
                force=force,
            )
            return {"_events": [result, _doc_list_event(paths, pid)]}

        if msg_type == "project.doc.rename":
            doc_path = str(message.get("path", ""))
            if not doc_path:
                raise ProjectApiError("project.doc.rename requires path")
            title = str(message.get("title") or message.get("name") or "")
            new_path = str(message.get("new_path") or "")
            if not title and not new_path:
                raise ProjectApiError("project.doc.rename requires title or new_path")
            result = rename_project_doc(
                paths, pid, doc_path, title=title, new_path=new_path
            )
            return {"_events": [result, _doc_list_event(paths, pid)]}

        if msg_type == "project.doc.delete":
            doc_path = str(message.get("path", ""))
            if not doc_path:
                raise ProjectApiError("project.doc.delete requires path")
            result = delete_project_doc(paths, pid, doc_path)
            events: list[dict[str, Any]] = [result, _doc_list_event(paths, pid)]
            undo = result.get("undo") if isinstance(result, dict) else None
            if isinstance(undo, dict):
                events.append({
                    "type": "project.undo.available",
                    "description": f"已移入回收站：{result.get('name') or doc_path}",
                    "undo_kind": "restore_doc",
                    "undo_payload": undo,
                })
            return {"_events": events}

        if msg_type == "project.doc.undo":
            payload = message.get("undo_payload") or message.get("payload") or {}
            if not isinstance(payload, dict):
                raise ProjectApiError("project.doc.undo requires undo_payload")
            path = str(payload.get("path") or "")
            if not path:
                raise ProjectApiError("project.doc.undo requires path")
            result = restore_project_doc_from_trash(
                paths,
                pid,
                path=path,
                trash_path=str(payload.get("trash_path") or ""),
                content=str(payload.get("content") or ""),
            )
            return {"_events": [result, _doc_list_event(paths, pid)]}

    except ProjectModeError as exc:
        if getattr(exc, "code", "") == "doc_conflict":
            details = getattr(exc, "details", {}) or {}
            return {
                "type": "project.doc.write.conflict",
                "message": str(exc),
                "path": details.get("path", ""),
                "mtime": details.get("mtime", 0),
                "content_hash": details.get("content_hash", ""),
                "size": details.get("size", 0),
            }
        return {"type": "error", "message": str(exc)}

    raise ProjectApiError(f"unknown doc message type: {msg_type}")



def perform_project_open(
    session: Session,
    paths: AgentPaths,
    project_id: str,
) -> tuple[Session, list[dict[str, Any]]]:
    from project_switch import open_project_on_session

    try:
        updated, msg = open_project_on_session(paths, session, project_id)
    except ProjectModeError as exc:
        raise ProjectApiError(str(exc)) from exc
    events: list[dict[str, Any]] = [
        {"type": "notice", "text": msg},
        project_state_payload(updated, paths),
        session_banner_event(updated),
    ]
    return updated, events


def perform_project_switch(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
) -> tuple[Session, list[dict[str, Any]]]:
    from project_switch import (
        build_switch_request_payload,
        execute_project_switch,
        plan_project_switch,
    )

    project_id = message.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ProjectApiError("project.switch requires project_id")
    confirm = bool(message.get("confirm"))
    request_id = message.get("request_id")
    rid = request_id.strip() if isinstance(request_id, str) and request_id.strip() else str(uuid.uuid4())

    plan = plan_project_switch(paths, session, project_id.strip())
    current = (session.meta.project_id or "").strip()
    needs_confirm = bool(
        current
        and current != plan.project_id
        and plan.action in {"load_session", "new_session"}
    )
    if needs_confirm and not confirm:
        preview = build_switch_request_payload(
            paths,
            session,
            project_id.strip(),
            request_id=rid,
        )
        return session, [preview]

    updated, msg = execute_project_switch(paths, session, plan)
    session_replaced = updated.conversation_id != session.conversation_id
    events: list[dict[str, Any]] = [
        {
            "type": "project.switch.done",
            "request_id": rid,
            "project_id": plan.project_id,
            "session_id": updated.conversation_id,
            "action": plan.action,
            "message": msg,
            "session_replaced": session_replaced,
        },
        project_state_payload(updated, paths),
        session_banner_event(updated),
    ]
    if session_replaced:
        from context import session_memory_event
        from session import session_history_event

        events.append(session_memory_event(updated, quick=not updated._messages_fully_loaded))
        events.append(session_history_event(updated))
        events.extend(corruption_notice_events(updated))
    events.append({"type": "notice", "text": msg})
    return updated, events


def perform_project_thread_new(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any] | None = None,
) -> tuple[Session, list[dict[str, Any]]]:
    from project_switch import start_new_project_thread

    message = message or {}
    project_id = message.get("project_id")
    pid = project_id.strip() if isinstance(project_id, str) and project_id.strip() else None
    request_id = message.get("request_id")
    rid = request_id.strip() if isinstance(request_id, str) and request_id.strip() else str(uuid.uuid4())

    try:
        updated, msg = start_new_project_thread(paths, session, project_id=pid)
    except ProjectModeError as exc:
        raise ProjectApiError(str(exc)) from exc

    session_replaced = updated.conversation_id != session.conversation_id
    events: list[dict[str, Any]] = [
        {
            "type": "project.thread.new.done",
            "request_id": rid,
            "project_id": updated.meta.project_id,
            "session_id": updated.conversation_id,
            "previous_session_id": session.conversation_id,
            "message": msg,
            "session_replaced": session_replaced,
        },
        project_state_payload(updated, paths),
        session_banner_event(updated),
    ]
    if session_replaced:
        from context import session_memory_event
        from session import session_history_event

        events.append(session_memory_event(updated, quick=not updated._messages_fully_loaded))
        events.append(session_history_event(updated))
        events.extend(corruption_notice_events(updated))
    events.append({"type": "notice", "text": msg})
    return updated, events



def _demo() -> None:
    from session import create_new

    paths = AgentPaths.discover()
    demo_dir = paths.data / "sessions" / "project-api-demo"
    if demo_dir.is_dir():
        import shutil

        shutil.rmtree(demo_dir)
    session = create_new(paths, conversation_id="project-api-demo")
    events: list[dict[str, Any]] = []

    def emit(event: dict[str, Any]) -> None:
        events.append(event)

    listed = dispatch_project_message(session, paths, {"type": "project.list"})
    assert listed["type"] == "project.list"
    print("[PASS] project.list payload")

    from project_cli import run_project_command, ParsedProjectCommand

    run_project_command(
        session,
        paths,
        ParsedProjectCommand(kind="new", project_id="api-demo"),
        output_fn=lambda _t: None,
    )
    state = project_state_payload(session, paths)
    assert "needs_plan_confirm" not in state
    print("[PASS] project.state (no plan gate)")

    demo_script = paths.workspace / "api-demo" / "demo.py"
    demo_script.parent.mkdir(parents=True, exist_ok=True)
    demo_script.write_text("print('ok')", encoding="utf-8")
    verify = dispatch_project_message(session, paths, {"type": "project.verify"})
    assert verify["type"] == "project.verify.done"
    assert verify.get("passed") is True
    print("[PASS] project.verify")

    import shutil

    shutil.rmtree(paths.workspace / "api-demo", ignore_errors=True)
    shutil.rmtree(demo_dir, ignore_errors=True)
    print("[PASS] T-1109: project_api demo")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
