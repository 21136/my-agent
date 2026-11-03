from __future__ import annotations
import asyncio
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings, ReadOnlyWorkRecordStore
from work_records import WorkRecordStore, _digest
from work_card_ledger import WorkCardLedger, linked_item_copy_request_id
from work_card_presentations import WorkCardPresentations
import server
from server import WsBridge, WsSessionHandler


def _setup(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns = ConversationWorkRecordBindings(parent)
    rows = ns.bind_existing_tasks(batch_id="batch", tasks=tasks, authorized_sources=sources)
    run_id = str(uuid.uuid4())
    for r in rows:
        ns.record_worker_start(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"],
            reservation_id="reservation-"+r["task_id"], parent_run_id=run_id, turn_epoch=1, project_id=r["project_id"], task_id=r["task_id"])
        ns.record_worker_lifecycle(binding_id=r["binding_id"], worker_id=r["worker_id"], run_id=r["run_id"], sequence=1,
                                   status="model_turn_finished", finish_reason="completed")
    proposal = ns.prepare_item_copies(binding_ids=[r["binding_id"] for r in rows], authorized_sources=sources)
    ns.commit_confirmed_item_copies(proposed=proposal, authorized_sources=sources, is_current=lambda: True)
    store = WorkRecordStore(parent)
    events = []
    bridge = WsBridge(emit=events.append, paths=parent.paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    return parent, ns, rows, run_id, store, events, bridge, repl


def test_handler_recovery_publishes_parent_only_and_received_is_idempotent(tmp_path, monkeypatch):
    parent, ns, rows, run_id, store, events, bridge, repl = _setup(tmp_path)
    monkeypatch.setattr(ReadOnlyWorkRecordStore, "_read_items_locked", lambda *_: (_ for _ in ()).throw(AssertionError("source read forbidden")))
    assert server._resume_linked_item_copy_cards(repl, bridge, parent, store, binding_ids=[rows[0]["binding_id"]], emit_event=True) == 1
    cards = WorkCardLedger(parent, store).list_cards()
    assert len(cards) == 1 and cards[0]["snapshot"]["kind"] == "linked_item_copy"
    assert cards[0]["snapshot"]["completeness_scope"] == "item_requirement"
    assert cards[0]["snapshot"]["operation_result"] == "unknown" and cards[0]["snapshot"]["verification"] == "not_verified"
    presents = [e for e in events if e.get("type") == "work-card.present"]
    assert len(presents) == 1 and presents[0]["card_id"] == cards[0]["card_id"]
    assert ns.get_binding(rows[0]["binding_id"])["card_copy"]["intent"]["state"] == "reference_queued"
    assert ns.get_binding(rows[1]["binding_id"])["card_copy"]["intent"]["state"] == "pending"
    present = presents[0]
    handler = WsSessionHandler(parent.paths)
    assert WorkCardPresentations(parent, store, WorkCardLedger(parent, store)).mark_transport_offer(present["presentation_id"], present["card_id"]).status == "offered"
    async def ack():
        for _ in range(2):
            await handler._handle_incoming({"type": "work-card.received", "session_id": parent.conversation_id,
                "presentation_id": present["presentation_id"], "card_id": present["card_id"]}, repl, bridge)
    asyncio.run(ack())
    fact = WorkCardPresentations(parent, store, WorkCardLedger(parent, store)).get(present["presentation_id"]).fact
    assert fact.receipt_state == "received" and fact.acknowledged_at is None, events
    server._emit_pending_work_card_references(repl, WsBridge(emit=events.append, paths=parent.paths))
    assert len(WorkCardLedger(parent, store).list_cards()) == 2
    assert len([e for e in events if e.get("type") == "work-card.present" and e.get("card_id") == present["card_id"]]) == 1


@pytest.mark.parametrize("crash", ["after_save", "after_reference"])
def test_recovery_reuses_same_card_after_each_crash(tmp_path, monkeypatch, crash):
    parent, ns, rows, run_id, store, events, bridge, repl = _setup(tmp_path)
    before = server._emit_work_card_reference
    if crash == "after_save":
        monkeypatch.setattr(server, "_emit_work_card_reference", lambda *a, **kw: False)
    else:
        monkeypatch.setattr(ConversationWorkRecordBindings, "mark_item_copy_reference_queued", lambda *a, **kw: (_ for _ in ()).throw(OSError("crash")))
    server._resume_linked_item_copy_cards(repl, bridge, parent, store, binding_ids=[rows[0]["binding_id"]], emit_event=True)
    cards = WorkCardLedger(parent, store).list_cards()
    assert len(cards) == 1
    card_id = cards[0]["card_id"]
    assert ns.get_binding(rows[0]["binding_id"])["card_copy"]["intent"]["state"] == "pending"
    monkeypatch.undo()
    new_bridge = WsBridge(emit=events.append, paths=parent.paths)
    server._emit_pending_work_card_references(repl, new_bridge)
    cards = WorkCardLedger(parent, store).list_cards()
    assert len(cards) == 2 and len([r for r in cards if r["card_id"] == card_id]) == 1
    assert ns.get_binding(rows[0]["binding_id"])["card_copy"]["intent"]["state"] == "reference_queued"
    assert len([m for m in parent.messages if m.get("role") == "work_card_presentation" and m.get("card_id") == card_id]) == 1


def test_wrong_session_fails_closed(tmp_path):
    parent, ns, rows, run_id, store, events, bridge, repl = _setup(tmp_path)
    repl.agent.session = object()
    assert server._resume_linked_item_copy_cards(repl, bridge, parent, store, binding_ids=[rows[0]["binding_id"]], emit_event=True) == 0
    assert WorkCardLedger(parent, store).list_cards() == []




def test_crash_after_queue_before_emit_replays_without_new_card(tmp_path):
    parent, ns, rows, run_id, store, events, bridge, repl = _setup(tmp_path)
    def fail_offer(event):
        if event.get("type") == "work-card.present":
            raise OSError("synthetic disconnect")
        events.append(event)
    bridge.emit = fail_offer
    assert server._resume_linked_item_copy_cards(repl, bridge, parent, store,
        binding_ids=[rows[0]["binding_id"]], emit_event=True) == 0
    assert ns.get_binding(rows[0]["binding_id"])["card_copy"]["intent"]["state"] == "reference_queued"
    card_id = WorkCardLedger(parent, store).list_cards()[0]["card_id"]
    server._emit_pending_work_card_references(repl, WsBridge(emit=events.append, paths=parent.paths))
    assert len([c for c in WorkCardLedger(parent, store).list_cards() if c["card_id"] == card_id]) == 1
    assert len([e for e in events if e.get("type") == "work-card.present" and e.get("card_id") == card_id]) == 1


def test_inactive_or_wrong_turn_does_not_consume(tmp_path):
    parent, ns, rows, run_id, store, events, bridge, repl = _setup(tmp_path)
    assert server._resume_linked_item_copy_cards(repl, bridge, parent, store,
        active_run_id=uuid.UUID(run_id).hex, turn_epoch=1, emit_event=True) == 0
    assert not WorkCardLedger(parent, store).list_cards()
    assert all(ns.get_binding(r["binding_id"])["card_copy"]["intent"]["state"] == "pending" for r in rows)


def test_real_turn_consumes_matching_confirmed_copy_without_running_worker(tmp_path, monkeypatch):
    import project_api
    parent, ns, rows, run_id, store, events, bridge, repl = _setup(tmp_path)
    bridge.lifecycle._run_id_factory = lambda: uuid.UUID(run_id).hex
    repl.handle_line = lambda _line: None  # synthetic agent turn; no model or worker
    monkeypatch.setattr(project_api, "after_turn_project_hooks", lambda *_: None)
    asyncio.run(server._run_line(repl, bridge, "synthetic turn", parent.paths))
    assert len([e for e in events if e.get("type") == "work-card.present"]) == 2
    assert all(ns.get_binding(r["binding_id"])["card_copy"]["intent"]["state"] == "reference_queued" for r in rows)
    assert any(e.get("type") == "turn.end" and e.get("ok") is True for e in events)
