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
from project_cli import confirm_project_plan
from project_mode import (
    ProjectModeError,
    acceptance_script_exists,
    acceptance_workspace_path,
    add_task_to_tasks_md,
    create_project_doc,
    detect_potential_project,
    list_project_docs,
    list_projects,
    normalize_project_id,
    parse_acceptance_spec,
    plan_allows_code_writes,
    project_dir,
    read_project_artifacts,
    read_project_doc,
    read_task_stats,
    run_acceptance_check,
    snapshot_plan_fingerprints,
    sync_plan_dirty_if_structure_changed,
)
from plan_agent import PlanAgent, get_plan_agent
from session import Session, corruption_notice_events, session_banner_event, utc_now_iso

EmitFn = Callable[[dict[str, Any]], None]

_PLAN_PREVIEW_MAX = 4000
_PROJECT_SUMMARY_MAX = 1200


class ProjectApiError(Exception):
    """Invalid project WS message."""


def _ack_human_plan_adopt(session: Session, paths: AgentPaths, agent: PlanAgent) -> None:
    """Snap fingerprints after human adopt/confirm so plan_dirty clears (Phase 40)."""
    pid = (session.meta.project_id or "").strip()
    if not pid:
        return
    agent.confirm_plan()
    snapshot_plan_fingerprints(session, paths, pid)
    if session.meta.project_plan_status == "plan_dirty":
        session.meta.project_plan_status = "confirmed"
    session.meta.updated_at = utc_now_iso()
    session.save()


def maybe_clear_stale_plan_dirty(session: Session, paths: AgentPaths, agent: PlanAgent) -> bool:
    """Clear persisted plan_dirty when disk already matches and nothing waits for adopt.

    Restart does not wipe session meta — users who already confirmed once can get stuck
    on plan_dirty after Phase titles change on disk (e.g. Phase 7 待实现 → 全部完成).
    """
    if session.meta.project_plan_status != "plan_dirty":
        return False
    if not (session.meta.project_plan_confirmed_at or "").strip():
        return False
    if agent._pending_gated:
        return False
    _ack_human_plan_adopt(session, paths, agent)
    return True


def _project_summary(project_md: str) -> str:
    text = project_md.strip()
    if not text:
        return "（PROJECT.md 为空）"
    if len(text) <= _PROJECT_SUMMARY_MAX:
        return text
    return text[:_PROJECT_SUMMARY_MAX] + "\n…(truncated)"


def project_state_payload(session: Session, paths: AgentPaths) -> dict[str, Any]:
    pid = (session.meta.project_id or "").strip()
    root = (session.meta.project_root or "").strip()
    plan_status = session.meta.project_plan_status or "draft"
    artifacts = read_project_artifacts(paths, pid) if pid else {}
    tasks_md = artifacts.get("TASKS.md", "")
    map_md = artifacts.get("MAP.md", "")
    stats = read_task_stats(project_dir(paths, pid) / "TASKS.md") if pid else read_task_stats(Path())
    acceptance = parse_acceptance_spec(artifacts.get("PROJECT.md", "")) if pid else None
    can_verify = bool(
        pid
        and plan_allows_code_writes(plan_status)
        and acceptance is not None
        and acceptance_script_exists(paths, pid, acceptance)
    )
    return {
        "type": "project.state",
        "project_id": pid or None,
        "project_root": root or None,
        "plan_status": plan_status,
        "tasks_markdown": tasks_md,
        "map_markdown": map_md,
        "tasks_done": stats.done,
        "tasks_total": stats.total,
        "tasks_open": stats.open_count,
        "tasks_all_done": stats.all_done,
        "project_summary": _project_summary(artifacts.get("PROJECT.md", "")),
        "needs_plan_confirm": plan_status in {"draft", "plan_dirty"},
        "acceptance_command": acceptance.display if acceptance else None,
        "acceptance_expected_exit": acceptance.expected_exit_code if acceptance else None,
        "can_verify": can_verify,
    }


def project_list_payload(paths: AgentPaths, session: Session | None = None) -> dict[str, Any]:
    from project_switch import lookup_project_session

    items: list[dict[str, Any]] = []
    current_pid = (session.meta.project_id or "").strip() if session else ""
    for project_id in list_projects(paths):
        stats = read_task_stats(project_dir(paths, project_id) / "TASKS.md")
        sid = lookup_project_session(paths, project_id)
        items.append(
            {
                "id": project_id,
                "root": f"workspace/{project_id}",
                "tasks_done": stats.done,
                "tasks_total": stats.total,
                "session_id": sid,
                "is_current": project_id == current_pid,
            }
        )
    return {"type": "project.list", "projects": items}


