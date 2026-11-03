from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import work_record_visibility_clock as clock_module
from paths import AgentPaths
from agent import Agent
from session import Session, SessionMeta
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations, PRESENTATIONS_FILENAME
from work_record_cards import build_independent_work_record_card, build_project_work_record_cards
from work_records import WorkRecordStore
from work_records_overview_api import query_work_records_overview
from work_records_overview import render_project_work_records_overview


@dataclass(frozen=True)
class Sample:
    boot_id: str
    monotonic_ns: int
    wall_utc: str


def _session(tmp_path: Path, session_id="20261030-a1abcd01", project_id="project-api"):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, "synthetic", SessionMeta(project_id=project_id), [], paths)
    return session, WorkRecordStore(session)


def _completed_card(session, store, monkeypatch=None):
    row = store.create_item(title="completed result", scope={"kind": "project", "project_id": "project-api"})
    req = store.add_requirement(row["item_id"], "ship safely", event_id="add-requirement")
    store.begin_requirement_run(row["item_id"], req["requirement_id"], run_id="run-1", expected_current_run_id=None)
    store.record_result_if_current_run(
        row["item_id"], req["requirement_id"], run_id="run-1", result_id="result-1",
        status="completed", summary="verified only as recorded", verification={"status": "not_verified"},
    )
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "project-api"))
    assert saved.status == "saved"
    presentations = WorkCardPresentations(session, store, ledger)
    presentation_id = str(uuid.uuid4())
    assert presentations.create(presentation_id, saved.card_id).status == "created"
    if monkeypatch is not None:
        monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
            "boot-api", 1_000_000_000, "2026-10-30T00:00:00Z"))
        assert presentations.acknowledge_result(
            presentation_id, saved.card_id, "result-1", action="result_detail", trusted_action=True,
        ).status == "acknowledged"
    return row, req, presentations, saved.card_id, presentation_id


def _prestart_blocked_project_record(tmp_path, *, project_id="project-api", publish_card=True):
    from agent import _record_current_dialogue_worker_lifecycle
    from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
    from work_card_project_scope import _task_identity, build_project_scoped_work_record_cards, project_scoped_snapshot_request_id

    session, store = _session(tmp_path)
    item = store.create_item(title="prestart blocked synthetic task", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "synthetic host preflight task", event_id="prestart-blocked-requirement",
        source_ref={"kind": "project", "id": project_id},
    )
    reservation = ProjectWorkerReservation(
        reservation_id=uuid.uuid4().hex, worker_id=uuid.uuid4().hex,
        worker_run_id=uuid.uuid4().hex, parent_run_id=uuid.uuid4().hex, turn_epoch=3,
        conversation_id=session.conversation_id, project_id=project_id,
        task_id=_task_identity(session.conversation_id, item["item_id"], requirement["requirement_id"], project_id),
    )
    outcome = ProjectWorkerHostOutcome(
        worker_id=reservation.worker_id, run_id=reservation.parent_run_id, turn_epoch=reservation.turn_epoch,
        conversation_id=session.conversation_id, project_id=project_id, task_id=reservation.task_id,
        status="blocked", model_turn=None, parent_run_id=reservation.parent_run_id,
        worker_run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id,
        error_code="worker_dispatch_submit_failed",
    )
    assert _record_current_dialogue_worker_lifecycle(
        store, session_id=session.conversation_id, item_id=item["item_id"],
        requirement_id=requirement["requirement_id"], outcome=outcome, reservation=reservation,
        expected_requirement_text=requirement["text"],
    ) == "blocked_recorded"
    assert "current_run_id" not in store.get_item(item["item_id"])["requirements"][0]

    ledger = WorkCardLedger(session, store)
    presentation_id = None
    result_id = store.get_item(item["item_id"])["runs"][0]["result_id"]
    if publish_card:
        from work_card_presentations import WorkCardPresentations
        snapshot = build_project_scoped_work_record_cards(store, project_id)
        assert snapshot.completeness == "complete"
        saved = ledger.save_project_scoped_snapshot(project_scoped_snapshot_request_id(snapshot), snapshot)
        assert saved.status == "saved"
        presentations = WorkCardPresentations(session, store, ledger)
        presentation_id = str(uuid.uuid4())
        assert presentations.create(presentation_id, saved.card_id).status == "created"
    return session, store, item, requirement, ledger, presentation_id, result_id


def test_empty_session_returns_explicit_no_records_and_does_not_open_other_sessions(tmp_path):
    session, _store = _session(tmp_path)
    _foreign, foreign_store = _session(tmp_path, "20261030-foreign1", "foreign-project")
    foreign_store.create_item(title="secret foreign work", scope={"kind": "independent"})

    result = query_work_records_overview(session)

    assert result["state"] == "no_records"
    assert result["groups"] == []
    assert result["unread_important_results"] == []
    assert "secret foreign work" not in json.dumps(result)


