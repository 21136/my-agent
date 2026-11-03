from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_card_ledger import WorkCardLedger
from work_card_project_scope import build_project_scoped_work_record_cards
from work_card_presentations import WorkCardPresentations
from work_records import WorkRecordStore


_SESSION_ID = "20261030-abcdef01"
_PARENT_RUN_ID = "c6b23429-fc35-4ded-aa0c-5873ab8f0a11"


def _session(tmp_path: Path):
    root = tmp_path / "agent-root"
    paths = AgentPaths(root, root / "workspace", root / "data", root / "evolve")
    session_dir = paths.data / "sessions" / _SESSION_ID
    session_dir.mkdir(parents=True)
    session = Session(
        conversation_id=_SESSION_ID,
        session_dir=session_dir,
        goal="synthetic project scoped card fixture",
        meta=SessionMeta(project_id=""),
        messages=[],
        paths=paths,
    )
    return session, WorkRecordStore(session)


def _add_task(store, item_id: str, project_id: str, text: str, suffix: str, parent_run_id: str = _PARENT_RUN_ID):
    requirement = store.add_requirement(
        item_id, text, event_id=f"event-{suffix}",
        source_ref={"kind": "project", "id": project_id},
    )
    requirement_id = requirement["requirement_id"]
    run_id = f"worker-run-{suffix}"
    task_id = "task-" + hashlib.sha256(
        "\0".join((_SESSION_ID, item_id, requirement_id, project_id)).encode("utf-8")
    ).hexdigest()
    store.begin_requirement_run(item_id, requirement_id, run_id=run_id, expected_current_run_id=None)
    receipt = {
        "kind": "project_worker_host_lifecycle",
        "result_status": "unknown",
        "project_id": project_id,
        "task_id": task_id,
        "worker_id": f"worker-{suffix}",
        "worker_run_id": run_id,
        "reservation_id": f"reservation-{suffix}",
        "parent_run_id": parent_run_id,
        "parent_epoch": 3,
        "host_status": "model_turn_finished",
        "acceptance_status": "not_run",
        "write_receipts_available": True,
        "write_receipts": [{
            "path": f"docs/{suffix}.md",
            "status": "written",
            "verification": "content_match",
            "characters_read_back": 12,
            "error_code": "",
            "readback_succeeded": True,
        }],
    }
    store.record_result_if_current_run(
        item_id, requirement_id,
        run_id=run_id,
        result_id="worker-result-" + hashlib.sha256(
            "\0".join((_SESSION_ID, item_id, requirement_id, run_id)).encode("utf-8")
        ).hexdigest(),
        status="unknown",
        summary=json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        verification={
            "status": "not_verified",
            "evidence_refs": [],
            "host_execution": {"sequence": 3, "finish_reason": "completed"},
        },
        sources=[],
    )
    return requirement_id


def _source_fixture(tmp_path: Path):
    session, store = _session(tmp_path)
    item = store.create_item(title="多项目任务批次", scope={"kind": "independent"})
    alpha_req = _add_task(store, item["item_id"], "synthetic-alpha", "Alpha requirement", "alpha")
    beta_req = _add_task(store, item["item_id"], "synthetic-beta", "Beta requirement", "beta")
    return session, store, item["item_id"], alpha_req, beta_req


def test_project_scoped_focus_full_server_reconnect_chain_multi_project_parent_isolation(tmp_path, monkeypatch):
    from agent import _record_current_dialogue_worker_lifecycle
    from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
    from server import _emit_pending_work_card_references, _publish_current_dialogue_project_work_card
    from work_card_ledger import WorkCardLedger
    from work_card_project_scope import _task_identity, build_project_scoped_work_record_cards
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from session import Session
    from work_records import WorkRecordStore
    import server

    session, store = _session(tmp_path)
    item = store.create_item(title="multi-project focus batch", scope={"kind": "independent"})
    requirement_by_project = {}
    for project_id in ("synthetic-alpha", "synthetic-beta"):
        requirement_by_project[project_id] = [
            store.add_requirement(
                item["item_id"], f"{project_id} task one", event_id=f"{project_id}-one",
                source_ref={"kind": "project", "id": project_id},
            ),
            store.add_requirement(
                item["item_id"], f"{project_id} task two", event_id=f"{project_id}-two",
                source_ref={"kind": "project", "id": project_id},
            ),
        ]
    session.save()
    ledger = WorkCardLedger(session, store)

    # Preserve no-focus historical cards before any trusted worker settlement intent exists.
    old_cards = {}
    for project_id in ("synthetic-alpha", "synthetic-beta"):
        old_snapshot = build_project_scoped_work_record_cards(store, project_id)
        assert old_snapshot.change_focus is None
        saved_old = ledger.save_project_scoped_snapshot(str(uuid.uuid4()), old_snapshot)
        assert saved_old.status == "saved"
        old_cards[project_id] = saved_old.record["snapshot"]

    latest_receipts = {}
    parent_runs = {}
    for project_id in ("synthetic-alpha", "synthetic-beta"):
        for index, requirement in enumerate(requirement_by_project[project_id], start=1):
            requirement_id = requirement["requirement_id"]
            worker_run_id = uuid.uuid4().hex
            parent_run_id = uuid.uuid4().hex
            reservation = ProjectWorkerReservation(
                reservation_id=uuid.uuid4().hex, worker_id=f"worker-{project_id}-{index}",
                worker_run_id=worker_run_id, parent_run_id=parent_run_id, turn_epoch=index,
                conversation_id=session.conversation_id, project_id=project_id,
                task_id=_task_identity(session.conversation_id, item["item_id"], requirement_id, project_id),
            )
            store.begin_requirement_run(
                item["item_id"], requirement_id, run_id=worker_run_id,
                expected_current_run_id=None,
            )
            outcome = ProjectWorkerHostOutcome(
                worker_id=reservation.worker_id, run_id=parent_run_id, turn_epoch=index,
                conversation_id=session.conversation_id, project_id=project_id,
                task_id=reservation.task_id, status="model_turn_finished",
                parent_run_id=parent_run_id, worker_run_id=worker_run_id,
                reservation_id=reservation.reservation_id,
            )
            _record_current_dialogue_worker_lifecycle(
                store, session_id=session.conversation_id, item_id=item["item_id"],
                requirement_id=requirement_id, reservation=reservation, outcome=outcome,
                expected_requirement_text=requirement["text"],
            )
            result_id = "worker-result-" + hashlib.sha256(
                "\0".join((session.conversation_id, item["item_id"], requirement_id, worker_run_id)).encode("utf-8")
            ).hexdigest()
            latest_receipts[project_id] = {"item_id": item["item_id"],
                                           "requirement_id": requirement_id,
                                           "result_id": result_id}
            parent_runs.setdefault(project_id, []).append(parent_run_id)

    intents = {row["project_id"]: row for row in ProjectScopedCardOutbox(session, store).list_intents()}
    assert set(intents) == {"synthetic-alpha", "synthetic-beta"}
    for project_id, focus in latest_receipts.items():
        assert intents[project_id]["latest_item_id"] == focus["item_id"]
        assert intents[project_id]["latest_requirement_id"] == focus["requirement_id"]
        assert intents[project_id]["latest_result_id"] == focus["result_id"]
        assert intents[project_id]["latest_run_id"] == store.get_item(item["item_id"])["requirements"][
            next(i for i, req in enumerate(store.get_item(item["item_id"])["requirements"])
                 if req["requirement_id"] == focus["requirement_id"])
        ]["current_run_id"]
        assert len(intents[project_id]["source_result_ids"]) == 2

    # Use a controlled due timer; storage, settlement, publisher and ledger remain real.
    original_remaining = ProjectScopedCardOutbox.remaining_seconds
    monkeypatch.setattr(ProjectScopedCardOutbox, "remaining_seconds", lambda _self, _intent, **_kwargs: 0.0)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    published = {}
    for project_id in ("synthetic-alpha", "synthetic-beta"):
        published[project_id] = _publish_current_dialogue_project_work_card(
            repl, bridge, session, store, project_id,
        )
    monkeypatch.setattr(ProjectScopedCardOutbox, "remaining_seconds", original_remaining)
    assert published == {"synthetic-alpha": True, "synthetic-beta": True}

    first_events = {event["card"]["snapshot"]["project_id"]: event
                    for event in events if event.get("type") == "work-card.present"}
    assert set(first_events) == {"synthetic-alpha", "synthetic-beta"}
    for project_id, focus in latest_receipts.items():
        event = first_events[project_id]
        snapshot = event["card"]["snapshot"]
        assert snapshot["change_focus"] == focus
        req_rows = snapshot["cards"][0]["requirements"]
        assert len(req_rows) == 2
        assert {row["source_ref"]["id"] for row in req_rows} == {project_id}
        assert json.loads(json.dumps(snapshot))["change_focus"] == focus  # public event payload is JSON-safe
        parent_run_id = server._current_dialogue_card_parent_run_id(
            build_project_scoped_work_record_cards(store, project_id),
        )
        assert parent_run_id in parent_runs[project_id]
        assert event["presentation_id"] == server._work_card_presentation_id(
            session.conversation_id, parent_run_id, event["card_id"],
        )
        persisted = ledger.get_card(event["card_id"])
        assert persisted["snapshot"] == snapshot
        assert ProjectScopedCardOutbox(session, store).list_intents()[
            0 if project_id == "synthetic-alpha" else 1
        ]["state"] == "reference_queued"

    # Both pre-focus cards remain immutable and continue to omit focus.
    for project_id, old_snapshot in old_cards.items():
        old_record = next(row for row in ledger.list_cards()
                          if row["snapshot"]["project_id"] == project_id
                          and "change_focus" not in row["snapshot"])
        assert old_record["snapshot"] == old_snapshot

    # Reconnect replays the durable references and exact focus-bearing snapshots.
    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    replay_events = []
    replay_bridge = WsBridge(emit=replay_events.append, paths=restored.paths)
    replay_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    assert _emit_pending_work_card_references(replay_repl, replay_bridge) == 2
    replayed = {event["card"]["snapshot"]["project_id"]: event
                for event in replay_events if event.get("type") == "work-card.present"}
    assert set(replayed) == set(first_events)
    for project_id in first_events:
        assert replayed[project_id]["presentation_id"] == first_events[project_id]["presentation_id"]
        assert replayed[project_id]["card"]["snapshot"] == first_events[project_id]["card"]["snapshot"]
    for project_id, old_snapshot in old_cards.items():
        old_record = next(row for row in WorkCardLedger(restored, restored_store).list_cards()
                          if row["snapshot"]["project_id"] == project_id
                          and "change_focus" not in row["snapshot"])
        assert old_record["snapshot"] == old_snapshot

    # A different parent Session with identical project names cannot read these intents/cards.
    other_id = str(uuid.uuid4())
    other_dir = session.paths.data / "sessions" / other_id
    other_dir.mkdir(parents=True)
    other = Session(other_id, other_dir, "", SessionMeta(), [], session.paths)
    other.save()
    other_store = WorkRecordStore(other)
    assert ProjectScopedCardOutbox(other, other_store).list_intents() == []
    assert WorkCardLedger(other, other_store).list_cards() == []


