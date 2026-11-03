from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid

import pytest
from pathlib import Path
from types import SimpleNamespace

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import server
from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge, _begin_work_record_run_binding, _settle_work_record_run
from work_card_ledger import WorkCardLedger
from work_records import WorkRecordStore
from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter


def _fixture(tmp_path: Path, *, capture: bool = True, batch: bool = False):
    root = tmp_path / "agent"
    root.mkdir()
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.resolve())))
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, "", SessionMeta(), [], paths)
    store = WorkRecordStore(session)
    request_id = str(uuid.uuid4()) if capture else None
    if batch:
        batch_digest = "a" * 64
        item, _created = store.create_item_with_requirement(
            title="多项目任务批次", scope={"kind": "independent"},
            requirement="original multi-project request",
            event_id=f"worker-batch-original-{batch_digest}",
            idempotency_key=f"worker-batch-original:{batch_digest}",
            return_commit_status=True,
        )
        requirement = store.add_requirement(
            item["item_id"], "project worker task", event_id=f"worker-batch-task-{batch_digest}-0",
            source_ref={"kind": "project", "id": "project-a"},
            idempotency_key=f"worker-batch-task:{batch_digest}:0",
        )
        if capture:
            source = "请记录独立事项：合成任务需要交付报告。"
            key = f"desktop:user.message:{request_id}:{hashlib.sha256(source.encode()).hexdigest()}"
            requirement = store.add_requirement(
                item["item_id"], "Synthetic explicit capture attached to the batch container",
                event_id=request_id, idempotency_key=key,
            )
    elif capture:
        source = "请记录独立事项：合成任务需要交付报告。"
        key = f"desktop:user.message:{request_id}:{hashlib.sha256(source.encode()).hexdigest()}"
        item, _created = store.create_item_with_requirement(
            title="Synthetic standalone report", scope={"kind": "independent"},
            requirement="Deliver a synthetic report", event_id=request_id,
            idempotency_key=key, return_commit_status=True,
        )
        event = next(row for row in item["history"] if row.get("event_id") == request_id)
        requirement = {"requirement_id": event["payload"]["requirement_id"]}
    else:
        item = store.create_item(title="Unbound standalone record", scope={"kind": "independent"})
        requirement = store.add_requirement(item["item_id"], "not a captured task", event_id="manual-source")
    return session, store, item["item_id"], requirement["requirement_id"], request_id


def _begin(session, store, item_id, requirement_id, request_id, *, emit=None):
    events = []
    bridge = WsBridge(emit=emit or events.append, paths=session.paths)
    assert bridge.begin_turn(mode="ordinary")
    snapshot = bridge.lifecycle.snapshot()
    bridge.lifecycle_conversation_id = session.conversation_id
    store.begin_requirement_run(item_id, requirement_id, run_id=snapshot.run_id, expected_current_run_id=None)
    binding = _begin_work_record_run_binding(
        session=session,
        capture=SimpleNamespace(status="recorded", item_id=item_id, requirement_id=requirement_id),
        run_id=snapshot.run_id, current_session=session, bridge=bridge,
        emit=events.append, request_id=request_id,
    )
    assert binding is not None
    run_store, execution = binding
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    return events, bridge, snapshot, run_store, execution, repl


def test_legacy_independent_result_replay_reports_no_intent_without_claiming_incomplete(tmp_path):
    session, store, item_id, requirement_id, request_id = _fixture(tmp_path)
    events, bridge, started, run_store, binding, repl = _begin(
        session, store, item_id, requirement_id, request_id,
    )
    assert bridge.finish_execution("completed", ok=True, run_id=started.run_id)
    terminal = bridge.lifecycle.snapshot()
    receipt = ExecutionReceipt(
        session_id=session.conversation_id, item_id=item_id, requirement_id=requirement_id,
        run_id=started.run_id, sequence=terminal.sequence,
        outcome="succeeded", finish_reason="completed",
    )
    first = WorkRecordSettlementAdapter(run_store, binding).settle(receipt)
    assert first.status == "recorded"
    assert store.list_independent_publication_intents(include_queued=True) == []

    settled = _settle_work_record_run(
        current_session=session, bridge=bridge, store=run_store, binding=binding,
        repl=repl, emit=events.append,
    )

    assert settled is True
    assert store.list_independent_publication_intents(include_queued=True) == []
    notices = [event["text"] for event in events if event.get("type") == "turn.notice"]
    assert any("未创建或改写发布 intent" in text and "未自动追溯补发" in text for text in notices)
    assert any("不表示工作未完成" in text and "未知、未经验证" in text for text in notices)
    assert not any(event.get("type") == "work-card.present" for event in events)