def test_default_page_has_six_groups_and_snapshot_cursor_expires_after_source_change(tmp_path):
    session, store = _session(tmp_path)
    for index in range(8):
        row = store.create_item(title=f"independent-{index}", scope={"kind": "independent"})
        store.add_requirement(row["item_id"], f"requirement-{index}", event_id=f"requirement-{index}")

    first = query_work_records_overview(session)

    assert first["state"] == "visible"
    assert len(first["groups"]) == 6
    assert first["page"]["has_more"] is True
    assert first["page"]["total_groups"] == 8
    assert first["page"]["next_cursor"]
    assert sum(group["kind"] == "independent" for group in first["groups"]) == 6
    assert first["groups"][0]["items"][0]["requirements"][0]["status"] == "pending"

    second = query_work_records_overview(session, cursor=first["page"]["next_cursor"])
    assert second["state"] == "visible"
    assert second["page"]["offset"] == 6
    assert second["page"]["has_more"] is False
    assert len(second["groups"]) == 2
    assert second["snapshot_id"] == first["snapshot_id"]

    store.create_item(title="concurrent addition", scope={"kind": "independent"})
    later = query_work_records_overview(session, cursor=first["page"]["next_cursor"])

    assert later["state"] == "expired"
    assert later["groups"] == []
    assert later["page"]["next_cursor"] is None


def test_result_read_and_thirty_minute_projection_persist_only_safe_checkpoint(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    _row, _req, _presentations, _card_id, _presentation_id = _completed_card(session, store, monkeypatch)
    presentation_path = session.session_dir / PRESENTATIONS_FILENAME
    tracked_sidecars = [store._path, _presentations._ledger._ledger_store._path, presentation_path]
    before = [path.read_bytes() for path in tracked_sidecars]

    visible = query_work_records_overview(session)
    assert visible["state"] == "visible"
    result = visible["groups"][0]["items"][0]["current_results"][0]
    assert result == {"requirement_id": _req["requirement_id"], "result_id": "result-1", "status": "completed", "read_status": "viewed"}
    assert visible["unread_important_results"] == []
    assert visible["unread_status"] == "known"
    assert [path.read_bytes() for path in tracked_sidecars] == before  # no checkpoint or migration write

    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-api", 1_000_000_000 + 30 * 60 * 1_000_000_000, "2026-10-30T00:30:00Z"))
    collapsed = query_work_records_overview(session)

    assert collapsed["state"] == "collapsed"
    assert collapsed["groups"] == []
    assert collapsed["visibility_counts"]["collapsed_groups"] == 1
    assert collapsed["page"]["total_visible_groups"] == 0
    assert [path.read_bytes() for path in tracked_sidecars[:2]] == before[:2]
    assert presentation_path.read_bytes() != before[2]
    persisted = json.loads(presentation_path.read_text(encoding="utf-8"))
    evidence = persisted["items"][0]["result_views"][0]["elapsed_evidence"]
    assert evidence["accrued_ns"] == 30 * 60 * 1_000_000_000
    assert evidence["checkpoint_monotonic_ns"] == 1_000_000_000 + 30 * 60 * 1_000_000_000


def test_unacknowledged_current_result_returns_stable_identity_as_unread(tmp_path):
    session, store = _session(tmp_path)
    row = store.create_item(title="has an important result", scope={"kind": "project", "project_id": "project-api"})
    req = store.add_requirement(row["item_id"], "check outcome", event_id="add-unread")
    store.begin_requirement_run(row["item_id"], req["requirement_id"], run_id="run-unread", expected_current_run_id=None)
    store.record_result_if_current_run(
        row["item_id"], req["requirement_id"], run_id="run-unread", result_id="result-unread",
        status="completed", summary="completed", verification={"status": "not_verified"},
    )

    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "project-api"))
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(str(uuid.uuid4()), saved.card_id).status == "created"

    result = query_work_records_overview(session)

    assert result["state"] == "visible", result
    assert result["unread_status"] == "known"
    assert result["groups"][0]["items"][0]["current_results"] == [{
        "requirement_id": req["requirement_id"], "result_id": "result-unread", "status": "completed", "read_status": "unread",
    }]
    assert result["unread_important_results"] == [{
        "group": ["project", "project-api"], "item_id": row["item_id"],
        "requirement_id": req["requirement_id"], "result_id": "result-unread",
    }]


def test_unbound_session_input_is_an_error_not_a_filesystem_lookup(tmp_path):
    result = query_work_records_overview(object())

    assert result["state"] == "error"
    assert result["groups"] == []



def test_result_without_a_matching_immutable_presentation_is_explicitly_unknown(tmp_path):
    session, store = _session(tmp_path)
    row = store.create_item(title="result lacks presentation proof", scope={"kind": "project", "project_id": "project-api"})
    req = store.add_requirement(row["item_id"], "review result", event_id="add-unknown")
    store.begin_requirement_run(row["item_id"], req["requirement_id"], run_id="run-unknown", expected_current_run_id=None)
    store.record_result_if_current_run(
        row["item_id"], req["requirement_id"], run_id="run-unknown", result_id="result-unknown",
        status="completed", summary="completed", verification={"status": "not_verified"},
    )

    result = query_work_records_overview(session)

    assert result["state"] == "visible"
    assert result["unread_status"] == "unknown"
    assert result["unread_important_results"] == []
    assert result["groups"][0]["items"][0]["current_results"] == [{
        "requirement_id": req["requirement_id"], "result_id": "result-unknown", "status": "completed", "read_status": "unknown",
    }]


