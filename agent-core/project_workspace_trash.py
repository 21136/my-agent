"""Companion-level soft and hard trash for workspace projects (recoverable under agent ``_trash/``)."""

from __future__ import annotations

import fnmatch
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from paths import AgentPaths
from project_mode import ProjectModeError, is_project_dir, list_projects, normalize_project_id, project_dir
from project_switch import lookup_project_session, read_project_sessions
from session import Session, list_session_ids


@dataclass(frozen=True, slots=True)
class ProjectTrashRow:
    project_id: str
    status: Literal[
        "trashed",
        "already_trashed",
        "purged",
        "skipped",
        "not_found",
        "rejected",
        "failed",
        "external_unlinked",
    ]
    trash_path: str = ""
    work_cards_trashed: int = 0
    reason: str = ""


def resolve_trash_project_ids(
    paths: AgentPaths,
    *,
    project_ids: list[str] | None = None,
    glob_pattern: str | None = None,
) -> list[str]:
    catalog = list_projects(paths)
    if project_ids:
        selected: list[str] = []
        for raw in project_ids:
            try:
                pid = normalize_project_id(raw)
            except ProjectModeError:
                continue
            if pid in catalog:
                selected.append(pid)
        return selected
    pattern = (glob_pattern or "").strip()
    if pattern:
        return sorted(pid for pid in catalog if fnmatch.fnmatchcase(pid, pattern))
    return []


def _agent_trash_root(paths: AgentPaths) -> Path:
    return paths.agent_root / "_trash"


def _unique_trash_dir(trash_root: Path, name: str) -> Path:
    candidate = trash_root / name
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        alt = trash_root / f"{name}-{index}"
        if not alt.exists():
            return alt
        index += 1


def _unlink_project_session(paths: AgentPaths, project_id: str) -> None:
    from paths import read_agent_state_payload, write_agent_state_payload
    from project_switch import PROJECT_SESSIONS_KEY

    payload = read_agent_state_payload(paths)
    mapping = read_project_sessions(paths)
    mapping.pop(project_id, None)
    payload[PROJECT_SESSIONS_KEY] = mapping
    write_agent_state_payload(paths, payload)


def _cascade_one_session(session: Session, *, project_id: str, request_id: str) -> int:
    from work_card_disposition import WorkCardDisposition
    from work_card_ledger import WorkCardLedger
    from work_records import WorkRecordStore

    try:
        store = WorkRecordStore(session)
        ledger = WorkCardLedger(session, store)
        disposition = WorkCardDisposition(session, store, ledger)
        cascade_request = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{request_id}:{project_id}:{session.conversation_id}"))
        result = disposition.soft_delete(
            cascade_request,
            project_id=project_id,
            include_bindings=True,
        )
    except Exception:
        return 0
    if result.status in {"trashed", "already_trashed"}:
        return (
            len(result.affected_card_ids)
            + len(result.affected_binding_ids)
            + len(result.affected_refresh_ids)
        )
    return 0


def _cascade_work_cards(
    paths: AgentPaths,
    *,
    project_id: str,
    request_id: str,
    conversation_ids: set[str],
    live_sessions: tuple[Session, ...] = (),
) -> int:
    affected = 0
    seen: set[str] = set()
    for session in live_sessions:
        if not session.conversation_id:
            continue
        seen.add(session.conversation_id)
        affected += _cascade_one_session(session, project_id=project_id, request_id=request_id)
    for conversation_id in sorted(conversation_ids):
        if not conversation_id or conversation_id in seen:
            continue
        try:
            session = Session.load(paths, conversation_id)
        except Exception:
            continue
        affected += _cascade_one_session(session, project_id=project_id, request_id=request_id)
    return affected