def build_plan_request_payload(session: Session, paths: AgentPaths) -> dict[str, Any] | None:
    pid = (session.meta.project_id or "").strip()
    if not pid or session.meta.active_shell != "project":
        return None
    plan_status = session.meta.project_plan_status or "draft"
    if plan_status not in {"draft", "plan_dirty"}:
        return None
    artifacts = read_project_artifacts(paths, pid)
    tasks_md = artifacts.get("TASKS.md", "")
    if not tasks_md.strip():
        return None
    preview = tasks_md
    if len(preview) > _PLAN_PREVIEW_MAX:
        preview = preview[:_PLAN_PREVIEW_MAX] + "\n…(truncated)"
    stats = read_task_stats(project_dir(paths, pid) / "TASKS.md")
    title = "计划已变更 · 请确认" if plan_status == "plan_dirty" else "计划待确认"
    return {
        "type": "plan.request",
        "request_id": str(uuid.uuid4()),
        "project_id": pid,
        "project_root": session.meta.project_root,
        "plan_status": plan_status,
        "title": title,
        "summary": _project_summary(artifacts.get("PROJECT.md", "")),
        "tasks_preview": preview,
        "tasks_done": stats.done,
        "tasks_total": stats.total,
    }


def emit_project_session_bundle(session: Session, paths: AgentPaths, emit: EmitFn) -> None:
    """Emit banner + project.state when a project is bound."""
    agent = _plan_agent(session, paths)
    if agent is not None:
        maybe_clear_stale_plan_dirty(session, paths, agent)
    emit(session_banner_event(session))
    if session.meta.project_id and session.meta.active_shell == "project":
        emit(project_state_payload(session, paths))


def maybe_emit_plan_request(session: Session, paths: AgentPaths, emit: EmitFn) -> None:
    sync_plan_dirty_if_structure_changed(session, paths)
    payload = build_plan_request_payload(session, paths)
    if payload is not None:
        emit(payload)


def after_turn_project_hooks(session: Session, paths: AgentPaths, emit: EmitFn) -> None:
    if session.meta.active_shell != "project" or not session.meta.project_id:
        # Not in project mode: detect potential workspace projects
        maybe_emit_project_detect(session, paths, emit)
        return
    if sync_plan_dirty_if_structure_changed(session, paths):
        session.save()
    emit(project_state_payload(session, paths))
    emit(session_banner_event(session))

    # Plan Agent: emit plan state (build_state runs auto_fix + quality_check internally)
    agent = _plan_agent(session, paths)
    if agent is not None:
        plan_state = agent.build_state(session)
        for action in plan_state.get("auto_fix_actions", []):
            emit({"type": "notice", "text": action})
        emit(plan_state)

    maybe_emit_plan_request(session, paths, emit)


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

    if msg_type == "plan.response":
        request_id = message.get("request_id")
        choice = message.get("choice")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ProjectApiError("plan.response requires request_id")
        if not isinstance(choice, str) or not choice.strip():
            raise ProjectApiError("plan.response requires choice")
        normalized = choice.strip().casefold()
        if normalized == "confirm":
            try:
                confirm_project_plan(session)
            except ProjectModeError as exc:
                raise ProjectApiError(str(exc)) from exc
            session.save()
            return {
                **project_state_payload(session, paths),
                "plan_choice": "confirm",
                "request_id": request_id,
            }
        if normalized == "edit":
            return {
                **project_state_payload(session, paths),
                "plan_choice": "edit",
                "request_id": request_id,
            }
        raise ProjectApiError("plan.response choice must be confirm or edit")

    if msg_type == "project.verify":
        pid = (session.meta.project_id or "").strip()
        if not pid or session.meta.active_shell != "project":
            raise ProjectApiError("no active project session")
        plan_status = session.meta.project_plan_status or "draft"
        if not plan_allows_code_writes(plan_status):
            raise ProjectApiError("计划未确认，无法运行验收")
        artifacts = read_project_artifacts(paths, pid)
        acceptance = parse_acceptance_spec(artifacts.get("PROJECT.md", ""))
        if acceptance is None:
            raise ProjectApiError("PROJECT.md 未定义验收命令（命令：`python …`）")
        if not acceptance_script_exists(paths, pid, acceptance):
            rel = acceptance_workspace_path(pid, acceptance)
            raise ProjectApiError(f"验收脚本不存在：workspace/{rel}")
        result = run_acceptance_check(paths, pid, acceptance)
        return {"type": "project.verify.done", **result}

    if msg_type == "project.task.toggle":
        _project_pid(session)
        line = message.get("line")
        done = message.get("done")
        if not isinstance(line, int) or line < 0:
            raise ProjectApiError("project.task.toggle requires line (non-negative int)")
        if not isinstance(done, bool):
            raise ProjectApiError("project.task.toggle requires done (bool)")
        agent = _plan_agent(session, paths)
        if agent is None:
            raise ProjectApiError("PlanAgent not available")
        try:
            result = agent.toggle_task(line, done)
        except ProjectModeError as exc:
            return {"type": "project.task.toggle.error", "line": line, "message": str(exc)}
        return _plan_task_result(agent, session, paths, result)

    if msg_type == "project.task.reorder":
        _project_pid(session)
        line = message.get("line")
        direction = message.get("direction")
        if not isinstance(line, int) or line < 0:
            raise ProjectApiError("project.task.reorder requires line (non-negative int)")
        if direction not in ("up", "down"):
            raise ProjectApiError("project.task.reorder requires direction: up or down")
        agent = _plan_agent(session, paths)
        if agent is None:
            raise ProjectApiError("PlanAgent not available")
        try:
            result = agent.reorder_task(line, str(direction))
        except ProjectModeError as exc:
            return {"type": "project.task.reorder.error", "line": line, "message": str(exc)}
        return _plan_task_result(agent, session, paths, result)

    if msg_type == "project.task.drop":
        _project_pid(session)
        line = message.get("line")
        if not isinstance(line, int) or line < 0:
            raise ProjectApiError("project.task.drop requires line (non-negative int)")
        agent = _plan_agent(session, paths)
        if agent is None:
            raise ProjectApiError("PlanAgent not available")
        try:
            result = agent.drop_task(line)
        except ProjectModeError as exc:
            return {"type": "project.task.drop.error", "line": line, "message": str(exc)}
        return _plan_task_result(agent, session, paths, result)

    if msg_type == "project.task.skip":
        _project_pid(session)
        line = message.get("line")
        if not isinstance(line, int) or line < 0:
            raise ProjectApiError("project.task.skip requires line (non-negative int)")
        agent = _plan_agent(session, paths)
        if agent is None:
            raise ProjectApiError("PlanAgent not available")
        try:
            result = agent.skip_task(line)
        except ProjectModeError as exc:
            return {"type": "project.task.skip.error", "line": line, "message": str(exc)}
        return _plan_task_result(agent, session, paths, result)

    if msg_type.startswith("project.plan."):
        return _dispatch_plan_message(session, paths, message)

    raise ProjectApiError(f"unknown project message type: {msg_type}")