def test_corrupt_current_session_source_is_an_error(tmp_path):
    session, store = _session(tmp_path)
    store.create_item(title="will be corrupted", scope={"kind": "independent"})
    store._path.write_text("{not-json", encoding="utf-8")

    result = query_work_records_overview(session)

    assert result["state"] == "error"
    assert result["groups"] == []


def test_cursor_expires_when_only_the_thirty_minute_visibility_projection_changes(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    clock = [Sample("boot-api", 1_000_000_000, "2026-10-30T00:00:00Z")]
    _completed_card(session, store, monkeypatch)
    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: clock[0])
    for index in range(8):
        row = store.create_item(title=f"pending-{index}", scope={"kind": "independent"})
        store.add_requirement(row["item_id"], f"pending req {index}", event_id=f"pending-{index}")

    first = query_work_records_overview(session, page_size=8)
    assert first["page"]["has_more"] is True
    clock[0] = Sample("boot-api", 1_000_000_000 + 30 * 60 * 1_000_000_000, "2026-10-30T00:30:00Z")

    stale = query_work_records_overview(session, cursor=first["page"]["next_cursor"])

    assert stale["state"] == "expired"
    assert stale["reason"] == "source_snapshot_changed"
    assert stale["unread_status"] == "unknown"
    assert stale["groups"] == []


def test_malformed_cursor_is_rejected_without_a_session_search(tmp_path):
    session, _store = _session(tmp_path)

    result = query_work_records_overview(session, cursor="not-a-cursor")

    assert result["state"] == "error"
    assert result["reason"] == "invalid_cursor"
    assert result["groups"] == []


def test_prestart_blocked_ledger_binding_indexes_runs_and_card_history_once(monkeypatch):
    import hashlib
    import work_records_overview_api as overview_api
    from work_card_project_scope import _task_identity

    session_id = "20261030-a1abcd01"
    project_id = "project-perf"
    item_id = "item-multi-requirement"
    requirement_total = 128
    card_total = 129
    requirements = []
    raw_runs = []
    frozen_requirements = []
    for index in range(requirement_total):
        requirement_id = f"requirement-{index}"
        run_id = f"worker-run-{index}"
        result_id = "worker-result-" + hashlib.sha256(
            "\0".join((session_id, item_id, requirement_id, run_id)).encode("utf-8")
        ).hexdigest()
        receipt = {
            "kind": "project_worker_host_lifecycle", "result_status": "blocked",
            "session_id": session_id, "item_id": item_id, "requirement_id": requirement_id,
            "project_id": project_id,
            "task_id": _task_identity(session_id, item_id, requirement_id, project_id),
            "worker_id": f"worker-{index}", "worker_run_id": run_id,
            "reservation_id": f"reservation-{index}", "parent_run_id": uuid.uuid4().hex,
            "parent_epoch": 1, "host_status": "blocked", "host_error_code": "preflight_blocked",
            "worker_execution_started": False, "worker_result_claimed": False,
            "acceptance_status": "not_run",
        }
        summary = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        raw_runs.append({
            "requirement_id": requirement_id, "result_id": result_id, "run_id": run_id,
            "status": "blocked", "result_summary": summary,
            "verification": {"status": "not_verified", "evidence_refs": []}, "sources": [],
        })
        requirements.append({
            "requirement_id": requirement_id, "status": "blocked",
            "source_ref": {"kind": "project", "id": project_id},
        })
        frozen_requirements.append({
            "requirement_id": requirement_id, "project_id": project_id, "status": "blocked",
            "source_ref": {"kind": "project", "id": project_id}, "current_run_id": None,
            "latest_result_is_current": None, "current_result_missing": False,
            "latest_result": {
                "requirement_id": requirement_id, "result_id": result_id, "run_id": run_id,
                "status": "blocked", "summary": summary, "verification_status": "not_verified",
                "sources": [],
            },
        })

    history = [{"kind": "requirement_added", "payload": {"requirement_id": row["requirement_id"]}}
               for row in requirements]
    items = [{"item_id": item_id, "requirements": requirements, "runs": raw_runs, "history": history}]
    frozen_item = {"item_id": item_id, "requirements": frozen_requirements}
    class CountingCards(dict):
        yielded_cards = 0

        def values(self):
            for card in super().values():
                self.yielded_cards += 1
                yield card

    cards = CountingCards({
        f"card-{index}": {"snapshot": {
            "kind": "project_scoped", "session_id": session_id, "project_id": project_id,
            "completeness": "complete",
            "cards": ([frozen_item] if index == 0 else [{
                "item_id": f"unrelated-item-{index}",
                "requirements": [{
                    "requirement_id": f"unrelated-requirement-{index}",
                    "project_id": project_id, "status": "pending",
                    "source_ref": {"kind": "project", "id": project_id},
                }],
            }]),
        }}
        for index in range(card_total)
    })
    run_index_calls = []
    original_index = overview_api._index_runs_by_requirement
    def count_run_index(runs):
        run_index_calls.append(len(runs))
        return original_index(runs)
    monkeypatch.setattr(overview_api, "_index_runs_by_requirement", count_run_index)
    history_index_calls = []
    original_history_index = overview_api._index_requirement_run_starts
    def count_history_index(events):
        history_index_calls.append(len(events))
        return original_history_index(events)
    monkeypatch.setattr(overview_api, "_index_requirement_run_starts", count_history_index)

    assert overview_api._all_current_results(items) == {}
    assert run_index_calls == [requirement_total]
    run_index_calls.clear()
    bound, uncertain = overview_api._bound_prestart_blocked_results(items, cards, session_id)

    assert not uncertain
    assert sum(len(rows) for rows in bound.values()) == requirement_total
    assert cards.yielded_cards == card_total
    assert run_index_calls == [requirement_total]
    assert history_index_calls == [requirement_total]