def _record_prestart_blocked_task(store, session, item_id, requirement):
    from agent import _record_current_dialogue_worker_lifecycle
    from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
    from work_card_project_scope import _task_identity

    project_id = requirement["source_ref"]["id"]
    reservation = ProjectWorkerReservation(
        reservation_id=uuid.uuid4().hex, worker_id=uuid.uuid4().hex,
        worker_run_id=uuid.uuid4().hex, parent_run_id=uuid.uuid4().hex,
        turn_epoch=7, conversation_id=session.conversation_id, project_id=project_id,
        task_id=_task_identity(session.conversation_id, item_id, requirement["requirement_id"], project_id),
    )
    outcome = ProjectWorkerHostOutcome(
        worker_id=reservation.worker_id, run_id=reservation.parent_run_id, turn_epoch=reservation.turn_epoch,
        conversation_id=session.conversation_id, project_id=project_id, task_id=reservation.task_id,
        status="blocked", model_turn=None, parent_run_id=reservation.parent_run_id,
        worker_run_id=reservation.worker_run_id, reservation_id=reservation.reservation_id,
        error_code="worker_dispatch_submit_failed",
    )
    status = _record_current_dialogue_worker_lifecycle(
        store, session_id=session.conversation_id, item_id=item_id,
        requirement_id=requirement["requirement_id"], outcome=outcome,
        reservation=reservation, expected_requirement_text=requirement["text"],
    )
    return status, reservation


