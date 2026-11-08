"""Structured overview queries for one caller-authorized Session.

This module deliberately accepts a Session object rather than an identifier or
path to search. It reads that Session's work-record, immutable-card and
presentation sidecars under their shared lock. Valid elapsed checkpoints are
atomically persisted to the presentation sidecar; untrusted wall-clock gaps are
discarded rather than accrued.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from collections import Counter
from typing import Any

from session import Session
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_visibility_projection import (
    _WAIT_NS, _WALL_SKEW_NS, _advance_evidence, _wall_ns,
    _current_results,
    _snapshot_matches,
)
import work_record_visibility_clock
from work_records import WorkRecordCorrupt, WorkRecordError, WorkRecordScopeError, WorkRecordStore, _file_lock
from work_records_overview import _make_groups, _overview_items, _trusted_worker_batch_projects

_DEFAULT_PAGE_SIZE = 6
_DEFAULT_VISIBILITY_RECHECK_MS = 10 * 60 * 1000
_MIN_VISIBILITY_RECHECK_MS = 60 * 1000
_MAX_PAGE_SIZE = 50
_MAX_CURSOR_CHARS = 4096
_DETAIL_PAGE_SIZE = 5
_MAX_UNREAD_SAMPLE = 100
_STATUS_ORDER = ("blocked", "unknown", "waiting_confirmation", "in_progress", "stopped", "queued", "pending", "completed")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _session_scope(store: WorkRecordStore) -> str:
    identity = {
        "conversation_id": store._conversation_id,
        "session_dir": str(store._session_dir.resolve(strict=False)).casefold(),
        "agent_root": str(store._agent_root.resolve(strict=False)).casefold(),
        "project_id": store._bound_project_id,
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _status(items: tuple[dict[str, Any], ...]) -> str:
    statuses = [
        requirement.get("status")
        for item in items
        for requirement in item.get("requirements", [])
        if type(requirement) is dict
    ]
    if not statuses:
        return "no_requirements"
    return min(
        (value if type(value) is str and value in _STATUS_ORDER else "unknown" for value in statuses),
        key=lambda value: _STATUS_ORDER.index(value),
    )


def _status_counts(items: tuple[dict[str, Any], ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        for requirement in item.get("requirements", []):
            if type(requirement) is dict:
                status = requirement.get("status")
                counts[status if type(status) is str and status else "other"] += 1
    return dict(sorted(counts.items()))


def _index_runs_by_requirement(runs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Index result rows once while preserving their append/history order."""
    by_requirement: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if type(run) is not dict:
            continue
        requirement_id = run.get("requirement_id")
        if type(requirement_id) is str:
            by_requirement.setdefault(requirement_id, []).append(run)
    return by_requirement


def _result_snapshot_fact(value: Any, *, frozen: bool) -> tuple[Any, ...] | None:
    if type(value) is not dict:
        return None
    if frozen:
        summary = value.get("summary")
        verification_status = value.get("verification_status")
    else:
        summary = value.get("result_summary")
        verification = value.get("verification")
        verification_status = verification.get("status") if type(verification) is dict else None
    sources = value.get("sources")
    if type(sources) is not list:
        return None
    try:
        source_facts = _canonical_json(sources)
    except (TypeError, ValueError, UnicodeError):
        return None
    return (value.get("run_id"), value.get("status"), summary, verification_status, source_facts)


def _all_current_results(items: list[dict[str, Any]]) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    """Map every uniquely identified result on a requirement's current run.

    Visibility collapsing intentionally uses the narrower completed-only
    projection. Read certainty must not: blocked/unknown (and other persisted
    current result statuses) can still be important updates.
    """
    by_item: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for item in items:
        if type(item) is not dict or type(item.get("requirements")) is not list or type(item.get("runs")) is not list:
            continue
        item_id = item.get("item_id")
        if type(item_id) is not str or not item_id:
            continue
        runs_by_requirement = _index_runs_by_requirement(item["runs"])
        current: dict[tuple[str, str], dict[str, Any]] = {}
        for requirement in item["requirements"]:
            if type(requirement) is not dict:
                continue
            requirement_id = requirement.get("requirement_id")
            run_id = requirement.get("current_run_id")
            if type(requirement_id) is not str or not requirement_id or type(run_id) is not str or not run_id:
                continue
            matches = [run for run in runs_by_requirement.get(requirement_id, ())
                       if run.get("run_id") == run_id]
            if len(matches) != 1:
                continue
            result_id = matches[0].get("result_id")
            if type(result_id) is str and result_id:
                current[(requirement_id, result_id)] = matches[0]
        if current:
            by_item[item_id] = current
    return by_item


def _index_requirement_run_starts(history: Any) -> dict[str, dict[str, Any]] | None:
    """Index only events relevant to pre-start proof, preserving its exact predicate."""
    if type(history) is not list:
        return None
    by_run_id: dict[str, dict[str, Any]] = {}
    for event in history:
        if (type(event) is dict and event.get("kind") == "requirement_run_started"
                and type(event.get("payload")) is dict
                and type(event["payload"].get("run_id")) is str):
            by_run_id.setdefault(event["payload"]["run_id"], event)
    return by_run_id