def test_prestart_blocked_result_is_reported_unread_until_its_bound_card_result_is_viewed(tmp_path, monkeypatch):
    session, store, item, requirement, ledger, presentation_id, result_id = _prestart_blocked_project_record(tmp_path)
    assert presentation_id is not None

    unread = query_work_records_overview(session)

    assert unread["unread_status"] == "known"
    assert unread["unread_important_results"] == [{
        "group": ["independent", item["item_id"]], "item_id": item["item_id"],
        "requirement_id": requirement["requirement_id"], "result_id": result_id,
    }]
    item_row = unread["groups"][0]["items"][0]
    assert item_row["status"] == "blocked"
    assert item_row["current_results"] == [{
        "requirement_id": requirement["requirement_id"], "result_id": result_id,
        "status": "blocked", "read_status": "unread",
    }]

    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-api", 1_000_000_000, "2026-10-30T00:00:00Z"))
    presentations = WorkCardPresentations(session, store, ledger)
    card = next(row for row in ledger.list_cards() if row["snapshot"]["kind"] == "project_scoped")
    assert presentations.acknowledge_result(
        presentation_id, card["card_id"], result_id, action="result_detail", trusted_action=True,
    ).status == "acknowledged"

    viewed = query_work_records_overview(session)
    assert viewed["unread_status"] == "known"
    assert viewed["unread_important_results"] == []
    viewed_item = viewed["groups"][0]["items"][0]
    assert viewed_item["status"] == "blocked"
    assert viewed_item["current_results"][0]["read_status"] == "viewed"


