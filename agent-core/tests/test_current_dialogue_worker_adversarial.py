"""Adversarial coverage for the current-dialogue multi-project worker path."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import pytest
import threading
import uuid
import sys
from pathlib import Path

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from project_mode import create_project
from project_switch import record_project_session
from server import WsBridge, _build_repl
from session import create_new
from work_records import WorkRecordStore
from tests.isolation_helpers import temporary_agent_paths


def test_prestart_blocked_workers_keep_tasks_without_fabricating_runs(monkeypatch):
    """A real host scope rejection must not settle success or cross-bind projects."""
    with temporary_agent_paths() as paths:
        source_sessions = {}
        for project_id in ("project-alpha", "project-beta"):
            create_project(paths, project_id, project_template="light")
            source = create_new(paths, conversation_id=f"20261030-{project_id[-5:]}")
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.save()
            record_project_session(paths, project_id, source.conversation_id)
            source_sessions[project_id] = source

        beta_project_before = (paths.workspace / 'project-beta' / 'PROJECT.md').read_bytes()

        session = create_new(paths, conversation_id="20261030-abc12345")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session.meta.write_scope_root = "workspace/project-alpha"
        session._ordinary_work_intent = "execute"
        session.messages.append({"role": "user", "content": "Update alpha docs and beta docs."})
        session.save()

        desktop_events=[]
        bridge = WsBridge(emit=desktop_events.append, paths=paths, confirm_timeout=1)
        bridge.confirm_fn = lambda *_args: "y"
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        monkeypatch.setattr(repl.agent.project_worker_host,"_llm_client_factory",
                            lambda: pytest.fail("preflight-blocked worker must not construct a model client"))
        run_id, epoch = str(uuid.uuid4()), 1
        repl.agent.bind_project_worker_live_context(
            run_id, epoch, lambda bound_session, actual_run, actual_epoch:
                bound_session is session and (actual_run, actual_epoch) == (run_id, epoch),
        )

        result = repl.agent._dispatch_project_workers_tool({"workers": [
            {"project_id": "project-alpha", "task": "Update alpha docs.",
             "read_targets": ["README.md"], "write_targets": ["../outside.txt"]},
            {"project_id": "project-beta", "task": "Update beta docs.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]},
        ]})

        current_items = WorkRecordStore(session).list_items()
        assert len(current_items) == 1
        assert len(current_items[0]["runs"]) == 2
        blocked_runs = {run["requirement_id"]: run for run in current_items[0]["runs"]}
        assert len(blocked_runs) == 2
        for requirement in current_items[0]["requirements"][1:]:
            assert requirement["status"] == "blocked"
            assert requirement.get("current_run_id") is None  # no worker-start receipt
            blocked = blocked_runs[requirement["requirement_id"]]
            assert blocked["status"] == "blocked"
            assert blocked["verification"]["status"] == "not_verified"
            summary = json.loads(blocked["result_summary"])
            assert summary["host_status"] == "blocked"
            assert summary["worker_execution_started"] is False
            assert summary["worker_result_claimed"] is False
            assert summary["acceptance_status"] == "not_run"
        assert [row["text"] for row in current_items[0]["requirements"][1:]] == [
            "Update alpha docs.", "Update beta docs.",
        ]
        assert len(repl.agent.project_worker_host._reservations) == 2
        assert all(consumed for _reservation, consumed in repl.agent.project_worker_host._reservations.values())
        assert not (paths.workspace / "project-alpha" / "outside.txt").exists()
        assert (paths.workspace / "project-beta" / "PROJECT.md").read_bytes() == beta_project_before
        assert result.ok is True
        workers = result.data["workers"]
        assert [(row["project_id"], row["status"]) for row in workers] == [
            ("project-alpha", "blocked"), ("project-beta", "blocked"),
        ]
        assert all(row["record_lifecycle_status"] == "blocked_recorded" for row in workers)
        assert result.data["acceptance_status"] == "not_run"
        assert result.data["work_record_binding_status"] == "created_current_dialogue"
        candidates = repl.agent._current_dialogue_project_card_candidates
        assert len(candidates) == 1
        assert candidates[0]["project_ids"] == ("project-alpha", "project-beta")

        # Exercise the actual server publisher/ledger/desktop-event boundary with
        # only the synthetic blocked facts; skip only the coalescing delay.
        from server import _publish_current_dialogue_project_work_card
        from work_card_ledger import WorkCardLedger
        from work_card_publish_outbox import ProjectScopedCardOutbox
        monkeypatch.setattr(ProjectScopedCardOutbox,"remaining_seconds",lambda self,intent:0)
        store=WorkRecordStore(session)
        for project_id in candidates[0]["project_ids"]:
            assert _publish_current_dialogue_project_work_card(repl,bridge,session,store,project_id) is True
        payloads=[event for event in desktop_events if event.get("type")=="work-card.present"]
        assert len(payloads)==2
        by_project={event["card"]["snapshot"]["project_id"]:event for event in payloads}
        assert set(by_project)=={"project-alpha","project-beta"}
        cards=WorkCardLedger(session,store).list_cards()
        assert len(cards)==2
        from session import _valid_work_card_ref
        references=[row for row in session.messages if _valid_work_card_ref(row)]
        assert len(references)==2
        assert {row["card_id"] for row in references}=={event["card_id"] for event in payloads}
        for project_id,event in by_project.items():
            card=event["card"];snapshot=card["snapshot"]
            assert snapshot["session_id"]==session.conversation_id
            assert snapshot["completeness"]=="complete"
            projected=[req for work_item in snapshot["cards"] for req in work_item["requirements"]]
            assert len(projected)==1 and projected[0]["status"]=="blocked"
            assert projected[0]["current_run_id"] is None
            assert projected[0]["latest_result_is_current"] is None
            blocked_result=projected[0]["latest_result"]
            assert blocked_result["status"]=="blocked"
            assert blocked_result["verification_status"]=="not_verified"
            receipt=json.loads(blocked_result["summary"])
            assert receipt["project_id"]==project_id
            assert receipt["worker_execution_started"] is False
            assert receipt["acceptance_status"]=="not_run"
            assert "received" not in event and "viewed" not in event
        from work_records_overview_api import query_work_records_overview
        overview = query_work_records_overview(session)
        projected = [req for group in overview["groups"] for item in group["items"]
                     if item["item_id"] == current_items[0]["item_id"] for req in item["requirements"]]
        assert {req["text"]: req["status"] for req in projected} == {
            "Update alpha docs.": "blocked", "Update beta docs.": "blocked",
        }
        from work_record_cards import _build_independent_snapshot
        item_card=_build_independent_snapshot(session.conversation_id,current_items[0]["item_id"],current_items[0]).card
        assert item_card is not None
        for requirement_card in item_card.requirements[1:]:
            assert requirement_card.status=="blocked"
            assert requirement_card.latest_result.status=="blocked"
            assert requirement_card.latest_result.verification_status=="not_verified"
            assert requirement_card.current_run_id is None and requirement_card.latest_result_is_current is None
            assert requirement_card.current_result_missing is False

        item = current_items[0]
        assert item["scope"]["kind"] == "independent"
        requirements = item["requirements"]
        assert [(row["text"], row["source_ref"]) for row in requirements[1:]] == [
            ("Update alpha docs.", {"kind": "project", "id": "project-alpha"}),
            ("Update beta docs.", {"kind": "project", "id": "project-beta"}),
        ]
        assert len(item["runs"]) == 2
        for requirement, worker in zip(requirements[1:], workers):
            assert requirement.get("current_run_id") is None
            assert requirement["status"] == "blocked"
            assert worker["record_lifecycle_status"] == "blocked_recorded"

        for source in source_sessions.values():
            assert WorkRecordStore(source).list_items() == []
        assert not (paths.workspace / "project-alpha" / "outside.txt").exists()
        assert (paths.workspace / "project-beta" / "PROJECT.md").read_bytes() == beta_project_before
        repl.agent.clear_project_worker_live_context(run_id, epoch)
        assert repl.agent.project_worker_host._reservations == {}













def test_partial_current_dialogue_persistence_failure_never_reserves_or_starts_workers(monkeypatch):
    """A partially saved batch is reported, not dispatched or marked as started."""
    with temporary_agent_paths() as paths:
        project_ids = ("project-alpha", "project-beta")
        before = {}
        sources = {}
        for index, project_id in enumerate(project_ids):
            create_project(paths, project_id, project_template="light")
            before[project_id] = (paths.workspace / project_id / "PROJECT.md").read_bytes()
            source = create_new(paths, conversation_id=f"20261030-fail{index:04d}")
            source.meta.project_id = project_id
            source.meta.project_root = f"workspace/{project_id}"
            source.save()
            record_project_session(paths, project_id, source.conversation_id)
            sources[project_id] = source

        session = create_new(paths, conversation_id="20261030-failbatch")
        session.meta.turn_mode = "agent"
        session.meta.harness = "desktop"
        session.meta.active_shell = "project"
        session.meta.project_id = "project-alpha"
        session.meta.project_root = "workspace/project-alpha"
        session._ordinary_work_intent = "execute"
        session.messages.append({"role": "user", "content": "Update alpha docs and beta docs."})
        session.save()

        bridge = WsBridge(emit=lambda _event: None, paths=paths, confirm_timeout=1)
        bridge.confirm_fn = lambda *_args: "y"
        repl = _build_repl(session, paths, bridge, work_record_controller_factory=lambda *_args: None)
        run_id, epoch = "parent-run-persistence-failure", 1
        repl.agent.bind_project_worker_live_context(
            run_id, epoch, lambda bound_session, actual_run, actual_epoch:
                bound_session is session and (actual_run, actual_epoch) == (run_id, epoch),
        )

        real_add_requirement = WorkRecordStore.add_requirement
        add_calls = 0

        def fail_second_task_persist(store, *args, **kwargs):
            nonlocal add_calls
            add_calls += 1
            if add_calls == 2:
                raise OSError("injected second requirement persistence failure")
            return real_add_requirement(store, *args, **kwargs)

        monkeypatch.setattr(WorkRecordStore, "add_requirement", fail_second_task_persist)

        host = repl.agent.project_worker_host
        reserve_calls = []
        run_calls = []
        real_reserve_many = host.reserve_many
        real_run_many = host.run_many

        def observe_reserve(*args, **kwargs):
            reserve_calls.append((args, kwargs))
            return real_reserve_many(*args, **kwargs)

        def observe_run(*args, **kwargs):
            run_calls.append((args, kwargs))
            return real_run_many(*args, **kwargs)

        host.reserve_many = observe_reserve
        host.run_many = observe_run
        result = repl.agent._dispatch_project_workers_tool({"workers": [
            {"project_id": "project-alpha", "task": "Update alpha docs.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]},
            {"project_id": "project-beta", "task": "Update beta docs.",
             "read_targets": ["README.md"], "write_targets": ["PROJECT.md"]},
        ]})

        assert result.ok is False
        assert "CURRENT_DIALOGUE_RECORD_PARTIAL" in result.error.message
        assert "1/2 task requirements" in result.error.message
        assert "no worker was dispatched" in result.error.message
        assert add_calls == 2
        assert reserve_calls == []
        assert run_calls == []
        assert host._reservations == {}

        items = WorkRecordStore(session).list_items()
        assert len(items) == 1
        assert [row["text"] for row in items[0]["requirements"]] == [
            "Update alpha docs and beta docs.", "Update alpha docs.",
        ]
        assert items[0]["runs"] == []
        for project_id in project_ids:
            assert WorkRecordStore(sources[project_id]).list_items() == []
            assert (paths.workspace / project_id / "PROJECT.md").read_bytes() == before[project_id]



def _blocked_attempt(session, item_id, requirement_id, task_text, *, reservation_project="project-alpha"):
    from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
    task_id="task-"+hashlib.sha256("\0".join((session.conversation_id,item_id,requirement_id,reservation_project)).encode("utf-8")).hexdigest()
    reservation=ProjectWorkerReservation(
        reservation_id=str(uuid.uuid4()),worker_id="worker-alpha",worker_run_id=str(uuid.uuid4()),
        parent_run_id=str(uuid.uuid4()),turn_epoch=7,conversation_id=session.conversation_id,
        project_id=reservation_project,task_id=task_id,
    )
    outcome=ProjectWorkerHostOutcome(
        worker_id=reservation.worker_id,run_id=reservation.parent_run_id,turn_epoch=reservation.turn_epoch,
        conversation_id=session.conversation_id,project_id=reservation.project_id,task_id=reservation.task_id,
        status="blocked",model_turn=None,error_code="project_or_target_scope_invalid",
        parent_run_id=reservation.parent_run_id,worker_run_id=reservation.worker_run_id,
        reservation_id=reservation.reservation_id,
    )
    return reservation,outcome


def test_prestart_blocked_result_is_idempotent_and_cross_project_rejected():
    from agent import _record_current_dialogue_worker_lifecycle
    from session import create_new
    with temporary_agent_paths() as paths:
        session=create_new(paths,conversation_id="20261107-blocked01")
        store=WorkRecordStore(session)
        item=store.create_item(title="Synthetic blocked batch",scope={"kind":"independent"})
        req=store.add_requirement(item["item_id"],"Synthetic alpha task",event_id="blocked-alpha",
            source_ref={"kind":"project","id":"project-alpha"})
        reservation,outcome=_blocked_attempt(session,item["item_id"],req["requirement_id"],req["text"])
        def settle():
            return _record_current_dialogue_worker_lifecycle(store,session_id=session.conversation_id,
                item_id=item["item_id"],requirement_id=req["requirement_id"],outcome=outcome,
                reservation=reservation,expected_requirement_text=req["text"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _index:settle(),range(4)))
        assert results==["blocked_recorded"]*4
        saved=store.get_item(item["item_id"]);requirement=saved["requirements"][0]
        assert requirement["status"]=="blocked" and requirement.get("current_run_id") is None
        runs=[run for run in saved["runs"] if run["requirement_id"]==req["requirement_id"]]
        assert len(runs)==1 and runs[0]["status"]=="blocked"
        assert not any(event.get("kind")=="requirement_run_started"
                       and event.get("payload",{}).get("requirement_id")==req["requirement_id"]
                       for event in saved["history"])
        summary=json.loads(runs[0]["result_summary"])
        assert summary["project_id"]=="project-alpha" and summary["task_id"]==reservation.task_id
        assert summary["reservation_id"]==reservation.reservation_id
        assert summary["host_status"]=="blocked" and summary["host_error_code"]==outcome.error_code
        assert summary["worker_execution_started"] is False and summary["worker_result_claimed"] is False
        assert summary["acceptance_status"]=="not_run" and runs[0]["verification"]["status"]=="not_verified"

        other=store.add_requirement(item["item_id"],"Synthetic beta task",event_id="blocked-beta",
            source_ref={"kind":"project","id":"project-beta"})
        wrong_reservation,wrong_outcome=_blocked_attempt(session,item["item_id"],other["requirement_id"],other["text"])
        with pytest.raises(ValueError,match="requirement binding"):
            _record_current_dialogue_worker_lifecycle(store,session_id=session.conversation_id,
                item_id=item["item_id"],requirement_id=other["requirement_id"],outcome=wrong_outcome,
                reservation=wrong_reservation,expected_requirement_text=other["text"])
        assert not any(run["requirement_id"]==other["requirement_id"] for run in store.get_item(item["item_id"])["runs"])


def test_prestart_blocked_record_loses_race_to_existing_current_run():
    from agent import _record_current_dialogue_worker_lifecycle
    from session import create_new
    from work_records import WorkRecordStaleRun
    with temporary_agent_paths() as paths:
        session=create_new(paths,conversation_id="20261107-blocked02")
        store=WorkRecordStore(session);item=store.create_item(title="Synthetic race",scope={"kind":"independent"})
        req=store.add_requirement(item["item_id"],"Synthetic task",event_id="blocked-race",
            source_ref={"kind":"project","id":"project-alpha"})
        reservation,outcome=_blocked_attempt(session,item["item_id"],req["requirement_id"],req["text"])
        original_get=store.get_item;observed=threading.Event();continue_after_start=threading.Event()
        def delayed_get(item_id):
            value=original_get(item_id)
            if item_id==item["item_id"]:
                observed.set()
                assert continue_after_start.wait(3),"concurrent current run did not start"
            return value
        store.get_item=delayed_get
        def settle():
            return _record_current_dialogue_worker_lifecycle(store,session_id=session.conversation_id,
                item_id=item["item_id"],requirement_id=req["requirement_id"],outcome=outcome,
                reservation=reservation,expected_requirement_text=req["text"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(settle)
            assert observed.wait(3),"blocked receipt did not reach its pre-write read"
            store.begin_requirement_run(item["item_id"],req["requirement_id"],run_id="other-current-run",
                expected_current_run_id=None,expected_requirement_text=req["text"])
            continue_after_start.set()
            assert pending.result(timeout=3)=="blocked_not_recorded"
        store.get_item=original_get
        latest=store.get_item(item["item_id"]);latest_req=latest["requirements"][0]
        assert latest_req["current_run_id"]=="other-current-run" and latest_req["status"]=="in_progress"
        assert latest["runs"]==[]
