"""Whether current-dialogue project-scoped cards belong on the default chat surface."""
from __future__ import annotations

from typing import Any

try:
    from work_card_project_scope import ProjectScopedWorkRecordCardsSnapshot
except ImportError:
    ProjectScopedWorkRecordCardsSnapshot = Any  # type: ignore[misc, assignment]


def worker_batch_should_defer_project_cards_to_chat(rows: object) -> bool:
    """Worker-orchestrated batch snapshots stay in work records / 当前工作, not main chat."""
    if not isinstance(rows, list) or not rows:
        return False
    try:
        from worker_outcome_user_copy import format_worker_batch_failure_notice

        if format_worker_batch_failure_notice(rows):
            return True
    except Exception:
        pass
    failure_statuses = frozenset({"blocked", "failed", "cancelled", "error", "timeout", "provider_error"})
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") in failure_statuses or row.get("model_turn_status") in failure_statuses:
            return True
        if row.get("record_lifecycle_status") == "blocked_recorded":
            return True
    return False


def candidate_defers_chat_surface(candidate: object) -> bool:
    return isinstance(candidate, dict) and candidate.get("defer_chat_surface") is True


def project_scoped_snapshot_deferred_from_chat(snapshot: object) -> bool:
    """Keep failed / blocked project-scoped snapshots off chat replay and resume."""
    if isinstance(snapshot, ProjectScopedWorkRecordCardsSnapshot):
        payload: dict[str, Any] = {
            "kind": snapshot.kind,
            "cards": snapshot.cards,
        }
    elif isinstance(snapshot, dict):
        payload = snapshot
    else:
        return False
    if payload.get("kind") != "project_scoped":
        return False
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return False
    blocked_statuses = frozenset({"blocked", "failed"})
    saw_requirement = False
    for card in cards:
        if not isinstance(card, dict):
            continue
        requirements = card.get("requirements")
        if not isinstance(requirements, list):
            continue
        for requirement in requirements:
            if not isinstance(requirement, dict):
                continue
            saw_requirement = True
            if requirement.get("status") in blocked_statuses:
                return True
            latest = requirement.get("latest_result")
            if isinstance(latest, dict) and latest.get("status") in blocked_statuses:
                return True
            summary = latest.get("summary") if isinstance(latest, dict) else None
            if isinstance(summary, str) and '"host_status": "blocked"' in summary:
                return True
            if isinstance(summary, str) and '"host_status": "failed"' in summary:
                return True
    return False
