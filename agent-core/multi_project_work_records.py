"""Read-only, user-confirmed overview of selected project work-record sidecars."""
from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from evolution.contracts import is_conversation_id
from paths import AgentPaths
from project_mode import ProjectModeError, normalize_project_id
from project_switch import PROJECT_SESSIONS_KEY
from session import META_FILENAME, SESSIONS_DIRNAME, Session, SessionMeta
from work_records import WorkRecordStore
from work_records_overview import _format_overview
from work_record_visibility_projection import project_visible_items


_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_META_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400
_CONVERSATION_READ_SCOPE_NOTICE = (
    "汇总来源不会读取所选其他项目会话的 goal.md/messages.jsonl；"
    "当前对话仍按原有机制加载并记录本问答。"
)


class _Unverifiable(RuntimeError):
    pass


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _Unverifiable("duplicate JSON keys")
        result[key] = value
    return result


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _check_path_ancestors(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    anchor = Path(absolute.anchor)
    for component in (absolute, *absolute.parents):
        try:
            info = component.lstat()
        except OSError as exc:
            raise _Unverifiable("path component unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise _Unverifiable("path contains a link or reparse point")
        if component == anchor:
            return
    raise _Unverifiable("path has no filesystem anchor")


def _handle_stamp(info: os.stat_result) -> tuple[int, int, int, int]:
    """Fields stable between Windows lstat and fstat for the same open file."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
    )


def _path_stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Path observations also include ctime to catch mutation/replacement races."""
    return (*_handle_stamp(info), getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)))


def _read_private_json(path: Path, *, max_bytes: int) -> Any:
    """Read one private regular file after no-link and open-file identity checks."""
    _check_path_ancestors(path)
    try:
        before = path.lstat()
    except OSError as exc:
        raise _Unverifiable("required file is missing or unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(before)
        or getattr(before, "st_nlink", 1) != 1
        or before.st_size > max_bytes
    ):
        raise _Unverifiable("required file is not a bounded private regular file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise _Unverifiable("required file could not be opened safely") from exc
    try:
        opened = os.fstat(fd)
        try:
            current = path.lstat()
        except OSError as exc:
            raise _Unverifiable("required file changed while opening") from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or getattr(opened, "st_nlink", 1) != 1
            or _handle_stamp(opened) != _handle_stamp(before)
            or _path_stamp(current) != _path_stamp(before)
        ):
            raise _Unverifiable("required file identity changed while opening")

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        try:
            final_path_info = path.lstat()
        except OSError as exc:
            raise _Unverifiable("required file changed while reading") from exc
        if (
            len(raw) > max_bytes
            or _handle_stamp(after) != _handle_stamp(opened)
            or _path_stamp(final_path_info) != _path_stamp(current)
            or _handle_stamp(final_path_info) != _handle_stamp(after)
        ):
            raise _Unverifiable("required file changed while reading")
    finally:
        os.close(fd)

    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_pairs)
    except _Unverifiable:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _Unverifiable("required JSON is corrupt") from exc


def _read_project_sessions(paths: AgentPaths) -> dict[str, str]:
    state_path = Path(paths.data) / "state.json"
    payload = _read_private_json(state_path, max_bytes=_MAX_STATE_BYTES)
    if type(payload) is not dict:
        raise _Unverifiable("state root is not an object")
    mapping = payload.get(PROJECT_SESSIONS_KEY)
    if type(mapping) is not dict:
        raise _Unverifiable("project session map is missing or invalid")

    clean: dict[str, str] = {}
    for project_id, session_id in mapping.items():
        if type(project_id) is not str or type(session_id) is not str:
            raise _Unverifiable("project session map contains non-string identities")
        try:
            normalized_project_id = normalize_project_id(project_id)
        except (ProjectModeError, AttributeError, TypeError) as exc:
            raise _Unverifiable("project session map contains an invalid project id") from exc
        if normalized_project_id != project_id or not is_conversation_id(session_id):
            raise _Unverifiable("project session map contains an invalid identity pair")
        clean[project_id] = session_id
    return clean


