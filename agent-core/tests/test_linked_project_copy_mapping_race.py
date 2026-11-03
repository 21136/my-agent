from dataclasses import replace
from pathlib import Path
import sys
import threading
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings
from project_switch import record_project_session, read_project_sessions
from work_records import _digest


def test_mapping_changes_after_last_check_never_claim_current_mapping(tmp_path, monkeypatch):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns = ConversationWorkRecordBindings(parent)
    rows = ns.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)
    row = rows[0]; source = sources['project-a']
    ns.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        reservation_id='reservation', parent_run_id='parent-run', turn_epoch=1,
        project_id=row['project_id'], task_id=row['task_id'])
    ns.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        sequence=1, status='model_turn_finished', finish_reason='completed')
    record_project_session(parent.paths, 'project-a', source.store.session_id)
    preview = ns.prepare_project_session_copy(project_id='project-a', binding_ids=[row['binding_id']], source=source)
    assert preview['mapping_claim'] == 'not_asserted'
    publish = ns._publish
    reached, changed = threading.Event(), threading.Event()
    failures = []
    def remap():
        try:
            if not reached.wait(5):
                raise AssertionError('copy did not reach publication')
            record_project_session(parent.paths, 'project-a', 'different-session')
        except Exception as exc:
            failures.append(exc)
        finally:
            changed.set()
    writer = threading.Thread(target=remap)
    def concurrent_remap(data):
        reached.set()
        assert changed.wait(5)
        assert not failures
        publish(data)
    monkeypatch.setattr(ns, '_publish', concurrent_remap)
    writer.start()
    try:
        saved = ns.prepare_project_session_copy(project_id='project-a', binding_ids=[row['binding_id']], source=source,
            expected=preview, commit=True, is_current=lambda: read_project_sessions(parent.paths).get('project-a') == source.store.session_id)
    finally:
        reached.set()
        writer.join(timeout=5)
    assert not writer.is_alive() and not failures
    assert read_project_sessions(parent.paths)['project-a'] == 'different-session'
    reopened = ConversationWorkRecordBindings(parent).get_binding(row['binding_id'])['project_session_copy']
    assert reopened == saved and saved['snapshot']['mapping_claim'] == 'not_asserted'
    assert saved['snapshot']['source_session_id'] == source.store.session_id
    assert saved['snapshot']['project_id'] == source.store.project_id == 'project-a'


def test_pre_marker_snapshot_remains_readable_without_current_mapping_claim(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns = ConversationWorkRecordBindings(parent)
    row = ns.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)[0]
    ns.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        reservation_id='reservation', parent_run_id='parent-run', turn_epoch=1, project_id=row['project_id'], task_id=row['task_id'])
    ns.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
        sequence=1, status='model_turn_finished', finish_reason='completed')
    args = dict(project_id='project-a', binding_ids=[row['binding_id']], source=sources['project-a'])
    preview = ns.prepare_project_session_copy(**args)
    ns.prepare_project_session_copy(**args, expected=preview, commit=True, is_current=lambda: True)
    data = ns._read()
    saved = data['batches'][0]['bindings'][0]['project_session_copy']
    saved['snapshot'].pop('mapping_claim')
    saved['intent']['copy_id'] = _digest(saved['snapshot'])
    ns._validate_payload(data)
    ns._publish(data)
    old = ConversationWorkRecordBindings(parent).get_binding(row['binding_id'])['project_session_copy']['snapshot']
    assert old['scope'] == 'authorized_source_session_only' and 'mapping_claim' not in old