def _bound_prestart_blocked_results(items: list[dict[str, Any]], cards: dict[str, dict[str, Any]],
                                   session_id: str) -> tuple[dict[str, dict[tuple[str, str], dict[str, Any]]], bool]:
    """Expose runless blocked receipts only when receipt and frozen project card agree.

    These are result facts, not started runs. Invalid or not-yet-published facts
    make read certainty unknown; they never become unread or disappear from status.
    Card snapshots and each item's run history are indexed once, so matching is
    proportional to stored card facts plus source rows rather than their product.
    """
    from work_card_project_scope import _valid_prestart_blocked_receipt

    candidates: list[tuple[str, str, str, str, dict[str, Any], tuple[Any, ...]]] = []
    uncertain = False
    for item in items:
        if type(item) is not dict or type(item.get("item_id")) is not str:
            continue
        item_id = item["item_id"]
        requirements = item.get("requirements")
        runs = item.get("runs")
        if type(requirements) is not list or type(runs) is not list:
            continue
        runs_by_requirement = _index_runs_by_requirement(runs)
        has_runless_blocked = any(
            type(requirement) is dict and requirement.get("status") == "blocked"
            and requirement.get("current_run_id") is None
            for requirement in requirements
        )
        history = item.get("history")
        run_starts = _index_requirement_run_starts(history) if has_runless_blocked else None
        for requirement in requirements:
            if (type(requirement) is not dict or requirement.get("status") != "blocked"
                    or requirement.get("current_run_id") is not None):
                continue
            requirement_id = requirement.get("requirement_id")
            if type(requirement_id) is not str or not requirement_id:
                continue
            matching = runs_by_requirement.get(requirement_id, [])
            if not matching or matching[-1].get("status") != "blocked":
                continue
            result = matching[-1]
            source_ref = requirement.get("source_ref")
            project_id = (source_ref.get("id") if type(source_ref) is dict
                          and source_ref.get("kind") == "project" else None)
            result_id = result.get("result_id")
            fact = _result_snapshot_fact(result, frozen=False)
            receipt_history = history
            if run_starts is not None:
                start_event = run_starts.get(result.get("run_id"))
                receipt_history = [start_event] if start_event is not None else []
            if (type(project_id) is not str or not project_id
                    or not _valid_prestart_blocked_receipt(
                        requirement, result, session_id=session_id, item_id=item_id,
                        project_id=project_id, history=receipt_history,
                    )
                    or type(result_id) is not str or not result_id or fact is None):
                uncertain = True
                continue
            candidates.append((project_id, item_id, requirement_id, result_id, result, fact))

    if not candidates:
        return {}, uncertain

    # Index only frozen blocked results in the exact same-session project scope.
    # Store compact facts equivalent to _snapshot_matches to make duplicate
    # immutable snapshots for the same source result an O(1) membership check.
    frozen_by_identity: dict[tuple[str, str, str, str], set[tuple[Any, ...]]] = {}
    for card in cards.values():
        if type(card) is not dict or type(card.get("snapshot")) is not dict:
            continue
        snapshot = card["snapshot"]
        if (snapshot.get("kind") != "project_scoped"
                or snapshot.get("session_id") != session_id
                or snapshot.get("completeness") != "complete"):
            continue
        project_id = snapshot.get("project_id")
        if type(project_id) is not str or not project_id:
            continue
        card_items = snapshot.get("cards")
        if type(card_items) is not list:
            continue
        for card_item in card_items:
            if type(card_item) is not dict or type(card_item.get("item_id")) is not str:
                continue
            item_id = card_item["item_id"]
            frozen_requirements = card_item.get("requirements")
            if type(frozen_requirements) is not list:
                continue
            for frozen_requirement in frozen_requirements:
                if (type(frozen_requirement) is not dict
                        or frozen_requirement.get("project_id") != project_id
                        or frozen_requirement.get("source_ref") != {"kind": "project", "id": project_id}
                        or frozen_requirement.get("status") != "blocked"
                        or frozen_requirement.get("current_run_id") is not None
                        or frozen_requirement.get("latest_result_is_current") is not None
                        or frozen_requirement.get("current_result_missing") is not False):
                    continue
                requirement_id = frozen_requirement.get("requirement_id")
                frozen_result = frozen_requirement.get("latest_result")
                if (type(requirement_id) is not str or not requirement_id
                        or type(frozen_result) is not dict
                        or type(frozen_result.get("result_id")) is not str or not frozen_result["result_id"]
                        or type(frozen_result.get("run_id")) is not str or not frozen_result["run_id"]
                        or frozen_result.get("status") != "blocked"):
                    continue
                fact = _result_snapshot_fact(frozen_result, frozen=True)
                if fact is not None:
                    key = (project_id, item_id, requirement_id, frozen_result["result_id"])
                    frozen_by_identity.setdefault(key, set()).add(fact)

    by_item: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for project_id, item_id, requirement_id, result_id, result, fact in candidates:
        key = (project_id, item_id, requirement_id, result_id)
        if fact not in frozen_by_identity.get(key, ()):
            uncertain = True
            continue
        by_item.setdefault(item_id, {})[(requirement_id, result_id)] = result
    return by_item, uncertain

def _trusted_remaining_ns(evidence: Any, sample) -> int | None:
    """Return a same-boot monotonic remainder, rejecting uncertain intervals."""
    if type(evidence) is not dict or sample is None or set(evidence) != {
        "ack_boot_id", "ack_monotonic_ns", "ack_wall_utc", "accrued_ns",
        "checkpoint_boot_id", "checkpoint_monotonic_ns", "checkpoint_wall_utc",
    }:
        return None
    try:
        if (type(evidence["ack_boot_id"]) is not str or not evidence["ack_boot_id"]
                or type(evidence["checkpoint_boot_id"]) is not str
                or evidence["checkpoint_boot_id"] != sample.boot_id
                or type(evidence["ack_monotonic_ns"]) is not int or evidence["ack_monotonic_ns"] < 0
                or type(evidence["checkpoint_monotonic_ns"]) is not int
                or evidence["checkpoint_monotonic_ns"] < evidence["ack_monotonic_ns"]
                or type(evidence["accrued_ns"]) is not int or evidence["accrued_ns"] < 0
                or type(sample.monotonic_ns) is not int):
            return None
        ack_wall = _wall_ns(evidence["ack_wall_utc"])
        checkpoint_wall = _wall_ns(evidence["checkpoint_wall_utc"])
        now_wall = _wall_ns(sample.wall_utc)
        elapsed = sample.monotonic_ns - evidence["checkpoint_monotonic_ns"]
        if elapsed < 0 or abs((now_wall - checkpoint_wall) - elapsed) > _WALL_SKEW_NS:
            return None
        # Wall time validates discontinuity only; elapsed duration comes from the
        # same-boot monotonic sample and already-proven monotonic accrual.
        if ack_wall > checkpoint_wall + _WALL_SKEW_NS:
            return None
        return max(0, _WAIT_NS - evidence["accrued_ns"] - elapsed)
    except Exception:
        return None