def test_prestart_blocked_two_project_host_card_view_and_overview_chain_is_isolated(tmp_path, monkeypatch):
    from agent import _record_current_dialogue_worker_lifecycle
    from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
    from work_card_project_scope import _task_identity, build_project_scoped_work_record_cards, project_scoped_snapshot_request_id

    session, store = _session(tmp_path)
    item = store.create_item(title="two-project preflight batch", scope={"kind": "independent"})
    requirements = {}
    for project_id in ("project-alpha", "project-beta"):
        requirements[project_id] = store.add_requirement(
            item["item_id"], f"{project_id} preflight task", event_id=f"{project_id}-blocked",
            source_ref={"kind": "project", "id": project_id},
        )

    result_ids = {}
    reservations = {}
    for project_id, requirement in requirements.items():
        reservation = ProjectWorkerReservation(
            reservation_id=uuid.uuid4().hex, worker_id=uuid.uuid4().hex,
            worker_run_id=uuid.uuid4().hex, parent_run_id=uuid.uuid4().hex, turn_epoch=11,
            conversation_id=session.conversation_id, project_id=project_id,
            task_id=_task_identity(session.conversation_id, item["item_id"], requirement["requirement_id"], project_id),
        )
        outcome = ProjectWorkerHostOutcome(
            worker_id=reservation.worker_id, run_id=reservation.parent_run_id, turn_epoch=reservation.turn_epoch,
            conversation_id=session.conversation_id, project_id=project_id, task_id=reservation.task_id,
            status="blocked", model_turn=None, parent_run_id=reservation.parent_run_id,
            worker_run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id,
            error_code="worker_dispatch_submit_failed",
        )
        assert _record_current_dialogue_worker_lifecycle(
            store, session_id=session.conversation_id, item_id=item["item_id"],
            requirement_id=requirement["requirement_id"], outcome=outcome, reservation=reservation,
            expected_requirement_text=requirement["text"],
        ) == "blocked_recorded"
        reservations[project_id] = reservation
        saved_item = store.get_item(item["item_id"])
        raw_requirement = next(row for row in saved_item["requirements"]
                               if row["requirement_id"] == requirement["requirement_id"])
        assert "current_run_id" not in raw_requirement
        raw_result = next(row for row in saved_item["runs"]
                          if row["requirement_id"] == requirement["requirement_id"])
        assert raw_result["status"] == "blocked"
        assert raw_result["verification"]["status"] == "not_verified"
        result_ids[project_id] = raw_result["result_id"]

    ledger = WorkCardLedger(session, store)
    presentations = WorkCardPresentations(session, store, ledger)
    presentation_ids = {}
    card_ids = {}
    for project_id, requirement in requirements.items():
        snapshot = build_project_scoped_work_record_cards(store, project_id)
        assert snapshot.completeness == "complete"
        card_requirements = [req for card in snapshot.cards for req in card.requirements]
        assert [req.requirement_id for req in card_requirements] == [requirement["requirement_id"]]
        assert card_requirements[0].status == "blocked"
        assert card_requirements[0].current_run_id is None
        assert card_requirements[0].latest_result_is_current is None
        assert card_requirements[0].latest_result.result_id == result_ids[project_id]
        saved = ledger.save_project_scoped_snapshot(project_scoped_snapshot_request_id(snapshot), snapshot)
        assert saved.status == "saved"
        card_ids[project_id] = saved.card_id
        presentation_id = str(uuid.uuid4())
        assert presentations.create(presentation_id, saved.card_id).status == "created"
        presentation_ids[project_id] = presentation_id

    before_view = query_work_records_overview(session)
    assert before_view["unread_status"] == "known"
    item_row = before_view["groups"][0]["items"][0]
    assert item_row["status"] == "blocked"
    assert {row["requirement_id"] for row in item_row["requirements"]} == {
        requirement["requirement_id"] for requirement in requirements.values()
    }
    assert {row["result_id"] for row in item_row["current_results"]} == set(result_ids.values())
    assert {row["read_status"] for row in item_row["current_results"]} == {"unread"}
    assert {row["result_id"] for row in before_view["unread_important_results"]} == set(result_ids.values())

    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-api", 1_000_000_000, "2026-10-30T00:00:00Z"))
    assert presentations.acknowledge_result(
        presentation_ids["project-alpha"], card_ids["project-alpha"], result_ids["project-alpha"],
        action="result_detail", trusted_action=True,
    ).status == "acknowledged"

    after_alpha_view = query_work_records_overview(session)
    alpha_beta_reads = {row["result_id"]: row["read_status"]
                        for row in after_alpha_view["groups"][0]["items"][0]["current_results"]}
    assert alpha_beta_reads == {
        result_ids["project-alpha"]: "viewed", result_ids["project-beta"]: "unread",
    }
    assert [row["result_id"] for row in after_alpha_view["unread_important_results"]] == [
        result_ids["project-beta"],
    ]

    assert presentations.acknowledge_result(
        presentation_ids["project-beta"], card_ids["project-beta"], result_ids["project-beta"],
        action="result_detail", trusted_action=True,
    ).status == "acknowledged"
    after_both_views = query_work_records_overview(session)
    assert after_both_views["unread_status"] == "known"
    assert after_both_views["unread_important_results"] == []
    assert {row["read_status"] for row in after_both_views["groups"][0]["items"][0]["current_results"]} == {"viewed"}
    assert all("current_run_id" not in row for row in store.get_item(item["item_id"])["requirements"])


def test_untrusted_or_unbound_prestart_blocked_result_makes_unread_certainty_unknown(tmp_path):
    for mode in ("cross_project_receipt", "cross_project_source", "no_ledger_card"):
        case = tmp_path / mode
        published = mode != "no_ledger_card"
        session, store, item, requirement, _ledger, _presentation_id, result_id = _prestart_blocked_project_record(
            case, publish_card=published,
        )
        if mode == "cross_project_receipt":
            def forge_receipt(data):
                target = next(row for row in data["items"] if row["item_id"] == item["item_id"])
                stored = next(row for row in target["runs"] if row["result_id"] == result_id)
                receipt = json.loads(stored["result_summary"])
                receipt["project_id"] = "synthetic-other-project"
                stored["result_summary"] = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
                return None, True
            store._mutate(forge_receipt)
        elif mode == "cross_project_source":
            def forge_source(data):
                target = next(row for row in data["items"] if row["item_id"] == item["item_id"])
                source_req = next(row for row in target["requirements"]
                                  if row["requirement_id"] == requirement["requirement_id"])
                source_req["source_ref"] = {"kind": "project", "id": "synthetic-other-project"}
                return None, True
            store._mutate(forge_source)

        result = query_work_records_overview(session)
        assert result["unread_status"] == "unknown", mode
        assert result["unread_important_results"] == [], mode
        assert result["groups"][0]["items"][0]["status"] == "blocked", mode
        assert result["groups"][0]["items"][0]["current_results"] == [], mode
        assert result_id not in json.dumps(result), mode