def _project_pid(session: Session) -> str:
    pid = (session.meta.project_id or "").strip()
    if not pid or session.meta.active_shell != "project":
        raise ProjectApiError("no active project session")
    return pid


def _plan_agent(session: Session, paths: AgentPaths) -> PlanAgent | None:
    pid = (session.meta.project_id or "").strip()
    if not pid or session.meta.active_shell != "project":
        return None
    try:
        return get_plan_agent(paths, pid)
    except Exception:
        return None


def _plan_task_result(agent: PlanAgent, session: Session, paths: AgentPaths,
                      result: dict[str, Any]) -> dict[str, Any]:
    """Wrap a PlanAgent task mutation result with project.state + plan.state + auto_fix notices."""
    events: list[dict[str, Any]] = [result, project_state_payload(session, paths)]

    # Emit auto_fix actions from mutation result as notices
    for action in result.pop("_auto_fix_actions", []):
        events.append({"type": "notice", "text": action})

    # Emit undo toast if available
    undo_desc = result.pop("_undo_desc", None)
    if undo_desc:
        events.append({"type": "project.undo.available", "description": undo_desc})

    # Build plan state (auto_fix runs again inside, extract any new actions too)
    plan_state = agent.build_state(session)
    for action in plan_state.get("auto_fix_actions", []):
        events.append({"type": "notice", "text": action})
    events.append(plan_state)
    return {"_events": events}


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
            docs = list_project_docs(paths, pid)
            return {"type": "project.doc.list.done", "docs": docs}

        if msg_type == "project.doc.read":
            doc_path = str(message.get("path", ""))
            if not doc_path:
                raise ProjectApiError("project.doc.read requires path")
            return read_project_doc(paths, pid, doc_path)

        if msg_type == "project.doc.create":
            doc_path = str(message.get("path", ""))
            content = str(message.get("content", ""))
            if not doc_path:
                raise ProjectApiError("project.doc.create requires path")
            result = create_project_doc(paths, pid, doc_path, content)
            return {
                "_events": [
                    result,
                    {"type": "project.doc.list.done", "docs": list_project_docs(paths, pid)},
                ]
            }

    except ProjectModeError as exc:
        return {"type": "error", "message": str(exc)}

    raise ProjectApiError(f"unknown doc message type: {msg_type}")


