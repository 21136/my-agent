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
