from __future__ import annotations
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from session import Session
from test_work_card_recall_agent import setup_case
from work_card_presentations import PresentationResult, WorkCardPresentations


def _fixture(tmp_path, monkeypatch):
    agent, session, _store, _ledger, card = setup_case(tmp_path, monkeypatch)
    run = str(uuid.uuid4())
    pid = server._work_card_presentation_id(session.conversation_id, run, card)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=session.paths)
    repl = SimpleNamespace(session=session, agent=agent)
    return session, repl, bridge, events, pid, card, run


def _reopen(session):
    restored = Session.load(session.paths, session.conversation_id)
    events = []
    bridge = server.WsBridge(emit=events.append, paths=restored.paths)
    repl = SimpleNamespace(session=restored, agent=SimpleNamespace(session=restored))
    return restored, repl, bridge, events


@pytest.mark.parametrize('mode', ['not_committed', 'committed', 'unreadable'])
def test_uncertain_presentation_recovers_exact_id_from_durable_intent(tmp_path, monkeypatch, mode):
    session, repl, bridge, events, pid, card, run = _fixture(tmp_path, monkeypatch)
    original_create = WorkCardPresentations.create
    original_get = WorkCardPresentations.get
    def uncertain(self, *args, **kwargs):
        if mode == 'committed':
            assert original_create(self, *args, **kwargs).status == 'created'
        return PresentationResult('uncertain')
    monkeypatch.setattr(WorkCardPresentations, 'create', uncertain)
    if mode == 'unreadable':
        monkeypatch.setattr(WorkCardPresentations, 'get', lambda *_a, **_k: PresentationResult('corrupt'))
    assert not server._emit_work_card_reference(repl, bridge, session, pid, card, run, active_run_id=None)
    assert not any(e.get('type') == 'work-card.present' for e in events)
    monkeypatch.setattr(WorkCardPresentations, 'create', original_create)
    monkeypatch.setattr(WorkCardPresentations, 'get', original_get)
    restored, repl2, bridge2, replay = _reopen(session)
    server._emit_pending_work_card_references(repl2, bridge2)
    assert [e['presentation_id'] for e in replay if e.get('type') == 'work-card.present'] == [pid]
    assert [r['presentation_id'] for r in restored.messages if r.get('_work_card_reference') == 1] == [pid]
    _, repl3, bridge3, replay2 = _reopen(session)
    server._emit_pending_work_card_references(repl3, bridge3)
    assert [e['presentation_id'] for e in replay2 if e.get('type') == 'work-card.present'] == [pid]


def test_crash_after_intent_before_presentation_recovers(tmp_path, monkeypatch):
    session, repl, bridge, _events, pid, card, run = _fixture(tmp_path, monkeypatch)
    original = WorkCardPresentations.create
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt('synthetic crash')
    monkeypatch.setattr(WorkCardPresentations, 'create', crash)
    with pytest.raises(KeyboardInterrupt):
        server._emit_work_card_reference(repl, bridge, session, pid, card, run, active_run_id=None)
    monkeypatch.setattr(WorkCardPresentations, 'create', original)
    restored, repl2, bridge2, events = _reopen(session)
    server._emit_pending_work_card_references(repl2, bridge2)
    assert [e['presentation_id'] for e in events if e.get('type') == 'work-card.present'] == [pid]
    assert [r['presentation_id'] for r in restored.messages if r.get('_work_card_reference') == 1] == [pid]


def test_crash_after_presentation_before_history_recovers_once(tmp_path, monkeypatch):
    session, repl, bridge, _events, pid, card, run = _fixture(tmp_path, monkeypatch)
    original = Session.append_sent_work_card_reference
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt('synthetic crash')
    monkeypatch.setattr(Session, 'append_sent_work_card_reference', crash)
    with pytest.raises(KeyboardInterrupt):
        server._emit_work_card_reference(repl, bridge, session, pid, card, run, active_run_id=None)
    monkeypatch.setattr(Session, 'append_sent_work_card_reference', original)
    restored, repl2, bridge2, events = _reopen(session)
    server._emit_pending_work_card_references(repl2, bridge2)
    assert [e['presentation_id'] for e in events if e.get('type') == 'work-card.present'] == [pid]
    assert [r['presentation_id'] for r in restored.messages if r.get('_work_card_reference') == 1] == [pid]