def test_trusted_prestart_blocked_receipt_projects_and_saves_without_fabricated_current_run(tmp_path):
    from work_card_ledger import WorkCardLedger
    from work_card_project_scope import project_scoped_snapshot_request_id
    from work_records import WorkRecordStore

    session, store = _session(tmp_path)
    item = store.create_item(title="prestart block batch", scope={"kind": "independent"})
    blocked = store.add_requirement(
        item["item_id"], "blocked alpha task", event_id="prestart-alpha-blocked",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    running = store.add_requirement(
        item["item_id"], "other alpha task running", event_id="prestart-alpha-running",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    store.begin_requirement_run(
        item["item_id"], running["requirement_id"], run_id=uuid.uuid4().hex,
        expected_current_run_id=None,
    )
    pending = store.add_requirement(
        item["item_id"], "other alpha task queued", event_id="prestart-alpha-pending",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )

    record_status, reservation = _record_prestart_blocked_task(store, session, item["item_id"], blocked)
    saved_item = store.get_item(item["item_id"])
    blocked_req = next(row for row in saved_item["requirements"] if row["requirement_id"] == blocked["requirement_id"])
    run = next(row for row in saved_item["runs"] if row["requirement_id"] == blocked["requirement_id"])
    assert record_status == "blocked_recorded"
    assert "current_run_id" not in blocked_req
    assert not any(event.get("kind") == "requirement_run_started"
                   and event.get("payload", {}).get("run_id") == reservation.worker_run_id
                   for event in saved_item["history"])
    assert run["status"] == "blocked"
    assert run["verification"] == {"status": "not_verified", "evidence_refs": []}
    receipt = json.loads(run["result_summary"])
    assert receipt["worker_execution_started"] is False
    assert receipt["worker_result_claimed"] is False
    assert receipt["host_status"] == "blocked" and receipt["acceptance_status"] == "not_run"

    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert snapshot.completeness == "complete"
    projected = {row.requirement_id: row for row in snapshot.cards[0].requirements}
    blocked_row = projected[blocked["requirement_id"]]
    assert blocked_row.status == "blocked"
    assert blocked_row.current_run_id is None
    assert blocked_row.latest_result_is_current is None
    assert blocked_row.latest_result is not None and blocked_row.latest_result.status == "blocked"
    assert projected[running["requirement_id"]].status == "in_progress"
    assert projected[pending["requirement_id"]].status == "pending"
    # Keep the existing project-scoped card top-level contract conservative for the current renderer.
    assert snapshot.cards[0].status == "unknown"
    assert snapshot.change_focus is None

    request_id = project_scoped_snapshot_request_id(snapshot)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_project_scoped_snapshot(request_id, snapshot)
    assert saved.status == "saved"
    reopened = Session.load(session.paths, session.conversation_id)
    reopened_ledger = WorkCardLedger(reopened, WorkRecordStore(reopened))
    reread = reopened_ledger.get_card(saved.card_id)
    assert reread["snapshot"].get("change_focus") is None
    saved_blocked = reread["snapshot"]["cards"][0]["requirements"][0]
    assert saved_blocked["status"] == "blocked"
    assert saved_blocked["current_run_id"] is None and saved_blocked["latest_result_is_current"] is None


def test_prestart_blocked_projection_rejects_forged_receipt_identity_and_source(tmp_path):
    from work_card_project_scope import build_project_scoped_work_record_cards

    for mutation in ("project", "task", "reservation", "started"):
        case = tmp_path / mutation
        session, store = _session(case)
        item = store.create_item(title="forged prestart block", scope={"kind": "independent"})
        requirement = store.add_requirement(
            item["item_id"], "alpha task", event_id=f"forged-{mutation}",
            source_ref={"kind": "project", "id": "synthetic-alpha"},
        )
        _record_prestart_blocked_task(store, session, item["item_id"], requirement)
        def forge(data):
            target = next(row for row in data["items"] if row["item_id"] == item["item_id"])
            stored = next(row for row in target["runs"] if row["requirement_id"] == requirement["requirement_id"])
            receipt = json.loads(stored["result_summary"])
            if mutation == "project":
                receipt["project_id"] = "synthetic-beta"
            elif mutation == "task":
                receipt["task_id"] = "task-forged"
            elif mutation == "reservation":
                receipt.pop("reservation_id")
            else:
                receipt["worker_execution_started"] = True
            stored["result_summary"] = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            return None, True
        store._mutate(forge)
        snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
        assert snapshot.completeness == "incomplete", mutation
        assert snapshot.incomplete_reason == "untrusted_task_receipt"

    # The receipt can’t be reinterpreted under a different project source scope.
    session, store = _session(tmp_path / "source-mismatch")
    item = store.create_item(title="cross project prestart block", scope={"kind": "independent"})
    alpha = store.add_requirement(
        item["item_id"], "valid alpha pending", event_id="source-alpha-valid",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    blocked = store.add_requirement(
        item["item_id"], "blocked alpha task", event_id="source-alpha-blocked",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    _record_prestart_blocked_task(store, session, item["item_id"], blocked)
    def change_source(data):
        target = next(row for row in data["items"] if row["item_id"] == item["item_id"])
        next(row for row in target["requirements"] if row["requirement_id"] == blocked["requirement_id"])["source_ref"] = {
            "kind": "project", "id": "synthetic-beta",
        }
        return None, True
    store._mutate(change_source)
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert snapshot.completeness == "complete"
    assert [row.requirement_id for row in snapshot.cards[0].requirements] == [alpha["requirement_id"]]
    assert all(row.status != "blocked" for row in snapshot.cards[0].requirements)
    # Re-scoping the same receipt to beta must not make alpha's identity appear valid there.
    beta_snapshot = build_project_scoped_work_record_cards(store, "synthetic-beta")
    assert beta_snapshot.completeness == "incomplete"
    assert beta_snapshot.incomplete_reason == "untrusted_task_receipt"


def test_project_scoped_snapshot_projects_only_its_requirements_and_real_receipts(tmp_path):
    session, store, _item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)

    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")

    assert snapshot.kind == "project_scoped"
    assert snapshot.project_id == "synthetic-alpha"
    assert snapshot.session_id == session.conversation_id
    assert snapshot.completeness == "complete"
    assert snapshot.change_focus is None  # result-only legacy facts have no durable publication identity
    assert snapshot.item_count == 1
    card = snapshot.cards[0]
    assert {requirement.text for requirement in card.requirements} == {"Alpha requirement"}
    requirement = card.requirements[0]
    assert requirement.status == "unknown"
    assert requirement.latest_result.status == "unknown"
    assert requirement.latest_result.verification_status == "not_verified"
    receipt = json.loads(requirement.latest_result.summary)
    assert receipt["project_id"] == "synthetic-alpha"
    assert receipt["write_receipts"][0]["path"] == "docs/alpha.md"
    assert receipt["acceptance_status"] == "not_run"


def test_project_scoped_ledger_is_immutable_and_presentation_result_ack_binds_to_card(tmp_path):
    session, store, _item_id, alpha_req, _beta_req = _source_fixture(tmp_path)
    ledger = WorkCardLedger(session, store)
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    request_id = str(uuid.uuid4())

    saved = ledger.save_project_scoped_snapshot(request_id, snapshot)

    assert saved.status == "saved"
    assert saved.record["snapshot"]["kind"] == "project_scoped"
    assert [row["requirement_id"] for row in saved.record["snapshot"]["cards"][0]["requirements"]] == [alpha_req]
    replay = ledger.save_project_scoped_snapshot(request_id, snapshot)
    assert replay.status == "already_saved"
    changed_card = snapshot.cards[0]
    changed_requirement = changed_card.requirements[0]
    changed = changed_requirement.latest_result
    forged = json.loads(json.dumps(saved.record["snapshot"]))
    forged["cards"][0]["requirements"][0]["latest_result"]["summary"] = "forged"
    result = ledger.save_project_scoped_snapshot(str(uuid.uuid4()), forged)
    assert result.status == "rejected"

    presentation_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == "created"
    assert presentations.acknowledge_result(
        presentation_id, saved.card_id, changed.result_id,
        action="result_detail", trusted_action=True,
    ).status == "acknowledged"
    assert presentations.get(presentation_id).fact.result_views[0].requirement_id == alpha_req


def test_project_scoped_snapshot_rejects_unreceipted_or_cross_project_task_claims(tmp_path):
    session, store, item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    requirement = store.add_requirement(
        item_id, "Unreceipted alpha result", event_id="event-unreceipted",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    run_id = "worker-run-unreceipted"
    store.begin_requirement_run(item_id, requirement["requirement_id"], run_id=run_id,
                                expected_current_run_id=None)
    store.record_result_if_current_run(
        item_id, requirement["requirement_id"], run_id=run_id, result_id="unreceipted-result",
        status="unknown", summary="not a host receipt",
        verification={"status": "not_verified", "evidence_refs": [],
                      "host_execution": {"sequence": 1, "finish_reason": "completed"}},
        sources=[],
    )

    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")

    assert snapshot.completeness == "incomplete"
    assert snapshot.cards == ()
    assert snapshot.incomplete_reason == "untrusted_task_receipt"

from project_worker_host import ProjectWorkerHostOutcome, ProjectWorkerReservation
from project_worker_model_loop import ProjectWorkerModelOutcome, ProjectWorkerWriteReceipt
from project_worker_execution import ProjectReadAttempt
from agent import _record_current_dialogue_worker_lifecycle


def test_agent_persists_actual_worker_write_receipts_without_upgrading_acceptance(tmp_path):
    session, store = _session(tmp_path)
    item = store.create_item(title="batch", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "Alpha task", event_id="receipt-task",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    task_id = "task-" + hashlib.sha256(
        "\0".join((session.conversation_id, item["item_id"], requirement["requirement_id"],
                    "synthetic-alpha")).encode("utf-8")
    ).hexdigest()
    worker_run_id = "worker-run-receipt"
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                run_id=worker_run_id, expected_current_run_id=None)
    reservation = ProjectWorkerReservation(
        reservation_id="reservation-receipt", worker_id="worker-receipt",
        worker_run_id=worker_run_id, parent_run_id=_PARENT_RUN_ID, turn_epoch=3,
        conversation_id=session.conversation_id, project_id="synthetic-alpha", task_id=task_id,
    )
    write = ProjectWorkerWriteReceipt(
        path="docs/receipt.md", status="written", verification="content_match",
        characters_read_back=12, error_code="", readback_succeeded=True,
    )
    outcome = ProjectWorkerHostOutcome(
        worker_id=reservation.worker_id, run_id=reservation.parent_run_id,
        turn_epoch=reservation.turn_epoch, conversation_id=reservation.conversation_id,
        project_id=reservation.project_id, task_id=reservation.task_id,
        status="model_turn_finished",
        model_turn=ProjectWorkerModelOutcome(
            project_id=reservation.project_id, status="model_turn_finished", writes=(write,),
            read_attempts=(ProjectReadAttempt(
                read_id=1, path="docs/source.md", attempt=1, result="retrying",
                error_code="sharing_violation",
            ), ProjectReadAttempt(
                read_id=1, path="docs/source.md", attempt=1, result="cancelled",
            )),
        ),
        parent_run_id=reservation.parent_run_id, worker_run_id=reservation.worker_run_id,
        reservation_id=reservation.reservation_id,
    )

    _record_current_dialogue_worker_lifecycle(
        store, session_id=session.conversation_id, item_id=item["item_id"],
        requirement_id=requirement["requirement_id"], outcome=outcome, reservation=reservation,
        expected_requirement_text=requirement["text"],
    )
    # A repeated host receipt is an idempotent replay, not a second result fact.
    _record_current_dialogue_worker_lifecycle(
        store, session_id=session.conversation_id, item_id=item["item_id"],
        requirement_id=requirement["requirement_id"], outcome=outcome, reservation=reservation,
        expected_requirement_text=requirement["text"],
    )

    saved = store.get_item(item["item_id"])
    assert len(saved["runs"]) == 1
    run = saved["runs"][0]
    receipt = json.loads(run["result_summary"])
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert receipt["acceptance_status"] == "not_run"
    assert receipt["write_receipts_available"] is True
    assert receipt["read_attempts_available"] is True
    assert receipt["read_attempts"] == [
        {"read_id": 1, "path": "docs/source.md", "attempt": 1,
         "result": "retrying", "error_code": "sharing_violation"},
        {"read_id": 1, "path": "docs/source.md", "attempt": 1,
         "result": "cancelled", "error_code": ""},
    ]
    assert receipt["write_receipts"] == [{
        "path": "docs/receipt.md", "status": "written", "verification": "content_match",
        "characters_read_back": 12, "error_code": "", "readback_succeeded": True,
    }]
    projected = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    projected_receipt = json.loads(projected.cards[0].requirements[0].latest_result.summary)
    assert projected_receipt["read_attempts"] == receipt["read_attempts"]
    from work_card_publish_outbox import ProjectScopedCardOutbox
    intent = ProjectScopedCardOutbox(session, store).list_intents()[0]
    assert intent["source_result_ids"] == [run["result_id"]]
    assert intent["state"] == "pending"
import asyncio
from types import SimpleNamespace

from server import WsBridge, _schedule_current_dialogue_project_work_cards


def test_server_coalescer_publishes_distinct_project_cards_through_history_and_presentations(tmp_path, monkeypatch):
    session, store, _item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr("server.PROJECT_WORK_CARD_MERGE_SECONDS", 0.01)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def schedule_and_wait():
        scheduled_count = _schedule_current_dialogue_project_work_cards(
            repl=repl, bridge=bridge, session=session,
            project_ids=("synthetic-alpha", "synthetic-beta"),
        )
        await asyncio.sleep(0.45)
        return scheduled_count
    try:
        scheduled = loop.run_until_complete(schedule_and_wait())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    cards = [event["card"]["snapshot"] for event in events if event.get("type") == "work-card.present"]
    assert scheduled == 2
    assert {card["project_id"] for card in cards} == {"synthetic-alpha", "synthetic-beta"}
    assert all(card["kind"] == "project_scoped" for card in cards)
    by_project = {
        card["project_id"]: {requirement["text"] for item in card["cards"]
                             for requirement in item["requirements"]}
        for card in cards
    }
    assert by_project == {
        "synthetic-alpha": {"Alpha requirement"},
        "synthetic-beta": {"Beta requirement"},
    }
    assert all(
        requirement["latest_result"]["status"] == "unknown"
        and requirement["latest_result"]["verification_status"] == "not_verified"
        for card in cards for item in card["cards"] for requirement in item["requirements"]
    )
    assert len([row for row in session.messages if row.get("role") == "work_card_presentation"]) == 2
    assert len(WorkCardLedger(session, store).list_cards()) == 2
    bridge._work_card_update_coalescer.close()
from server import _run_line


def test_server_run_line_consumes_agent_candidate_and_emits_project_card(tmp_path, monkeypatch):
    session, store, item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    agent = SimpleNamespace(session=session, work_card_reference_intent=None,
                            _current_dialogue_project_card_candidates=[])
    repl = SimpleNamespace(session=session, agent=agent, last_turn_finish_reason=None)

    def handle_line(_line):
        run = bridge.lifecycle.snapshot()
        assert run is not None
        agent._current_dialogue_project_card_candidates.append({
            "session_id": session.conversation_id,
            "parent_run_id": run.run_id,
            "turn_epoch": run.sequence,
            "item_id": item_id,
            "project_ids": ("synthetic-alpha", "synthetic-beta"),
        })
        repl.last_turn_finish_reason = "completed"
        return None

    repl.handle_line = handle_line
    monkeypatch.setattr("server.PROJECT_WORK_CARD_MERGE_SECONDS", 0.01)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_line(repl, bridge, "synthetic request", session.paths))
        loop.run_until_complete(asyncio.sleep(0.08))
    finally:
        if bridge._work_card_update_coalescer is not None:
            bridge._work_card_update_coalescer.close()
        loop.close()
        asyncio.set_event_loop(None)

    snapshots = [event["card"]["snapshot"] for event in events if event.get("type") == "work-card.present"]
    assert {snapshot["project_id"] for snapshot in snapshots} == {"synthetic-alpha", "synthetic-beta"}
    assert agent._current_dialogue_project_card_candidates == []
    assert len(WorkCardLedger(session, store).list_cards()) == 2

@pytest.mark.parametrize("intent_state", ["pending", "card_saved", "reference_queued"])
def test_historical_project_focus_intent_remains_readable_after_requirement_starts_new_run(
    tmp_path, intent_state,
):
    from work_card_ledger import WorkCardLedger
    from work_card_project_scope import build_project_scoped_work_record_cards, project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from session import Session
    from work_records import WorkRecordStore

    session, store = _session(tmp_path)
    item = store.create_item(title="historical focus", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "historical alpha requirement", event_id="historical-alpha",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    saved = _atomic_worker_result(session, store, item["item_id"], requirement["requirement_id"])
    old_run_id = "worker-run-atomic"
    outbox = ProjectScopedCardOutbox(session, store)
    intent = outbox.list_intents()[0]
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert snapshot.change_focus is not None
    request_id = project_scoped_snapshot_request_id(snapshot)
    assert request_id == intent["request_id"]

    if intent_state in {"card_saved", "reference_queued"}:
        card = WorkCardLedger(session, store).save_project_scoped_snapshot(
            request_id, snapshot, generation=intent["generation"],
        )
        assert card.status == "saved"
        assert outbox.mark_card_saved(
            project_id="synthetic-alpha", generation=intent["generation"],
            request_id=request_id, card_id=card.card_id,
        ) == "saved"
        if intent_state == "reference_queued":
            assert outbox.mark_reference_queued(
                project_id="synthetic-alpha", generation=intent["generation"],
                request_id=request_id, card_id=card.card_id,
            ) == "queued"

    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"], run_id="worker-run-next",
        expected_current_run_id=old_run_id,
    )
    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    restored_item = restored_store.get_item(item["item_id"])
    restored_intent = ProjectScopedCardOutbox(restored, restored_store).list_intents()[0]

    assert restored_item["requirements"][0]["current_run_id"] == "worker-run-next"
    assert restored_item["runs"][0]["result_id"] == saved["result_id"]
    assert restored_intent["state"] == intent_state
    assert restored_intent["latest_run_id"] == old_run_id
    assert restored_intent["latest_result_id"] == saved["result_id"]


def test_project_scoped_focus_rejects_requirement_source_ref_mismatch_even_when_project_is_linked(tmp_path):
    from work_records import WorkRecordCorrupt

    session, store, item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    focus_requirement = store.add_requirement(
        item_id, "receipt source to corrupt", event_id="focus-source-corrupt",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    _atomic_worker_result(session, store, item_id, focus_requirement["requirement_id"])

    data = json.loads(store._path.read_text(encoding="utf-8"))
    item = next(row for row in data["items"] if row["item_id"] == item_id)
    requirement = next(row for row in item["requirements"]
                       if row["requirement_id"] == focus_requirement["requirement_id"])
    requirement["source_ref"] = {"kind": "project", "id": "synthetic-beta"}
    store._path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(WorkRecordCorrupt, match="focus.*source|source.*focus"):
        WorkRecordStore(session).list_items()


def test_mutate_validates_complete_payload_before_atomic_publish(tmp_path):
    from work_records import WorkRecordCorrupt

    session, store = _session(tmp_path)
    item = store.create_item(title="prepublish validation", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "valid source", event_id="valid-source")
    prior_bytes = store._path.read_bytes()

    def corrupt_payload(data):
        req = next(row for row in data["items"][0]["requirements"]
                   if row["requirement_id"] == requirement["requirement_id"])
        req["source_ref"] = {"kind": "unsupported-kind", "id": "synthetic"}
        return None, True

    with pytest.raises(WorkRecordCorrupt):
        store._mutate(corrupt_payload)

    assert store._path.read_bytes() == prior_bytes
    assert WorkRecordStore(session).get_item(item["item_id"]) is not None


@pytest.mark.parametrize("state", ["pending", "in_progress"])
def test_project_scoped_snapshot_includes_unsettled_same_project_requirements_without_fabricated_receipts(tmp_path, state):
    session, store, item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    requirement = store.add_requirement(
        item_id, f"{state} alpha requirement", event_id=f"event-{state}",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    if state == "in_progress":
        store.begin_requirement_run(
            item_id, requirement["requirement_id"], run_id=f"worker-run-{state}",
            expected_current_run_id=None,
        )

    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")

    assert snapshot.completeness == "complete"
    assert snapshot.incomplete_reason is None
    card = snapshot.cards[0]
    projected = next(row for row in card.requirements if row.requirement_id == requirement["requirement_id"])
    assert projected.status == state
    assert projected.latest_result is None
    assert projected.current_result_missing is False
    assert projected.current_run_id == (f"worker-run-{state}" if state == "in_progress" else None)



def test_project_scoped_pending_deadline_survives_process_reconnect_without_extending(tmp_path):
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store = _session(tmp_path)
    item = store.create_item(title="deadline source", scope={"kind": "independent"})
    store.add_requirement(
        item["item_id"], "alpha pending", event_id="deadline-alpha-pending",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    outbox = ProjectScopedCardOutbox(session, store)
    request_id = str(uuid.uuid4())
    first = outbox.record_update(
        "synthetic-alpha", request_id, now_monotonic=100.0, now_wall=1_700_000_100.0,
        window_seconds=30.0,
    )
    refreshed = outbox.record_update(
        "synthetic-alpha", str(uuid.uuid4()), now_monotonic=108.0, now_wall=1_700_000_108.0,
        window_seconds=30.0,
    )
    assert refreshed["generation"] == first["generation"]
    assert refreshed["window_started_monotonic"] == 100.0
    assert refreshed["deadline_monotonic"] == 130.0
    assert outbox.remaining_seconds(
        refreshed, now_monotonic=117.0, now_wall=1_700_000_117.0,
    ) == 13.0
    assert outbox.remaining_seconds(
        refreshed, now_monotonic=117.0, now_wall=1_700_000_217.0,
    ) == 30.0  # wall/boot-clock discontinuity fails conservatively and persists a new safe window
    refreshed = outbox.list_intents()[0]
    assert outbox.remaining_seconds(
        refreshed, now_monotonic=5.0, now_wall=1_700_000_005.0,
    ) == 30.0  # reboot/monotonic reset cannot make the old deadline early
    refreshed = outbox.list_intents()[0]
    assert outbox.remaining_seconds(
        refreshed, now_monotonic=36.0, now_wall=1_700_000_036.0,
    ) == 0.0
    legacy_row = outbox.list_intents()[0]
    assert "latest_item_id" not in legacy_row and "latest_result_id" not in legacy_row


def test_project_scoped_pending_write_failure_is_not_reported_as_scheduled(tmp_path, monkeypatch):
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store = _session(tmp_path)
    outbox = ProjectScopedCardOutbox(session, store)
    monkeypatch.setattr(store, "_mutate", lambda _change: (_ for _ in ()).throw(OSError("injected")))
    from work_card_publish_outbox import WorkCardPublishOutboxError
    with pytest.raises(WorkCardPublishOutboxError, match="not durably committed"):
        outbox.record_update("synthetic-alpha", str(uuid.uuid4()), now_monotonic=10.0,
                             now_wall=1_700_000_010.0)


def test_project_scoped_reconnect_resumes_remaining_deadline_without_worker_replay(tmp_path, monkeypatch):
    from server import _resume_current_dialogue_project_work_cards
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from work_records import WorkRecordStore

    session, store, _item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    events = []
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr("server.PROJECT_WORK_CARD_MERGE_SECONDS", 0.5)

    async def first_connection():
        first = WsBridge(emit=lambda _event: None, paths=session.paths)
        assert _schedule_current_dialogue_project_work_cards(
            repl=repl, bridge=first, session=session,
            project_ids=("synthetic-alpha", "synthetic-beta"),
        ) == 2
        before = ProjectScopedCardOutbox(session, store).list_intents()
        await asyncio.sleep(0.1)
        first._work_card_update_coalescer.close()
        return before

    first_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(first_loop)
    try:
        before = first_loop.run_until_complete(first_connection())
    finally:
        first_loop.close()
        asyncio.set_event_loop(None)

    resumed_store = WorkRecordStore(session)
    bridge = WsBridge(emit=events.append, paths=session.paths)
    async def reconnect():
        scheduled = _resume_current_dialogue_project_work_cards(repl, bridge, session, resumed_store)
        await asyncio.sleep(0.45)
        return scheduled

    second_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(second_loop)
    try:
        scheduled = second_loop.run_until_complete(reconnect())
    finally:
        if bridge._work_card_update_coalescer is not None:
            bridge._work_card_update_coalescer.close()
        second_loop.close()
        asyncio.set_event_loop(None)

    after = ProjectScopedCardOutbox(session, resumed_store).list_intents()
    cards = [event["card"]["snapshot"] for event in events if event.get("type") == "work-card.present"]
    assert scheduled == 2
    assert {row["project_id"] for row in after} == {"synthetic-alpha", "synthetic-beta"}
    assert {row["project_id"]: row["window_started_monotonic"] for row in after} == {
        row["project_id"]: row["window_started_monotonic"] for row in before
    }
    assert {card["project_id"] for card in cards} == {"synthetic-alpha", "synthetic-beta"}
    assert all(row["state"] == "reference_queued" for row in after)


def test_project_scoped_card_saved_stage_failure_keeps_pending_intent(tmp_path, monkeypatch):
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store, _item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    request_id = str(uuid.uuid4())
    outbox = ProjectScopedCardOutbox(session, store)
    intent = outbox.record_update("synthetic-alpha", request_id, window_seconds=30.0)
    card_id = "wc-" + hashlib.sha256(
        f"{session.conversation_id}\0{request_id}".encode("utf-8"),
    ).hexdigest()
    monkeypatch.setattr(store, "_mutate", lambda _change: (_ for _ in ()).throw(OSError("injected stage failure")))
    assert outbox.mark_card_saved(
        project_id="synthetic-alpha", generation=intent["generation"],
        request_id=request_id, card_id=card_id,
    ) == "uncertain"
    monkeypatch.undo()
    recovered = ProjectScopedCardOutbox(session, store).list_intents()[0]
    assert recovered["state"] == "pending"
    assert recovered["card_id"] is None
    assert ProjectScopedCardOutbox(session, store).mark_card_saved(
        project_id="synthetic-alpha", generation=intent["generation"],
        request_id=request_id, card_id=card_id,
    ) == "saved"


def test_project_scoped_publisher_refuses_early_timer_callback(tmp_path):
    from server import _publish_current_dialogue_project_work_card
    from work_card_project_scope import project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store, item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    focus_requirement = store.add_requirement(
        item_id, "persisted focus receipt", event_id="focus-receipt",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    focus_result = _atomic_worker_result(
        session, store, item_id, focus_requirement["requirement_id"],
    )
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert snapshot.change_focus is not None
    expected_focus = {
        "item_id": item_id, "requirement_id": focus_requirement["requirement_id"],
        "result_id": focus_result["result_id"],
    }
    assert {
        "item_id": snapshot.change_focus.item_id,
        "requirement_id": snapshot.change_focus.requirement_id,
        "result_id": snapshot.change_focus.result_id,
    } == expected_focus
    request_id = project_scoped_snapshot_request_id(snapshot)
    outbox = ProjectScopedCardOutbox(session, store)
    outbox.record_update("synthetic-alpha", request_id, window_seconds=30.0)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

    published = _publish_current_dialogue_project_work_card(
        repl, bridge, session, store, "synthetic-alpha",
    )

    assert published is False
    assert not any(event.get("type") == "work-card.present" for event in events)
    assert outbox.list_intents()[0]["state"] == "pending"


def test_project_scoped_saved_card_is_retried_after_reference_emit_failure(tmp_path, monkeypatch):
    from server import _emit_pending_work_card_references, _publish_current_dialogue_project_work_card, _resume_current_dialogue_project_work_cards
    from work_card_project_scope import project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from work_records import WorkRecordStore

    session, store, item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    focus_requirement = store.add_requirement(
        item_id, "persisted focus receipt", event_id="focus-receipt-saved",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    focus_result = _atomic_worker_result(
        session, store, item_id, focus_requirement["requirement_id"], window_seconds=0.001,
    )
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert snapshot.change_focus is not None
    expected_focus = {
        "item_id": item_id, "requirement_id": focus_requirement["requirement_id"],
        "result_id": focus_result["result_id"],
    }
    request_id = project_scoped_snapshot_request_id(snapshot)
    outbox = ProjectScopedCardOutbox(session, store)
    outbox.record_update("synthetic-alpha", request_id, window_seconds=0.001)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    import time
    time.sleep(0.01)
    monkeypatch.setattr("server._emit_work_card_reference", lambda *_args, **_kwargs: False)

    assert _publish_current_dialogue_project_work_card(
        repl, bridge, session, store, "synthetic-alpha",
    ) is False
    saved_intent = outbox.list_intents()[0]
    assert saved_intent["state"] == "card_saved"
    saved_record = WorkCardLedger(session, store).get_by_request_id(request_id)
    assert saved_record["snapshot"]["change_focus"] == expected_focus
    assert not any(event.get("type") == "work-card.present" for event in events)
    monkeypatch.undo()

    resumed_events = []
    resumed_bridge = WsBridge(emit=resumed_events.append, paths=session.paths)
    resumed_store = WorkRecordStore(session)
    assert _resume_current_dialogue_project_work_cards(
        repl, resumed_bridge, session, resumed_store,
    ) == 1
    assert _emit_pending_work_card_references(repl, resumed_bridge) == 1
    replayed_card = next(event["card"] for event in resumed_events
                         if event.get("type") == "work-card.present")
    assert replayed_card["snapshot"]["change_focus"] == expected_focus
    assert ProjectScopedCardOutbox(session, resumed_store).list_intents()[0]["card_id"] == saved_intent["card_id"]
    assert len(WorkCardLedger(session, resumed_store).list_cards()) == 1


def test_project_scoped_expired_persisted_deadline_publishes_on_reconnect(tmp_path):
    from server import _resume_current_dialogue_project_work_cards
    from work_card_project_scope import project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from work_records import WorkRecordStore
    import time

    session, store, _item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    request_id = project_scoped_snapshot_request_id(snapshot)
    ProjectScopedCardOutbox(session, store).record_update(
        "synthetic-alpha", request_id, window_seconds=30.0,
        now_monotonic=time.monotonic() - 31.0, now_wall=time.time() - 31.0,
    )
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def reconnect():
        result = _resume_current_dialogue_project_work_cards(
            repl, bridge, session, WorkRecordStore(session),
        )
        await asyncio.sleep(0.02)
        return result
    try:
        recovered = loop.run_until_complete(reconnect())
    finally:
        if bridge._work_card_update_coalescer is not None:
            bridge._work_card_update_coalescer.close()
        loop.close()
        asyncio.set_event_loop(None)

    assert recovered >= 1
    assert any(event.get("type") == "work-card.present"
               and event.get("card", {}).get("snapshot", {}).get("project_id") == "synthetic-alpha"
               for event in events)
    if bridge._work_card_update_coalescer is not None:
        bridge._work_card_update_coalescer.close()


def test_current_dialogue_parent_run_skips_pending_requirement_before_completed_result(tmp_path):
    from server import _current_dialogue_card_parent_run_id

    session, store = _session(tmp_path)
    item = store.create_item(title="mixed project batch", scope={"kind": "independent"})
    pending = store.add_requirement(
        item["item_id"], "queued alpha task", event_id="queued-first",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    _add_task(store, item["item_id"], "synthetic-alpha", "finished alpha task", "finished-after-pending")
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")

    assert snapshot.completeness == "complete"
    assert snapshot.cards[0].requirements[0].requirement_id == pending["requirement_id"]
    assert _current_dialogue_card_parent_run_id(snapshot) == _PARENT_RUN_ID


def test_pending_without_parent_retries_on_reconnect(tmp_path, monkeypatch):
    import time
    from server import _current_dialogue_card_parent_run_id, _publish_current_dialogue_project_work_card, _resume_current_dialogue_project_work_cards
    from work_card_project_scope import project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from work_records import WorkRecordStore

    session, store, _item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    request_id = project_scoped_snapshot_request_id(snapshot)
    outbox = ProjectScopedCardOutbox(session, store)
    outbox.record_update("synthetic-alpha", request_id, window_seconds=0.001)
    time.sleep(0.01)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    monkeypatch.setattr("server._current_dialogue_card_parent_run_id", lambda _snapshot: None)

    assert _publish_current_dialogue_project_work_card(repl, bridge, session, store, "synthetic-alpha") is False
    intent = outbox.list_intents()[0]
    assert intent["state"] == "pending"  # no card is advanced without a bindable host run
    assert len(WorkCardLedger(session, store).list_cards()) == 0
    assert not any(event.get("type") == "work-card.present" for event in events)

    monkeypatch.setattr("server._current_dialogue_card_parent_run_id", _current_dialogue_card_parent_run_id)
    resumed_bridge = WsBridge(emit=events.append, paths=session.paths)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def reconnect():
        result = _resume_current_dialogue_project_work_cards(
            repl, resumed_bridge, session, WorkRecordStore(session),
        )
        await asyncio.sleep(0.02)
        return result
    try:
        assert loop.run_until_complete(reconnect()) == 2
    finally:
        if resumed_bridge._work_card_update_coalescer is not None:
            resumed_bridge._work_card_update_coalescer.close()
        loop.close()
        asyncio.set_event_loop(None)
    assert len([event for event in events if event.get("type") == "work-card.present"]) == 1
    assert outbox.list_intents()[0]["state"] == "reference_queued"


def test_crash_after_immutable_ledger_save_recovers_same_card_before_delivery(tmp_path, monkeypatch):
    import time
    from server import _emit_pending_work_card_references, _publish_current_dialogue_project_work_card, _resume_current_dialogue_project_work_cards
    from work_card_project_scope import build_project_scoped_work_record_cards, project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from work_records import WorkRecordStore

    session, store, item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    focus_requirement = store.add_requirement(
        item_id, "crash-recovery focus", event_id="crash-focus-receipt",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    focus_result = _atomic_worker_result(
        session, store, item_id, focus_requirement["requirement_id"], window_seconds=0.001,
    )
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    expected_focus = {
        "item_id": item_id, "requirement_id": focus_requirement["requirement_id"],
        "result_id": focus_result["result_id"],
    }
    request_id = project_scoped_snapshot_request_id(snapshot)
    ProjectScopedCardOutbox(session, store).record_update("synthetic-alpha", request_id, window_seconds=0.001)
    time.sleep(0.01)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    original_mark = ProjectScopedCardOutbox.mark_card_saved
    monkeypatch.setattr(ProjectScopedCardOutbox, "mark_card_saved", lambda *_args, **_kwargs: "uncertain")

    assert _publish_current_dialogue_project_work_card(
        repl, bridge, session, store, "synthetic-alpha",
    ) is False
    pending = ProjectScopedCardOutbox(session, store).list_intents()[0]
    assert pending["state"] == "pending"
    persisted = WorkCardLedger(session, store).get_by_request_id(request_id)
    assert persisted["snapshot"]["change_focus"] == expected_focus
    assert len(WorkCardLedger(session, store).list_cards()) == 1
    assert not any(event.get("type") == "work-card.present" for event in events)

    monkeypatch.setattr(ProjectScopedCardOutbox, "mark_card_saved", original_mark)
    resumed_bridge = WsBridge(emit=events.append, paths=session.paths)
    assert _resume_current_dialogue_project_work_cards(
        repl, resumed_bridge, session, WorkRecordStore(session),
    ) == 1
    assert _emit_pending_work_card_references(repl, resumed_bridge) == 1
    cards = [event for event in events if event.get("type") == "work-card.present"]
    assert len(cards) == 1
    assert cards[0]["card"]["snapshot"]["change_focus"] == expected_focus
    assert len(WorkCardLedger(session, WorkRecordStore(session)).list_cards()) == 1
    assert ProjectScopedCardOutbox(session, store).list_intents()[0]["card_id"] == cards[0]["card_id"]


def test_card_saved_intent_retries_same_card_without_being_treated_as_delivered(tmp_path, monkeypatch):
    import time
    import server
    from server import _publish_current_dialogue_project_work_card
    from work_card_project_scope import build_project_scoped_work_record_cards, project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store, _item_id, _alpha_req, _beta_req = _source_fixture(tmp_path)
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    request_id = project_scoped_snapshot_request_id(snapshot)
    outbox = ProjectScopedCardOutbox(session, store)
    outbox.record_update("synthetic-alpha", request_id, window_seconds=0.001)
    time.sleep(0.01)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    original_emit = server._emit_work_card_reference
    attempts = {"count": 0}
    def fail_once(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return False
        return original_emit(*args, **kwargs)
    monkeypatch.setattr(server, "_emit_work_card_reference", fail_once)

    assert _publish_current_dialogue_project_work_card(repl, bridge, session, store, "synthetic-alpha") is False
    saved = outbox.list_intents()[0]
    assert saved["state"] == "card_saved"
    assert _publish_current_dialogue_project_work_card(repl, bridge, session, store, "synthetic-alpha") is True

    current = outbox.list_intents()[0]
    cards = [event for event in events if event.get("type") == "work-card.present"]
    assert current["state"] == "reference_queued"  # durable history queue is not a client delivery receipt
    assert current["generation"] == saved["generation"]
    assert current["card_id"] == saved["card_id"]
    assert len(cards) == 1
    assert len(WorkCardLedger(session, store).list_cards()) == 1


def test_new_project_update_does_not_overwrite_unqueued_saved_card_intent(tmp_path):
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store = _session(tmp_path)
    item = store.create_item(title="saved card source", scope={"kind": "independent"})
    store.add_requirement(
        item["item_id"], "alpha pending", event_id="saved-card-alpha-pending",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    outbox = ProjectScopedCardOutbox(session, store)
    first_request = str(uuid.uuid4())
    first = outbox.record_update("synthetic-alpha", first_request)
    card_id = "wc-" + hashlib.sha256(
        f"{session.conversation_id}\0{first_request}".encode("utf-8"),
    ).hexdigest()
    assert outbox.mark_card_saved(
        project_id="synthetic-alpha", generation=first["generation"],
        request_id=first_request, card_id=card_id,
    ) == "saved"

    second = outbox.record_update("synthetic-alpha", str(uuid.uuid4()))
    rows = outbox.list_intents()

    assert len(rows) == 2
    assert next(row for row in rows if row["generation"] == first["generation"])["state"] == "card_saved"
    assert second["state"] == "pending"
    assert second["generation"] > first["generation"]


def _atomic_worker_result(session, store, item_id, requirement_id, project_id="synthetic-alpha", window_seconds=30.0):
    task_id = "task-" + hashlib.sha256(
        "\0".join((session.conversation_id, item_id, requirement_id, project_id)).encode("utf-8")
    ).hexdigest()
    run_id = "worker-run-atomic"
    store.begin_requirement_run(item_id, requirement_id, run_id=run_id, expected_current_run_id=None)
    receipt = {
        "kind": "project_worker_host_lifecycle", "result_status": "unknown",
        "project_id": project_id, "task_id": task_id, "worker_id": "worker-atomic",
        "worker_run_id": run_id, "reservation_id": "reservation-atomic",
        "parent_run_id": _PARENT_RUN_ID, "parent_epoch": 3,
        "host_status": "model_turn_finished", "acceptance_status": "not_run",
        "write_receipts_available": False, "write_receipts": [],
    }
    result_id = "worker-result-" + hashlib.sha256(
        "\0".join((session.conversation_id, item_id, requirement_id, run_id)).encode("utf-8")
    ).hexdigest()
    return store.record_project_scoped_result_if_current_run(
        item_id, requirement_id, run_id=run_id, result_id=result_id,
        status="unknown", summary=json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        verification={"status": "not_verified", "evidence_refs": [],
                      "host_execution": {"sequence": 3, "finish_reason": "completed"}},
        sources=[], project_id=project_id, publication_window_seconds=window_seconds,
    )


def test_trusted_worker_result_and_project_pending_commit_atomically(tmp_path):
    from work_card_project_scope import project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store = _session(tmp_path)
    item = store.create_item(title="atomic project work", scope={"kind": "independent"})
    first_req = store.add_requirement(
        item["item_id"], "atomic alpha result one", event_id="atomic-requirement-one",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    second_req = store.add_requirement(
        item["item_id"], "atomic alpha result two", event_id="atomic-requirement-two",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )

    first = _atomic_worker_result(session, store, item["item_id"], first_req["requirement_id"])
    first_intent = ProjectScopedCardOutbox(session, store).list_intents()[0]
    second = _atomic_worker_result(session, store, item["item_id"], second_req["requirement_id"])
    second_intent = ProjectScopedCardOutbox(session, store).list_intents()[0]
    replay = _atomic_worker_result(session, store, item["item_id"], first_req["requirement_id"])

    assert first["result_id"] == replay["result_id"]
    assert len(store.get_item(item["item_id"])["runs"]) == 2
    assert len(ProjectScopedCardOutbox(session, store).list_intents()) == 1
    assert second_intent["state"] == "pending"
    assert second_intent["window_started_monotonic"] == first_intent["window_started_monotonic"]
    assert set(second_intent["source_result_ids"]) == {first["result_id"], second["result_id"]}
    latest_snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert second_intent["request_id"] == project_scoped_snapshot_request_id(latest_snapshot)
    assert latest_snapshot.change_focus is not None
    assert (latest_snapshot.change_focus.item_id, latest_snapshot.change_focus.requirement_id,
            latest_snapshot.change_focus.result_id) == (
                item["item_id"], second_req["requirement_id"], second["result_id"],
            )
    assert second_intent["latest_run_id"] == "worker-run-atomic"
    assert ProjectScopedCardOutbox(session, store).list_intents() == [second_intent]


def test_stale_project_publisher_cannot_save_old_snapshot_after_new_merge(tmp_path, monkeypatch):
    from server import _publish_current_dialogue_project_work_card
    from work_card_ledger import WorkCardLedger
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store = _session(tmp_path)
    item = store.create_item(title="publisher race", scope={"kind": "independent"})
    req_a = store.add_requirement(
        item["item_id"], "publisher A", event_id="publisher-a",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    req_b = store.add_requirement(
        item["item_id"], "publisher B", event_id="publisher-b",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    _atomic_worker_result(session, store, item["item_id"], req_a["requirement_id"], window_seconds=0.001)
    original_record_update = ProjectScopedCardOutbox.record_update
    injected = {"done": False}

    def merge_before_stale_enqueue(outbox, project_id, request_id, **kwargs):
        if not injected["done"]:
            injected["done"] = True
            _atomic_worker_result(
                session, store, item["item_id"], req_b["requirement_id"], window_seconds=0.001,
            )
        return original_record_update(outbox, project_id, request_id, **kwargs)

    monkeypatch.setattr(ProjectScopedCardOutbox, "record_update", merge_before_stale_enqueue)
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))

    published = _publish_current_dialogue_project_work_card(
        repl, bridge, session, store, "synthetic-alpha",
    )

    latest = ProjectScopedCardOutbox(session, store).list_intents()[0]
    assert injected["done"] is True
    assert published is False
    assert latest["latest_requirement_id"] == req_b["requirement_id"]
    assert latest["latest_run_id"] == "worker-run-atomic"
    assert WorkCardLedger(session, store).list_cards() == []
    assert not any(event.get("type") == "work-card.present" for event in events)


def test_replaying_old_settlement_does_not_rewrite_legacy_pending_intent(tmp_path):
    from dataclasses import replace
    from work_card_project_scope import build_project_scoped_work_record_cards, project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store = _session(tmp_path)
    item = store.create_item(title="legacy intent replay", scope={"kind": "independent"})
    req_a = store.add_requirement(
        item["item_id"], "legacy A", event_id="legacy-a",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    req_b = store.add_requirement(
        item["item_id"], "legacy B", event_id="legacy-b",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    _atomic_worker_result(session, store, item["item_id"], req_a["requirement_id"])
    _atomic_worker_result(session, store, item["item_id"], req_b["requirement_id"])
    outbox = ProjectScopedCardOutbox(session, store)
    current = outbox.list_intents()[0]
    base_snapshot = replace(
        build_project_scoped_work_record_cards(store, "synthetic-alpha"), change_focus=None,
    )
    legacy_request = project_scoped_snapshot_request_id(base_snapshot)

    def downgrade_to_legacy_intent(data):
        row = next(row for row in data["project_scoped_card_intents"]
                   if row["project_id"] == "synthetic-alpha")
        for key in ("source_result_ids", "latest_item_id", "latest_requirement_id",
                    "latest_run_id", "latest_result_id"):
            row.pop(key, None)
        row["request_id"] = legacy_request
        return row, True
    store._mutate(downgrade_to_legacy_intent)
    legacy_before = outbox.list_intents()[0]
    assert legacy_before["request_id"] == legacy_request
    assert "latest_result_id" not in legacy_before

    _atomic_worker_result(session, store, item["item_id"], req_a["requirement_id"])

    assert outbox.list_intents() == [legacy_before]


def test_old_project_scoped_request_cannot_rewind_or_save_over_newer_receipt(tmp_path):
    from work_card_ledger import WorkCardLedger
    from work_card_project_scope import build_project_scoped_work_record_cards, project_scoped_snapshot_request_id
    from work_card_publish_outbox import ProjectScopedCardOutbox

    session, store = _session(tmp_path)
    item = store.create_item(title="generation race", scope={"kind": "independent"})
    req_a = store.add_requirement(
        item["item_id"], "generation A", event_id="generation-a",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    req_b = store.add_requirement(
        item["item_id"], "generation B", event_id="generation-b",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    _atomic_worker_result(session, store, item["item_id"], req_a["requirement_id"])
    outbox = ProjectScopedCardOutbox(session, store)
    old_intent = outbox.list_intents()[0]
    old_snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert old_snapshot.change_focus is not None
    old_request = project_scoped_snapshot_request_id(old_snapshot)
    assert old_request == old_intent["request_id"]

    _atomic_worker_result(session, store, item["item_id"], req_b["requirement_id"])
    current = outbox.list_intents()[0]
    assert current["request_id"] != old_request
    assert current["generation"] == old_intent["generation"]

    stale_retry = outbox.record_update("synthetic-alpha", old_request)
    ledger = WorkCardLedger(session, store)
    stale_save = ledger.save_project_scoped_snapshot(
        old_request, old_snapshot, generation=old_intent["generation"],
    )
    current_snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    current_request = project_scoped_snapshot_request_id(current_snapshot)
    stale_generation_save = ledger.save_project_scoped_snapshot(
        current_request, current_snapshot, generation=current["generation"] + 1,
    )

    assert stale_retry == current
    assert outbox.list_intents() == [current]
    assert stale_save.status == "rejected"
    assert stale_generation_save.status == "rejected"
    assert ledger.list_cards() == []


def test_atomic_result_commit_failure_leaves_neither_result_nor_project_intent(tmp_path, monkeypatch):
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from work_records import WorkRecordWriteError

    session, store = _session(tmp_path)
    item = store.create_item(title="atomic failure", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "atomic failure result", event_id="atomic-failure-requirement",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"],
                                run_id="worker-run-atomic", expected_current_run_id=None)
    # Install the full current-run receipt without allowing the sidecar replace.
    task_id = "task-" + hashlib.sha256(
        "\0".join((session.conversation_id, item["item_id"], requirement["requirement_id"],
                    "synthetic-alpha")).encode("utf-8")
    ).hexdigest()
    summary = json.dumps({
        "kind": "project_worker_host_lifecycle", "result_status": "unknown",
        "project_id": "synthetic-alpha", "task_id": task_id, "worker_id": "worker-atomic",
        "worker_run_id": "worker-run-atomic", "reservation_id": "reservation-atomic",
        "parent_run_id": _PARENT_RUN_ID, "parent_epoch": 3, "host_status": "model_turn_finished",
        "acceptance_status": "not_run", "write_receipts_available": False, "write_receipts": [],
    }, sort_keys=True, separators=(",", ":"))
    result_id = "worker-result-" + hashlib.sha256(
        "\0".join((session.conversation_id, item["item_id"], requirement["requirement_id"],
                    "worker-run-atomic")).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(store, "_publish", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected atomic replace failure")))

    with pytest.raises(WorkRecordWriteError):
        store.record_project_scoped_result_if_current_run(
            item["item_id"], requirement["requirement_id"], run_id="worker-run-atomic",
            result_id=result_id, status="unknown", summary=summary,
            verification={"status": "not_verified", "evidence_refs": [],
                          "host_execution": {"sequence": 3, "finish_reason": "completed"}},
            sources=[], project_id="synthetic-alpha",
        )

    monkeypatch.undo()
    assert store.get_item(item["item_id"])["runs"] == []
    assert ProjectScopedCardOutbox(session, store).list_intents() == []


def test_project_scoped_focus_end_to_end_two_projects_merge_ledger_and_transport_replay(tmp_path):
    from server import _emit_pending_work_card_references, _publish_current_dialogue_project_work_card
    from work_card_ledger import WorkCardLedger
    from work_card_project_scope import build_project_scoped_work_record_cards
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from session import Session
    from work_records import WorkRecordStore

    session, store, item_id, alpha_req, beta_req = _source_fixture(tmp_path)
    ledger = WorkCardLedger(session, store)

    # Preserve a legacy no-focus card before new CAS-backed updates arrive.
    legacy_snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")
    assert legacy_snapshot.change_focus is None
    legacy = ledger.save_project_scoped_snapshot(str(uuid.uuid4()), legacy_snapshot)
    assert legacy.status == "saved"
    legacy_bytes = json.dumps(legacy.record["snapshot"], sort_keys=True)

    alpha_second = store.add_requirement(
        item_id, "Alpha second change", event_id="alpha-second-change",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    alpha_first_result = _atomic_worker_result(
        session, store, item_id, alpha_second["requirement_id"], window_seconds=0.001,
    )
    alpha_latest = store.add_requirement(
        item_id, "Alpha latest change", event_id="alpha-latest-change",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    alpha_latest_result = _atomic_worker_result(
        session, store, item_id, alpha_latest["requirement_id"], window_seconds=0.001,
    )
    beta_second = store.add_requirement(
        item_id, "Beta second change", event_id="beta-second-change",
        source_ref={"kind": "project", "id": "synthetic-beta"},
    )
    beta_first_result = _atomic_worker_result(
        session, store, item_id, beta_second["requirement_id"], project_id="synthetic-beta",
        window_seconds=0.001,
    )
    beta_latest = store.add_requirement(
        item_id, "Beta latest change", event_id="beta-latest-change",
        source_ref={"kind": "project", "id": "synthetic-beta"},
    )
    beta_latest_result = _atomic_worker_result(
        session, store, item_id, beta_latest["requirement_id"], project_id="synthetic-beta",
        window_seconds=0.001,
    )

    outbox = ProjectScopedCardOutbox(session, store)
    intents = {row["project_id"]: row for row in outbox.list_intents()}
    assert set(intents) == {"synthetic-alpha", "synthetic-beta"}
    assert intents["synthetic-alpha"]["generation"] == intents["synthetic-beta"]["generation"] == 1
    assert set(intents["synthetic-alpha"]["source_result_ids"]) == {
        alpha_first_result["result_id"], alpha_latest_result["result_id"],
    }
    assert set(intents["synthetic-beta"]["source_result_ids"]) == {
        beta_first_result["result_id"], beta_latest_result["result_id"],
    }

    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    assert _publish_current_dialogue_project_work_card(
        repl, bridge, session, store, "synthetic-alpha",
    ) is True
    assert _publish_current_dialogue_project_work_card(
        repl, bridge, session, store, "synthetic-beta",
    ) is True

    first_events = {event["card"]["snapshot"]["project_id"]: event
                    for event in events if event.get("type") == "work-card.present"}
    assert set(first_events) == {"synthetic-alpha", "synthetic-beta"}
    expected_focus = {
        "synthetic-alpha": {
            "item_id": item_id, "requirement_id": alpha_latest["requirement_id"],
            "result_id": alpha_latest_result["result_id"],
        },
        "synthetic-beta": {
            "item_id": item_id, "requirement_id": beta_latest["requirement_id"],
            "result_id": beta_latest_result["result_id"],
        },
    }
    for project_id, event in first_events.items():
        snapshot = event["card"]["snapshot"]
        assert snapshot["change_focus"] == expected_focus[project_id]
        assert set(row["source_ref"]["id"] for card in snapshot["cards"]
                   for row in card["requirements"] if row["source_ref"] is not None) == {project_id}
        assert len(snapshot["cards"][0]["requirements"]) == 3
        assert outbox.list_intents()[0]  # each intent remains durable through publication/retry
        saved = ledger.get_card(event["card_id"])
        assert saved["snapshot"] == snapshot

    # The old immutable card remains byte-for-byte focus-free after both updates.
    legacy_after = ledger.get_card(legacy.card_id)
    assert json.dumps(legacy_after["snapshot"], sort_keys=True) == legacy_bytes
    assert "change_focus" not in legacy_after["snapshot"]

    # Simulated reconnect replays durable references and their exact immutable snapshots.
    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    replay_events = []
    replay_bridge = WsBridge(emit=replay_events.append, paths=restored.paths)
    replay_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    replay_count = _emit_pending_work_card_references(replay_repl, replay_bridge)
    replayed = {event["card"]["snapshot"]["project_id"]: event
                for event in replay_events if event.get("type") == "work-card.present"}
    assert replay_count == 2
    assert set(replayed) == set(first_events)
    for project_id in first_events:
        assert replayed[project_id]["card_id"] == first_events[project_id]["card_id"]
        assert replayed[project_id]["card"]["snapshot"] == first_events[project_id]["card"]["snapshot"]
    assert WorkCardLedger(restored, restored_store).get_card(legacy.card_id)["snapshot"] == legacy_after["snapshot"]

    # A different parent Session with the same project name cannot read these cards/intents.
    other_id = str(uuid.uuid4())
    other_dir = session.paths.data / "sessions" / other_id
    other_dir.mkdir(parents=True)
    other = Session(other_id, other_dir, "", SessionMeta(), [], session.paths)
    other.save()
    other_store = WorkRecordStore(other)
    assert WorkCardLedger(other, other_store).list_cards() == []
    assert ProjectScopedCardOutbox(other, other_store).list_intents() == []


def test_atomic_project_intent_recovers_after_restart_before_server_schedule(tmp_path, monkeypatch):
    from server import _resume_current_dialogue_project_work_cards
    from work_card_publish_outbox import ProjectScopedCardOutbox
    from work_records import WorkRecordStore

    session, store = _session(tmp_path)
    item = store.create_item(title="atomic restart", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "persisted before server callback", event_id="atomic-restart-requirement",
        source_ref={"kind": "project", "id": "synthetic-alpha"},
    )
    monkeypatch.setattr("server.PROJECT_WORK_CARD_MERGE_SECONDS", 0.08)

    # This is the agent boundary: result and pending projection intent commit together.
    saved_run = _atomic_worker_result(
        session, store, item["item_id"], requirement["requirement_id"], window_seconds=0.08,
    )
    before = ProjectScopedCardOutbox(session, store).list_intents()[0]
    events = []
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    bridge = WsBridge(emit=events.append, paths=session.paths)
    resumed_store = WorkRecordStore(session)

    async def restart_reconnect():
        scheduled = _resume_current_dialogue_project_work_cards(repl, bridge, session, resumed_store)
        await asyncio.sleep(0.12)
        return scheduled
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        scheduled = loop.run_until_complete(restart_reconnect())
    finally:
        if bridge._work_card_update_coalescer is not None:
            bridge._work_card_update_coalescer.close()
        loop.close()
        asyncio.set_event_loop(None)

    after = ProjectScopedCardOutbox(session, resumed_store).list_intents()[0]
    cards = [event for event in events if event.get("type") == "work-card.present"]
    assert scheduled == 1
    assert after["window_started_monotonic"] == before["window_started_monotonic"]
    assert after["source_result_ids"] == [saved_run["result_id"]]
    assert after["state"] == "reference_queued"
    assert len(cards) == 1 and cards[0]["card"]["snapshot"]["project_id"] == "synthetic-alpha"
    assert len(resumed_store.get_item(item["item_id"])["runs"]) == 1  # reconnect did not rerun worker


def test_project_scoped_receipt_accepts_canonical_compact_parent_run_id(tmp_path):
    session, store = _session(tmp_path)
    item = store.create_item(title="compact parent run", scope={"kind": "independent"})
    compact_parent = _PARENT_RUN_ID.replace("-", "")
    _add_task(store, item["item_id"], "synthetic-alpha", "alpha result", "compact-parent",
              parent_run_id=compact_parent)
    snapshot = build_project_scoped_work_record_cards(store, "synthetic-alpha")

    assert snapshot.completeness == "complete"
    from server import _current_dialogue_card_parent_run_id
    assert _current_dialogue_card_parent_run_id(snapshot) == compact_parent


@pytest.mark.parametrize("attempts, expected", [
    ((ProjectReadAttempt(1, "docs/a.md", 1, "retrying", "sharing_violation"),
      ProjectReadAttempt(1, "docs/a.md", 1, "cancelled")),
     (True, [{"read_id": 1, "path": "docs/a.md", "attempt": 1,
              "result": "retrying", "error_code": "sharing_violation"},
             {"read_id": 1, "path": "docs/a.md", "attempt": 1,
              "result": "cancelled", "error_code": ""}])),
    ((ProjectReadAttempt(1, "docs/a.md", 1, "success"),),
     (True, [{"read_id": 1, "path": "docs/a.md", "attempt": 1,
              "result": "success", "error_code": ""}])),
    ((), (True, [])),
    ((ProjectReadAttempt(1, "../secret", 1, "retrying"),), (False, [])),
    ((ProjectReadAttempt(1, "docs/a.md", 1, "retrying", "SECRET BODY"),), (False, [])),
    ((ProjectReadAttempt(1, "docs/a.md", 2, "failed", "sharing_violation"),), (False, [])),
    ((ProjectReadAttempt(1, "docs/a.md", 1, "failed", "sharing_violation"),),
     (True, [{"read_id": 1, "path": "docs/a.md", "attempt": 1,
              "result": "failed", "error_code": "sharing_violation"}])),
    ((ProjectReadAttempt(1, "docs/a.md", 1, "retrying", "sharing_violation"),
      ProjectReadAttempt(1, "docs/a.md", 1, "failed", "sharing_violation")), (False, [])),
])
def test_project_worker_read_attempt_serializer_is_bounded_and_content_free(attempts, expected):
    from types import SimpleNamespace
    from agent import _serialize_project_worker_read_attempts

    assert _serialize_project_worker_read_attempts(SimpleNamespace(read_attempts=attempts)) == expected
    assert _serialize_project_worker_read_attempts(SimpleNamespace()) == (False, [])

def test_project_scoped_rerun_hides_old_result_but_saves_truthful_history_counts(tmp_path):
    from dataclasses import asdict
    from work_card_ledger import WorkCardLedgerCorrupt, _validate_project_scoped_payload

    session, store, item_id, alpha_req, _ = _source_fixture(tmp_path)
    store.begin_requirement_run(item_id, alpha_req, run_id='synthetic-new-run', expected_current_run_id='worker-run-alpha')
    snapshot = build_project_scoped_work_record_cards(store, 'synthetic-alpha')
    requirement = snapshot.cards[0].requirements[0]
    assert snapshot.completeness == 'complete' and snapshot.history_complete is False
    assert requirement.status == 'in_progress' and requirement.current_run_id == 'synthetic-new-run'
    assert requirement.latest_result is None and requirement.latest_result_is_current is False
    assert requirement.result_count == requirement.omitted_result_count == 1
    assert snapshot.result_count == snapshot.omitted_result_count == 1
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_project_scoped_snapshot(str(uuid.uuid4()), snapshot)
    assert saved.status == 'saved'
    assert WorkCardLedger(session, store).get_card(saved.card_id)['snapshot']['cards'][0]['requirements'][0]['latest_result'] is None
    for field, bad in [('omitted_result_count', 0), ('latest_result_is_current', True), ('project_id', 'synthetic-beta')]:
        payload = asdict(snapshot)
        payload.pop('change_focus', None)
        payload['cards'][0]['requirements'][0][field] = bad
        with pytest.raises(WorkCardLedgerCorrupt):
            _validate_project_scoped_payload(payload, store=store, session_id=session.conversation_id)

