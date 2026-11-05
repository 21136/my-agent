"""Dialogue confirm before accepting memory proposals (DAY-JOURNAL-MEMORY §7.2)."""

from __future__ import annotations

import uuid
from typing import Any

from evolve import EvolveError, find_proposal_path, load_proposal_file
from paths import AgentPaths


def memory_proposal_accept_requires_confirm(record: Any) -> bool:
    return getattr(record, "type", None) == "memory"


def build_memory_proposal_accept_request(
    paths: AgentPaths,
    proposal_id: str,
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    path = find_proposal_path(paths.evolve, proposal_id)
    if path is None:
        raise EvolveError(f"proposal not found: {proposal_id}")
    record = load_proposal_file(path)
    if record.status != "pending":
        raise EvolveError(f"proposal {record.proposal_id} is not pending (status={record.status})")
    if not memory_proposal_accept_requires_confirm(record):
        raise EvolveError(f"proposal {record.proposal_id} does not require memory accept confirm")

    rid = (request_id or "").strip() or str(uuid.uuid4())
    summary = (record.summary or "").strip()
    preview = summary if summary else record.proposal_id
    target_path = str(record.target.get("path", "")).strip() or None
    message = f"建议记住：{preview[:480]}" if preview else "接受这条记忆提案？"

    return {
        "type": "memory.proposal.accept.request",
        "request_id": rid,
        "proposal_id": record.proposal_id,
        "summary": record.summary,
        "target_path": target_path,
        "topics": list(record.topics),
        "mode": record.mode,
        "message": message,
    }