@pytest.mark.parametrize("result_status", ["unknown", "blocked"])
def test_current_noncompleted_result_is_not_silently_omitted_from_unread_certainty(tmp_path, result_status):
    session, store = _session(tmp_path)
    row = store.create_item(title=f"{result_status} current result", scope={"kind": "project", "project_id": "project-api"})
    req = store.add_requirement(row["item_id"], "inspect noncompleted result", event_id=f"add-{result_status}")
    run_id = f"run-{result_status}"
    result_id = f"result-{result_status}"
    store.begin_requirement_run(row["item_id"], req["requirement_id"], run_id=run_id, expected_current_run_id=None)
    store.record_result_if_current_run(
        row["item_id"], req["requirement_id"], run_id=run_id, result_id=result_id,
        status=result_status, summary=f"{result_status} result summary", verification={"status": "not_verified"},
    )

    result = query_work_records_overview(session)

    assert result["unread_status"] == "unknown"
    assert result["unread_important_results"] == []
    assert result["groups"][0]["items"][0]["current_results"] == [{
        "requirement_id": req["requirement_id"], "result_id": result_id, "status": result_status, "read_status": "unknown",
    }]


def test_mixed_multi_project_results_without_ack_proof_remain_visible_without_obsolete_limit(tmp_path):
    session, store = _session(tmp_path, project_id="project-alpha")
    digest = "b" * 64
    original_result = store.create_item_with_requirement(
        title="多项目任务批次", scope={"kind": "independent"}, requirement="trace-only original request",
        event_id=f"worker-batch-original-{digest}", idempotency_key=f"worker-batch-original:{digest}",
    )
    row = original_result[0] if isinstance(original_result, tuple) else original_result
    by_project = {}
    for index, (project_id, result_status) in enumerate((
        ("project-alpha", "completed"), ("project-beta", "blocked"),
    )):
        requirement = store.add_requirement(
            row["item_id"], f"{project_id} task", event_id=f"worker-batch-task-{digest}-{index}",
            source_ref={"kind": "project", "id": project_id},
            idempotency_key=f"worker-batch-task:{digest}:{index}",
        )
        run_id = f"run-{project_id}"
        result_id = f"result-{project_id}"
        store.begin_requirement_run(row["item_id"], requirement["requirement_id"],
                                   run_id=run_id, expected_current_run_id=None)
        store.record_result_if_current_run(
            row["item_id"], requirement["requirement_id"], run_id=run_id, result_id=result_id,
            status=result_status, summary=f"{project_id} {result_status}",
            verification={"status": "not_verified"},
        )
        by_project[project_id] = (requirement["requirement_id"], result_id)

    result = query_work_records_overview(session)

    project_alpha = render_project_work_records_overview(session, "project-alpha")
    assert "project-alpha task" in project_alpha
    assert "project-beta task" not in project_alpha
    assert "trace-only original request" not in project_alpha
    assert project_alpha.count("事项：") == 1

    state_path = session.paths.data / "state.json"
    state_path.write_text(json.dumps({"project_sessions": {"project-alpha": session.conversation_id}}), encoding="utf-8")
    agent = object.__new__(Agent)
    agent.session = session
    agent.executor = SimpleNamespace(confirm_fn=None)
    agent.enable_work_record_recall = True
    agent.work_record_recall_controller = SimpleNamespace(_store=store)
    agent.enable_work_record_recording = False
    agent.work_record_recording_controller = None
    agent._run_turn_without_work_record_recording = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("current project overview must not fall through to the model"))
    routed = agent.run_turn("当前项目情况如何？")
    assert routed.finish_reason == "current_project_work_record_overview"
    assert "project-alpha task" in routed.assistant_text
    assert "project-beta task" not in routed.assistant_text
    assert routed.assistant_text.count("事项：") == 1

    groups = {group["id"]: group for group in result["groups"]}
    assert set(groups) == {"project-alpha", "project-beta"}
    for project_id, (requirement_id, result_id) in by_project.items():
        assert groups[project_id]["visibility"] == "visible"
        assert groups[project_id]["items"][0]["current_results"] == [{
            "requirement_id": requirement_id, "result_id": result_id,
            "status": "completed" if project_id == "project-alpha" else "blocked",
            "read_status": "unknown",
        }]
    assert result["unread_status"] == "unknown"
    assert result["visibility_limitations"] == []


