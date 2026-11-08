from dataclasses import replace
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings, WorkRecordBindingConflict, _reader_for
from work_records import _digest


def test_authorized_project_session_preview_is_complete_within_source_and_minimal(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    source = _reader_for(sources['project-a'].store)
    source.add_requirement(tasks[0].item_id, 'second requirement', event_id='req-extra')
    other = source.create_item(title='second item', scope={'kind':'project','project_id':'project-a'})
    source.add_requirement(other['item_id'], 'other requirement', event_id='req-other')
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    bindings = ConversationWorkRecordBindings(parent)
    rows = bindings.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)
    r = rows[0]
    bindings.record_worker_start(binding_id=r['binding_id'], worker_id=r['worker_id'], run_id=r['run_id'],
        reservation_id='reservation-a', parent_run_id='parent-run', turn_epoch=1, project_id=r['project_id'], task_id=r['task_id'])
    bindings.record_worker_lifecycle(binding_id=r['binding_id'], worker_id=r['worker_id'], run_id=r['run_id'],
        sequence=1, status='model_turn_finished', finish_reason='completed')
    before = source._path.read_bytes()
    preview = bindings.prepare_project_session_copy(project_id='project-a', binding_ids=[r['binding_id']], source=sources['project-a'])
    assert preview['scope'] == 'authorized_source_session_only'
    assert preview['project_id'] == 'project-a' and len(preview['items']) == 2
    assert sum(len(item['requirements']) for item in preview['items']) == 3
    assert preview['worker_facts'][0]['operation_result'] == 'unknown'
    assert preview['worker_facts'][0]['verification'] == 'not_verified'
    assert 'history' not in str(preview) and 'task_input' not in str(preview) and 'project-b' not in str(preview)
    assert source._path.read_bytes() == before
    assert bindings.prepare_project_session_copy(project_id='project-a', binding_ids=[r['binding_id']], source=sources['project-a'], expected=preview) == preview
    source.add_requirement(other['item_id'], 'later', event_id='later')
    with pytest.raises(WorkRecordBindingConflict):
        bindings.prepare_project_session_copy(project_id='project-a', binding_ids=[r['binding_id']], source=sources['project-a'], expected=preview)
    with pytest.raises(PermissionError):
        bindings.prepare_project_session_copy(project_id='project-a', binding_ids=[r['binding_id']], source=sources['project-b'])


def test_batch_project_session_copy_preview_lists_exact_sources_and_rejects_partial_commit(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    bindings = ConversationWorkRecordBindings(parent)
    rows = bindings.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)
    for index, row in enumerate(rows):
        bindings.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            reservation_id=f'res-{index}', parent_run_id='parent-run', turn_epoch=1,
            project_id=row['project_id'], task_id=row['task_id'])
        bindings.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            sequence=1, status='model_turn_finished', finish_reason='completed')
    entries = [
        {'project_id': project_id,
         'binding_ids': [row['binding_id'] for row in rows if row['project_id'] == project_id],
         'source': sources[project_id]}
        for project_id in sorted({row['project_id'] for row in rows})
    ]
    before = bindings._path.read_bytes()
    preview = bindings.prepare_project_session_copies_batch(entries=entries)
    assert preview['scope'] == 'authorized_source_session_only'
    assert preview['destination'] == {
        'conversation_id': parent.conversation_id,
        'scope': 'current_parent_conversation_binding_store',
    }
    assert [row['project_id'] for row in preview['sources']] == sorted(sources)
    assert {row['source_session_id'] for row in preview['sources']} == {
        sources[project_id].store.session_id for project_id in sources
    }
    for row in preview['sources']:
        expected = {f"{row['project_id'][-1].upper()} requirement"}
        actual = {req['text'] for item in row['snapshot']['items'] for req in item['requirements']}
        assert expected <= actual
        other_project_requirements = {f"{pid[-1].upper()} requirement" for pid in sources if pid != row['project_id']}
        assert not (actual & other_project_requirements)
        assert row['snapshot']['project_id'] == row['project_id']
        assert row['snapshot']['source_session_id'] == row['source_session_id']
    assert bindings._path.read_bytes() == before

    # A stale/partial approval payload is rejected without persisting any source.
    with pytest.raises(WorkRecordBindingConflict):
        bindings.prepare_project_session_copies_batch(
            entries=entries, expected={**preview, 'sources': preview['sources'][:-1]},
            is_current=lambda: True, commit=True,
        )
    assert bindings._path.read_bytes() == before
    assert not [row for row in bindings.list_bindings() if 'project_session_copy' in row]

    bindings.prepare_project_session_copies_batch(
        entries=entries, expected=preview, is_current=lambda: True, commit=True,
    )
    saved = [row for row in bindings.list_bindings() if 'project_session_copy' in row]
    assert len(saved) == len(entries)
    assert {row['project_session_copy']['snapshot']['project_id'] for row in saved} == set(sources)