def dispatch_plan_user_message(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Handle project.plan.message — compat API (Phase 39 · routes via Plan subagent).

    Does **not** append to main session messages.jsonl; emits plan.subagent.* + state.
    """
    _project_pid(session)
    text = str(message.get("text") or message.get("description") or "").strip()
    if not text:
        raise ProjectApiError("project.plan.message requires text")

    agent = _plan_agent(session, paths)
    if agent is None:
        raise ProjectApiError("PlanAgent not available for current session")

    include_last = bool(message.get("include_last_user"))
    include_n = 1 if include_last else 0

    from subagent import SubagentRunner

    preview = text if len(text) <= 120 else text[:117] + "…"
    events: list[dict[str, Any]] = [
        {"type": "plan.subagent.start", "task_preview": preview},
    ]

    try:
        runner = SubagentRunner(paths=paths)
        result = runner.run_plan(
            text,
            session=session,
            include_recent_user_lines=include_n,
        )
    except Exception as exc:
        agent.set_partner_notices(f"计划搭档出错：{exc}")
        events.append(
            {
                "type": "plan.subagent.done",
                "summary": str(exc),
                "proposal_count": 0,
                "ok": False,
            }
        )
        events.extend([project_state_payload(session, paths), agent.build_state(session)])
        return {"_events": events}

    events.append(
        {
            "type": "plan.subagent.done",
            "summary": result.summary,
            "proposal_count": len(result.proposal_ids),
            "proposal_ids": list(result.proposal_ids),
            "adopt_pending": result.adopt_pending,
            "ok": True,
        }
    )
    events.append({"type": "assistant.done", "text": result.summary})
    events.extend([project_state_payload(session, paths), agent.build_state(session)])
    return {"_events": events}


def dispatch_task_add(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Handle project.task.add — legacy alias for Plan channel (compat)."""
    return dispatch_plan_user_message(
        session,
        paths,
        {
            "type": "project.plan.message",
            "text": str(message.get("description", "")).strip(),
        },
    )


def _clear_plan_chat_events(
    paths: AgentPaths,
    project_id: str,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    from plan_agent import clear_plan_chat_on_enter

    agent = clear_plan_chat_on_enter(paths, project_id)
    events: list[dict[str, Any]] = [{"type": "project.plan.transcript.clear"}]
    if session is not None:
        events.append(agent.build_state(session))
    return events


def _dispatch_plan_message(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Handle project.plan.* messages."""
    msg_type = message.get("type")
    agent = _plan_agent(session, paths)
    if agent is None:
        raise ProjectApiError("PlanAgent not available for current session")

    try:
        if msg_type == "project.plan.toggle_task":
            line = message["line"] if isinstance(message.get("line"), int) else -1
            done = bool(message.get("done"))
            if line < 0:
                raise ProjectApiError("project.plan.toggle_task requires line")
            result = agent.toggle_task(line, done)
            return _plan_task_result(agent, session, paths, result)

        if msg_type == "project.plan.reorder_task":
            line = message["line"] if isinstance(message.get("line"), int) else -1
            direction = str(message.get("direction", ""))
            if line < 0 or direction not in ("up", "down"):
                raise ProjectApiError("project.plan.reorder_task requires line and direction")
            result = agent.reorder_task(line, direction)
            return _plan_task_result(agent, session, paths, result)

        if msg_type == "project.plan.drop_task":
            line = message["line"] if isinstance(message.get("line"), int) else -1
            if line < 0:
                raise ProjectApiError("project.plan.drop_task requires line")
            result = agent.drop_task(line)
            return _plan_task_result(agent, session, paths, result)

        if msg_type == "project.plan.skip_task":
            line = message["line"] if isinstance(message.get("line"), int) else -1
            if line < 0:
                raise ProjectApiError("project.plan.skip_task requires line")
            result = agent.skip_task(line)
            return _plan_task_result(agent, session, paths, result)

        if msg_type == "project.plan.confirm_changes":
            _ack_human_plan_adopt(session, paths, agent)
            return {
                "_events": [
                    {"type": "project.plan.confirm_changes.done"},
                    project_state_payload(session, paths),
                    agent.build_state(session),
                ]
            }

        if msg_type == "project.plan.state":
            return agent.build_state(session)

        if msg_type == "project.plan.undo":
            entry = agent.undo_last()
            if entry is None:
                return {"type": "error", "message": "没有可撤销的操作"}
            return {
                "_events": [
                    {"type": "notice", "text": f"已撤销：{entry.description}"},
                    project_state_payload(session, paths),
                    agent.build_state(session),
                ]
            }

        if msg_type == "project.plan.split_task":
            line = message.get("line")
            if not isinstance(line, int) or line < 0:
                raise ProjectApiError("project.plan.split_task requires line (non-negative int)")
            summary = agent.split_task(line)
            return {
                "_events": [
                    {"type": "notice", "text": summary},
                    project_state_payload(session, paths),
                    agent.build_state(session),
                ]
            }

        if msg_type == "project.plan.accept_suggestion":
            sid = str(message.get("suggestion_id") or "").strip()
            if not sid:
                raise ProjectApiError("project.plan.accept_suggestion requires suggestion_id")
            result = agent.accept_suggestion(sid)
            if isinstance(result, dict) and result.get("ok") is not False:
                _ack_human_plan_adopt(session, paths, agent)
            events: list[dict[str, Any]] = [
                project_state_payload(session, paths),
                agent.build_state(session),
            ]
            summary = result.get("summary") if isinstance(result, dict) else None
            if isinstance(summary, str) and summary.strip():
                events.insert(0, {"type": "notice", "text": summary})
            undo_desc = result.get("_undo_desc") if isinstance(result, dict) else None
            if isinstance(undo_desc, str) and undo_desc:
                events.insert(0, {"type": "project.undo.available", "description": undo_desc})
            for action in (result.get("_auto_fix_actions") or []) if isinstance(result, dict) else []:
                events.insert(0, {"type": "notice", "text": action})
            return {"_events": events}

        if msg_type == "project.plan.ignore_suggestion":
            sid = str(message.get("suggestion_id") or "").strip()
            if not sid:
                raise ProjectApiError("project.plan.ignore_suggestion requires suggestion_id")
            agent.ignore_suggestion(sid)
            return agent.build_state(session)

        if msg_type == "project.plan.report_progress":
            task_line = message.get("task_line")
            summary = str(message.get("summary", ""))
            result = agent.report_progress(
                task_line if isinstance(task_line, int) else None,
                summary,
            )
            return result

        if msg_type == "project.plan.classify":
            return {"type": "project.plan.classify.done", "decision": "forward"}

        if msg_type == "project.plan.message":
            return dispatch_plan_user_message(session, paths, message)

    except ProjectModeError as exc:
        return {"type": "error", "message": str(exc)}

    raise ProjectApiError(f"unknown plan message type: {msg_type}")


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
    events.extend(_clear_plan_chat_events(paths, project_id, updated))
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

        events.append(session_memory_event(updated))
        events.append(session_history_event(updated))
        events.extend(corruption_notice_events(updated))
    events.append({"type": "notice", "text": msg})
    # C6 / S-192 — entering target project clears Plan chat memory
    events.extend(_clear_plan_chat_events(paths, plan.project_id, updated))
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

        events.append(session_memory_event(updated))
        events.append(session_history_event(updated))
        events.extend(corruption_notice_events(updated))
    events.append({"type": "notice", "text": msg})
    return updated, events


def handle_plan_response(
    session: Session,
    paths: AgentPaths,
    message: dict[str, Any],
    emit: EmitFn,
) -> None:
    result = dispatch_project_message(session, paths, message)
    choice = result.pop("plan_choice", None)
    request_id = result.pop("request_id", message.get("request_id"))
    emit({"type": "plan.done", "request_id": request_id, "choice": choice})
    emit(result)
    emit(session_banner_event(session))

    # Sync PlanAgent fingerprint on plan confirm
    if choice == "confirm":
        agent = _plan_agent(session, paths)
        if agent is not None:
            agent.confirm_plan()
            emit(agent.build_state(session))

    if choice == "confirm":
        emit({"type": "notice", "text": "计划已确认，可以开始写代码。"})
    elif choice == "edit":
        emit({"type": "notice", "text": "继续修改计划；完成后请再次确认开工。"})


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
    assert state["plan_status"] == "draft"
    assert state["needs_plan_confirm"] is True
    print("[PASS] project.state draft")

    plan = build_plan_request_payload(session, paths)
    assert plan is not None and plan["type"] == "plan.request"
    print("[PASS] plan.request built")

    handle_plan_response(
        session,
        paths,
        {"type": "plan.response", "request_id": plan["request_id"], "choice": "confirm"},
        emit,
    )
    assert session.meta.project_plan_status == "confirmed"
    assert any(e.get("type") == "plan.done" for e in events)
    print("[PASS] plan.response confirm")

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
