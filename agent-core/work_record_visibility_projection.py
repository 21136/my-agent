"""Conservative projection of current work records after verified result views."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
import work_record_visibility_clock
from work_records import WorkRecordStore, _file_lock

_WAIT_NS = 30 * 60 * 1_000_000_000
_WALL_SKEW_NS = 120 * 1_000_000_000
_CHECKPOINT_INTERVAL_NS = 60 * 1_000_000_000
_EVIDENCE_KEYS = {"ack_boot_id", "ack_monotonic_ns", "ack_wall_utc", "accrued_ns",
                  "checkpoint_boot_id", "checkpoint_monotonic_ns", "checkpoint_wall_utc"}
_TRUSTED_BATCH_PROJECTION = object()


def _wall_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return int(parsed.timestamp() * 1_000_000_000)


def trusted_worker_batch_projection(
    item: dict[str, Any],
) -> tuple[dict[str, str], tuple[str, str] | None] | None:
    """Return the in-memory trust handoff emitted only by this projection module."""
    value = item.get("_work_record_visibility_batch")
    if type(value) is not tuple or len(value) != 3 or value[0] is not _TRUSTED_BATCH_PROJECTION:
        return None
    projects, original = value[1], value[2]
    if type(projects) is not dict or (original is not None and type(original) is not tuple):
        return None
    return projects, original


def _current_results(items: list[dict[str, Any]]) -> tuple[dict[str, dict[tuple[str, str], dict[str, Any]]], set[str]]:
    """Map current completed runs and separately note fully completed items."""
    by_item: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    fully_completed: set[str] = set()
    for item in items:
        if type(item) is not dict or type(item.get("requirements")) is not list or type(item.get("runs")) is not list:
            continue
        requirements = item["requirements"]
        item_id = item.get("item_id")
        if not requirements or type(item_id) is not str or not item_id:
            continue
        current: dict[tuple[str, str], dict[str, Any]] = {}
        all_completed = True
        for req in requirements:
            if type(req) is not dict:
                all_completed = False
                continue
            if req.get("status") != "completed":
                all_completed = False
                continue
            requirement_id = req.get("requirement_id")
            current_run = req.get("current_run_id")
            if type(requirement_id) is not str or not requirement_id or type(current_run) is not str or not current_run:
                all_completed = False
                continue
            matches = [run for run in item["runs"] if type(run) is dict
                       and run.get("requirement_id") == requirement_id and run.get("run_id") == current_run]
            if len(matches) != 1 or matches[0].get("status") != "completed":
                all_completed = False
                continue
            result_id = matches[0].get("result_id")
            if type(result_id) is not str or not result_id:
                all_completed = False
                continue
            current[(requirement_id, result_id)] = matches[0]
        if current:
            by_item[item_id] = current
        if all_completed and len(current) == len(requirements):
            fully_completed.add(item_id)
    return by_item, fully_completed


def _snapshot_matches(run: dict[str, Any], result: dict[str, Any]) -> bool:
    verification = run.get("verification")
    expected_verification = verification.get("status") if type(verification) is dict else None
    return (run.get("requirement_id") == result.get("requirement_id")
            and run.get("result_id") == result.get("result_id")
            and run.get("run_id") == result.get("run_id") and run.get("status") == result.get("status")
            and run.get("result_summary") == result.get("summary")
            and expected_verification == result.get("verification_status")
            and run.get("sources") == result.get("sources"))


def _advance_evidence(evidence: Any, sample) -> tuple[bool, bool]:
    """Return (changed, mature); only monotonic intervals count toward 30 minutes."""
    if type(evidence) is not dict or set(evidence) != _EVIDENCE_KEYS:
        return False, False
    try:
        if (type(evidence["ack_boot_id"]) is not str or not evidence["ack_boot_id"]
                or type(evidence["checkpoint_boot_id"]) is not str or not evidence["checkpoint_boot_id"]
                or type(evidence["ack_monotonic_ns"]) is not int or evidence["ack_monotonic_ns"] < 0
                or type(evidence["checkpoint_monotonic_ns"]) is not int or evidence["checkpoint_monotonic_ns"] < 0
                or type(evidence["accrued_ns"]) is not int or evidence["accrued_ns"] < 0):
            return False, False
        _wall_ns(evidence["ack_wall_utc"])
        checkpoint_wall = _wall_ns(evidence["checkpoint_wall_utc"])
        now_wall = _wall_ns(sample.wall_utc)
        if evidence["checkpoint_boot_id"] != sample.boot_id:
            # The gap across reboot is unknowable without trusting wall time. Keep
            # already proven uptime and resume accumulation from this boot.
            evidence.update(checkpoint_boot_id=sample.boot_id,
                            checkpoint_monotonic_ns=sample.monotonic_ns,
                            checkpoint_wall_utc=sample.wall_utc)
            return True, evidence["accrued_ns"] >= _WAIT_NS
        elapsed = sample.monotonic_ns - evidence["checkpoint_monotonic_ns"]
        if elapsed < 0:
            return False, False
        wall_elapsed = now_wall - checkpoint_wall
        if (evidence["ack_boot_id"] == evidence["checkpoint_boot_id"]
                and evidence["checkpoint_monotonic_ns"] < evidence["ack_monotonic_ns"]):
            return False, False
        if abs(wall_elapsed - elapsed) > _WALL_SKEW_NS:
            # A detected wall-clock discontinuity invalidates only this interval.
            evidence.update(checkpoint_monotonic_ns=sample.monotonic_ns,
                            checkpoint_wall_utc=sample.wall_utc)
            return True, False
        mature = evidence["accrued_ns"] + elapsed >= _WAIT_NS
        if elapsed >= _CHECKPOINT_INTERVAL_NS or mature:
            evidence["accrued_ns"] += elapsed
            evidence.update(checkpoint_monotonic_ns=sample.monotonic_ns,
                            checkpoint_wall_utc=sample.wall_utc)
            return True, evidence["accrued_ns"] >= _WAIT_NS
        return False, mature
    except Exception:
        return False, False


def project_visible_items(session, store: WorkRecordStore,
                          items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide eligible completed work in the current overview, never delete history.

    Ordinary/project-scoped records retain their legacy whole-item/project-group
    behavior. A trusted independent worker batch additionally projects each
    complete project requirement group independently; trace-only source requests,
    unmarked requirements, and other projects remain visible.
    """
    try:
        sample = work_record_visibility_clock.sample_elapsed_clock()
        if sample is None:
            return items
        current_by_item, fully_completed = _current_results(items)
        if not current_by_item:
            return items
        # Keep attribution centralized with overview's writer-key/event validation.
        from work_records_overview import _trusted_worker_batch_projects

        trusted_batches: dict[str, tuple[dict[str, str], tuple[str, str]]] = {}
        for item in items:
            if type(item) is not dict or type(item.get("item_id")) is not str:
                continue
            projects, original = _trusted_worker_batch_projects(item)
            if projects and original is not None:
                trusted_batches[item["item_id"]] = (projects, original)

        ledger = WorkCardLedger(session, store)
        presentations = WorkCardPresentations(session, store, ledger)
        outcome: dict[str, Any] = {}

        def advance(data):
            presentations._validate_binding()
            latest_source = store._read()
            if latest_source.get("items") != items:
                outcome["stale_source"] = True
                return None, False
            cards = presentations._cards_locked(data)
            # Keep the receipt-tracking schema intact while upgrading historical
            # presentation rows; visibility ACKs remain a separate field.
            needs_migration = data["schema_version"] != 4
            presentations._migrate(data)
            if data["schema_version"] != 4:
                data["schema_version"] = 4
            dirty = needs_migration
            required_views: set[tuple[str, str]] = set()
            viewed_views: set[tuple[str, str]] = set()
            evidenceful_views: set[tuple[str, str]] = set()
            mature_views: set[tuple[str, str]] = set()
            for presentation in data["items"]:
                card_id = presentation["card_id"]
                card = cards.get(card_id)
                if card is None:
                    continue
                snapshot = card["snapshot"]
                card_items = [snapshot["card"]] if snapshot.get("kind") == "independent" else snapshot.get("cards", [])
                represented: dict[tuple[str, str], dict[str, Any]] = {}
                for card_item in card_items:
                    item_id = card_item.get("item_id")
                    for req in card_item.get("requirements", []):
                        result = req.get("latest_result")
                        if (type(item_id) is str and type(result) is dict
                                and type(req.get("requirement_id")) is str
                                and type(result.get("result_id")) is str):
                            represented[(req["requirement_id"], result["result_id"])] = result
                current_pairs: set[tuple[str, str]] = set()
                for pair, frozen in represented.items():
                    matches = [runs[pair] for runs in current_by_item.values() if pair in runs]
                    if len(matches) == 1 and _snapshot_matches(matches[0], frozen):
                        current_pairs.add(pair)
                        required_views.add(pair)
                for view in presentation.get("result_views", []):
                    pair = (view.get("requirement_id"), view.get("result_id"))
                    if pair not in current_pairs:
                        continue
                    viewed_views.add(pair)
                    if type(view.get("elapsed_evidence")) is not dict:
                        continue
                    evidenceful_views.add(pair)
                    changed, mature = _advance_evidence(view["elapsed_evidence"], sample)
                    dirty = dirty or changed
                    if mature:
                        mature_views.add(pair)

            def result_is_mature(pair: tuple[str, str]) -> bool:
                # A single explicit ACK for this exact current frozen result is
                # sufficient. Reposts are separate presentation history, not
                # separate copies of the result the user must read and wait on.
                return (pair in required_views and pair in viewed_views
                        and pair in evidenceful_views and pair in mature_views)

            def requirement_is_mature(item_id: str, requirement_id: str) -> bool:
                pairs = [pair for pair in current_by_item.get(item_id, {}) if pair[0] == requirement_id]
                return len(pairs) == 1 and result_is_mature(pairs[0])

            project_requirements: dict[tuple[str, str], set[str]] = {}
            for item_id, (projects, _original) in trusted_batches.items():
                for requirement_id, project_id in projects.items():
                    project_requirements.setdefault((item_id, project_id), set()).add(requirement_id)
            hidden_batch_requirements: dict[str, set[str]] = {}
            for (item_id, _project_id), requirement_ids in project_requirements.items():
                if requirement_ids and all(requirement_is_mature(item_id, rid) for rid in requirement_ids):
                    hidden_batch_requirements.setdefault(item_id, set()).update(requirement_ids)

            # Preserve the pre-existing compatibility rule for ordinary items and
            # project-scoped groups. A single independent batch may still disappear
            # entirely once every one of its current results has qualified.
            grouped: dict[tuple[str, str], list[str]] = {}
            for item in items:
                scope = item.get("scope") if type(item) is dict else None
                if type(scope) is not dict:
                    return None, dirty
                kind = scope.get("kind")
                item_id = item.get("item_id")
                if type(item_id) is not str or not item_id:
                    return None, dirty
                if kind == "project" and type(scope.get("project_id")) is str and scope["project_id"]:
                    key = ("project", scope["project_id"])
                elif kind == "independent":
                    key = ("independent", item_id)
                else:
                    return None, dirty
                grouped.setdefault(key, []).append(item_id)
            hidden_items: set[str] = set()
            for (kind, _), item_ids in grouped.items():
                if kind == "project" and any(item_id not in fully_completed for item_id in item_ids):
                    continue
                group_ready = True
                for item_id in item_ids:
                    pairs = current_by_item.get(item_id, {})
                    if not pairs or item_id not in fully_completed:
                        group_ready = False
                        break
                    if any(not result_is_mature(pair) for pair in pairs):
                        group_ready = False
                        break
                if group_ready:
                    hidden_items.update(item_ids)
            outcome["hidden_items"] = hidden_items
            outcome["hidden_batch_requirements"] = hidden_batch_requirements
            return None, dirty

        presentations._presentations_store._mutate(advance)
        if outcome.get("stale_source"):
            return items
        hidden_items = outcome.get("hidden_items", set())
        hidden_requirements = outcome.get("hidden_batch_requirements", {})
        visible: list[dict[str, Any]] = []
        for item in items:
            item_id = item.get("item_id") if type(item) is dict else None
            if item_id in hidden_items:
                continue
            batch = trusted_batches.get(item_id) if type(item_id) is str else None
            hidden_ids = hidden_requirements.get(item_id, set())
            if batch and hidden_ids:
                projects, original = batch
                remaining = [req for req in item.get("requirements", [])
                             if type(req) is not dict or req.get("requirement_id") not in hidden_ids]
                # The original request is trace-only. It must not be rendered as
                # an independent pending requirement after its project tasks hide.
                if not any(req.get("requirement_id") != original[0] for req in remaining):
                    continue
                projected = dict(item)
                projected["requirements"] = remaining
                projected["_work_record_visibility_batch"] = (
                    _TRUSTED_BATCH_PROJECTION, projects, original,
                )
                visible.append(projected)
            else:
                visible.append(item)
        return visible
    except Exception:
        return items