def test_batch_project_session_copy_rejects_source_change_after_confirmation_preview(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    bindings = ConversationWorkRecordBindings(parent)
    rows = bindings.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)
    for index, row in enumerate(rows):
        bindings.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            reservation_id=f'res-{index}', parent_run_id='parent-run', turn_epoch=1,
            project_id=row['project_id'], task_id=row['task_id'])
        bindings.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            sequence=1, status='model_turn_finished', finish_reason='completed')
    entries = [
        {'project_id': project_id,
         'binding_ids': [row['binding_id'] for row in rows if row['project_id'] == project_id],
         'source': sources[project_id]}
        for project_id in sorted(sources)
    ]
    preview = bindings.prepare_project_session_copies_batch(entries=entries)
    source_reader = _reader_for(sources['project-a'].store)
    changed_item = source_reader.create_item(
        title='new source item after preview', scope={'kind': 'project', 'project_id': 'project-a'},
    )
    source_reader.add_requirement(changed_item['item_id'], 'changed after preview', event_id='after-preview')
    before = bindings._path.read_bytes()

    with pytest.raises(WorkRecordBindingConflict, match='changed after preview'):
        bindings.prepare_project_session_copies_batch(
            entries=entries, expected=preview, is_current=lambda: True, commit=True,
        )

    assert bindings._path.read_bytes() == before
    assert not [row for row in bindings.list_bindings() if 'project_session_copy' in row]


def test_batch_project_session_copy_rechecks_live_confirmation_before_publish(tmp_path):
    parent, sources, tasks = _fixture(tmp_path)
    tasks = [replace(t, source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    bindings = ConversationWorkRecordBindings(parent)
    rows = bindings.bind_existing_tasks(batch_id='batch', tasks=tasks, authorized_sources=sources)
    for index, row in enumerate(rows):
        bindings.record_worker_start(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            reservation_id=f'res-{index}', parent_run_id='parent-run', turn_epoch=1,
            project_id=row['project_id'], task_id=row['task_id'])
        bindings.record_worker_lifecycle(binding_id=row['binding_id'], worker_id=row['worker_id'], run_id=row['run_id'],
            sequence=1, status='model_turn_finished', finish_reason='completed')
    entries = [
        {'project_id': project_id,
         'binding_ids': [row['binding_id'] for row in rows if row['project_id'] == project_id],
         'source': sources[project_id]}
        for project_id in sorted(sources)
    ]
    preview = bindings.prepare_project_session_copies_batch(entries=entries)
    before = bindings._path.read_bytes()

    with pytest.raises(WorkRecordBindingConflict, match='no longer current'):
        bindings.prepare_project_session_copies_batch(
            entries=entries, expected=preview, is_current=lambda: False, commit=True,
        )

    assert bindings._path.read_bytes() == before
    assert not [row for row in bindings.list_bindings() if 'project_session_copy' in row]