def _validate_project_selection(project_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(project_ids, (str, bytes)) or not isinstance(project_ids, (list, tuple)):
        raise _Unverifiable("project ids must be an explicit sequence")
    if not 1 <= len(project_ids) <= 6:
        raise _Unverifiable("select between one and six projects")
    selected: list[str] = []
    for project_id in project_ids:
        if type(project_id) is not str:
            raise _Unverifiable("project id is not a string")
        try:
            normalized = normalize_project_id(project_id)
        except (ProjectModeError, AttributeError, TypeError) as exc:
            raise _Unverifiable("project id is invalid") from exc
        if normalized != project_id or project_id in selected:
            raise _Unverifiable("project ids must be unique canonical ids")
        selected.append(project_id)
    return tuple(selected)


def _mapping_matches(before: dict[str, str], after: dict[str, str], project_ids: tuple[str, ...]) -> bool:
    return all(project_id in after and before.get(project_id) == after.get(project_id) for project_id in project_ids)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _preview(current_session_id: str, project_ids: tuple[str, ...], sources: dict[str, str]) -> str:
    lines = [
        "读取当前对话独立事项及所选项目会话的已保存工作记录概况？",
        f"当前会话独立事项来源：会话 {current_session_id}；该来源仅取独立事项；如当前会话也是所选项目会话，其项目事项另按所选项目范围纳入。",
        "确认时项目来源（来自当前 project_sessions 映射）：",
    ]
    lines.extend(f"- 项目 {project_id} → 会话 {sources[project_id]}" for project_id in project_ids)
    lines.extend([
        "范围：当前会话只呈现 independent 项目外事项；各项目会话只呈现明确绑定到对应所选项目的事项，不汇入这些会话的 independent 事项。",
        _CONVERSATION_READ_SCOPE_NOTICE + "结果会进入当前对话，后续可能被模型看到。",
        "仅覆盖本次确认的已映射项目会话；未映射项目/其他历史未检查，含未绑定项目也不能据此断言无事项。",
        "这不是完整来源汇总或项目实时执行状态；state.json 可能被并发直接写入，本操作只做前后映射核对。",
        "工作记录侧车读取使用 WorkRecordStore OS 读锁；锁文件不存在时可能创建最小 .work-records.lock；计时推进可能更新 presentation sidecar checkpoint，但不改工作记录或历史卡片。state.json 映射无跨项目原子快照。",
        "仅明确返回 'y' 授权。",
    ])
    return "\n".join(lines)


def _validate_session_directory(paths: AgentPaths, session_id: str) -> Path:
    sessions = Path(paths.data) / SESSIONS_DIRNAME
    target = sessions / session_id
    _check_path_ancestors(target)
    try:
        sessions_resolved = sessions.resolve(strict=True)
        target_resolved = target.resolve(strict=True)
    except OSError as exc:
        raise _Unverifiable("mapped session directory is unavailable") from exc
    if not sessions.is_dir() or not target.is_dir() or target_resolved.parent != sessions_resolved:
        raise _Unverifiable("mapped session directory escapes the sessions root")
    return target


def _load_project_meta(meta_path: Path, project_id: str) -> SessionMeta:
    payload = _read_private_json(meta_path, max_bytes=_MAX_META_BYTES)
    if type(payload) is not dict or type(payload.get("project_id")) is not str:
        raise _Unverifiable("session metadata is missing or corrupt")
    try:
        meta = SessionMeta.from_dict(payload)
    except Exception as exc:
        raise _Unverifiable("session metadata is invalid") from exc
    if meta.project_id != project_id or payload["project_id"] != project_id:
        raise _Unverifiable("session project binding does not match the approved project")
    return meta




def render_selected_projects_overview(
    current_session: Session,
    project_ids: Sequence[str],
    confirm_fn: Callable[[str, bool], str],
) -> str:
    """Render a read-only, explicitly confirmed overview of 1–6 mapped projects.

    No caller-supplied session id/path is accepted. Target goal and messages are
    never loaded. Sidecars use WorkRecordStore's OS read lock; the global project
    mapping does not provide an atomic cross-process snapshot.
    """
    try:
        if not isinstance(current_session, Session) or not isinstance(current_session.paths, AgentPaths):
            raise _Unverifiable("current Session is invalid")
        if not callable(confirm_fn):
            raise _Unverifiable("confirmation function is unavailable")
        selected = _validate_project_selection(project_ids)
        current_store = WorkRecordStore(current_session)
        current_store._validate_scope()
        before_confirmation = _read_project_sessions(current_session.paths)
        if any(project_id not in before_confirmation for project_id in selected):
            raise _Unverifiable("one or more projects have no approved session mapping")
        approved_sources = {project_id: before_confirmation[project_id] for project_id in selected}
    except Exception:
        return "无法核对项目会话映射或所选范围；未读取其他会话元数据或工作记录侧车。"

    preview = _preview(current_session.conversation_id, selected, approved_sources)
    try:
        choice = confirm_fn(preview, False)
    except Exception:
        return "确认过程异常；未读取其他会话元数据或工作记录侧车。"
    if choice != "y":
        return "已拒绝或未明确授权；未读取其他会话元数据或工作记录侧车。"
    confirmed_at = _utc_now()

    try:
        current_store._validate_scope()
        after_confirmation = _read_project_sessions(current_session.paths)
        if not _mapping_matches(approved_sources, after_confirmation, selected):
            raise _Unverifiable("selected project mapping changed after confirmation")

        prepared_projects: list[tuple[str, str, WorkRecordStore]] = []
        for project_id in selected:
            session_id = approved_sources[project_id]
            current_store._validate_scope()
            before_read = _read_project_sessions(current_session.paths)
            if not _mapping_matches(approved_sources, before_read, selected):
                raise _Unverifiable("selected project mapping changed before read")

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
            store = WorkRecordStore(source_session)
            store._validate_scope()
            if store._bound_project_id != project_id:
                raise _Unverifiable("WorkRecordStore project binding does not match")
            prepared_projects.append((project_id, session_id, store))

        # Validate all mapped target paths and metadata before opening any sidecar.
        current_store._validate_scope()
        initial_mapping = _read_project_sessions(current_session.paths)
        if not _mapping_matches(approved_sources, initial_mapping, selected):
            raise _Unverifiable("selected project mapping changed before sidecar reads")

        current_recorded_items = current_store.list_items()
        raw_current_independent = [
            item for item in current_recorded_items
            if isinstance(item, dict) and isinstance(item.get("scope"), dict)
            and item["scope"].get("kind") == "independent"
        ]
        has_scoped_records = bool(raw_current_independent)
        current_items = project_visible_items(current_session, current_store, current_recorded_items)
        current_read_at = _utc_now()
        current_store._validate_scope()
        mapping_after_current = _read_project_sessions(current_session.paths)
        if not _mapping_matches(approved_sources, mapping_after_current, selected):
            raise _Unverifiable("selected project mapping changed during current-session read")
        if not isinstance(current_items, list):
            raise _Unverifiable("validated current sidecar has an invalid shape")
        combined_items = [
            item for item in current_items
            if isinstance(item, dict)
            and isinstance(item.get("scope"), dict)
            and item["scope"].get("kind") == "independent"
        ]

        source_lines = [
            f"确认时来源：当前对话独立事项 / 会话 {current_session.conversation_id}；读取时间：{current_read_at}"
        ]
        if not combined_items:
            if raw_current_independent:
                source_lines.append("当前会话 independent 项目外已记录事项均已读并收起；工作记录和历史卡片仍保留。")
            else:
                source_lines.append("当前会话 independent 项目外事项侧车未发现已记录事项；其他历史或侧车外来源未检查。")
        for project_id, session_id, store in prepared_projects:
            current_store._validate_scope()
            store._validate_scope()
            before_sidecar = _read_project_sessions(current_session.paths)
            if not _mapping_matches(approved_sources, before_sidecar, selected):
                raise _Unverifiable("selected project mapping changed before sidecar read")

            recorded_items = store.list_items()
            raw_project_items = [
                item for item in recorded_items
                if isinstance(item, dict) and isinstance(item.get("scope"), dict)
                and item["scope"].get("kind") == "project"
                and item["scope"].get("project_id") == project_id
            ]
            has_scoped_records = has_scoped_records or bool(raw_project_items)
            items = project_visible_items(store._session, store, recorded_items)
            read_at = _utc_now()
            store._validate_scope()
            current_store._validate_scope()
            after_sidecar = _read_project_sessions(current_session.paths)
            if not _mapping_matches(approved_sources, after_sidecar, selected):
                raise _Unverifiable("selected project mapping changed during sidecar read")
            if not isinstance(items, list):
                raise _Unverifiable("validated sidecar has an invalid shape")
            project_items = [
                item for item in items
                if isinstance(item, dict)
                and isinstance(item.get("scope"), dict)
                and item["scope"].get("kind") == "project"
                and item["scope"].get("project_id") == project_id
            ]
            combined_items.extend(project_items)
            source_lines.append(
                f"确认时来源：项目 {project_id} / 会话 {session_id}；读取时间：{read_at}"
            )
            if not project_items:
                if raw_project_items:
                    source_lines.append(
                        f"项目 {project_id} 的已记录事项已读并收起；工作记录和历史卡片仍保留。"
                    )
                else:
                    source_lines.append(
                        f"项目 {project_id} 会话侧车未发现该项目范围已记录事项；其他历史或侧车外来源未检查。"
                    )

        overview = _format_overview(combined_items)
        if combined_items:
            overview = overview.replace("当前会话已记录事项概况", "本次确认来源的已记录事项概况")
            overview = overview.replace("当前会话已记录事项概况", "本次确认来源的已记录事项概况")
            overview = overview.replace("全部已记录要求状态计数", "本次纳入记录的要求状态计数")
            overview = overview.replace(
                "以上已展开全部记录组；记录缺失不代表相关工作没有发生。",
                "本次纳入的记录组已全部展开；未映射项目、其他历史和侧车外来源未检查。",
            )
        else:
            if has_scoped_records:
                overview = (
                    "本次确认范围内的已记录事项均已完成、重要结果均已逐项查看并满足收起等待；"
                    "它们仅从当前概况收起，工作记录、历史卡片及原对话仍保留。"
                    "未映射项目、其他历史和侧车外来源未检查。"
                )
            else:
                overview = (
                    "本次确认的当前会话独立事项及所选项目会话中均未发现已记录事项；"
                    "未映射项目、其他历史和侧车外来源未检查，不能据此断言没有工作。"
                )

        final_mapping = _read_project_sessions(current_session.paths)
        current_store._validate_scope()
        if not _mapping_matches(approved_sources, final_mapping, selected):
            raise _Unverifiable("selected project mapping changed after read")
    except Exception:
        return "无法核对：读取期间来源映射、路径、会话元数据或工作记录侧车发生异常；结果已整体丢弃。"

    return "\n\n".join([
        f"已按用户确认的来源生成工作记录概况。确认时间：{confirmed_at}",
        *source_lines,
        overview,
        "范围说明：包含当前会话的 independent 项目外事项，以及确认时映射到的所选项目会话中的对应项目事项；不汇入项目会话的 independent 事项。",
        "仅覆盖本次确认的已映射项目会话；未映射项目/其他历史未检查，含未绑定项目不能据此断言无事项。",
        "不是完整来源汇总、跨项目原子快照；也不是项目实时执行状态，映射与侧车仍可能在检查间隔内并发变化。",
        _CONVERSATION_READ_SCOPE_NOTICE + "摘要进入当前对话后，后续可能被模型看到；本操作不修改工作记录或历史卡片，已读计时可能更新 presentation sidecar checkpoint。读取可创建最小 .work-records.lock。",
    ])
