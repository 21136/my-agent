import sys, uuid
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture, _session
from multi_project_work_record_bindings import ConversationWorkRecordBindings, WorkRecordBindingConflict, _reader_for, _issue_authorized_project_record_source
from work_records import WorkRecordStore, _digest
from work_card_ledger import WorkCardLedger
from server import WsBridge, _emit_pending_work_card_references


def test_refresh_append_and_restart_replay_without_source_or_worker(tmp_path, monkeypatch):
    parent, sources, tasks = _fixture(tmp_path)
    source = _reader_for(sources['project-a'].store)
    other = source.create_item(title='second item', scope={'kind': 'project', 'project_id': 'project-a'})
    source.add_requirement(other['item_id'], 'before', event_id='before')
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns = ConversationWorkRecordBindings(parent)
    row = ns.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)[0]
    old_run = str(uuid.uuid4())
    ns.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'], reservation_id='reservation',
        parent_run_id=old_run, turn_epoch=1, project_id=row['project_id'], task_id=row['task_id'])
    ns.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'], sequence=1,
        status='model_turn_finished', finish_reason='completed')
    kwargs = dict(project_id='project-a', binding_ids=[row['binding_id']], source=sources['project-a'])
    old = ns.prepare_project_session_copy(**kwargs)
    ns.prepare_project_session_copy(**kwargs, expected=old, commit=True, is_current=lambda: True)
    store = WorkRecordStore(parent); events=[]; repl=SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    _emit_pending_work_card_references(repl, WsBridge(emit=events.append, paths=parent.paths))
    first_card = WorkCardLedger(parent, store).list_cards()[0]
    source.add_requirement(other['item_id'], 'after', event_id='after')
    fresh = _issue_authorized_project_record_source('fresh', parent.conversation_id, 'project-a', sources['project-a'].store)
    refresh_id, refresh_run = str(uuid.uuid4()), str(uuid.uuid4())
    opts = dict(project_id='project-a', binding_ids=[row['binding_id']], source=fresh,
                refresh_id=refresh_id, refresh_run_id=refresh_run, refresh_turn_epoch=5)
    with pytest.raises(PermissionError):
        ns.prepare_project_session_refresh(**dict(opts, source=sources['project-b']))
    with pytest.raises(PermissionError):
        ns.prepare_project_session_refresh(**dict(opts, source=sources['project-a']))
    preview = ns.prepare_project_session_refresh(**opts)
    with pytest.raises(WorkRecordBindingConflict):
        ns.prepare_project_session_refresh(**opts, expected=preview, commit=True, is_current=lambda session, run, epoch: False)
    assert ns.list_project_session_refreshes() == []
    source.add_requirement(other['item_id'], 'changed during confirmation', event_id='interleave')
    with pytest.raises(WorkRecordBindingConflict):
        ns.prepare_project_session_refresh(**opts, expected=preview, commit=True, is_current=lambda session, run, epoch: session is parent and run == uuid.UUID(refresh_run).hex and epoch == 5)
    assert ns.list_project_session_refreshes() == []
    preview = ns.prepare_project_session_refresh(**opts)
    saved = ns.prepare_project_session_refresh(**opts, expected=preview, commit=True, is_current=lambda session, run, epoch: session is parent and run == uuid.UUID(refresh_run).hex and epoch == 5)
    assert ns.prepare_project_session_refresh(**opts, expected=preview, commit=True, is_current=lambda session, run, epoch: session is parent and run == uuid.UUID(refresh_run).hex and epoch == 5) == saved
    assert ns.get_binding(row['binding_id'])['project_session_copy']['snapshot'] == old
    orig = WorkRecordStore._read
    def parent_only(store):
        assert store._conversation_id == parent.conversation_id
        return orig(store)
    monkeypatch.setattr(WorkRecordStore, '_read', parent_only)
    import server
    emit_reference = server._emit_work_card_reference
    monkeypatch.setattr(server, '_emit_work_card_reference', lambda *a, **kw: False)
    server._resume_linked_project_session_refreshes(repl, WsBridge(emit=events.append, paths=parent.paths), parent, store, emit_event=True)
    assert ns.list_project_session_refreshes()[0]['copy']['intent']['state'] == 'pending'
    assert len(WorkCardLedger(parent, store).list_cards()) == 2
    monkeypatch.setattr(server, '_emit_work_card_reference', emit_reference)
    parent = _session(tmp_path, parent.conversation_id, 'project-a')
    parent._messages_fully_loaded = False
    parent.ensure_messages_loaded()
    store = WorkRecordStore(parent)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    _emit_pending_work_card_references(repl, WsBridge(emit=events.append, paths=parent.paths))
    cards = WorkCardLedger(parent, store).list_cards()
    assert len(cards) == 2 and cards[0] == first_card
    new = cards[1]
    assert new['snapshot']['source']['worker_facts'][0]['host_start']['parent_run_id'] == old_run
    refs = [m for m in parent.messages if m.get('role') == 'work_card_presentation' and m.get('card_id') == new['card_id']]
    assert len(refs) == 1 and refs[0]['run_id'] == refresh_run
    _emit_pending_work_card_references(repl, WsBridge(emit=events.append, paths=parent.paths))
    assert len(WorkCardLedger(parent, store).list_cards()) == 2


    # A syntactically valid presentation for another real card must not be
    # accepted merely because this refresh's intent was already queued.
    from server import _work_card_presentation_id
    from work_card_presentations import WorkCardPresentations
    reopened_ns = ConversationWorkRecordBindings(parent)
    wrong_card = first_card['card_id']
    wrong_presentation = _work_card_presentation_id(parent.conversation_id, refresh_run, wrong_card)
    presentations = WorkCardPresentations(parent, store, WorkCardLedger(parent, store))
    assert presentations.create(wrong_presentation, wrong_card, run_id=refresh_run).status == 'created'
    assert reopened_ns.mark_project_refresh_reference_queued(refresh_id=refresh_id, card_id=wrong_card,
        presentation_id=wrong_presentation, is_current=lambda: True) == 'rejected'
    current_states = iter([True, False])
    assert reopened_ns.mark_project_refresh_reference_queued(refresh_id=refresh_id, card_id=new['card_id'],
        presentation_id=refs[0]['presentation_id'], is_current=lambda: next(current_states)) == 'rejected'
    assert reopened_ns.mark_project_refresh_reference_queued(refresh_id=refresh_id, card_id=new['card_id'],
        presentation_id=refs[0]['presentation_id'], is_current=lambda: True) == 'already_queued'
    assert len(WorkCardLedger(parent, store).list_cards()) == 2
