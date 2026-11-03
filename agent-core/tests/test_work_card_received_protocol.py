from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))
_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import server
from paths import AgentPaths
from session import Session, SessionMeta
from server import WsBridge, WsSessionHandler
from test_work_card_ledger import make_independent_session, REQUEST_ID
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations, PRESENTATIONS_FILENAME
from work_records import WorkRecordStore


def _presentation(tmp_path, *, run_id: str | None = None, ack_user: bool = False):
    session, store, snapshot, _item, _requirement = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == "saved"
    presentation_id = (server._work_card_presentation_id(session.conversation_id, run_id, saved.card_id)
                       if run_id is not None else str(uuid.uuid4()))
    presentations = WorkCardPresentations(session, store, ledger)
    created = presentations.create(presentation_id, saved.card_id, run_id=run_id)
    assert created.status == "created"
    if run_id is not None:
        session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=run_id)
    if ack_user:
        assert presentations.acknowledge(presentation_id, saved.card_id, explicit_user_action=True).status == "acknowledged"
    return session, store, ledger, presentations, presentation_id, saved.card_id


def test_receive_receipt_is_distinct_idempotent_and_scoped(tmp_path):
    session, _store, _ledger, presentations, presentation_id, card_id = _presentation(
        tmp_path, run_id=str(uuid.uuid4()), ack_user=True,
    )
    before = presentations.get(presentation_id).fact
    assert before.acknowledged_at is not None
    assert before.receipt_state == "pending"
    assert before.received_at is None

    first_offer = presentations.mark_transport_offer(presentation_id, card_id)
    second_offer = presentations.mark_transport_offer(presentation_id, card_id)
    third_offer = presentations.mark_transport_offer(presentation_id, card_id)
    assert [first_offer.status, second_offer.status, third_offer.status] == ["offered", "offered", "deferred"]
    offered = presentations.get(presentation_id).fact
    assert offered.receipt_state == "deferred" and offered.receipt_attempts == 3
    assert offered.received_at is None
    assert offered.acknowledged_at == before.acknowledged_at
    assert presentations.list_retryable_receipts(limit=20) == ()
    assert presentations.receipt_backlog_counts()["deferred"] == 1

    received = presentations.mark_received(presentation_id, card_id, session.conversation_id, before.run_id)
    assert received.status == "received" and received.fact.received_at
    assert received.fact.acknowledged_at == before.acknowledged_at
    again = presentations.mark_received(presentation_id, card_id, session.conversation_id, before.run_id)
    assert again.status == "already_received" and again.fact == received.fact
    assert presentations.mark_received(presentation_id, card_id, "22222222-2222-4222-8222-222222222222", before.run_id).status == "rejected"
    assert presentations.mark_received(presentation_id, "wc-" + "0" * 64, session.conversation_id, before.run_id).status == "conflict"


def test_sender_success_is_offer_not_received_and_send_failure_does_not_consume_attempt(tmp_path):
    session, _store, _ledger, presentations, presentation_id, card_id = _presentation(
        tmp_path, run_id=str(uuid.uuid4()),
    )
    handler = object.__new__(WsSessionHandler)
    handler.paths = session.paths
    from websockets.exceptions import ConnectionClosed

    class SocketFailure:
        async def send(self, _payload):
            raise ConnectionClosed(None, None)

    failed = asyncio.Queue()
    failed.put_nowait({"type": "work-card.present", "session_id": session.conversation_id,
                       "presentation_id": presentation_id, "card_id": card_id})
    bridge = WsBridge(emit=lambda _event: None, paths=session.paths)
    asyncio.run(handler._sender(SocketFailure(), failed, bridge))
    fact = presentations.get(presentation_id).fact
    assert fact.receipt_attempts == 0 and fact.received_at is None

    class SocketSuccess:
        async def send(self, _payload):
            return None

    delivered = asyncio.Queue()
    delivered.put_nowait({"type": "work-card.present", "session_id": session.conversation_id,
                          "presentation_id": presentation_id, "card_id": card_id})
    delivered.put_nowait(None)
    asyncio.run(handler._sender(SocketSuccess(), delivered, bridge))
    fact = presentations.get(presentation_id).fact
    assert fact.receipt_attempts == 1 and fact.receipt_state == "pending"
    assert fact.received_at is None


def test_legacy_presentation_schema_is_untracked_not_misread_as_received_or_unread(tmp_path):
    session, store, ledger, presentations, presentation_id, _card_id = _presentation(
        tmp_path, run_id=None, ack_user=True,
    )
    path = session.session_dir / PRESENTATIONS_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 3
    for row in data["items"]:
        row.pop("run_id")
        row.pop("receipt_state")
        row.pop("receipt_attempts")
        row.pop("last_offer_at")
        row.pop("received_at")
    path.write_text(json.dumps(data), encoding="utf-8")

    reopened_session = Session.load(session.paths, session.conversation_id)
    reopened_store = WorkRecordStore(reopened_session)
    reopened = WorkCardPresentations(reopened_session, reopened_store, WorkCardLedger(reopened_session, reopened_store))
    fact = reopened.get(presentation_id).fact
    assert fact.receipt_state == "legacy_untracked"
    assert fact.received_at is None
    assert fact.acknowledged_at is not None
    assert reopened.list_retryable_receipts(limit=20) == ()
    assert reopened.receipt_backlog_counts() == {"pending": 0, "deferred": 0, "legacy_untracked": 1}