def test_explicit_independent_settlement_persists_and_publishes_exact_immutable_card(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, request_id = _fixture(tmp_path)
    events, bridge, started, run_store, binding, repl = _begin(
        session, store, item_id, requirement_id, request_id,
    )
    assert binding.capture_request_id == request_id
    assert server._work_record_settlement_publication_scope(run_store, binding) == "independent"
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 0.01, raising=False)
    async def settle_and_wait_for_publish():
        assert bridge.finish_execution("completed", ok=True, run_id=started.run_id)
        assert _settle_work_record_run(
            current_session=session, bridge=bridge, store=run_store, binding=binding,
            repl=repl, emit=events.append,
        )
        assert len(store.list_independent_publication_intents(include_queued=True)) == 1
        await asyncio.sleep(0.08)
    asyncio.run(settle_and_wait_for_publish())
    if bridge._work_card_update_coalescer is not None:
        bridge._work_card_update_coalescer.close()

    assert store.list_independent_publication_intents(include_queued=True)[0]["state"] == "reference_queued", events
    cards = [event for event in events if event.get("type") == "work-card.present"]
    assert len(cards) == 1, events
    card = cards[0]
    assert card["session_id"] == session.conversation_id
    assert card["card"]["snapshot"]["kind"] == "independent"
    assert card["card"]["snapshot"]["item_id"] == item_id
    requirement = card["card"]["snapshot"]["card"]["requirements"][0]
    assert requirement["requirement_id"] == requirement_id
    assert requirement["latest_result"]["run_id"] == started.run_id
    assert requirement["status"] == requirement["latest_result"]["status"] == "unknown"
    assert requirement["latest_result"]["verification_status"] == "not_verified"
    assert card["card"]["snapshot"]["change_focus"] == {
        "item_id": item_id, "requirement_id": requirement_id,
        "result_id": requirement["latest_result"]["result_id"],
    }
    assert len(WorkCardLedger(session, store).list_cards()) == 1
    assert len(store.get_item(item_id)["runs"]) == 1
    intents = store.list_independent_publication_intents(include_queued=True)
    assert len(intents) == 1 and intents[0]["state"] == "reference_queued"
    assert _settle_work_record_run(
        current_session=session, bridge=bridge, store=run_store, binding=binding,
        repl=repl, emit=events.append,
    )
    assert len([event for event in events if event.get("type") == "work-card.present"]) == 1
    assert len(store.get_item(item_id)["runs"]) == 1


def test_independent_pending_merge_replaying_first_requirement_preserves_latest_intent(tmp_path):
    session, store, item_id, first_requirement_id, request_id = _fixture(tmp_path)
    _events, _bridge, _started, _run_store, first_binding, _repl = _begin(
        session, store, item_id, first_requirement_id, request_id,
    )

    def settle(requirement_id, run_id):
        binding = BoundExecution(session.conversation_id, item_id, requirement_id, run_id)
        return store.record_independent_server_settlement_if_current_run(
            item_id, requirement_id, run_id=run_id,
            result_id=WorkRecordSettlementAdapter._result_id(binding),
            status="unknown", summary=f"synthetic settlement {run_id}",
            verification={"status": "not_verified", "evidence_refs": [],
                          "host_execution": {"sequence": 1}},
        )

    settle(first_requirement_id, first_binding.run_id)
    second = store.add_requirement(item_id, "Synthetic second requirement", event_id="second-independent")
    second_run_id = uuid.uuid4().hex
    store.begin_requirement_run(
        item_id, second["requirement_id"], run_id=second_run_id,
        expected_current_run_id=None,
    )
    settle(second["requirement_id"], second_run_id)

    before_replay = store.list_independent_publication_intents()
    assert len(before_replay) == 1
    assert before_replay[0]["latest_requirement_id"] == second["requirement_id"]
    assert before_replay[0]["generation"] == 2

    settle(first_requirement_id, first_binding.run_id)

    assert store.list_independent_publication_intents() == before_replay


