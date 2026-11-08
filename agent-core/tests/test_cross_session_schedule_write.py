from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from threading import Event

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))

from multi_project_work_record_authorization import authorize_project_record_sources
from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerHostRequest, ProjectWorkerReservation
from cross_session_schedule_write import (
    authorize_cross_session_schedule_write, claim_and_start_cross_session_schedule,
    recover_cross_session_schedule_unknown, settle_cross_session_schedule,
)
from session import Session, SessionMeta
from work_records import WorkRecordStore
from paths import AgentPaths


def _fixture(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace", data=tmp_path / "data", evolve=tmp_path / "evolve")
    paths.workspace.mkdir(parents=True); paths.evolve.mkdir()
    (paths.data / "sessions").mkdir(parents=True)
    parent_id, source_id = "20261107-aaaaaaaa", "20261107-bbbbbbbb"
    parent_dir = paths.data / "sessions" / parent_id; parent_dir.mkdir()
    source_dir = paths.data / "sessions" / source_id; source_dir.mkdir()
    (source_dir / "meta.json").write_text(json.dumps({"project_id": "project-alpha"}), encoding="utf-8")
    (paths.data / "state.json").write_text(json.dumps({"project_sessions": {"project-alpha": source_id}}), encoding="utf-8")
    parent = Session(parent_id, parent_dir, "", SessionMeta(), [], paths)
    source_session = Session(source_id, source_dir, "", SessionMeta(project_id="project-alpha"), [], paths)
    source_store = WorkRecordStore(source_session)
    item = source_store.create_item(title="queued", scope={"kind": "project", "project_id": "project-alpha"})
    req = source_store.add_requirement(item["item_id"], "write the agreed file", event_id="queued-req")
    source = authorize_project_record_sources(parent, ["project-alpha"], confirm_fn=lambda *_: "y", cancel_event=Event())["project-alpha"]
    request = ProjectWorkerHostRequest("project-alpha", "task-exact", req["text"], ("README.md",), ("RESULT.md",))
    reservation = ProjectWorkerReservation("res-exact", "worker-exact", "worker-run-exact", "parent-run-exact", 5, parent_id, "project-alpha", "task-exact")
    live = lambda session, run, epoch: session is parent and run == reservation.parent_run_id and epoch == reservation.turn_epoch
    return parent, source, source_store, item, req, request, reservation, live


def _authorize(args, choice="y", **kwargs):
    parent, source, _store, item, req, request, reservation, live = args
    return authorize_cross_session_schedule_write(
        source, item_id=item["item_id"], requirement_id=req["requirement_id"], task=req["text"],
        request=request, reservation=reservation, confirm_fn=lambda preview: kwargs.get("preview", preview) and choice,
        cancel_event=kwargs.get("cancel_event", Event()), live_binding_check=kwargs.get("live", live),
        parent_session=parent, expires_in_seconds=kwargs.get("ttl", 300),
    )


def test_cross_session_authorization_is_separate_explicit_and_exact(tmp_path):
    args = _fixture(tmp_path); previews = []
    parent, source, _store, item, req, request, reservation, live = args
    capability = authorize_cross_session_schedule_write(
        source, item_id=item["item_id"], requirement_id=req["requirement_id"], task=req["text"],
        request=request, reservation=reservation, confirm_fn=lambda p: previews.append(p) or "y",
        cancel_event=Event(), live_binding_check=live, parent_session=parent,
    )
    assert "CROSS_SESSION_SCHEDULE_SOURCE_WRITE_CONFIRM" in previews[0]
    for exact in (source.store.session_id, parent.conversation_id, "project-alpha", item["item_id"], req["requirement_id"], reservation.parent_run_id, reservation.reservation_id, "RESULT.md"):
        assert exact in previews[0]
    assert capability.source_session_id == source.store.session_id


def test_rejection_expiry_source_change_and_dispatch_mismatch_fail_closed(tmp_path):
    args = _fixture(tmp_path)
    with pytest.raises(PermissionError): _authorize(args, "n")
    capability = _authorize(args, ttl=1)
    time.sleep(1.05)
    with pytest.raises(PermissionError):
        claim_and_start_cross_session_schedule(capability, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1])
    changed = _fixture(tmp_path / "other")
    cap = _authorize(changed)
    changed[2].add_requirement(changed[3]["item_id"], "new row", event_id="source-change")
    with pytest.raises(PermissionError):
        claim_and_start_cross_session_schedule(cap, parent_session=changed[0], cancel_event=Event(), live_binding_check=changed[-1])
    cap = _authorize(args)
    with pytest.raises(PermissionError):
        claim_and_start_cross_session_schedule(cap, parent_session=args[0], cancel_event=Event(), live_binding_check=lambda *_: False)