def test_received_protocol_requires_exact_current_session_durable_reference(tmp_path):
    session, store, _ledger, presentations, presentation_id, card_id = _presentation(
        tmp_path, run_id=str(uuid.uuid4()),
    )
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    handler = object.__new__(WsSessionHandler)
    handler.paths = session.paths

    async def dispatch(payload):
        await handler._dispatch_work_card_protocol(payload, repl, bridge)

    payload = {"type": "work-card.received", "session_id": session.conversation_id,
               "presentation_id": presentation_id, "card_id": card_id}
    asyncio.run(dispatch(payload))
    assert events[-1].get("status") == "rejected"
    assert presentations.get(presentation_id).fact.receipt_attempts == 0

    class SocketSuccess:
        async def send(self, _payload):
            return None
    queue = asyncio.Queue()
    queue.put_nowait({"type": "work-card.present", "session_id": session.conversation_id,
                      "presentation_id": presentation_id, "card_id": card_id})
    queue.put_nowait(None)
    asyncio.run(handler._sender(SocketSuccess(), queue, bridge))
    asyncio.run(dispatch(payload))
    assert any(event.get("type") == "work-card.received.done" for event in events), events
    assert events[-1].get("status") == "received", events
    fact = presentations.get(presentation_id).fact
    assert fact.receipt_state == "received" and fact.received_at
    assert fact.acknowledged_at is None
    response = events[-1]
    assert response["status"] == "received"
    assert {key: response[key] for key in ("session_id", "presentation_id", "card_id")} == {
        key: payload[key] for key in ("session_id", "presentation_id", "card_id")
    }
    asyncio.run(dispatch(payload))
    assert events[-1]["status"] == "already_received"
    assert presentations.get(presentation_id).fact.received_at == fact.received_at

    invalid = {**payload, "session_id": "22222222-2222-4222-8222-222222222222"}
    asyncio.run(dispatch(invalid))
    invalid_item = {**payload, "card_id": "wc-" + "0" * 64}
    asyncio.run(dispatch(invalid_item))
    assert presentations.get(presentation_id).fact.received_at == fact.received_at
    assert events[-1]["status"] == "rejected"


def test_early_renderer_receipt_waits_for_in_flight_send_marker(tmp_path):
    session, _store, _ledger, presentations, presentation_id, card_id = _presentation(
        tmp_path, run_id=str(uuid.uuid4()),
    )
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    handler = object.__new__(WsSessionHandler)
    handler.paths = session.paths
    payload = {"type": "work-card.received", "session_id": session.conversation_id,
               "presentation_id": presentation_id, "card_id": card_id}
    entered_send = asyncio.Event()
    release_send = asyncio.Event()

    class BlockingSocket:
        async def send(self, _payload):
            entered_send.set()
            await release_send.wait()

    async def run_race():
        queue = asyncio.Queue()
        queue.put_nowait({"type": "work-card.present", "session_id": session.conversation_id,
                          "presentation_id": presentation_id, "card_id": card_id})
        queue.put_nowait(None)
        sender = asyncio.create_task(handler._sender(BlockingSocket(), queue, bridge))
        await entered_send.wait()
        receiver = asyncio.create_task(handler._dispatch_work_card_protocol(payload, repl, bridge))
        await asyncio.sleep(0)
        assert not receiver.done(), "receipt processing must wait until the in-flight socket send is classified"
        release_send.set()
        await sender
        await receiver

    asyncio.run(run_race())
    fact = presentations.get(presentation_id).fact
    assert fact.receipt_state == "received" and fact.received_at
    assert fact.receipt_attempts == 1
    assert fact.acknowledged_at is None
    assert events[-1]["status"] == "received"


def test_reconnect_delivery_retry_is_bounded_and_backlog_is_reported(tmp_path, monkeypatch):
    session, _store, _ledger, presentations, _presentation_id, _card_id = _presentation(
        tmp_path, run_id=str(uuid.uuid4()),
    )
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    second_presentation_id = server._work_card_presentation_id(
        session.conversation_id, str(uuid.uuid4()), _card_id,
    )
    second_run_id = second_presentation_id  # safe canonical UUID; this synthetic fixture binds it to the same card
    assert presentations.create(second_presentation_id, _card_id, run_id=second_run_id).status == "created"
    session.append_sent_work_card_reference(second_presentation_id, _card_id, run_id=second_run_id)
    monkeypatch.setattr(server, "WORK_CARD_RECEIPT_RETRY_BATCH", 1, raising=False)
    server._emit_pending_work_card_references(repl, bridge)
    assert len([event for event in events if event.get("type") == "work-card.present"]) == 1
    assert any(event.get("type") == "work-card.receipt.backlog" for event in events)