def test_socket_enqueue_failure_replays_the_same_saved_card_without_reexecution(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, request_id = _fixture(tmp_path)
    events = []
    failed = {"once": False}
    def fail_first_card(event):
        if event.get("type") == "work-card.present" and not failed["once"]:
            failed["once"] = True
            raise OSError("synthetic enqueue failure")
        events.append(event)
    events, bridge, started, run_store, binding, repl = _begin(
        session, store, item_id, requirement_id, request_id, emit=fail_first_card,
    )
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 0.01, raising=False)

    async def settle_and_fail_enqueue():
        assert bridge.finish_execution("completed", ok=True, run_id=started.run_id)
        assert _settle_work_record_run(
            current_session=session, bridge=bridge, store=run_store, binding=binding,
            repl=repl, emit=events.append,
        )
        await asyncio.sleep(0.08)
    asyncio.run(settle_and_fail_enqueue())
    if bridge._work_card_update_coalescer is not None:
        bridge._work_card_update_coalescer.close()

    assert failed["once"] is True
    intents = store.list_independent_publication_intents(include_queued=True)
    assert len(intents) == 1 and intents[0]["state"] == "reference_queued"
    saved_card = WorkCardLedger(session, store).get_card(intents[0]["card_id"])
    assert saved_card["snapshot"]["kind"] == "independent"
    assert saved_card["snapshot"]["change_focus"] == {
        "item_id": item_id, "requirement_id": requirement_id,
        "result_id": store.get_item(item_id)["runs"][-1]["result_id"],
    }

    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    replayed = []
    replay_bridge = WsBridge(emit=replayed.append, paths=restored.paths)
    replay_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    assert server._emit_pending_work_card_references(replay_repl, replay_bridge) == 1
    card_event = next(event for event in replayed if event.get("type") == "work-card.present")
    assert card_event["card_id"] == intents[0]["card_id"]
    assert card_event["card"]["snapshot"]["change_focus"] == saved_card["snapshot"]["change_focus"]
    assert card_event["presentation_id"] == server._work_card_presentation_id(
        restored.conversation_id, started.run_id, intents[0]["card_id"],
    )
    assert len(restored_store.get_item(item_id)["runs"]) == 1
    assert len(WorkCardLedger(restored, restored_store).list_cards()) == 1