def trash_workspace_projects(
    paths: AgentPaths,
    *,
    project_ids: list[str] | None = None,
    glob_pattern: str | None = None,
    confirm_permanent: bool = False,
    dry_run: bool = False,
    request_id: str | None = None,
    bound_session: Session | None = None,
) -> dict[str, Any]:
    """Soft-trash workspace dirs to agent ``_trash/``; hard delete only with confirm_permanent."""
    if confirm_permanent is not True and not (project_ids or glob_pattern):
        return {
            "ok": False,
            "error": "project_ids or glob_pattern required",
            "results": [],
        }
    selected = resolve_trash_project_ids(
        paths, project_ids=project_ids, glob_pattern=glob_pattern
    )
    if not selected:
        return {"ok": False, "error": "no matching workspace projects", "results": []}

    rid = (request_id or "").strip() or str(uuid.uuid4())
    rows: list[ProjectTrashRow] = []
    trash_root = _agent_trash_root(paths)

    conversation_ids: set[str] = set(list_session_ids(paths, include_internal=True))
    for pid in selected:
        conversation_ids.add(lookup_project_session(paths, pid) or "")
    if bound_session is not None:
        conversation_ids.add(bound_session.conversation_id)
    live_sessions = (bound_session,) if bound_session is not None else ()

    for pid in selected:
        from external_project import validate_external_binding

        if validate_external_binding(paths, pid) is not None:
            if dry_run:
                rows.append(ProjectTrashRow(pid, "external_unlinked", reason="external_root_not_moved"))
                continue
            _unlink_project_session(paths, pid)
            cards = _cascade_work_cards(
                paths,
                project_id=pid,
                request_id=rid,
                conversation_ids=conversation_ids,
                live_sessions=live_sessions,
            )
            rows.append(
                ProjectTrashRow(pid, "external_unlinked", work_cards_trashed=cards)
            )
            continue

        root = project_dir(paths, pid)
        if not is_project_dir(root):
            rows.append(ProjectTrashRow(pid, "not_found", reason="missing_workspace"))
            continue

        rel_source = f"workspace/{pid}"
        rel_trash = f"_trash/{pid}"
        target = _unique_trash_dir(trash_root, pid)
        rel_trash = paths.to_agent_relative(target)

        if dry_run:
            rows.append(ProjectTrashRow(pid, "trashed", trash_path=rel_trash))
            continue

        try:
            if confirm_permanent is True:
                shutil.rmtree(root)
                _unlink_project_session(paths, pid)
                cards = _cascade_work_cards(
                    paths,
                    project_id=pid,
                    request_id=rid,
                    conversation_ids=conversation_ids,
                    live_sessions=live_sessions,
                )
                rows.append(ProjectTrashRow(pid, "purged", work_cards_trashed=cards))
            else:
                trash_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(root), str(target))
                _unlink_project_session(paths, pid)
                cards = _cascade_work_cards(
                    paths,
                    project_id=pid,
                    request_id=rid,
                    conversation_ids=conversation_ids,
                    live_sessions=live_sessions,
                )
                rows.append(
                    ProjectTrashRow(pid, "trashed", trash_path=rel_trash, work_cards_trashed=cards)
                )
        except OSError as exc:
            rows.append(ProjectTrashRow(pid, "failed", reason=str(exc)))

    cleared_binding: str | None = None
    if bound_session is not None:
        bound_pid = (bound_session.meta.project_id or "").strip()
        if bound_pid in selected:
            from project_cli import clear_session_project_binding

            if clear_session_project_binding(bound_session):
                bound_session.save()
                cleared_binding = bound_pid

    from companion_home_policy import TRASH_CLEARED_BINDING_HINT

    companion_stays_unbound = bool(
        bound_session is not None and not (bound_session.meta.project_id or "").strip()
    )

    return {
        "ok": True,
        "dry_run": dry_run,
        "confirm_permanent": confirm_permanent is True,
        "request_id": rid,
        "project_ids": selected,
        "cleared_session_binding": cleared_binding,
        "companion_stays_unbound": companion_stays_unbound,
        "companion_hint": TRASH_CLEARED_BINDING_HINT if cleared_binding else None,
        "results": [
            {
                "project_id": row.project_id,
                "status": row.status,
                "trash_path": row.trash_path or None,
                "work_cards_trashed": row.work_cards_trashed,
                "reason": row.reason or None,
            }
            for row in rows
        ],
    }