def test_default_page_only_paginates_six_visible_groups_and_counts_collapsed_separately(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    clock = [Sample("boot-api", 1_000_000_000, "2026-10-30T00:00:00Z")]
    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: clock[0])
    ledger = WorkCardLedger(session, store)
    presentations = WorkCardPresentations(session, store, ledger)
    specs = [
        ("a-collapsed-1", True), ("b-active-1", False), ("c-collapsed-2", True),
        ("d-active-2", False), ("e-collapsed-3", True), ("f-active-3", False),
        ("g-active-4", False), ("h-active-5", False), ("i-active-6", False),
    ]
    for index, (title, acknowledge) in enumerate(specs):
        row = store.create_item(title=title, scope={"kind": "independent"})
        req = store.add_requirement(row["item_id"], f"requirement-{index}", event_id=f"add-{index}")
        run_id, result_id = f"run-{index}", f"result-{index}"
        store.begin_requirement_run(row["item_id"], req["requirement_id"], run_id=run_id,
                                   expected_current_run_id=None)
        store.record_result_if_current_run(
            row["item_id"], req["requirement_id"], run_id=run_id, result_id=result_id,
            status="completed", summary=title, verification={"status": "not_verified"},
        )
        saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, row["item_id"]))
        assert saved.status == "saved"
        presentation_id = str(uuid.uuid4())
        assert presentations.create(presentation_id, saved.card_id).status == "created"
        if acknowledge:
            assert presentations.acknowledge_result(
                presentation_id, saved.card_id, result_id, action="result_detail", trusted_action=True,
            ).status == "acknowledged"

    clock[0] = Sample("boot-api", 1_000_000_000 + 30 * 60 * 1_000_000_000, "2026-10-30T00:30:00Z")
    result = query_work_records_overview(session)

    assert result["state"] == "visible"
    assert len(result["groups"]) == 6
    assert all(group["visibility"] == "visible" for group in result["groups"])
    assert {group["items"][0]["title"] for group in result["groups"]} == {
        "b-active-1", "d-active-2", "f-active-3", "g-active-4", "h-active-5", "i-active-6",
    }
    assert result["visibility_counts"] == {
        "visible_groups": 6, "visible_items": 6, "collapsed_groups": 3, "collapsed_items": 3,
    }
    assert result["page"]["total_visible_groups"] == 6
    assert result["page"]["total_visible_items"] == 6
    assert result["page"]["has_more"] is False



def test_project_scoped_projection_collapses_mature_project_a_but_keeps_unread_project_b(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    clock = [Sample("boot-api", 1_000_000_000, "2026-10-30T00:00:00Z")]
    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: clock[0])
    digest = "c" * 64
    original = store.create_item_with_requirement(
        title="多项目任务批次", scope={"kind": "independent"}, requirement="trace-only request",
        event_id=f"worker-batch-original-{digest}", idempotency_key=f"worker-batch-original:{digest}",
    )
    row = original[0] if isinstance(original, tuple) else original
    requirement_by_project = {}
    for index, (project_id, status) in enumerate((("project-alpha", "completed"), ("project-beta", "blocked"))):
        requirement = store.add_requirement(
            row["item_id"], f"{project_id} task", event_id=f"worker-batch-task-{digest}-{index}",
            source_ref={"kind": "project", "id": project_id},
            idempotency_key=f"worker-batch-task:{digest}:{index}",
        )
        run_id, result_id = f"run-{project_id}", f"result-{project_id}"
        store.begin_requirement_run(row["item_id"], requirement["requirement_id"],
                                   run_id=run_id, expected_current_run_id=None)
        store.record_result_if_current_run(
            row["item_id"], requirement["requirement_id"], run_id=run_id, result_id=result_id,
            status=status, summary=f"{project_id} {status}", verification={"status": "not_verified"},
        )
        requirement_by_project[project_id] = (requirement["requirement_id"], result_id)

    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, row["item_id"]))
    assert saved.status == "saved"
    presentation_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == "created"
    assert presentations.acknowledge_result(
        presentation_id, saved.card_id, requirement_by_project["project-alpha"][1],
        action="result_detail", trusted_action=True,
    ).status == "acknowledged"
    clock[0] = Sample("boot-api", 1_000_000_000 + 30 * 60 * 1_000_000_000, "2026-10-30T00:30:00Z")

    result = query_work_records_overview(session)

    assert [group["id"] for group in result["groups"]] == ["project-beta"]
    assert result["visibility_counts"] == {
        "visible_groups": 1, "visible_items": 1, "collapsed_groups": 1, "collapsed_items": 1,
    }
    beta_result = result["groups"][0]["items"][0]["current_results"]
    assert beta_result == [{
        "requirement_id": requirement_by_project["project-beta"][0],
        "result_id": requirement_by_project["project-beta"][1],
        "status": "blocked", "read_status": "unread",
    }]
    assert result["unread_status"] == "known"
    assert result["unread_important_results"] == [{
        "group": ["project", "project-beta"], "item_id": row["item_id"],
        "requirement_id": requirement_by_project["project-beta"][0],
        "result_id": requirement_by_project["project-beta"][1],
    }]
    assert result["visibility_limitations"] == []

def test_server_suggests_monotonic_remaining_time_only_for_complete_single_group(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    _row, _req, _presentations, _card_id, _presentation_id = _completed_card(session, store, monkeypatch)
    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-api", 1_000_000_000 + 10 * 60 * 1_000_000_000, "2026-10-30T00:10:00Z"))

    result = query_work_records_overview(session)

    assert result["visibility_recheck_after_ms"] == 20 * 60 * 1000
    assert result["groups"][0]["visibility"] == "visible"

    # A different boot cannot prove the unobserved interval, so use a bounded
    # low-frequency retry rather than estimating from wall time.
    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "new-boot", 1_000_000_000, "2026-10-30T12:00:00Z"))
    restarted = query_work_records_overview(session)
    assert restarted["visibility_recheck_after_ms"] == 10 * 60 * 1000
    assert restarted["groups"][0]["visibility"] == "visible"

    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-api", 1_000_000_000 + 10 * 60 * 1_000_000_000, "2026-10-30T04:00:00Z"))
    clock_anomaly = query_work_records_overview(session)
    assert clock_anomaly["visibility_recheck_after_ms"] == 10 * 60 * 1000
    assert clock_anomaly["groups"][0]["visibility"] == "visible"