def _current_presentation_evidence(items, presentations_data, cards, sample, session_id):
    """Return read facts and the authoritative group-level visibility projection.

    This mirrors ``project_visible_items`` using validated sidecar snapshots.
    The caller holds the shared Session lock and persists any safe checkpoint
    changes atomically before releasing it.
    """
    current_by_item = _all_current_results(items)
    prestart_by_item, uncertain_prestart = _bound_prestart_blocked_results(items, cards, session_id)
    for item_id, blocked_results in prestart_by_item.items():
        current_by_item.setdefault(item_id, {}).update(blocked_results)
    completed_by_item, fully_completed = _current_results(items)
    trusted_batches: dict[str, tuple[dict[str, str], tuple[str, str]]] = {}
    for item in items:
        if type(item) is not dict or type(item.get("item_id")) is not str:
            continue
        projects, original = _trusted_worker_batch_projects(item)
        if projects and original is not None:
            trusted_batches[item["item_id"]] = (projects, original)
    if not current_by_item:
        return {}, current_by_item, set(), {}, trusted_batches, {}, False, uncertain_prestart

    required_read_views: set[tuple[str, str]] = set()
    viewed_read_views: set[tuple[str, str]] = set()
    required_views: set[tuple[str, str]] = set()
    viewed_views: set[tuple[str, str]] = set()
    evidenceful_views: set[tuple[str, str]] = set()
    mature_views: set[tuple[str, str]] = set()
    remaining_by_pair: dict[tuple[str, str], int] = {}
    checkpoint_changed = False

    for presentation in presentations_data["items"]:
        card_id = presentation["card_id"]
        card = cards.get(card_id)
        if card is None:
            continue
        snapshot = card["snapshot"]
        card_items = [snapshot["card"]] if snapshot.get("kind") == "independent" else snapshot.get("cards", [])
        represented: dict[tuple[str, str], dict[str, Any]] = {}
        for card_item in card_items:
            for requirement in card_item.get("requirements", []):
                result = requirement.get("latest_result")
                requirement_id = requirement.get("requirement_id")
                if type(result) is dict and type(requirement_id) is str:
                    result_id = result.get("result_id")
                    if type(result_id) is str:
                        represented[(requirement_id, result_id)] = result

        current_read_pairs: set[tuple[str, str]] = set()
        current_completed_pairs: set[tuple[str, str]] = set()
        for pair, frozen in represented.items():
            read_matches = [runs[pair] for runs in current_by_item.values() if pair in runs]
            if len(read_matches) == 1 and _snapshot_matches(read_matches[0], frozen):
                current_read_pairs.add(pair)
                required_read_views.add(pair)
            completed_matches = [runs[pair] for runs in completed_by_item.values() if pair in runs]
            if len(completed_matches) == 1 and _snapshot_matches(completed_matches[0], frozen):
                current_completed_pairs.add(pair)
                required_views.add(pair)

        for view in presentation.get("result_views", []):
            pair = (view.get("requirement_id"), view.get("result_id"))
            if pair in current_read_pairs:
                viewed_read_views.add(pair)
            if pair not in current_completed_pairs:
                continue
            viewed_views.add(pair)
            evidence = view.get("elapsed_evidence")
            if type(evidence) is not dict or sample is None:
                continue
            evidenceful_views.add(pair)
            remaining_ns = _trusted_remaining_ns(evidence, sample)
            if remaining_ns is not None:
                remaining_by_pair[pair] = min(remaining_ns, remaining_by_pair.get(pair, remaining_ns))
            # The caller holds the shared Session lock and persists this exact
            # validated sidecar snapshot atomically before releasing that lock.
            changed, mature = _advance_evidence(evidence, sample)
            checkpoint_changed = checkpoint_changed or changed
            if mature:
                mature_views.add(pair)

    read_status: dict[tuple[str, str], str] = {}
    for runs in current_by_item.values():
        for pair in runs:
            required = pair in required_read_views
            viewed = pair in viewed_read_views
            if not required:
                # No immutable presentation proves this result's exposure/read state.
                read_status[pair] = "unknown"
            elif viewed:
                read_status[pair] = "viewed"
            else:
                read_status[pair] = "unread"

    def result_is_mature(pair: tuple[str, str]) -> bool:
        return (pair in required_views and pair in viewed_views
                and pair in evidenceful_views and pair in mature_views)

    def requirement_is_mature(item_id: str, requirement_id: str) -> bool:
        pairs = [pair for pair in completed_by_item.get(item_id, {}) if pair[0] == requirement_id]
        return len(pairs) == 1 and result_is_mature(pairs[0])

    project_requirements: dict[tuple[str, str], set[str]] = {}
    for item_id, (projects, _original) in trusted_batches.items():
        for requirement_id, project_id in projects.items():
            project_requirements.setdefault((item_id, project_id), set()).add(requirement_id)
    hidden_batch_requirements: dict[str, set[str]] = {}
    for (item_id, _project_id), requirement_ids in project_requirements.items():
        if requirement_ids and all(requirement_is_mature(item_id, requirement_id) for requirement_id in requirement_ids):
            hidden_batch_requirements.setdefault(item_id, set()).update(requirement_ids)

    # Keep the authoritative projection's existing whole ordinary item/project
    # group rule, then separately record per-project worker-batch requirements.
    hidden_items: set[str] = set()
    for group in _make_groups(items):
        item_ids = [item.get("item_id") for item in group.items]
        if group.key[0] == "project" and any(item_id not in fully_completed for item_id in item_ids):
            continue
        group_ready = True
        for item_id in item_ids:
            pairs = completed_by_item.get(item_id, {})
            if not pairs or item_id not in fully_completed:
                group_ready = False
                break
            if any(not result_is_mature(pair) for pair in pairs):
                group_ready = False
                break
        if group_ready:
            hidden_items.update(item_ids)
    return (read_status, current_by_item, hidden_items, hidden_batch_requirements,
            trusted_batches, remaining_by_pair, checkpoint_changed, uncertain_prestart)