def test_transport_failure_replays_old_reference_after_more_than_200_history_items(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, request_id = _fixture(tmp_path)
    events, bridge, started, run_store, binding, repl = _begin(
        session, store, item_id, requirement_id, request_id,
    )
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 0.01, raising=False)

    async def settle_and_publish_to_socket_queue():
        assert bridge.finish_execution("completed", ok=True, run_id=started.run_id)
        assert _settle_work_record_run(
            current_session=session, bridge=bridge, store=run_store, binding=binding,
            repl=repl, emit=events.append,
        )
        await asyncio.sleep(0.08)
    asyncio.run(settle_and_publish_to_socket_queue())
    if bridge._work_card_update_coalescer is not None:
        bridge._work_card_update_coalescer.close()
    original = next(event for event in events if event.get("type") == "work-card.present")

    from websockets.exceptions import ConnectionClosed
    class SocketFailure:
        async def send(self, _payload):
            raise ConnectionClosed(None, None)
    handler = object.__new__(server.WsSessionHandler)
    handler.paths = session.paths
    failed_queue = asyncio.Queue()
    failed_queue.put_nowait(original)
    asyncio.run(handler._sender(SocketFailure(), failed_queue, bridge))

    for index in range(205):
        session.append_message({"role": "user", "content": f"synthetic padding {index}"})

    restored = Session.load(session.paths, session.conversation_id)
    retry_events = []
    retry_bridge = WsBridge(emit=retry_events.append, paths=restored.paths)
    retry_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    server._emit_pending_work_card_references(retry_repl, retry_bridge)
    retried = [event for event in retry_events if event.get("type") == "work-card.present"]
    assert len(retried) == 1
    assert retried[0]["presentation_id"] == original["presentation_id"]
    assert retried[0]["card_id"] == original["card_id"]

    async def confirm_transport_send():
        delivered_queue = asyncio.Queue()
        delivered_queue.put_nowait(retried[0])
        delivered_queue.put_nowait(None)
        class SocketSuccess:
            async def send(self, _payload):
                return None
        await handler._sender(SocketSuccess(), delivered_queue, retry_bridge)
    asyncio.run(confirm_transport_send())

    final = Session.load(session.paths, session.conversation_id)
    final_store = WorkRecordStore(final)
    from work_card_presentations import WorkCardPresentations
    presentations = WorkCardPresentations(final, final_store, WorkCardLedger(final, final_store))
    fact = presentations.get(original["presentation_id"]).fact
    assert fact is not None and fact.receipt_state == "pending" and fact.received_at is None
    assert fact.acknowledged_at is None
    assert final_store.list_independent_publication_intents(include_queued=True)[0]["state"] == "reference_queued"

    # A successful socket write and a user-view ACK are deliberately not renderer receipt proof.
    user_ack = presentations.acknowledge(
        original["presentation_id"], original["card_id"], explicit_user_action=True,
    )
    assert user_ack.status == "acknowledged"
    assert presentations.get(original["presentation_id"]).fact.receipt_state == "pending"
    unacknowledged_events = []
    unacknowledged_bridge = WsBridge(emit=unacknowledged_events.append, paths=final.paths)
    unacknowledged_repl = SimpleNamespace(session=final, agent=SimpleNamespace(session=final))
    server._emit_pending_work_card_references(unacknowledged_repl, unacknowledged_bridge)
    replayed_again = next(event for event in unacknowledged_events if event.get("type") == "work-card.present")
    assert replayed_again["presentation_id"] == original["presentation_id"]
    assert replayed_again["card_id"] == original["card_id"]

    # Only a renderer receipt tied to this current Session/reference transitions delivery state.
    received_events = []
    received_bridge = WsBridge(emit=received_events.append, paths=final.paths)
    received_repl = SimpleNamespace(session=final, agent=SimpleNamespace(session=final))
    handler = object.__new__(server.WsSessionHandler)
    handler.paths = final.paths
    asyncio.run(handler._dispatch_work_card_protocol({
        "type": "work-card.received", "session_id": final.conversation_id,
        "presentation_id": original["presentation_id"], "card_id": original["card_id"],
    }, received_repl, received_bridge))
    assert received_events[-1]["status"] == "received", received_events
    assert presentations.get(original["presentation_id"]).fact.receipt_state == "received"
    acknowledged_session = Session.load(session.paths, session.conversation_id)
    acknowledged_store = WorkRecordStore(acknowledged_session)
    acknowledged_events = []
    acknowledged_bridge = WsBridge(emit=acknowledged_events.append, paths=acknowledged_session.paths)
    acknowledged_repl = SimpleNamespace(
        session=acknowledged_session, agent=SimpleNamespace(session=acknowledged_session),
    )
    server._emit_pending_work_card_references(acknowledged_repl, acknowledged_bridge)
    assert not any(event.get("type") == "work-card.present" for event in acknowledged_events)
    refs = [message for message in acknowledged_session.messages
            if message.get("role") == "work_card_presentation"
            and message.get("presentation_id") == original["presentation_id"]]
    assert len(refs) == 1, "same presentation identity must not append duplicate history messages"
    assert len(acknowledged_store.get_item(item_id)["runs"]) == 1


