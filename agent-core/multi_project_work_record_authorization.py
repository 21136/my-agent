"""Issue read-scoped project work-record source receipts after host confirmation.

This helper is intentionally not wired into Agent/server/worker dispatch. A trusted
host supplies project candidates and a live user confirmation callback; session IDs
and record IDs are always derived from protected host state, never model input.
"""
from __future__ import annotations

from threading import Event
from typing import Callable, Sequence
from uuid import uuid4

from evolution.contracts import is_conversation_id
from multi_project_work_record_bindings import (
    AuthorizedProjectRecordSource,
    ReadOnlyWorkRecordStore,
    _issue_authorized_project_record_source,
)
from multi_project_work_records import (
    _load_project_meta,
    _mapping_matches,
    _read_project_sessions,
    _validate_project_selection,
    _validate_session_directory,
)
from paths import AgentPaths
from session import META_FILENAME, Session
from work_records import WorkRecordStore


def _confirmation_preview(project_ids: tuple[str, ...], session_ids: dict[str, str]) -> str:
    lines = [
        "是否授权当前对话读取以下所选项目 Session 的已保存工作记录？",
        "确认来源由主机从 project_sessions 映射解析；不读取模型提供的 Session ID、事项 ID 或记录 ID。",
        "本次只允许读取每个所列 Session 中明确绑定到对应项目的 WorkRecordStore 工作记录侧车；",
        "不读取这些 Session 的 goal.md、messages.jsonl 或其他会话内容，也不授予项目文件读写权限。",
        "读取结果若之后展示在当前对话，可能被模型看到；本确认本身不会读取或展示记录内容。",
        "所选项目与映射 Session：",
    ]
    lines.extend(f"- 项目 {project_id} → Session {session_ids[project_id]}" for project_id in project_ids)
    lines.extend([
        "仅本次列出的项目和映射 Session；未选择项目及其他历史 Session 不在授权范围。",
        "取消、拒绝、确认期间取消或来源映射变化都会拒绝；仅明确返回 'y' 授权。",
    ])
    return "\n".join(lines)


def authorize_project_record_sources(
    current_session: Session,
    candidate_project_ids: Sequence[str],
    *,
    confirm_fn: Callable[[str, bool], str] | None,
    cancel_event: Event,
) -> dict[str, AuthorizedProjectRecordSource]:
    """Create read-scoped receipts only for confirmed, securely resolved projects.

    Candidates contain project IDs only. The protected project-session index and
    checked session metadata determine each source Session; caller/model-provided
    session and record identities are not accepted. No work-record sidecar is read
    here. A host must still keep subsequent reads inside the returned store scope.
    """
    try:
        if (not isinstance(current_session, Session)
                or not isinstance(current_session.paths, AgentPaths)
                or not isinstance(cancel_event, Event)
                or not callable(confirm_fn)):
            raise PermissionError
        selected = _validate_project_selection(candidate_project_ids)
        current_store = WorkRecordStore(current_session)
        current_store._validate_scope()
        if cancel_event.is_set():
            raise PermissionError

        before = _read_project_sessions(current_session.paths)
        if any(project_id not in before for project_id in selected):
            raise PermissionError
        source_session_ids = {project_id: before[project_id] for project_id in selected}
        if any(not is_conversation_id(session_id) for session_id in source_session_ids.values()):
            raise PermissionError

        choice = confirm_fn(_confirmation_preview(selected, source_session_ids), False)
        if cancel_event.is_set() or type(choice) is not str or choice != "y":
            raise PermissionError

        current_store._validate_scope()
        after_confirmation = _read_project_sessions(current_session.paths)
        if not _mapping_matches(source_session_ids, after_confirmation, selected):
            raise PermissionError

        authorized: dict[str, AuthorizedProjectRecordSource] = {}
        for project_id in selected:
            if cancel_event.is_set():
                raise PermissionError
            session_id = source_session_ids[project_id]
            session_dir = _validate_session_directory(current_session.paths, session_id)
            meta = _load_project_meta(session_dir / META_FILENAME, project_id)
            source_session = Session(
                conversation_id=session_id,
                session_dir=session_dir,
                goal="",
                meta=meta,
                messages=[],
                paths=current_session.paths,
            )
            store = ReadOnlyWorkRecordStore(source_session)
            store.validate_scope()
            if store.project_id != project_id:
                raise PermissionError
            authorized[project_id] = _issue_authorized_project_record_source(
                authorization_id=str(uuid4()),
                conversation_id=current_session.conversation_id,
                project_id=project_id,
                store=store,
            )

        current_store._validate_scope()
        final_mapping = _read_project_sessions(current_session.paths)
        if (cancel_event.is_set()
                or not _mapping_matches(source_session_ids, final_mapping, selected)):
            raise PermissionError
        return authorized
    except PermissionError:
        raise PermissionError("project work-record source authorization was not established") from None
    except Exception:
        # Fail closed without returning partial capabilities or path/metadata details.
        raise PermissionError("project work-record source authorization could not be verified") from None