def test_legacy_active_redelivery_tracks_only_existing_exact_reference(tmp_path):
    session, store, ledger, _presentations, _unused, card_id = _presentation(tmp_path)
    presentations = WorkCardPresentations(session, store, ledger)
    run_id = str(uuid.uuid4())
    presentation_id = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    assert presentations.create(presentation_id, card_id).status == 'created'
    assert presentations.acknowledge(presentation_id, card_id, explicit_user_action=True).status == 'acknowledged'
    before = presentations.get(presentation_id).fact
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    emit = lambda: server._emit_work_card_reference(repl, bridge, session, presentation_id, card_id, run_id, active_run_id=None)
    assert not emit()  # a missing historical reference must not be manufactured as evidence
    assert presentations.get(presentation_id).fact.receipt_state == 'legacy_untracked'
    assert not any(row.get('presentation_id') == presentation_id for row in session.messages)
    session.append_sent_work_card_reference(presentation_id, card_id, run_id=run_id)
    assert server._emit_work_card_reference(repl, bridge, session, presentation_id, card_id, run_id, active_run_id=None, emit_event=False)
    assert presentations.get(presentation_id).fact.receipt_state == 'legacy_untracked'
    assert emit()
    fact = presentations.get(presentation_id).fact
    assert fact.receipt_state == 'pending' and fact.run_id == run_id
    assert fact.received_at is None and fact.acknowledged_at == before.acknowledged_at
    assert [event['presentation_id'] for event in events if event.get('type') == 'work-card.present'] == [presentation_id]
    assert len([row for row in session.messages if row.get('presentation_id') == presentation_id]) == 1
    assert emit()
    assert len([event for event in events if event.get('type') == 'work-card.present']) == 1
    assert presentations.receipt_backlog_counts()['legacy_untracked'] == 1  # unrelated old history stays untracked


def test_legacy_redelivery_rejects_conflicting_reference_run(tmp_path):
    session, store, ledger, _presentations, _unused, card_id = _presentation(tmp_path)
    presentations = WorkCardPresentations(session, store, ledger)
    run_id = str(uuid.uuid4())
    presentation_id = server._work_card_presentation_id(session.conversation_id, run_id, card_id)
    assert presentations.create(presentation_id, card_id).status == 'created'
    session.append_sent_work_card_reference(presentation_id, card_id, run_id=str(uuid.uuid4()))
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    assert not server._emit_work_card_reference(repl, bridge, session, presentation_id, card_id, run_id, active_run_id=None)
    assert presentations.get(presentation_id).fact.receipt_state == 'legacy_untracked'
    assert not any(event.get('type') == 'work-card.present' for event in events)


def test_explicit_backlog_continue_is_bounded_idempotent_and_session_scoped(tmp_path, monkeypatch):
    session, _store, _ledger, presentations, _pid, card_id = _presentation(tmp_path, run_id=str(uuid.uuid4()))
    for _ in range(22):
        run = str(uuid.uuid4())
        pid = server._work_card_presentation_id(session.conversation_id, run, card_id)
        assert presentations.create(pid, card_id, run_id=run).status == 'created'
        session.append_sent_work_card_reference(pid, card_id, run_id=run)
    # A legacy row is not a candidate even when the user continues.
    legacy_id = str(uuid.uuid4())
    assert presentations.create(legacy_id, card_id).status == 'created'
    events = []
    bridge = WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=SimpleNamespace(session=session))
    handler = WsSessionHandler(session.paths)
    monkeypatch.setattr(server, 'WORK_CARD_RECEIPT_RETRY_BATCH', 1)
    server._emit_pending_work_card_references(repl, bridge)
    assert sum(event.get('type') == 'work-card.present' for event in events) == 1
    async def send(payload):
        await handler._handle_incoming(payload, repl, bridge)
    request = {'type': 'work-card.receipt.continue', 'session_id': session.conversation_id,
               'request_id': str(uuid.uuid4())}
    asyncio.run(send({**request, 'session_id': str(uuid.uuid4())}))
    assert events[-1]['status'] == 'rejected'
    assert sum(event.get('type') == 'work-card.present' for event in events) == 1
    asyncio.run(send(request))
    response = events[-1]
    assert response['type'] == 'work-card.receipt.continue.done'
    assert response['status'] == 'ok' and response['queued_count'] == 5
    assert response['has_more'] is True and response['legacy_untracked_count'] == 1
    assert sum(event.get('type') == 'work-card.present' for event in events) == 6
    asyncio.run(send(request))
    assert events[-1]['status'] == 'already_processed'
    assert sum(event.get('type') == 'work-card.present' for event in events) == 6
    for expected in (5, 5, 5, 2):
        asyncio.run(send({**request, 'request_id': str(uuid.uuid4())}))
        assert events[-1]['queued_count'] == expected
        assert events[-1]['has_more'] is (expected != 2)
    ids = [event['presentation_id'] for event in events if event.get('type') == 'work-card.present']
    assert len(ids) == len(set(ids)) == 23 and legacy_id not in ids
    assert all(presentations.get(pid).fact.received_at is None for pid in ids)
