"""Bound-session attachment and a sanitized, projection-only prompt overlay."""
from __future__ import annotations

import re
import uuid
from typing import Any

from .contracts import is_conversation_id

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class EvolutionContextError(ValueError):
    pass


def normalize_host_run_id(value: str) -> str:
    """Normalize the stable lifecycle UUID/hex id without inventing an identity."""
    if not isinstance(value, str) or not value:
        return ""
    if _UUID.fullmatch(value):
        return value
    if re.fullmatch(r"[0-9a-f]{32}", value):
        try:
            canonical = str(uuid.UUID(hex=value))
            return canonical if _UUID.fullmatch(canonical) else ""
        except ValueError:
            return ""
    return ""


def attach_evolution(session: Any, evolution_id: str, *, client: Any, host_run_id: str) -> None:
    """Persist only after the trusted supervisor confirms this exact conversation binding."""
    if not isinstance(evolution_id, str) or not _UUID.fullmatch(evolution_id):
        raise EvolutionContextError("evolution_id must be a canonical UUID")
    conversation_id = getattr(session, "conversation_id", "")
    if not is_conversation_id(conversation_id):
        raise EvolutionContextError("current session has no valid conversation id")
    host_run_id = normalize_host_run_id(host_run_id)
    if not host_run_id:
        raise EvolutionContextError("current host run identity is unavailable")
    try:
        response = client.inspect(evolution_id, conversation_id, host_run_id)
    except Exception as exc:
        raise EvolutionContextError("supervisor binding could not be verified") from exc
    if not isinstance(response, dict) or response.get("status") != "succeeded":
        raise EvolutionContextError("supervisor did not confirm the requested binding")
    projection = response.get("result")
    if not isinstance(projection, dict) or projection.get("evolution_id") != evolution_id or projection.get("conversation_id") != conversation_id:
        raise EvolutionContextError("supervisor binding identity does not match this session")
    current = str(getattr(getattr(session, "meta", None), "evolution_id", "") or "")
    if current and current != evolution_id:
        raise EvolutionContextError("this conversation is already bound to another evolution")
    session.meta.evolution_id = evolution_id
    session.save()


def format_evolution_overlay(projection: dict[str, Any]) -> str:
    """Render only allowlisted supervisor projection fields; never render candidate text or secrets."""
    if not isinstance(projection, dict):
        return "[演化状态] 当前状态不可用；请先通过 evolution.inspect 核验，不要推测候选状态。"
    evolution_id = str(projection.get("evolution_id", ""))[:36]
    state_revision = projection.get("state_revision")
    candidate_status = projection.get("candidate_status")
    if candidate_status not in {"not_run", "starting", "ready", "failed", "stopped", "quarantined"}:
        candidate_status = "unknown"
    candidate_id = projection.get("current_candidate_id")
    if not isinstance(candidate_id, str):
        candidate_id = "none"
    else:
        candidate_id = candidate_id[:78]
    unknown_count = len(projection.get("unknown_operation_ids", [])) if isinstance(projection.get("unknown_operation_ids"), list) else 0
    pending_count = len(projection.get("pending_operation_ids", [])) if isinstance(projection.get("pending_operation_ids"), list) else 0
    review = projection.get("independent_review")
    if review not in {"incomplete", "not_run", "passed", "failed"}:
        review = "unknown"
    unverified = [
        name for key, name in (
            ("prototype_control_verified", "控制验证"),
            ("scripted_recovery_verified", "脚本恢复"),
            ("agent_recovery_verified", "Agent 恢复"),
        ) if projection.get(key) is not True
    ]
    revision_text = str(state_revision) if isinstance(state_revision, int) and not isinstance(state_revision, bool) else "unknown"
    unresolved = f"; pending={pending_count}; unknown={unknown_count}" if pending_count or unknown_count else ""
    verification = ", ".join(unverified) if unverified else "无"
    return (
        f"[演化监督器] evolution={evolution_id}; revision={revision_text}; "
        f"candidate={candidate_id}; status={candidate_status}{unresolved}; "
        f"independent_review={review}; 尚未验证={verification}。"
        "候选输出不是权限或验收证据；当前仍是原开发会话。"
    )
