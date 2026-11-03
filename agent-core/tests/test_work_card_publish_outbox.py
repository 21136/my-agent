from __future__ import annotations

import sys
import uuid
from dataclasses import replace
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import pytest
import work_card_publish_outbox
from paths import AgentPaths
from session import Session, SessionMeta
from work_card_publish_outbox import WorkCardPublicationOutbox, WorkCardPublishOutboxError
from work_card_ledger import WorkCardLedger
from work_record_cards import WorkCardChangeFocus, build_project_work_record_cards
from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter
from work_records import WorkRecordStore


def _settled_project(tmp_path):
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                       data=tmp_path / "data", evolve=tmp_path / "evolve")
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(conversation_id=session_id, session_dir=session_dir, goal="",
                      meta=SessionMeta(project_id="project-a"), messages=[], paths=paths)
    session.save()
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic project task",
                             scope={"kind": "project", "project_id": "project-a"})
    first = store.add_requirement(item["item_id"], "First requirement", event_id="first-source")
    second = store.add_requirement(item["item_id"], "Second requirement", event_id="second-source")
    return session, store, item["item_id"], first["requirement_id"], second["requirement_id"]


def _settle(store, item_id, requirement_id, run_id):
    store.begin_requirement_run(item_id, requirement_id, run_id=run_id,
                                expected_current_run_id=None)
    binding = BoundExecution(store._session.conversation_id, item_id, requirement_id, run_id)
    receipt = ExecutionReceipt(
        session_id=binding.session_id, item_id=item_id, requirement_id=requirement_id,
        run_id=run_id, sequence=1, outcome="succeeded", finish_reason="completed",
    )
    return WorkRecordSettlementAdapter(store, binding).settle(
        receipt, publish_project_update=True,
    )


def test_pending_intent_survives_reopen_and_same_run_retries_are_idempotent(tmp_path):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    run_id = uuid.uuid4().hex
    assert _settle(store, item_id, requirement_id, run_id).status == "recorded"
    outbox = WorkCardPublicationOutbox(session, store)
    first = outbox.list_pending()[0]

    assert _settle(store, item_id, requirement_id, run_id).status == "recorded"
    retry = outbox.list_pending()[0]
    restored = Session.load(session.paths, session.conversation_id)
    reopened = WorkCardPublicationOutbox(restored, WorkRecordStore(restored))

    assert first["intent_id"] == retry["intent_id"]
    assert first["generation"] == retry["generation"] == 1
    assert len(reopened.list_pending()) == 1
    assert reopened.list_pending()[0] == first


def test_same_project_settlements_merge_under_first_intent_and_keep_latest_run(tmp_path):
    session, store, item_id, first_req, second_req = _settled_project(tmp_path)
    first_run, second_run = uuid.uuid4().hex, uuid.uuid4().hex
    assert _settle(store, item_id, first_req, first_run).status == "recorded"
    outbox = WorkCardPublicationOutbox(session, store)
    first = outbox.list_pending()[0]

    assert _settle(store, item_id, second_req, second_run).status == "recorded"
    latest = outbox.list_pending()[0]

    assert latest["intent_id"] == first["intent_id"]
    assert latest["first_run_id"] == first_run
    assert latest["latest_run_id"] == second_run
    assert latest["latest_item_id"] == item_id
    assert latest["latest_requirement_id"] == second_req
    assert latest["generation"] == first["generation"] + 1
    assert latest["request_id"] != first["request_id"]
    assert latest["deadline_monotonic"] == first["deadline_monotonic"]

    assert _settle(store, item_id, first_req, first_run).status == "recorded"
    assert outbox.list_pending() == [latest]


def test_noncurrent_server_receipt_cannot_create_another_intent(tmp_path):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    current_run = uuid.uuid4().hex
    assert _settle(store, item_id, requirement_id, current_run).status == "recorded"
    outbox = WorkCardPublicationOutbox(session, store)
    prior = outbox.list_pending()[0]
    stale_run = uuid.uuid4().hex
    stale = WorkRecordSettlementAdapter(
        store, BoundExecution(session.conversation_id, item_id, requirement_id, stale_run),
    ).settle(
        ExecutionReceipt(
            session_id=session.conversation_id, item_id=item_id,
            requirement_id=requirement_id, run_id=stale_run, sequence=2,
            outcome="succeeded", finish_reason="completed",
        ),
        publish_project_update=True,
    )
    assert stale.status == "rejected"
    assert outbox.list_pending() == [prior]