def test_claim_is_one_shot_and_source_cas_blocks_competitor(tmp_path):
    args = _fixture(tmp_path); cap = _authorize(args)
    started = claim_and_start_cross_session_schedule(cap, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1])
    assert started["started"]["current_run_id"] == args[6].worker_run_id
    with pytest.raises(PermissionError):
        claim_and_start_cross_session_schedule(cap, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1])
    with pytest.raises(Exception):
        args[2].claim_scheduled_requirement("project-alpha", args[3]["item_id"], args[4]["requirement_id"],
            expected_revision=args[2]._read()["revision"], owner_id="other", reservation_id="other-res", parent_run_id="other-parent")


def test_failed_worker_start_marks_persisted_claim_unknown(tmp_path, monkeypatch):
    args = _fixture(tmp_path); cap = _authorize(args)

    def fail_start(*_args, **_kwargs):
        raise OSError("simulated crash boundary after claim commit")

    monkeypatch.setattr(WorkRecordStore, "begin_claimed_schedule_run", fail_start)
    with pytest.raises(OSError, match="simulated crash boundary"):
        claim_and_start_cross_session_schedule(
            cap, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1],
        )

    item = args[2].get_item(args[3]["item_id"])
    requirement = next(row for row in item["requirements"] if row["requirement_id"] == args[4]["requirement_id"])
    assert requirement["schedule_claim"]["state"] == "unknown"
    assert requirement.get("current_run_id") is None


def test_two_confirmed_reservations_race_on_one_source_revision(tmp_path):
    args = _fixture(tmp_path)
    first = _authorize(args)
    # A second explicit authorization observes the same still-queued revision.
    second = _authorize(args)
    started = claim_and_start_cross_session_schedule(first, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1])
    assert started["started"]["current_run_id"] == args[6].worker_run_id
    with pytest.raises(PermissionError):
        claim_and_start_cross_session_schedule(second, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1])
    req = next(row for row in args[2].get_item(args[3]["item_id"])["requirements"] if row["requirement_id"] == args[4]["requirement_id"])
    assert req["current_run_id"] == args[6].worker_run_id


def test_cross_session_settlement_checks_identities_and_keeps_unknown(tmp_path):
    args = _fixture(tmp_path); cap = _authorize(args)
    started = claim_and_start_cross_session_schedule(cap, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1])
    r = args[6]
    outcome = ProjectWorkerHostOutcome(r.worker_id, r.parent_run_id, r.turn_epoch, r.conversation_id,
        r.project_id, r.task_id, "model_turn_finished", parent_run_id=r.parent_run_id,
        worker_run_id=r.worker_run_id, reservation_id=r.reservation_id)
    bad = ProjectWorkerHostOutcome(r.worker_id, r.parent_run_id, r.turn_epoch, "wrong-parent", r.project_id,
        r.task_id, "model_turn_finished", parent_run_id=r.parent_run_id,
        worker_run_id=r.worker_run_id, reservation_id=r.reservation_id)
    rejected = settle_cross_session_schedule(started["store"], capability=cap,
        source_authorization=args[1], outcome=bad, sequence=r.turn_epoch)
    assert rejected.status == "rejected"
    result = settle_cross_session_schedule(started["store"], capability=cap,
        source_authorization=args[1], outcome=outcome, sequence=r.turn_epoch)
    assert result.status == "recorded" and result.record_status == "unknown"
    item = started["store"].get_item(args[3]["item_id"])
    req = next(row for row in item["requirements"] if row["requirement_id"] == args[4]["requirement_id"])
    assert req["status"] == "unknown" and "schedule_claim" not in req
    run = next(row for row in item["runs"] if row["run_id"] == r.worker_run_id)
    assert run["verification"]["status"] == "not_verified"


def test_unknown_recovery_marks_exact_claim_and_does_not_release_it(tmp_path):
    args = _fixture(tmp_path); cap = _authorize(args)
    started = claim_and_start_cross_session_schedule(cap, parent_session=args[0], cancel_event=Event(), live_binding_check=args[-1])
    assert recover_cross_session_schedule_unknown(cap, source_authorization=args[1]) is True
    item = started["store"].get_item(args[3]["item_id"])
    req = next(row for row in item["requirements"] if row["requirement_id"] == args[4]["requirement_id"])
    assert req["schedule_claim"]["state"] == "unknown"
    assert req["current_run_id"] == args[6].worker_run_id
    assert recover_cross_session_schedule_unknown(cap, source_authorization=args[1]) is True