def test_multiple_groups_and_pages_use_low_frequency_visibility_recheck(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    for index in range(7):
        row = store.create_item(title=f"pending-{index}", scope={"kind": "independent"})
        store.add_requirement(row["item_id"], f"pending-{index}", event_id=f"pending-{index}")
    result = query_work_records_overview(session)
    assert result["page"]["has_more"] is True
    assert result["visibility_recheck_after_ms"] == 10 * 60 * 1000
    multiple_groups = query_work_records_overview(session, page_size=50)
    assert multiple_groups["visibility_counts"]["visible_groups"] == 7
    assert multiple_groups["page"]["has_more"] is False
    assert multiple_groups["visibility_recheck_after_ms"] == 10 * 60 * 1000

def test_overview_persists_reboot_checkpoint_and_matures_after_a_full_new_boot_interval(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    _row, _req, _presentations, _card_id, _presentation_id = _completed_card(session, store, monkeypatch)
    presentation_path = session.session_dir / PRESENTATIONS_FILENAME

    # A real presentation sidecar is read through the desktop overview path.
    # The old boot's unknown gap is discarded; only this new boot starts accrual.
    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-after-restart", 8_000_000_000, "2026-10-31T12:00:00Z"))
    after_restart = query_work_records_overview(session)
    assert after_restart["state"] == "visible"
    saved = json.loads(presentation_path.read_text(encoding="utf-8"))
    evidence = saved["items"][0]["result_views"][0]["elapsed_evidence"]
    assert evidence["checkpoint_boot_id"] == "boot-after-restart"
    assert evidence["checkpoint_monotonic_ns"] == 8_000_000_000
    assert evidence["accrued_ns"] == 0

    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-after-restart", 8_000_000_000 + 30 * 60 * 1_000_000_000, "2026-10-31T12:30:00Z"))
    after_full_interval = query_work_records_overview(session)
    assert after_full_interval["state"] == "collapsed"
    saved = json.loads(presentation_path.read_text(encoding="utf-8"))
    evidence = saved["items"][0]["result_views"][0]["elapsed_evidence"]
    assert evidence["accrued_ns"] == 30 * 60 * 1_000_000_000


def test_identical_result_repost_does_not_require_another_ack_or_timer(tmp_path, monkeypatch):
    clock = [Sample("boot-api", 1_000_000_000, "2026-10-30T00:00:00Z")]
    session, store = _session(tmp_path)
    _row, _req, presentations, first_card_id, _first_id = _completed_card(session, store, monkeypatch)

    repost = WorkCardLedger(session, store).save_snapshot(
        str(uuid.uuid4()), build_project_work_record_cards(store, "project-api"))
    assert repost.status == "saved"
    assert repost.card_id != first_card_id
    assert presentations.create(str(uuid.uuid4()), repost.card_id).status == "created"

    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-api", 1_000_000_000 + 29 * 60 * 1_000_000_000, "2026-10-30T00:29:00Z"))
    still_waiting = query_work_records_overview(session)
    assert still_waiting["state"] == "visible"
    assert still_waiting["groups"][0]["items"][0]["current_results"][0]["read_status"] == "viewed"

    monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: Sample(
        "boot-api", 1_000_000_000 + 30 * 60 * 1_000_000_000, "2026-10-30T00:30:00Z"))
    assert query_work_records_overview(session)["state"] == "collapsed"


def test_new_current_result_stays_unread_despite_old_result_ack(tmp_path, monkeypatch):
    session, store = _session(tmp_path)
    _row, req, presentations, old_card_id, _old_presentation_id = _completed_card(session, store, monkeypatch)

    store.begin_requirement_run(_row["item_id"], req["requirement_id"], run_id="run-new", expected_current_run_id="run-1")
    store.record_result_if_current_run(
        _row["item_id"], req["requirement_id"], run_id="run-new", result_id="result-new",
        status="completed", summary="new verified snapshot", verification={"status": "not_verified"})
    ledger = WorkCardLedger(session, store)
    new_card = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, "project-api"))
    assert new_card.status == "saved" and new_card.card_id != old_card_id
    new_presentation_id = str(uuid.uuid4())
    assert presentations.create(new_presentation_id, new_card.card_id).status == "created"

    overview = query_work_records_overview(session)
    result = overview["groups"][0]["items"][0]["current_results"][0]
    assert result == {"requirement_id": req["requirement_id"], "result_id": "result-new",
                      "status": "completed", "read_status": "unread"}
    assert overview["unread_important_results"] == [{
        "group": ["project", "project-api"], "item_id": _row["item_id"],
        "requirement_id": req["requirement_id"], "result_id": "result-new",
    }]
