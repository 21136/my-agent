from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from day_journal import DayJournal
from day_journal_summary import source_fingerprint, verify_candidate_binding


class SummaryAcceptanceError(ValueError):
    """A candidate failed an explicit acceptance/provenance check."""


_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def accept_summary_candidate(
    journal: DayJournal,
    *,
    candidate: Mapping[str, Any],
    approval: Any,
    at: datetime,
    project_id: str,
    session_id: str,
    snapshot: Mapping[str, Any],
    expected_revision: int,
    segment_id: str,
    candidate_context_token: str,
    current_context_token: Callable[[], str],
    validate_snapshot: Callable[[dict[str, Any]], Any],
    finalize: Callable[[Callable[[], dict]], dict] | None = None,
) -> dict:
    """Append an approved model candidate, retaining evidence and an unverified label.

    `validate_snapshot` is the integration boundary that must confirm these exact
    supplied excerpts belong to the identified session/project/date snapshot.
    No source discovery, history scan, outbound call, or verification claim occurs.
    """
    if not callable(approval):
        raise SummaryAcceptanceError("an explicit save-confirmation callback is required")
    if (not isinstance(candidate, Mapping) or candidate.get("status") != "candidate"
            or not verify_candidate_binding(candidate)):
        raise SummaryAcceptanceError("candidate is missing or has an invalid generation-time binding")
    if candidate.get("verified") is not False:
        raise SummaryAcceptanceError("candidate must carry the unverified marker")
    summary = candidate.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise SummaryAcceptanceError("candidate summary is invalid")
    source_ids = candidate.get("sources")
    if (not isinstance(source_ids, list) or not source_ids
            or any(not isinstance(item, str) or not _SOURCE_ID_RE.fullmatch(item) for item in source_ids)
            or len(set(source_ids)) != len(source_ids)):
        raise SummaryAcceptanceError("candidate must retain unique source IDs")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
        raise SummaryAcceptanceError("an existing journal revision is required")
    candidate_fingerprint = candidate.get("source_fingerprint")
    if not isinstance(candidate_fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", candidate_fingerprint):
        raise SummaryAcceptanceError("candidate must carry a normalized original-source fingerprint")
    bound_context_token = candidate.get("context_token")
    if not isinstance(bound_context_token, str) or not bound_context_token:
        raise SummaryAcceptanceError("candidate must carry its generation context token")
    if not isinstance(candidate_context_token, str) or not candidate_context_token:
        raise SummaryAcceptanceError("candidate context token is required")
    if candidate_context_token != bound_context_token:
        raise SummaryAcceptanceError("provided context token does not match the generated candidate")
    if not callable(current_context_token) or not callable(validate_snapshot):
        raise SummaryAcceptanceError("snapshot and current-context validators are required")

    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "date", "project_id", "session_id", "sources", "context_token"
    }:
        raise SummaryAcceptanceError("snapshot descriptor has an invalid schema")
    try:
        snapshot_copy = json.loads(json.dumps(dict(snapshot), ensure_ascii=False))
        day_key, _ = journal._instant(at)
    except Exception as exc:
        raise SummaryAcceptanceError("snapshot or target timestamp is invalid") from exc
    if (snapshot_copy["date"] != day_key or snapshot_copy["project_id"] != project_id
            or snapshot_copy["session_id"] != session_id
            or snapshot_copy["context_token"] != bound_context_token):
        raise SummaryAcceptanceError("snapshot date/session/project/context does not match candidate target")
    records = snapshot_copy["sources"]
    if not isinstance(records, list) or not records:
        raise SummaryAcceptanceError("verified source snapshot is required")
    snapshot_ids: list[str] = []
    for record in records:
        if (not isinstance(record, dict) or set(record) != {"source_id", "text"}
                or not isinstance(record["source_id"], str)
                or not _SOURCE_ID_RE.fullmatch(record["source_id"])
                or not isinstance(record["text"], str) or not record["text"].strip()):
            raise SummaryAcceptanceError("snapshot source record is malformed")
        snapshot_ids.append(record["source_id"])
    if len(snapshot_ids) != len(set(snapshot_ids)) or not set(source_ids).issubset(snapshot_ids):
        raise SummaryAcceptanceError("candidate source IDs do not match the supplied snapshot")
    if source_fingerprint(records) != candidate_fingerprint:
        raise SummaryAcceptanceError("source text fingerprint differs from generation-time sources")
    if current_context_token() != candidate_context_token:
        raise SummaryAcceptanceError("candidate context is stale")
    try:
        validator_input = json.loads(json.dumps(snapshot_copy, ensure_ascii=False))
        validated = validate_snapshot(validator_input)
    except Exception as exc:
        raise SummaryAcceptanceError("snapshot validation failed") from exc
    if validated is not True:
        raise SummaryAcceptanceError("snapshot was not explicitly validated")
    if current_context_token() != candidate_context_token:
        raise SummaryAcceptanceError("context changed during snapshot validation")

    preview = {
        "action": "append_unverified_model_summary_candidate",
        "summary": summary,
        "date": day_key,
        "project_id": project_id,
        "session_id": session_id,
        "source_ids": list(source_ids),
        "source_fingerprint": candidate_fingerprint,
        "stored_as_unverified": True,
        "source_text_persisted": False,
        "append_only": True,
        "existing_day_summary_overwritten": False,
        "notice": "仅追加模型摘要候选；不复制/持久化来源原文；该摘要未经核实。",
    }
    preview_digest = hashlib.sha256(json.dumps(
        preview, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if approval(preview) is not True:
        raise SummaryAcceptanceError("summary save was not explicitly approved")
    after_digest = hashlib.sha256(json.dumps(
        preview, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if after_digest != preview_digest:
        raise SummaryAcceptanceError("confirmed save preview changed")
    if current_context_token() != bound_context_token:
        raise SummaryAcceptanceError("context changed during save confirmation")
    context_fingerprint = hashlib.sha256(bound_context_token.encode("utf-8")).hexdigest()

    def publish() -> dict:
        return journal.add_segment(
            at, project_id, summary, segment_id=segment_id,
            source={"kind": "session", "session_id": session_id},
            expected_revision=expected_revision, summary_authorized=True,
            summary_metadata={"kind": "model_summary_candidate", "verified": False,
                             "source_ids": source_ids, "source_fingerprint": candidate_fingerprint,
                             "context_fingerprint": context_fingerprint},
        )

    # Legacy callers keep the old API/behavior. Integrated source-backed callers
    # supply a finalizer that holds the work-record lock across final validation
    # and this publication (work-record lock -> journal lock).
    return finalize(publish) if finalize is not None else publish()