def _decode_cursor(cursor: str) -> dict[str, Any] | None:
    if type(cursor) is not str or not cursor or len(cursor) > _MAX_CURSOR_CHARS:
        return None
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate cursor key")
            result[key] = value
        return result
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
    except Exception:
        return None
    if type(value) is not dict:
        return None
    if value.get("v") == 1 and type(value.get("v")) is int:
        if (set(value) != {"v", "scope", "snapshot", "offset", "page_size"}
                or type(value["page_size"]) is not int
                or not 1 <= value["page_size"] <= _MAX_PAGE_SIZE):
            return None
    elif value.get("v") == 2 and type(value.get("v")) is int:
        if (set(value) != {"v", "scope", "snapshot", "offset", "kind", "group_kind", "group_id", "item_id"}
                or value["kind"] not in ("items", "requirements")
                or value["group_kind"] not in ("project", "independent", "linked_source_snapshot")
                or type(value["group_id"]) is not str or not value["group_id"]
                or (value["kind"] == "items" and value["item_id"] is not None)
                or (value["kind"] == "requirements" and (type(value["item_id"]) is not str or not value["item_id"]))):
            return None
    else:
        return None
    if (type(value["scope"]) is not str or type(value["snapshot"]) is not str
            or type(value["offset"]) is not int or value["offset"] < 0):
        return None
    return value


def _encode_cursor(scope: str, snapshot: str, offset: int, page_size: int) -> str:
    raw = _canonical_json({"v": 1, "scope": scope, "snapshot": snapshot,
                           "offset": offset, "page_size": page_size})
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _detail_cursor(scope, snapshot, kind, group, item_id, offset):
    raw = _canonical_json({"v": 2, "scope": scope, "snapshot": snapshot,
                           "kind": kind, "group_kind": group["kind"], "group_id": group["id"],
                           "item_id": item_id, "offset": offset})
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _page_facts(scope, snapshot, kind, group, item_id, offset, total):
    next_offset = min(offset + _DETAIL_PAGE_SIZE, total)
    more = next_offset < total
    return {"kind": kind, "group_kind": group["kind"], "group_id": group["id"],
            "item_id": item_id, "offset": offset, "total": total, "has_more": more,
            "next_cursor": _detail_cursor(scope, snapshot, kind, group, item_id, next_offset) if more else None}


