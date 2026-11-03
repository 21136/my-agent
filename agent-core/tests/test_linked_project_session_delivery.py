import sys, uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture, _session
from multi_project_work_record_bindings import ConversationWorkRecordBindings, ReadOnlyWorkRecordStore, _reader_for
from work_records import WorkRecordStore, _digest
from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_session_copy_request_id
from server import WsBridge
import server


def setup_copy(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    source = _reader_for(sources['project-a'].store)
    source.add_requirement(tasks[0].item_id, 'second requirement', event_id='extra')
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns = ConversationWorkRecordBindings(parent)
    rows = ns.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)
    run = str(uuid.uuid4())
    for row in rows:
        ns.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            reservation_id='res-'+row['task_id'], parent_run_id=run, turn_epoch=1,
            project_id=row['project_id'], task_id=row['task_id'])
        ns.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            sequence=1, status='model_turn_finished', finish_reason='completed')
    args = dict(project_id='project-a', binding_ids=[rows[0]['binding_id']], source=sources['project-a'])
    preview = ns.prepare_project_session_copy(**args)
    ns.prepare_project_session_copy(**args, expected=preview, commit=True, is_current=lambda: True)
    store = WorkRecordStore(parent); events = []; bridge = WsBridge(emit=events.append, paths=parent.paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    return parent, ns, rows, store, events, bridge, repl


def test_only_confirmed_a_offered_and_replay_never_reads_source(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    original_read = WorkRecordStore._read
    def parent_only(store):
        if store._conversation_id != parent.conversation_id:
            raise AssertionError('source read forbidden')
        return original_read(store)
    monkeypatch.setattr(WorkRecordStore, '_read', parent_only)
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 1
    cards = WorkCardLedger(parent, store).list_cards()
    assert len(cards) == 1
    snap = cards[0]['snapshot']
    assert snap['kind'] == 'linked_project_session_copy' and snap['source']['project_id'] == 'project-a'
    assert snap['source']['mapping_claim'] == 'not_asserted'
    assert len(snap['source']['items'][0]['requirements']) == 2
    assert all(f['operation_result'] == 'unknown' and f['verification'] == 'not_verified' for f in snap['source']['worker_facts'])
    assert len([e for e in events if e.get('type') == 'work-card.present']) == 1
    assert ns.get_binding(rows[0]['binding_id'])['project_session_copy']['intent']['state'] == 'reference_queued'
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 0
    assert len(WorkCardLedger(parent, store).list_cards()) == 1


def test_wrong_session_and_after_save_crash_recover_without_duplicate(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    repl.agent.session = object()
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 0
    repl.agent.session = parent
    monkeypatch.setattr(server, '_emit_work_card_reference', lambda *a, **k: False)
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 0
    card_id = WorkCardLedger(parent, store).list_cards()[0]['card_id']
    assert ns.get_binding(rows[0]['binding_id'])['project_session_copy']['intent']['state'] == 'pending'
    monkeypatch.undo()
    server._emit_pending_work_card_references(repl, WsBridge(emit=events.append, paths=parent.paths))
    assert [r['card_id'] for r in WorkCardLedger(parent, store).list_cards()] == [card_id]
    assert len([m for m in parent.messages if m.get('role') == 'work_card_presentation' and m.get('card_id') == card_id]) == 1


def test_forged_snapshot_and_foreign_parent_rejected(tmp_path):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    copy = ns.get_binding(rows[0]['binding_id'])['project_session_copy']
    snap = build_linked_project_session_copy_snapshot(parent.conversation_id, rows[0]['binding_id'], copy)
    req = linked_project_session_copy_request_id(parent.conversation_id, snap.copy_id)
    forged_source = dict(snap.source, mapping_claim='current')
    assert WorkCardLedger(parent, store).save_linked_project_session_copy_snapshot(req, replace(snap, source=forged_source)).status == 'rejected'
    forged_source = dict(snap.source, project_id='project-b')
    assert WorkCardLedger(parent, store).save_linked_project_session_copy_snapshot(req, replace(snap, source=forged_source)).status == 'rejected'
    foreign = _session(tmp_path, 'foreign-parent', 'project-a')
    assert WorkCardLedger(foreign, WorkRecordStore(foreign)).save_linked_project_session_copy_snapshot(req, snap).status == 'rejected'
    assert WorkCardLedger(parent, store).list_cards() == []


def test_restart_after_reference_persisted_before_mark_reuses_history(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    monkeypatch.setattr(ConversationWorkRecordBindings, 'mark_project_session_copy_reference_queued',
        lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic crash')))
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 0
    assert ns.get_binding(rows[0]['binding_id'])['project_session_copy']['intent']['state'] == 'pending'
    card_id = WorkCardLedger(parent, store).list_cards()[0]['card_id']
    monkeypatch.undo()
    reopened = _session(tmp_path, parent.conversation_id, 'project-a')
    reopened._messages_fully_loaded = False
    reopened.ensure_messages_loaded()
    new_repl = SimpleNamespace(session=reopened, agent=SimpleNamespace(session=reopened))
    server._emit_pending_work_card_references(new_repl, WsBridge(emit=events.append, paths=parent.paths))
    assert [c['card_id'] for c in WorkCardLedger(reopened, WorkRecordStore(reopened)).list_cards()] == [card_id]
    assert ConversationWorkRecordBindings(reopened).get_binding(rows[0]['binding_id'])['project_session_copy']['intent']['state'] == 'reference_queued'
    assert len([m for m in reopened.messages if m.get('role') == 'work_card_presentation' and m.get('card_id') == card_id]) == 1




def test_queue_then_send_failure_recovers_without_false_received(tmp_path):
    from work_card_presentations import WorkCardPresentations
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    def broken(event):
        if event.get('type') == 'work-card.present':
            raise OSError('send failure')
        events.append(event)
    bridge.emit = broken
    assert server._resume_linked_project_session_copies(repl, bridge, parent, store, emit_event=True) == 0
    assert ns.get_binding(rows[0]['binding_id'])['project_session_copy']['intent']['state'] == 'reference_queued'
    ledger = WorkCardLedger(parent, store)
    card_id = ledger.list_cards()[0]['card_id']
    refs = [m for m in parent.messages if m.get('role') == 'work_card_presentation' and m.get('card_id') == card_id]
    assert len(refs) == 1
    fact = WorkCardPresentations(parent, store, ledger).get(refs[0]['presentation_id']).fact
    assert fact.receipt_state == 'pending' and fact.received_at is None and fact.acknowledged_at is None
    server._emit_pending_work_card_references(repl, WsBridge(emit=events.append, paths=parent.paths))
    assert len([e for e in events if e.get('type') == 'work-card.present' and e.get('card_id') == card_id]) == 1
    assert len(WorkCardLedger(parent, store).list_cards()) == 1


def test_unpublishable_source_is_rejected_before_confirmation(tmp_path):
    from multi_project_work_record_bindings import WorkRecordBindingConflict
    parent, sources, tasks = _fixture(tmp_path)
    source = _reader_for(sources['project-a'].store)
    extra = source.create_item(title='other item', scope={'kind': 'project', 'project_id': 'project-a'})
    source.add_requirement(extra['item_id'], 'x' * 4097, event_id='long-text')
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns = ConversationWorkRecordBindings(parent)
    row = ns.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)[0]
    ns.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        reservation_id='reservation', parent_run_id=str(uuid.uuid4()), turn_epoch=1,
        project_id=row['project_id'], task_id=row['task_id'])
    ns.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        sequence=1, status='model_turn_finished', finish_reason='completed')
    with pytest.raises(WorkRecordBindingConflict):
        ns.prepare_project_session_copy(project_id='project-a', binding_ids=[row['binding_id']], source=sources['project-a'])
    assert 'project_session_copy' not in ns.get_binding(row['binding_id'])
    assert WorkCardLedger(parent, WorkRecordStore(parent)).list_cards() == []


def test_existing_binding_refuses_new_snapshot_after_other_item_changes(tmp_path):
    from multi_project_work_record_bindings import WorkRecordBindingConflict
    parent, sources, tasks = _fixture(tmp_path)
    source = _reader_for(sources['project-a'].store)
    other = source.create_item(title='other', scope={'kind': 'project', 'project_id': 'project-a'})
    source.add_requirement(other['item_id'], 'before', event_id='before')
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns = ConversationWorkRecordBindings(parent)
    row = ns.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)[0]
    ns.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        reservation_id='reservation', parent_run_id=str(uuid.uuid4()), turn_epoch=1,
        project_id=row['project_id'], task_id=row['task_id'])
    ns.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        sequence=1, status='model_turn_finished', finish_reason='completed')
    args = dict(project_id='project-a', binding_ids=[row['binding_id']], source=sources['project-a'])
    first_preview = ns.prepare_project_session_copy(**args)
    first = ns.prepare_project_session_copy(**args, expected=first_preview, commit=True, is_current=lambda: True)
    source.add_requirement(other['item_id'], 'after', event_id='after')
    # Never ask for another confirmation when the current schema cannot save it.
    with pytest.raises(WorkRecordBindingConflict, match='immutable project session copy'):
        ns.prepare_project_session_copy(**args)
    assert ns.get_binding(row['binding_id'])['project_session_copy'] == first