def test_no_presentation_attempt_when_intent_persistence_fails(tmp_path, monkeypatch):
    from work_card_reference_outbox import WorkCardReferenceOutbox
    session, repl, bridge, events, pid, card, run = _fixture(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(WorkCardReferenceOutbox, 'begin', lambda *_a, **_k: (_ for _ in ()).throw(OSError('synthetic')))
    monkeypatch.setattr(WorkCardPresentations, 'create', lambda *_a, **_k: calls.append(1))
    assert not server._emit_work_card_reference(repl, bridge, session, pid, card, run, active_run_id=None)
    assert calls == [] and not any(e.get('type') == 'work-card.present' for e in events)
    assert any('无法确认' in e.get('text', '') for e in events)


def test_reference_intent_rejects_bad_session_card_and_run(tmp_path, monkeypatch):
    from work_card_reference_outbox import WorkCardReferenceOutbox, validate_reference_intents
    from work_records import WorkRecordCorrupt, WorkRecordStore
    from work_card_ledger import WorkCardLedger
    session, _repl, _bridge, _events, pid, card, run = _fixture(tmp_path, monkeypatch)
    store = WorkRecordStore(session)
    outbox = WorkCardReferenceOutbox(session, store, WorkCardLedger(session, store))
    with pytest.raises(WorkRecordCorrupt):
        outbox.begin(pid, 'wc-' + '0' * 64, run)
    with pytest.raises(WorkRecordCorrupt):
        outbox.begin(pid, card, str(uuid.uuid4()))
    bad = {'work_card_reference_intents': [{'session_id': 'other', 'project_id': store._bound_project_id,
                                            'presentation_id': pid, 'card_id': card, 'run_id': run}]}
    with pytest.raises(WorkRecordCorrupt):
        validate_reference_intents(bad, session.conversation_id, store._bound_project_id)
    assert outbox.list() == []


def test_interruption_before_intent_clear_reuses_existing_presentation_and_history(tmp_path, monkeypatch):
    from work_card_reference_outbox import WorkCardReferenceOutbox
    session, repl, bridge, _events, pid, card, run = _fixture(tmp_path, monkeypatch)
    original = WorkCardReferenceOutbox.clear
    def crash(*_args, **_kwargs):
        raise KeyboardInterrupt('synthetic crash')
    monkeypatch.setattr(WorkCardReferenceOutbox, 'clear', crash)
    with pytest.raises(KeyboardInterrupt):
        server._emit_work_card_reference(repl, bridge, session, pid, card, run, active_run_id=None)
    monkeypatch.setattr(WorkCardReferenceOutbox, 'clear', original)
    restored, repl2, bridge2, events = _reopen(session)
    server._emit_pending_work_card_references(repl2, bridge2)
    assert [e['presentation_id'] for e in events if e.get('type') == 'work-card.present'] == [pid]
    assert [r['presentation_id'] for r in restored.messages if r.get('_work_card_reference') == 1] == [pid]


def test_reference_intent_recovery_is_bounded_and_reports_remainder(tmp_path, monkeypatch):
    from work_card_reference_outbox import WorkCardReferenceOutbox
    from work_records import WorkRecordStore
    from work_card_ledger import WorkCardLedger
    session, repl, bridge, events, _pid, card, _run = _fixture(tmp_path, monkeypatch)
    store = WorkRecordStore(session)
    outbox = WorkCardReferenceOutbox(session, store, WorkCardLedger(session, store))
    pids = []
    for _ in range(5):
        run = str(uuid.uuid4())
        pid = server._work_card_presentation_id(session.conversation_id, run, card)
        outbox.begin(pid, card, run)
        pids.append(pid)
    monkeypatch.setattr(server, 'WORK_CARD_RECEIPT_RETRY_BATCH', 2)
    server._emit_pending_work_card_references(repl, bridge)
    first = [e['presentation_id'] for e in events if e.get('type') == 'work-card.present']
    assert len(first) == 2 and set(first).issubset(set(pids))
    assert len(outbox.list()) == 3
    assert any('还有 3 条待安全恢复' in e.get('text', '') for e in events)
    assert len([r for r in session.messages if r.get('_work_card_reference') == 1]) == 2


def test_recovered_intent_stops_replaying_after_exact_renderer_receipt(tmp_path, monkeypatch):
    import asyncio
    session, repl, bridge, _events, pid, card, run = _fixture(tmp_path, monkeypatch)
    original = WorkCardPresentations.create
    monkeypatch.setattr(WorkCardPresentations, 'create', lambda *_a, **_k: PresentationResult('uncertain'))
    assert not server._emit_work_card_reference(repl, bridge, session, pid, card, run, active_run_id=None)
    monkeypatch.setattr(WorkCardPresentations, 'create', original)
    restored, repl2, bridge2, events = _reopen(session)
    server._emit_pending_work_card_references(repl2, bridge2)
    event = next(e for e in events if e.get('type') == 'work-card.present')
    class Socket:
        async def send(self, _payload):
            pass
    handler = server.WsSessionHandler(restored.paths)
    async def send_and_receive():
        queue = asyncio.Queue()
        queue.put_nowait(event)
        queue.put_nowait(None)
        await handler._sender(Socket(), queue, bridge2)
        await handler._handle_incoming({'type': 'work-card.received', 'session_id': restored.conversation_id,
                                        'presentation_id': pid, 'card_id': card}, repl2, bridge2)
    asyncio.run(send_and_receive())
    assert any(e.get('type') == 'work-card.received.done' and e.get('status') == 'received' for e in events)
    restored2, repl3, bridge3, replay = _reopen(session)
    server._emit_pending_work_card_references(repl3, bridge3)
    assert not any(e.get('type') == 'work-card.present' for e in replay)
    assert [r['presentation_id'] for r in restored2.messages if r.get('_work_card_reference') == 1] == [pid]


def test_concurrent_same_bridge_atomic_enqueue_and_failed_enqueue_can_retry(tmp_path, monkeypatch):
    import concurrent.futures
    import threading
    from work_card_ledger import WorkCardLedger
    from work_records import WorkRecordStore
    session, repl, _bridge, _events, pid, card, run = _fixture(tmp_path, monkeypatch)
    store = WorkRecordStore(session)
    presentations = WorkCardPresentations(session, store, WorkCardLedger(session, store))
    assert presentations.create(pid, card, run_id=run).status == 'created'
    session.append_sent_work_card_reference(pid, card, run_id=run)
    entered = threading.Event()
    release = threading.Event()
    events = []
    def emit(event):
        if event.get('type') == 'work-card.present':
            events.append(event)
            entered.set()
            release.wait(timeout=3)
    bridge = server.WsBridge(emit=emit, paths=session.paths)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(server._emit_work_card_reference, repl, bridge, session, pid, card, run, active_run_id=None)
        assert entered.wait(3)
        second = pool.submit(server._emit_pending_work_card_references, repl, bridge)
        threading.Event().wait(0.5)
        release.set()
        assert first.result(timeout=5) and second.result(timeout=5)
    assert len(events) == 1
    assert presentations.get(pid).fact.receipt_attempts == 0
    assert len([r for r in session.messages if r.get('presentation_id') == pid]) == 1
    import asyncio
    class Socket:
        async def send(self, _payload):
            pass
    async def send_once():
        queue = asyncio.Queue()
        queue.put_nowait(events[0])
        queue.put_nowait(None)
        await server.WsSessionHandler(session.paths)._sender(Socket(), queue, bridge)
    asyncio.run(send_once())
    assert presentations.get(pid).fact.receipt_attempts == 1

    failures = []
    def flaky(event):
        if event.get('type') == 'work-card.present':
            if not failures:
                failures.append('failed')
                raise OSError('synthetic enqueue failure before acceptance')
            failures.append('accepted')
    bridge2 = server.WsBridge(emit=flaky, paths=session.paths)
    assert not server._emit_work_card_reference(repl, bridge2, session, pid, card, run, active_run_id=None)
    assert server._emit_work_card_reference(repl, bridge2, session, pid, card, run, active_run_id=None)
    assert failures == ['failed', 'accepted']
    assert len([r for r in session.messages if r.get('presentation_id') == pid]) == 1


def test_unrecoverable_first_batch_does_not_starve_later_intents_across_reconnect(tmp_path, monkeypatch):
    from work_card_reference_outbox import WorkCardReferenceOutbox
    from work_records import WorkRecordStore
    from work_card_ledger import WorkCardLedger
    session, repl, bridge, events, _pid, card, _run = _fixture(tmp_path, monkeypatch)
    store = WorkRecordStore(session)
    outbox = WorkCardReferenceOutbox(session, store, WorkCardLedger(session, store))
    rows = []
    for _ in range(22):
        run = str(uuid.uuid4())
        rows.append((server._work_card_presentation_id(session.conversation_id, run, card), run))
    rows.sort()
    for pid, run in rows:
        outbox.begin(pid, card, run)
    blocked = {pid for pid, _run in rows[:20]}
    original = server._emit_work_card_reference
    attempts = []
    def selective(*args, **kwargs):
        pid = args[3]
        if kwargs.get('recover_reference_intent'):
            attempts.append(pid)
            if pid in blocked:
                return False
        return original(*args, **kwargs)
    monkeypatch.setattr(server, '_emit_work_card_reference', selective)
    server._emit_pending_work_card_references(repl, bridge)
    assert len(attempts) == 20 and set(attempts) == blocked
    assert not any(e.get('type') == 'work-card.present' for e in events)
    assert any('待安全恢复' in e.get('text', '') for e in events)
    _restored, repl2, bridge2, events2 = _reopen(session)
    attempts.clear()
    server._emit_pending_work_card_references(repl2, bridge2)
    assert len(attempts) == 20
    assert set(pid for pid, _run in rows[20:]).issubset(set(attempts))
    assert set(e['presentation_id'] for e in events2 if e.get('type') == 'work-card.present') == set(pid for pid, _run in rows[20:])
    assert {row['presentation_id'] for row in outbox.list()} == blocked
    assert any('待安全恢复' in e.get('text', '') for e in events2)


def test_crash_after_cursor_checkpoint_retains_intents_and_advances(tmp_path, monkeypatch):
    from work_card_reference_outbox import WorkCardReferenceOutbox
    from work_records import WorkRecordStore
    from work_card_ledger import WorkCardLedger
    session, repl, bridge, _events, _pid, card, _run = _fixture(tmp_path, monkeypatch)
    store = WorkRecordStore(session)
    outbox = WorkCardReferenceOutbox(session, store, WorkCardLedger(session, store))
    ids = []
    for _ in range(3):
        run = str(uuid.uuid4())
        ids.append((server._work_card_presentation_id(session.conversation_id, run, card), run))
    ids.sort()
    for pid, run in ids:
        outbox.begin(pid, card, run)
    monkeypatch.setattr(server, 'WORK_CARD_RECEIPT_RETRY_BATCH', 2)
    original = server._emit_work_card_reference
    monkeypatch.setattr(server, '_emit_work_card_reference', lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt('crash')))
    with pytest.raises(KeyboardInterrupt):
        server._emit_pending_work_card_references(repl, bridge)
    assert len(outbox.list()) == 3
    monkeypatch.setattr(server, '_emit_work_card_reference', original)
    restored, repl2, bridge2, events = _reopen(session)
    server._emit_pending_work_card_references(repl2, bridge2)
    recovered = {e['presentation_id'] for e in events if e.get('type') == 'work-card.present'}
    assert ids[-1][0] in recovered and len(recovered) == 2
    assert len(outbox.list()) == 1
    assert len([r for r in restored.messages if r.get('_work_card_reference') == 1]) == 2