def _snapshot_view_evidence_fingerprint(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind cursors to view identity/time, not advancing elapsed checkpoints."""
    result = []
    for row in data.get("items", []):
        evidence = row.get("evidence")
        result.append({
            "presentation_id": row["presentation_id"],
            "card_id": row["card_id"],
            "view": None if evidence is None else {
                "ack_boot_id": evidence["ack_boot_id"],
                "ack_monotonic_ns": evidence["ack_monotonic_ns"],
                "ack_wall_utc": evidence["ack_wall_utc"],
            },
        })
    return result


def _linked_source_snapshot_groups(session_id: str, binding_data: dict[str, Any],
                                   ledger_data: dict[str, Any], *,
                                   elapsed_by_identity: dict[tuple[str, str], str] | None = None,
                                   include_view_targets: bool = False):
    """Project confirmed parent-copied source snapshots as bounded attention groups.

    This consumes only the exact parent binding and immutable ledger sidecars. It
    never opens a source Session or infers that the copied project is still mapped.
    Old versions are coalesced per project; unviewed superseded versions remain an
    explicit attention count rather than separate stacked groups. A card viewed_at
    alone is not elapsed-time evidence. Collapse is permitted only when every
    requirement in the captured source snapshot was completed and the separate,
    durable linked-snapshot view-evidence primitive reports a trusted elapsed interval;
    unknown verification is not treated as running, while any captured nonterminal
    requirement remains visible. Missing, legacy, corrupt, or clock-anomalous view
    evidence is conservative and never authorizes collapse.
    """
    from dataclasses import asdict
    from uuid import NAMESPACE_URL, UUID, uuid5
    from work_card_ledger import (
        build_linked_project_session_copy_snapshot,
        linked_project_refresh_request_id,
        linked_project_session_copy_request_id,
    )

    records = ledger_data.get("items", [])
    candidates: list[dict[str, Any]] = []
    view_targets: list[tuple[str, str, str | None]] = []
    elapsed_by_identity = elapsed_by_identity or {}

    def add_candidate(binding_id: str, copy: Any, request_id: str, order: int,
                      run_id: str | None) -> None:
        if type(copy) is not dict or type(copy.get("intent")) is not dict:
            raise WorkRecordCorrupt("confirmed source snapshot intent is invalid")
        intent = copy["intent"]
        if intent.get("state") not in {"pending", "reference_queued"}:
            raise WorkRecordCorrupt("confirmed source snapshot state is invalid")
        # The builder consumes pending intents; use an ephemeral clone. No on-disk
        # state is changed and this never re-validates or reads a source capability.
        buildable = {**copy, "intent": {**intent, "state": "pending"}}
        snapshot = asdict(build_linked_project_session_copy_snapshot(session_id, binding_id, buildable))
        source = snapshot["source"]
        matches = [record for record in records if record.get("request_id") == request_id
                   and record.get("snapshot") == snapshot]
        if len(matches) > 1:
            raise WorkRecordCorrupt("source snapshot has duplicate ledger cards")
        record = matches[0] if matches else None
        card_id = record.get("card_id") if record is not None else None
        presentation_id = None
        if card_id is not None and type(run_id) is str:
            run_identity = UUID(run_id).hex
            presentation_id = str(uuid5(NAMESPACE_URL,
                f"my-agent-work-card:{session_id}:{run_identity}:{card_id}"))
            view_targets.append((card_id, presentation_id, record.get("viewed_at")))
        source_requirements = [req for item in source["items"] for req in item["requirements"]]
        captured_complete = bool(source_requirements) and all(req["status"] == "completed" for req in source_requirements)
        candidates.append({
            "project_id": source["project_id"], "source_session_id": source["source_session_id"],
            "source": source, "snapshot": snapshot, "intent_state": intent["state"],
            "confirmed_at": copy["confirmed_at"], "card_id": card_id,
            "presentation_id": presentation_id,
            "view_evidence": elapsed_by_identity.get((card_id, presentation_id), "unknown")
                if card_id is not None and presentation_id is not None else "unavailable",
            "captured_complete": captured_complete,
            "viewed_at": record.get("viewed_at") if record is not None else None,
            "order": order,
        })

    order = 0
    for batch in binding_data.get("batches", []):
        for row in batch.get("bindings", []):
            copy = row.get("project_session_copy")
            if copy is None:
                continue
            request_id = linked_project_session_copy_request_id(session_id, copy["intent"]["copy_id"])
            anchor = next((fact for fact in copy["snapshot"]["worker_facts"]
                           if fact.get("binding_id") == row["binding_id"]), None)
            run_id = anchor.get("host_start", {}).get("parent_run_id") if type(anchor) is dict else None
            add_candidate(row["binding_id"], copy, request_id, order, run_id)
            order += 1
    for refresh in binding_data.get("project_refreshes", []):
        copy = refresh["copy"]
        request_id = linked_project_refresh_request_id(session_id, refresh["refresh_id"])
        add_candidate(refresh["binding_id"], copy, request_id, order, refresh["run_id"])
        order += 1

    by_project: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_project.setdefault(candidate["project_id"], []).append(candidate)

    groups: list[dict[str, Any]] = []
    for project_id, versions in sorted(by_project.items()):
        # Confirmation timestamps use canonical UTC ISO form. Insertion order
        # breaks equal-time ties so a later confirmed refresh takes precedence.
        versions.sort(key=lambda row: (row["confirmed_at"], row["order"]))
        latest = versions[-1]
        older = versions[:-1]
        # Superseded capture-time status is historical, not current attention.
        # Retain only older versions that lack valid explicit-view elapsed proof;
        # an older in-progress/blocked state must not pin the latest completed
        # and reviewed snapshot forever once its own historical card matured.
        older_unviewed = sum(1 for row in older if row["view_evidence"] != "elapsed")
        card_missing = latest["card_id"] is None
        publication_pending = latest["intent_state"] == "pending" or card_missing
        latest_unviewed = latest["view_evidence"] != "elapsed"
        source = latest["source"]
        # Only source requirements captured completed plus durable explicit-view
        # elapsed proof may collapse. Host result verification remains unknown and
        # is not interpreted as live/running; nonterminal captured requirements
        # remain visible even after their card was read.
        current_capture_needs_attention = not latest["captured_complete"]
        superseded_needs_attention = older_unviewed > 0
        elapsed_all = latest["captured_complete"] and latest["view_evidence"] == "elapsed" and not superseded_needs_attention
        needs_attention = publication_pending or current_capture_needs_attention or latest_unviewed or superseded_needs_attention
        if not needs_attention and not elapsed_all:
            # Missing/corrupt/unavailable evidence is conservative, not elapsed.
            needs_attention = True

        facts_by_requirement: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for fact in source["worker_facts"]:
            facts_by_requirement.setdefault((fact["item_id"], fact["requirement_id"]), []).append({
                "binding_id": fact["binding_id"], "worker_id": fact["worker_id"],
                "worker_run_id": fact["worker_run_id"], "host_start": fact["host_start"],
                "host_lifecycle": fact["host_lifecycle"], "operation_result": "unknown",
                "verification": "not_verified",
            })
        items = []
        requirement_count = 0
        for item in source["items"]:
            requirements = []
            for requirement in item["requirements"]:
                requirements.append({
                    "requirement_id": requirement["requirement_id"], "text": requirement["text"],
                    "status": "unknown", "status_at_capture": requirement["status"],
                    "worker_facts": facts_by_requirement.get((item["item_id"], requirement["requirement_id"]), []),
                })
                requirement_count += 1
            items.append({
                "item_id": item["item_id"], "title": item["title"],
                "status": "unknown", "status_at_capture": item["status"],
                "status_counts": {"unknown": 1}, "requirements": requirements,
                "current_results": [],
            })
        stable_id = "linked-source-" + hashlib.sha256(project_id.encode("utf-8")).hexdigest()
        reason = ("publication_pending" if publication_pending else
                  "captured_requirement_unresolved" if current_capture_needs_attention else
                  "superseded_snapshot_attention" if superseded_needs_attention else
                  "view_evidence_unavailable" if latest["view_evidence"] == "unavailable" else
                  "view_not_confirmed")
        groups.append({
            "kind": "linked_source_snapshot", "id": stable_id,
            "visibility": "collapsed" if elapsed_all and not needs_attention else "visible",
            "status": "unknown", "status_counts": {"unknown": 1},
            "item_count": len(items), "requirement_count": requirement_count, "items": items,
            "attention_status": "none" if elapsed_all and not needs_attention else "unknown",
            "attention_reason": "elapsed_terminal_capture" if elapsed_all and not needs_attention else reason,
            "visibility_timer_status": "elapsed" if elapsed_all and not needs_attention else latest["view_evidence"],
            "source_snapshot": {
                "scope": "authorized_source_session_only", "mapping_claim": "not_asserted",
                "project_id": project_id, "source_session_id": source["source_session_id"],
                "source_revision": source["source_revision"], "captured_at": latest["confirmed_at"],
                "copy_id": latest["snapshot"]["copy_id"], "intent_state": latest["intent_state"],
                "card_id": latest["card_id"], "viewed_at": latest["viewed_at"],
                "superseded_snapshot_count": len(older),
                "superseded_unviewed_count": older_unviewed,
            },
        })
    return (groups, view_targets) if include_view_targets else groups


def _linked_snapshot_view_prepass(session: Session, store: WorkRecordStore, ledger: WorkCardLedger):
    """Advance/read view evidence outside the overview's long shared-lock section.

    The evidence primitive acquires the same parent lock itself. Its results are
    accepted later only if all parent sidecars still match this post-checkpoint
    baseline; a race therefore degrades to unknown instead of a stale collapse.
    """
    from linked_snapshot_view_evidence import LinkedSnapshotViewEvidence
    from multi_project_work_record_bindings import ConversationWorkRecordBindings

    bindings = ConversationWorkRecordBindings(session, lock_timeout=store._lock_timeout)
    presentations = WorkCardPresentations(session, store, ledger)
    evidence = LinkedSnapshotViewEvidence(session, store, ledger)
    with _file_lock(store._lock_path, store._lock_timeout):
        binding_data = bindings._view_locked(bindings._read())
        ledger_data = ledger._ledger_store._read()
        presentation_data = presentations._presentations_store._read()
        _groups, targets = _linked_source_snapshot_groups(
            session.conversation_id, binding_data, ledger_data, include_view_targets=True,
        )
        presentation_rows = {row["presentation_id"]: row for row in presentation_data["items"]}

    elapsed_by_identity: dict[tuple[str, str], str] = {}
    for card_id, presentation_id, _viewed_at in targets:
        row = presentation_rows.get(presentation_id)
        if (type(row) is not dict or row.get("card_id") != card_id
                or row.get("acknowledged_at") is None):
            elapsed_by_identity[(card_id, presentation_id)] = "not_viewed"
            continue
        elapsed_by_identity[(card_id, presentation_id)] = evidence.elapsed(presentation_id, card_id).status

    # Capture a coherent comparison baseline after elapsed() has advanced any
    # monotonic checkpoint. Main query must compare this with its own locked read.
    with _file_lock(store._lock_path, store._lock_timeout):
        baseline = {
            "bindings": bindings._view_locked(bindings._read()),
            "ledger": ledger._ledger_store._read(),
            "presentations": presentations._presentations_store._read(),
            "evidence": evidence._evidence._read(),
        }
    return bindings, evidence, elapsed_by_identity, baseline


def _status_rank(row):
    status = row.get("status")
    return _STATUS_ORDER.index(status) if status in _STATUS_ORDER else _STATUS_ORDER.index("unknown")


def _bounded_item(item, group, scope, snapshot, offset=0):
    result = dict(item)
    requirements = sorted(item["requirements"], key=_status_rank)
    selected = requirements[offset:offset + _DETAIL_PAGE_SIZE]
    ids = {req["requirement_id"] for req in selected}
    result["requirements"] = selected
    result["current_results"] = [row for row in item["current_results"] if row["requirement_id"] in ids]
    result["requirements_page"] = _page_facts(scope, snapshot, "requirements", group, item["item_id"], offset, len(requirements))
    return result


def _bounded_group(group, scope, snapshot, offset=0):
    result = dict(group)
    items = sorted(group["items"], key=_status_rank)
    result["items"] = [_bounded_item(item, group, scope, snapshot) for item in items[offset:offset + _DETAIL_PAGE_SIZE]]
    result["items_page"] = _page_facts(scope, snapshot, "items", group, None, offset, len(items))
    return result


def _empty(state: str, *, reason: str | None = None) -> dict[str, Any]:
    result = {
        "state": state,
        "groups": [],
        "unread_important_results": [],
        "visibility_limitations": [],
        "visibility_counts": {"visible_groups": 0, "visible_items": 0,
                              "collapsed_groups": 0, "collapsed_items": 0},
        "snapshot_id": None,
        "visibility_recheck_after_ms": _DEFAULT_VISIBILITY_RECHECK_MS,
        "unread_status": "known" if state == "no_records" else "unknown",
        "page": {"page_size": _DEFAULT_PAGE_SIZE, "offset": 0, "total_groups": 0,
                 "total_visible_groups": 0, "total_visible_items": 0,
                 "total_collapsed_groups": 0, "total_collapsed_items": 0,
                 "has_more": False, "next_cursor": None},
    }
    if reason is not None:
        result["reason"] = reason
    return result


def query_work_records_overview(
    session: Session,
    *,
    cursor: str | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    detail_paging: bool = False,
) -> dict[str, Any]:
    """Return structured current work records for exactly the supplied Session.

    Elapsed evidence checkpoints may be atomically persisted to the presentation
    sidecar under the same lock; work records, cards, and read acknowledgements
    are not changed. The first page defaults to six groups. Pass ``page.next_cursor`` to expand
    subsequent pages; every cursor is bound to both the Session scope and a
    canonical digest of contributing source sidecars and visible projection semantics. A changed
    source returns ``expired`` with no rows rather than mixing page snapshots.
    Nested pagination is opt-in via detail_paging=True on every call, including
    cursor follow-ups; the WebSocket handler forwards this flag when supplied.
    Statuses are persisted business state, not a claim that a worker process is
    currently alive or that a result passed verification.
    """
    if type(session) is not Session:
        return _empty("error", reason="exact_authorized_session_required")
    if type(detail_paging) is not bool:
        return _empty("error", reason="invalid_detail_paging")
    if type(page_size) is not int or not 1 <= page_size <= _MAX_PAGE_SIZE:
        return _empty("error", reason="invalid_page_size")
    decoded = None
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        if decoded is None:
            return _empty("error", reason="invalid_cursor")
        if decoded["v"] == 2 and not detail_paging:
            return _empty("error", reason="detail_paging_required")
        if decoded["v"] == 1:
            page_size = decoded["page_size"]

    try:
        store = WorkRecordStore(session)
        ledger = WorkCardLedger(session, store)
        presentations = WorkCardPresentations(session, store, ledger)
        scope = _session_scope(store)
        linked_bindings, snapshot_view_evidence, elapsed_by_identity, linked_baseline = (
            _linked_snapshot_view_prepass(session, store, ledger)
        )
        with _file_lock(store._lock_path, store._lock_timeout):
            store._validate_scope()
            source_data = store._read()
            ledger_data = ledger._ledger_store._read()
            presentation_data = presentations._presentations_store._read()
            binding_data = linked_bindings._view_locked(linked_bindings._read())
            view_evidence_data = snapshot_view_evidence._evidence._read()
            evidence_matches_baseline = (
                binding_data == linked_baseline["bindings"]
                and ledger_data == linked_baseline["ledger"]
                and presentation_data == linked_baseline["presentations"]
                and view_evidence_data == linked_baseline["evidence"]
            )
            current_elapsed = elapsed_by_identity if evidence_matches_baseline else {}
            linked_source_groups = _linked_source_snapshot_groups(
                session.conversation_id, binding_data, ledger_data,
                elapsed_by_identity=current_elapsed,
            )
            previous_presentation_data = copy.deepcopy(presentation_data)
            cards = presentations._cards_locked(presentation_data)
            sample = work_record_visibility_clock.sample_elapsed_clock()
            (read_status, current_results_by_item, hidden_item_ids,
             hidden_batch_requirements, trusted_batches, remaining_by_pair, checkpoint_changed,
             uncertain_prestart_blocked) = _current_presentation_evidence(
                source_data["items"], presentation_data, cards, sample, session.conversation_id,
            )
            if checkpoint_changed:
                presentation_data["revision"] += 1
                presentations._presentations_store._validate_payload(presentation_data)
                presentations._presentations_store._publish(
                    presentation_data, previous=previous_presentation_data,
                )
            overview_items = _overview_items(source_data["items"])
            raw_items_by_id = {row.get("item_id"): row for row in source_data["items"]
                               if type(row) is dict and type(row.get("item_id")) is str}
            grouped = _make_groups(overview_items)
            visibility_limitations: list[str] = []
            group_rows = list(linked_source_groups)
            unread = []
            has_unread_unknown = (uncertain_prestart_blocked
                                  or any(group["visibility"] == "visible" for group in linked_source_groups))
            if linked_source_groups:
                visibility_limitations.extend((
                    "linked_source_snapshot_currentness_unverified",
                    "linked_source_snapshot_elapsed_evidence_conservative",
                ))
            for group in grouped:
                def display_item_is_collapsed(item: dict[str, Any]) -> bool:
                    item_id = item.get("item_id")
                    if item_id in hidden_item_ids:
                        return True
                    if group.key[0] != "project" or type(item_id) is not str:
                        return False
                    batch = trusted_batches.get(item_id)
                    if batch is None:
                        return False
                    projects, _original = batch
                    project_requirement_ids = {
                        requirement_id for requirement_id, project_id in projects.items()
                        if project_id == group.key[1]
                    }
                    hidden_ids = hidden_batch_requirements.get(item_id, set())
                    return bool(project_requirement_ids) and project_requirement_ids <= hidden_ids

                visibility = "collapsed" if all(display_item_is_collapsed(item) for item in group.items) else "visible"
                item_rows = []
                for item in group.items:
                    requirements = []
                    current_results = []
                    for requirement in item.get("requirements", []):
                        if type(requirement) is not dict:
                            continue
                        requirement_id = requirement.get("requirement_id")
                        status = requirement.get("status") if type(requirement.get("status")) is str else "unknown"
                        dependencies = requirement.get("depends_on", [])
                        raw_item = raw_items_by_id.get(item.get("item_id"), {})
                        history_rows = []
                        events_by_run = {}
                        for history_event in raw_item.get("history", []):
                            if (type(history_event) is dict and history_event.get("kind") == "result_recorded"
                                    and type(history_event.get("payload")) is dict):
                                payload = history_event["payload"]
                                if payload.get("requirement_id") == requirement_id and type(payload.get("run_id")) is str:
                                    events_by_run[payload["run_id"]] = history_event
                        for run in raw_item.get("runs", []):
                            if (type(run) is not dict or run.get("requirement_id") != requirement_id
                                    or type(run.get("run_id")) is not str or type(run.get("result_id")) is not str):
                                continue
                            # A pre-start blocked worker receipt is displayable only
                            # when the immutable current project card independently
                            # proves its exact source. Do not leak its receipt/result
                            # identity through the generic run-history projection.
                            if (run.get("status") == "blocked"
                                    and requirement.get("current_run_id") is None):
                                continue
                            verification = run.get("verification")
                            event_row = events_by_run.get(run["run_id"], {})
                            history_rows.append({
                                "run_id": run["run_id"], "result_id": run["result_id"],
                                "status": run.get("status", "unknown"),
                                "summary": run.get("result_summary", "") if type(run.get("result_summary")) is str else "",
                                "verification_status": verification.get("status", "unknown") if type(verification) is dict else "unknown",
                                "recorded_at": event_row.get("at") if type(event_row.get("at")) in (int, float) else None,
                            })
                        history_rows = history_rows[-10:]
                        requirements.append({
                            "requirement_id": requirement_id,
                            "text": requirement.get("text", "") if type(requirement.get("text")) is str else "",
                            "status": status,
                            "depends_on": dependencies if type(dependencies) is list else [],
                            "depends_on_refs": requirement.get("depends_on_refs", []) if type(requirement.get("depends_on_refs", [])) is list else [],
                            "claim_recovery_needed": (type(requirement.get("schedule_claim")) is dict
                                and requirement["schedule_claim"].get("state") == "unknown"),
                            "history": history_rows,
                        })
                        item_results = current_results_by_item.get(item.get("item_id"), {})
                        current_pair = next((pair for pair in item_results if pair[0] == requirement_id), None)
                        if current_pair is not None:
                            result_fact = {
                                "requirement_id": current_pair[0],
                                "result_id": current_pair[1],
                                "status": item_results[current_pair].get("status", "unknown"),
                                "read_status": read_status.get(current_pair, "unknown"),
                            }
                            current_results.append(result_fact)
                            if visibility == "visible" and result_fact["read_status"] == "unknown":
                                has_unread_unknown = True
                            if visibility == "visible" and result_fact["read_status"] == "unread":
                                unread.append({
                                    "group": [group.key[0], group.key[1]],
                                    "item_id": item.get("item_id"),
                                    "requirement_id": current_pair[0],
                                    "result_id": current_pair[1],
                                })
                    item_rows.append({
                        "item_id": item.get("item_id"),
                        "title": item.get("title", "") if type(item.get("title")) is str else "",
                        "priority": item.get("priority", 3) if type(item.get("priority", 3)) is int else 3,
                        "status": _status((item,)),
                        "status_counts": _status_counts((item,)),
                        "requirements": requirements,
                        "current_results": current_results,
                    })
                group_rows.append({
                    "kind": group.key[0],
                    "id": group.key[1],
                    "visibility": visibility,
                    "status": _status(group.items),
                    "status_counts": _status_counts(group.items),
                    "item_count": group.item_count,
                    "requirement_count": group.requirement_count,
                    "items": item_rows,
                })
            projection = {
                "groups": group_rows,
                "unread_important_results": unread,
                "unread_status": "unknown" if has_unread_unknown else "known",
                "visibility_limitations": visibility_limitations,
            }
            fingerprint = hashlib.sha256(_canonical_json({
                "scope": scope,
                "work_records": source_data,
                "ledger": ledger_data,
                "presentations": presentation_data,
                "linked_bindings": binding_data,
                # Elapsed checkpoints are progress samples. Bind cursors to the
                # explicit view identity/time while ignoring monotonic checkpoint
                # advancement that does not change what the user sees.
                "snapshot_view_evidence": _snapshot_view_evidence_fingerprint(view_evidence_data),
                "projection": projection,
            })).hexdigest()

        visible_groups = [row for row in group_rows if row["visibility"] == "visible"]
        collapsed_groups = [row for row in group_rows if row["visibility"] == "collapsed"]
        visible_items = sum(row["item_count"] for row in visible_groups)
        collapsed_items = sum(row["item_count"] for row in collapsed_groups)
        visibility_counts = {
            "visible_groups": len(visible_groups),
            "visible_items": visible_items,
            "collapsed_groups": len(collapsed_groups),
            "collapsed_items": collapsed_items,
        }
        if decoded is not None:
            if decoded["scope"] != scope or decoded["snapshot"] != fingerprint:
                return _empty("expired", reason="source_snapshot_changed")
            offset = decoded["offset"] if decoded["v"] == 1 else 0
            if decoded["v"] == 1 and (offset > len(visible_groups) or offset % page_size != 0):
                return _empty("error", reason="invalid_cursor_offset")
        else:
            offset = 0
        page_groups = visible_groups[offset:offset + page_size]
        next_offset = offset + len(page_groups)
        has_more = next_offset < len(visible_groups)
        state = "visible" if visible_groups else "collapsed" if collapsed_groups else "no_records"
        recheck_after_ms = _DEFAULT_VISIBILITY_RECHECK_MS
        if any(group.get("kind") == "linked_source_snapshot" and group.get("visibility") == "visible"
               and group.get("visibility_timer_status") == "waiting" for group in visible_groups):
            # Evidence primitive only reports waiting/elapsed, not a trustworthy
            # remaining duration. Recheck conservatively at the minimum cadence;
            # never locally hide the source snapshot based on this timer.
            recheck_after_ms = min(recheck_after_ms, _MIN_VISIBILITY_RECHECK_MS)
        if (len(visible_groups) == 1 and len(page_groups) == 1 and not has_more
                and page_groups[0]["requirement_count"] > 0
                and page_groups[0]["status_counts"].get("completed") == page_groups[0]["requirement_count"]):
            group = page_groups[0]
            results = [result for item in group["items"] for result in item["current_results"]]
            requirements = [req for item in group["items"] for req in item["requirements"]]
            pairs = [(result["requirement_id"], result["result_id"]) for result in results]
            if (len(pairs) == len(requirements) and len(set(pairs)) == len(pairs)
                    and all(result["read_status"] == "viewed" for result in results)):
                remainders = [remaining_by_pair[pair] for pair in pairs if pair in remaining_by_pair]
                if len(remainders) != len(pairs):
                    remainders = []
                if remainders:
                    # Recheck when the earliest trusted explicit ACK can mature.
                    remaining_ms = (min(remainders) + 999_999) // 1_000_000
                    if remaining_ms > 0:
                        recheck_after_ms = max(_MIN_VISIBILITY_RECHECK_MS, remaining_ms)
        detail_page = None
        if decoded is not None and decoded["v"] == 2:
            matches = [g for g in visible_groups if g["kind"] == decoded["group_kind"] and g["id"] == decoded["group_id"]]
            if len(matches) != 1:
                return _empty("error", reason="invalid_detail_target")
            group = matches[0]
            detail_offset = decoded["offset"]
            if detail_offset <= 0 or detail_offset % _DETAIL_PAGE_SIZE:
                return _empty("error", reason="invalid_cursor_offset")
            if decoded["kind"] == "items":
                if detail_offset >= len(group["items"]):
                    return _empty("error", reason="invalid_cursor_offset")
                bounded = _bounded_group(group, scope, fingerprint, detail_offset)
                detail_page = bounded["items_page"]
            else:
                matches = [i for i in group["items"] if i["item_id"] == decoded["item_id"]]
                if len(matches) != 1 or detail_offset >= len(matches[0]["requirements"]):
                    return _empty("error", reason="invalid_detail_target")
                bounded = dict(group)
                bounded["items"] = [_bounded_item(matches[0], group, scope, fingerprint, detail_offset)]
                detail_page = bounded["items"][0]["requirements_page"]
                bounded["items_page"] = None
            page_groups = [bounded]
        elif detail_paging:
            page_groups = [_bounded_group(g, scope, fingerprint) for g in page_groups]
        return {
            "state": state,
            "work_record_revision": source_data["revision"],
            "groups": page_groups,
            "detail_page": detail_page,
            "unread_important_results_total": len(unread),
            "unread_important_results_truncated": detail_paging and len(unread) > _MAX_UNREAD_SAMPLE,
            "visibility_recheck_after_ms": recheck_after_ms,
            "unread_important_results": unread[:_MAX_UNREAD_SAMPLE] if detail_paging else unread,
            "unread_status": projection["unread_status"],
            "visibility_limitations": visibility_limitations,
            "visibility_counts": visibility_counts,
            "snapshot_id": fingerprint,
            "page": {
                "page_size": page_size,
                "offset": offset,
                # Pagination counts only rows actually returned by default.
                "total_groups": len(visible_groups),
                "total_visible_groups": len(visible_groups),
                "total_visible_items": visible_items,
                "total_collapsed_groups": len(collapsed_groups),
                "total_collapsed_items": collapsed_items,
                "has_more": has_more,
                "next_cursor": _encode_cursor(scope, fingerprint, next_offset, page_size) if has_more else None,
            },
        }
    except Exception as exc:
        # Do not leak sidecar contents or paths through a structured API error.
        if isinstance(exc, WorkRecordScopeError):
            reason = "session_scope_error"
        elif isinstance(exc, WorkRecordCorrupt):
            reason = "source_invalid"
        elif isinstance(exc, WorkRecordError):
            reason = "source_unavailable"
        else:
            reason = "source_unavailable"
        return _empty("error", reason=reason)


__all__ = ["query_work_records_overview"]