def test_atomic_settlement_failure_keeps_requirement_unverified_and_does_not_retry_task(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, request_id = _fixture(tmp_path)
    events, bridge, started, run_store, binding, repl = _begin(
        session, store, item_id, requirement_id, request_id,
    )
    assert bridge.finish_execution("completed", ok=True, run_id=started.run_id)
    monkeypatch.setattr(
        WorkRecordStore, "_publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic atomic commit failure")),
    )

    assert not _settle_work_record_run(
        current_session=session, bridge=bridge, store=run_store, binding=binding,
        repl=repl, emit=events.append,
    )
    item = WorkRecordStore(session).get_item(item_id)
    assert item["requirements"][0]["current_run_id"] == started.run_id
    assert item["runs"] == []
    assert WorkRecordStore(session).list_independent_publication_intents(include_queued=True) == []
    assert WorkCardLedger(session, store).list_cards() == []
    assert any(event.get("type") == "turn.notice" and event.get("level") == "warn"
               and "不会重做执行" in event.get("text", "") for event in events)


def test_crash_after_atomic_settlement_replace_recovers_result_and_intent_without_rerun(tmp_path, monkeypatch):
    session, store, item_id, requirement_id, request_id = _fixture(tmp_path)
    events, bridge, started, run_store, binding, repl = _begin(
        session, store, item_id, requirement_id, request_id,
    )
    monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 0.01, raising=False)
    assert bridge.finish_execution("completed", ok=True, run_id=started.run_id)
    original_publish = WorkRecordStore._publish

    class SimulatedProcessCrash(BaseException):
        pass

    def replace_then_crash(self, data, *, previous=None):
        original_publish(self, data, previous=previous)
        raise SimulatedProcessCrash()

    monkeypatch.setattr(WorkRecordStore, "_publish", replace_then_crash)
    try:
        _settle_work_record_run(
            current_session=session, bridge=bridge, store=run_store, binding=binding,
            repl=repl, emit=events.append,
        )
        raise AssertionError("simulated process crash did not occur")
    except SimulatedProcessCrash:
        pass
    monkeypatch.setattr(WorkRecordStore, "_publish", original_publish)

    restored = Session.load(session.paths, session.conversation_id)
    restored_store = WorkRecordStore(restored)
    restored_item = restored_store.get_item(item_id)
    pending = restored_store.list_independent_publication_intents()
    assert len(restored_item["runs"]) == 1
    assert restored_item["runs"][0]["run_id"] == started.run_id
    assert len(pending) == 1 and pending[0]["latest_run_id"] == started.run_id
    committed_run = restored_item["runs"][0]
    restored_store.record_independent_server_settlement_if_current_run(
        item_id, requirement_id, run_id=committed_run["run_id"],
        result_id=committed_run["result_id"], status=committed_run["status"],
        summary=committed_run["result_summary"], verification=committed_run["verification"],
        sources=committed_run["sources"],
    )
    assert restored_store.list_independent_publication_intents() == pending

    async def recover_publication():
        recovered_events = []
        recovered_bridge = WsBridge(emit=recovered_events.append, paths=restored.paths)
        recovered_repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
        server._emit_pending_work_card_references(recovered_repl, recovered_bridge)
        await asyncio.sleep(0.08)
        if recovered_bridge._work_card_update_coalescer is not None:
            recovered_bridge._work_card_update_coalescer.close()
        cards = [event for event in recovered_events if event.get("type") == "work-card.present"]
        assert len(cards) == 1
        assert cards[0]["card"]["snapshot"]["item_id"] == item_id
        assert len(WorkRecordStore(restored).get_item(item_id)["runs"]) == 1
    asyncio.run(recover_publication())


