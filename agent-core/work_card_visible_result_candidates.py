"""Bounded current-Session candidates from durably referenced, renderer-received cards.

This module is read-only: it neither loads another Session nor sends recognition
requests, creates messages/sessions, changes presentation state, or ACKs results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from session import (
    Session, _is_internal_work_card_message, _messages_unusable_for_ref,
    _session_messages_lock, _valid_work_card_ref,
)
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_card_result_reply_recognition import (
    ResultReplyCandidate, _MAX_CANDIDATES, _MAX_CANDIDATE_BYTES, _candidate_facts,
)
from work_records import WorkRecordCorrupt, WorkRecordError, WorkRecordStore, _file_lock

_LIFECYCLE_KIND = "project_worker_host_lifecycle"
_MAX_HISTORY_REFERENCES = 64
_STATUS_TEXT = {
    "completed": "工作结果已记录；尚未验证。",
    "blocked": "工作结果记录为受阻；验收未运行，尚未验证。",
    "stopped": "工作已停止；结果尚未验证。",
    "unknown": "工作结果待确认；尚未验证。",
    "in_progress": "执行状态待确认。",
}


@dataclass(frozen=True, slots=True)
class DisplayedResultCandidateSet:
    """A complete set for one exact Session, or an empty fail-closed result."""

    complete: bool
    candidates: tuple[ResultReplyCandidate, ...] = ()
    reason: str = ""


def _incomplete(reason: str) -> DisplayedResultCandidateSet:
    return DisplayedResultCandidateSet(False, (), reason)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate summary key")
        result[key] = value
    return result


def _safe_result_summary(value: Any, result_status: Any) -> str:
    if type(value) is not str:
        raise ValueError("result summary is invalid")
    try:
        decoded = json.loads(value, object_pairs_hook=_strict_object)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        if _LIFECYCLE_KIND in value:
            raise ValueError("host lifecycle summary is malformed") from None
        return value
    if (type(decoded) is dict and decoded.get("kind") == _LIFECYCLE_KIND) or _LIFECYCLE_KIND in value:
        safe = _STATUS_TEXT.get(result_status)
        if safe is None:
            raise ValueError("host lifecycle result status is unsupported")
        # Never forward the raw receipt, host error code, paths, or internal IDs.
        return safe
    return value


def _extract_card_candidates(
    session_id: str,
    presentation_id: str,
    card_id: str,
    snapshot: dict[str, Any],
) -> tuple[tuple[tuple[str, ...], ResultReplyCandidate], ...]:
    kind = snapshot.get("kind")
    if kind in {"linked_item_copy", "linked_project_session_copy"}:
        return ()  # copied-source cards intentionally carry no result ACK identity
    if kind == "independent":
        card_items = [snapshot.get("card")]
    elif kind in {"project", "project_scoped"}:
        card_items = snapshot.get("cards")
    else:
        raise ValueError("unsupported immutable card kind")
    if type(card_items) is not list or not card_items:
        raise ValueError("immutable card item list is invalid")
    result_map = WorkCardPresentations._result_map(snapshot)
    candidates: list[tuple[tuple[str, ...], ResultReplyCandidate]] = []
    for item in card_items:
        if type(item) is not dict or type(item.get("requirements")) is not list:
            raise ValueError("immutable card requirement list is invalid")
        title = item.get("title")
        if type(title) is not str:
            raise ValueError("immutable card title is invalid")
        for requirement in item["requirements"]:
            if type(requirement) is not dict:
                raise ValueError("immutable requirement is invalid")
            result = requirement.get("latest_result")
            if result is None:
                continue
            if type(result) is not dict:
                raise ValueError("immutable result is invalid")
            requirement_id = requirement.get("requirement_id")
            result_id = result.get("result_id")
            if (type(requirement_id) is not str or not requirement_id
                    or type(result_id) is not str or not result_id
                    or result_map.get(result_id) != [requirement_id]):
                raise ValueError("result identity is ambiguous in immutable snapshot")
            run_id = result.get("run_id")
            if type(run_id) is not str or not run_id:
                raise ValueError("immutable result run identity is invalid")
            candidate = ResultReplyCandidate(
                session_id=session_id,
                presentation_id=presentation_id,
                card_id=card_id,
                result_id=result_id,
                requirement_id=requirement_id,
                title=title,
                requirement_text=requirement.get("text"),
                result_summary=_safe_result_summary(result.get("summary"), result.get("status")),
                result_status=result.get("status"),
                verification_status=result.get("verification_status"),
            )
            # item and run identities are internal dedupe evidence, not sent to recognition.
            candidates.append(((item.get("item_id"), requirement_id, result_id, run_id), candidate))
            if len(candidates) > _MAX_CANDIDATES:
                raise OverflowError("candidate limit exceeded")
    return tuple(candidates)


def _candidate_display(candidate: ResultReplyCandidate) -> tuple[str, ...]:
    return (candidate.title, candidate.requirement_text, candidate.result_summary,
            candidate.result_status, candidate.verification_status or "")


def _received_order(value: str, reference_index: int) -> tuple[datetime, int]:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("received timestamp has no timezone")
    return parsed.astimezone(timezone.utc), reference_index


def collect_displayed_result_candidates(session: Session) -> DisplayedResultCandidateSet:
    """Collect only exact results from renderer-received cards referenced in this Session.

    Partial message histories are never completed here. A reference/presentation/card
    disagreement, corrupt store, duplicate candidate, or recognizer limit overflow
    returns no candidates with ``complete=False`` rather than a truncated set.
    """
    if type(session) is not Session:
        return _incomplete("invalid_session")
    if (type(session.conversation_id) is not str or not session.conversation_id
            or not session._messages_fully_loaded or session._messages_corrupt
            or session._messages_unpersisted):
        return _incomplete("session_history_unavailable")
    try:
        session.ensure_messages_loaded  # inspect only; deliberately do not load partial history
        with _session_messages_lock(session.session_dir):
            if (not session._messages_fully_loaded or session._messages_corrupt
                    or session._messages_unpersisted or _messages_unusable_for_ref(session)):
                return _incomplete("session_history_unavailable")
            history_version = session._messages_disk_version
            references = []
            seen_presentations: set[str] = set()
            for message in session.messages:
                if not _is_internal_work_card_message(message):
                    continue
                if not _valid_work_card_ref(message):
                    return _incomplete("invalid_history_reference")
                presentation_id = message["presentation_id"]
                if presentation_id in seen_presentations:
                    return _incomplete("duplicate_history_reference")
                seen_presentations.add(presentation_id)
                references.append(dict(message))
                # More persisted references than the recognizer's candidate cap
                # cannot safely be silently narrowed to a recent subset.
                if len(references) > _MAX_HISTORY_REFERENCES:
                    return _incomplete("history_reference_limit_exceeded")

        if not references:
            return DisplayedResultCandidateSet(True)
        store = WorkRecordStore(session)
        ledger = WorkCardLedger(session, store)
        presentations = WorkCardPresentations(session, store, ledger)
        selected: dict[tuple[str, ...], tuple[tuple[str, ...], tuple[datetime, int], ResultReplyCandidate]] = {}
        with _file_lock(store._lock_path, store._lock_timeout):
            store._validate_scope()
            ledger._validate_binding()
            presentations._validate_binding()
            presentation_data = presentations._presentations_store._read()
            cards = presentations._cards_locked(presentation_data)
            presentation_rows = {
                row["presentation_id"]: row for row in presentation_data["items"]
            }
            for reference_index, reference in enumerate(references):
                presentation_id = reference["presentation_id"]
                card_id = reference["card_id"]
                reference_run_id = reference.get("run_id")
                presentation = presentation_rows.get(presentation_id)
                if presentation is None:
                    return _incomplete("presentation_missing_for_history_reference")
                if presentation.get("card_id") != card_id:
                    return _incomplete("history_presentation_card_mismatch")
                if reference_run_id is None:
                    if presentation.get("receipt_state", "legacy_untracked") == "legacy_untracked":
                        continue
                    return _incomplete("history_presentation_run_mismatch")
                if presentation.get("run_id") != reference_run_id:
                    return _incomplete("history_presentation_run_mismatch")
                if presentation.get("receipt_state", "legacy_untracked") != "received":
                    continue
                if type(presentation.get("received_at")) is not str:
                    return _incomplete("received_presentation_invalid")
                card = cards.get(card_id)
                if type(card) is not dict or type(card.get("snapshot")) is not dict:
                    return _incomplete("immutable_card_unavailable")
                snapshot = card["snapshot"]
                if (snapshot.get("session_id") != session.conversation_id
                        or card.get("card_id") != card_id):
                    return _incomplete("immutable_card_scope_mismatch")
                try:
                    batch = _extract_card_candidates(
                        session.conversation_id, presentation_id, card_id, snapshot,
                    )
                    received_order = _received_order(presentation["received_at"], reference_index)
                except OverflowError:
                    return _incomplete("candidate_limit_exceeded")
                except Exception:
                    return _incomplete("immutable_candidate_invalid")
                for identity, candidate in batch:
                    display = _candidate_display(candidate)
                    prior = selected.get(identity)
                    if prior is not None:
                        if prior[0] != display:
                            return _incomplete("redelivered_result_content_conflict")
                        if received_order > prior[1]:
                            selected[identity] = (display, received_order, candidate)
                    else:
                        selected[identity] = (display, received_order, candidate)
                    if len(selected) > _MAX_CANDIDATES:
                        return _incomplete("candidate_limit_exceeded")

        candidates = tuple(row[2] for row in selected.values())
        if not candidates:
            return DisplayedResultCandidateSet(True)
        # Reuse the recognizer's exact public candidate-size/duplicate contract;
        # an over-budget set is rejected whole, never sliced to fit.
        validated = _candidate_facts(session.conversation_id, candidates, complete=True)
        if validated is None:
            byte_total = sum(
                len(value.encode("utf-8"))
                for candidate in candidates
                for value in (candidate.title, candidate.requirement_text, candidate.result_summary)
                if type(value) is str
            )
            reason = "candidate_payload_limit_exceeded" if byte_total > _MAX_CANDIDATE_BYTES else "candidate_set_invalid"
            return _incomplete(reason)
        with _session_messages_lock(session.session_dir):
            if (not session._messages_fully_loaded or session._messages_corrupt
                    or session._messages_unpersisted
                    or session._messages_disk_version != history_version
                    or _messages_unusable_for_ref(session)):
                return _incomplete("session_history_changed")
        return DisplayedResultCandidateSet(True, validated)
    except (WorkRecordError, WorkRecordCorrupt, OSError, ValueError, TypeError, RuntimeError):
        return _incomplete("session_card_data_unavailable")
    except Exception:
        return _incomplete("session_card_data_unavailable")