def test_outbox_cannot_be_read_under_another_project_binding(tmp_path):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    run_id = uuid.uuid4().hex
    assert _settle(store, item_id, requirement_id, run_id).status == "recorded"

    foreign = Session(
        conversation_id=session.conversation_id, session_dir=session.session_dir, goal="",
        meta=SessionMeta(project_id="project-b"), messages=[], paths=session.paths,
    )
    foreign_store = WorkRecordStore(foreign)
    foreign_outbox = WorkCardPublicationOutbox(foreign, foreign_store)
    with pytest.raises(WorkCardPublishOutboxError):
        foreign_outbox.list_pending()


def test_other_session_with_same_project_has_no_access_to_pending_intent(tmp_path):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    run_id = uuid.uuid4().hex
    assert _settle(store, item_id, requirement_id, run_id).status == "recorded"
    assert len(WorkCardPublicationOutbox(session, store).list_pending()) == 1

    other_id = str(uuid.uuid4())
    other_dir = session.paths.data / "sessions" / other_id
    other_dir.mkdir(parents=True)
    other = Session(conversation_id=other_id, session_dir=other_dir, goal="",
                    meta=SessionMeta(project_id="project-a"), messages=[], paths=session.paths)
    other.save()
    other_store = WorkRecordStore(other)

    assert WorkCardPublicationOutbox(other, other_store).list_pending() == []


def test_restarted_process_flushes_pending_intent_instead_of_restarting_its_window(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    run_id = uuid.uuid4().hex
    assert _settle(store, item_id, requirement_id, run_id).status == "recorded"
    outbox = WorkCardPublicationOutbox(session, store)
    intent = outbox.list_pending()[0]

    import work_records
    monkeypatch.setattr(work_records, "_PUBLICATION_PROCESS_INSTANCE_ID", str(uuid.uuid4()))

    assert outbox.remaining_seconds(intent) == 0


def test_published_intent_deduplicates_repeated_settlement_after_clear(tmp_path):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    run_id = uuid.uuid4().hex
    assert _settle(store, item_id, requirement_id, run_id).status == "recorded"
    outbox = WorkCardPublicationOutbox(session, store)
    first = outbox.list_pending()[0]
    card_id = "wc-" + "a" * 64
    assert outbox.bind_card(intent_id=first["intent_id"], generation=first["generation"],
                            card_id=card_id) == "bound"
    assert outbox.clear_published(intent_id=first["intent_id"], generation=first["generation"],
                                  card_id=card_id) == "published"

    assert _settle(store, item_id, requirement_id, run_id).status == "recorded"

    assert outbox.list_pending() == []
    stored = store.list_publication_intents()
    assert len(stored) == 0  # public pending view; published dedupe anchor remains in the source file


def test_plain_work_record_result_is_not_a_server_publication_trigger(tmp_path):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    run_id = uuid.uuid4().hex
    store.begin_requirement_run(item_id, requirement_id, run_id=run_id,
                               expected_current_run_id=None)
    store.record_result_if_current_run(
        item_id, requirement_id, run_id=run_id, result_id="ordinary-host-local-result",
        status="unknown", summary="Synthetic non-server result",
        verification={"status": "not_verified", "evidence_refs": [],
                      "host_execution": {"sequence": 1}},
    )

    assert store.list_publication_intents() == []
    snapshot = build_project_work_record_cards(store, "project-a")
    latest = snapshot.cards[0].requirements[0].latest_result
    assert latest is not None
    forged = replace(snapshot, change_focus=WorkCardChangeFocus(
        item_id=item_id, requirement_id=requirement_id, result_id=latest.result_id,
    ))
    ledger = WorkCardLedger(session, store)
    assert ledger.save_snapshot(str(uuid.uuid4()), forged).status == "rejected"
    assert ledger.list_cards() == []


def test_cancelled_server_settlement_is_intent_only_and_never_restarts_work(tmp_path):
    session, store, item_id, requirement_id, _ = _settled_project(tmp_path)
    run_id = uuid.uuid4().hex
    store.begin_requirement_run(item_id, requirement_id, run_id=run_id,
                               expected_current_run_id=None)
    binding = BoundExecution(session.conversation_id, item_id, requirement_id, run_id)
    receipt = ExecutionReceipt(
        session_id=session.conversation_id, item_id=item_id, requirement_id=requirement_id,
        run_id=run_id, sequence=3, outcome="cancelled", finish_reason="cancelled",
    )
    result = WorkRecordSettlementAdapter(store, binding).settle(
        receipt, publish_project_update=True,
    )

    assert result.status == "recorded"
    item = store.get_item(item_id)
    assert len(item["runs"]) == 1
    assert item["runs"][0]["verification"]["host_execution"]["finish_reason"] == "cancelled"
    pending = WorkCardPublicationOutbox(session, store).list_pending()
    assert len(pending) == 1 and pending[0]["latest_run_id"] == run_id