def test_independent_intents_reject_cross_session_and_cross_item_rebinding(tmp_path):
    base_one, base_two = tmp_path / "one", tmp_path / "two"
    base_one.mkdir(); base_two.mkdir()
    session, store, item_id, requirement_id, request_id = _fixture(base_one)
    _events, _bridge, _started, _run_store, binding, _repl = _begin(
        session, store, item_id, requirement_id, request_id,
    )
    item = store.get_item(item_id)
    requirement = item["requirements"][0]
    run_id = requirement["current_run_id"]
    result_id = WorkRecordSettlementAdapter._result_id(binding)
    store.record_independent_server_settlement_if_current_run(
        item_id, requirement_id, run_id=run_id, result_id=result_id,
        status="unknown", summary="synthetic trusted settlement",
        verification={"status": "not_verified", "evidence_refs": [],
                      "host_execution": {"sequence": 1}},
    )
    intent = store.list_independent_publication_intents()[0]
    other_session, other_store, _other_item, _other_req, _other_request = _fixture(base_two)
    from independent_work_card_publication import IndependentWorkCardPublicationOutbox
    assert not IndependentWorkCardPublicationOutbox(other_session, other_store).validate(intent)
    forged_item_intent = {**intent, "latest_item_id": str(uuid.uuid4())}
    assert not IndependentWorkCardPublicationOutbox(session, store).validate(forged_item_intent)

    cross_session = WorkRecordSettlementAdapter(other_store, binding).settle(
        ExecutionReceipt(session.conversation_id, item_id, requirement_id, run_id,
                         sequence=1, outcome="succeeded", finish_reason="completed"),
        publish_independent_update=True,
    )
    assert cross_session.status == "rejected"

    other_item, _created = store.create_item_with_requirement(
        title="Another standalone task", scope={"kind": "independent"},
        requirement="Different request", event_id=str(uuid.uuid4()),
        idempotency_key=f"other:{uuid.uuid4()}", return_commit_status=True,
    )
    other_requirement = other_item["requirements"][0]
    other_run = uuid.uuid4().hex
    store.begin_requirement_run(
        other_item["item_id"], other_requirement["requirement_id"],
        run_id=other_run, expected_current_run_id=None,
    )
    cross_item_binding = BoundExecution(
        session.conversation_id, other_item["item_id"], other_requirement["requirement_id"],
        other_run, capture_request_id=request_id,
    )
    cross_item = WorkRecordSettlementAdapter(store, cross_item_binding).settle(
        ExecutionReceipt(session.conversation_id, other_item["item_id"],
                         other_requirement["requirement_id"], other_run,
                         sequence=1, outcome="succeeded", finish_reason="completed"),
        publish_independent_update=True,
    )
    assert cross_item.status == "rejected"
    assert store.get_item(other_item["item_id"])["runs"] == []
    assert len(store.list_independent_publication_intents()) == 1
    assert other_store.list_independent_publication_intents() == []


def test_manual_independent_record_and_worker_batch_outer_record_never_auto_publish(tmp_path, monkeypatch):
    for index, options in enumerate((
        {"capture": False}, {"capture": False, "batch": True}, {"capture": True, "batch": True},
    )):
        case_root = tmp_path / str(index)
        case_root.mkdir()
        session, store, item_id, requirement_id, request_id = _fixture(case_root, **options)
        events, bridge, started, run_store, binding, repl = _begin(
            session, store, item_id, requirement_id, request_id,
        )
        monkeypatch.setattr(server, "PROJECT_WORK_CARD_MERGE_SECONDS", 0.01, raising=False)
        assert bridge.finish_execution("completed", ok=True, run_id=started.run_id)
        assert _settle_work_record_run(
            current_session=session, bridge=bridge, store=run_store, binding=binding,
            repl=repl, emit=events.append,
        )
        assert store.list_independent_publication_intents() == []
        assert WorkCardLedger(session, store).list_cards() == []
        assert not any(event.get("type") == "work-card.present" for event in events)
        if bridge._work_card_update_coalescer is not None:
            bridge._work_card_update_coalescer.close()

