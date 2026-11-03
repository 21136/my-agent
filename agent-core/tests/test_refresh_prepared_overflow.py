import copy
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_multi_project_work_record_bindings import _session, _fixture
from dataclasses import replace
from work_records import WorkRecordStore, _digest
from multi_project_work_record_bindings import _reader_for
from multi_project_work_record_bindings import ConversationWorkRecordBindings, ReadOnlyWorkRecordStore, _issue_authorized_project_record_source
import multi_project_work_record_bindings as bindings


def test_confirmed_overflow_is_recoverable_without_new_segment_or_source(tmp_path,monkeypatch):
    parent,sources,tasks=_fixture(tmp_path)
    source_reader=_reader_for(sources['project-a'].store)
    source_reader.add_requirement(tasks[0].item_id,'extra',event_id='extra')
    tasks=[replace(t,source_item_digest=_digest(sources[t.project_id].store.get_item(t.item_id))) for t in tasks]
    ns=ConversationWorkRecordBindings(parent)
    rows=ns.bind_existing_tasks(batch_id='batch',tasks=tasks,authorized_sources=sources)
    for row in rows:
        ns.record_worker_start(binding_id=row['binding_id'],worker_id=row['worker_id'],run_id=row['run_id'],
            reservation_id='res-'+row['task_id'],parent_run_id=str(uuid.uuid4()),turn_epoch=1,
            project_id=row['project_id'],task_id=row['task_id'])
        ns.record_worker_lifecycle(binding_id=row['binding_id'],worker_id=row['worker_id'],run_id=row['run_id'],
            sequence=1,status='model_turn_finished',finish_reason='completed')
    store=WorkRecordStore(parent)
    parent.save()
    source_session=_session(tmp_path,'project-a-session','project-a')
    source_session.save()
    def options(auth):
        return dict(project_id='project-a',binding_ids=[rows[0]['binding_id']],
            source=_issue_authorized_project_record_source(auth,parent.conversation_id,'project-a',ReadOnlyWorkRecordStore(source_session)),
            refresh_id=str(uuid.uuid4()),refresh_run_id=str(uuid.uuid4()),refresh_turn_epoch=5)
    opts1=options('fresh-1');preview1=ns.prepare_project_session_refresh(**opts1)
    first=ns.prepare_project_session_refresh(**opts1,expected=preview1,commit=True,is_current=lambda *_:True)
    # Build valid, isolated v1 history up to the real 100-entry limit.
    data=ns._read()
    # Existing rows model already-queued historical refreshes; only the newly
    # prepared refresh should be eligible for recovery below.
    data["project_refreshes"][0]["copy"]["intent"]["state"]="reference_queued"
    first=copy.deepcopy(data["project_refreshes"][0])
    for n in range(1,100):
        row=copy.deepcopy(first)
        row['refresh_id']=str(uuid.uuid4());row['run_id']=str(uuid.uuid4());row['authorization_id']=f'old-{n}'
        data['project_refreshes'].append(row)
    data['revision']+=1;ns._validate_payload(data);ns._publish(data)
    opts2=options('fresh-2');preview2=ns.prepare_project_session_refresh(**opts2)
    old=ns._path.read_bytes()
    with pytest.raises(bindings.WorkRecordBindingConflict):
        ns.prepare_project_session_refresh(**opts2,expected=preview2,commit=True,is_current=lambda *_:False)
    assert ns._path.read_bytes()==old
    # Child commits to the real parent sidecar and then hard-exits; no service/model.
    script='''
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from paths import AgentPaths
from session import Session,SessionMeta
from multi_project_work_record_bindings import ConversationWorkRecordBindings,ReadOnlyWorkRecordStore,_issue_authorized_project_record_source
root=Path(sys.argv[2]);spec=json.loads(sys.argv[3])
paths=AgentPaths(agent_root=root,workspace=root/'workspace',data=root/'data',evolve=root/'evolve')
parent=Session('parent-dialog',paths.data/'sessions'/'parent-dialog','',SessionMeta(project_id='project-a'),[],paths)
source_session=Session('project-a-session',paths.data/'sessions'/'project-a-session','',SessionMeta(project_id='project-a'),[],paths)
source=_issue_authorized_project_record_source('fresh-2',parent.conversation_id,'project-a',ReadOnlyWorkRecordStore(source_session))
ns=ConversationWorkRecordBindings(parent)
original_writer=ConversationWorkRecordBindings._write_refresh_segment_locked
def write_segment_then_crash(self,row):
    entry=original_writer(self,row)
    if row['refresh_id']==spec['refresh_id']:
        # Confirmed prepared row is durable; segment content is published but not indexed yet.
        os._exit(73)
    return entry
ConversationWorkRecordBindings._write_refresh_segment_locked=write_segment_then_crash
ns.prepare_project_session_refresh(project_id='project-a',binding_ids=[spec['binding_id']],source=source,
 refresh_id=spec['refresh_id'],refresh_run_id=spec['run_id'],refresh_turn_epoch=5,
 expected=spec['preview'],commit=True,is_current=lambda *_:True)
raise AssertionError('did not reach the injected post-segment/pre-index crash point')
'''
    core=Path(__file__).resolve().parents[1]
    spec={'binding_id':rows[0]['binding_id'],'refresh_id':opts2['refresh_id'],'run_id':opts2['refresh_run_id'],'preview':preview2}
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','PATHEXT'}}
    env.update({'TEMP':str(tmp_path),'TMP':str(tmp_path),'HOME':str(tmp_path),'USERPROFILE':str(tmp_path),'APPDATA':str(tmp_path),'LOCALAPPDATA':str(tmp_path),'PYTHONNOUSERSITE':'1'})
    child=subprocess.run([sys.executable,'-I','-B','-c',script,str(core),str(tmp_path),json.dumps(spec)],cwd=str(core),env=env,text=True,capture_output=True,timeout=20)
    assert child.returncode==73,child.stderr
    reopened=ConversationWorkRecordBindings(parent)
    all_rows=reopened.list_project_session_refreshes()
    assert len(all_rows)==101 and all_rows[0]==first
    second=all_rows[-1]
    staged=reopened._read()
    assert staged['refresh_prepared']==second
    assert len(staged.get('refresh_segments',[]))==0
    # Crash window: the durable prepared intent exists while its content-addressed
    # segment file is not yet referenced by the parent index.
    orphan_segments=list(parent.session_dir.glob('.project-refresh-*.json'))
    assert len(orphan_segments)==1
    assert ns.prepare_project_session_refresh(**opts2,expected=preview2,commit=True,is_current=lambda *_:True)==second
    assert reopened._read()['refresh_prepared']==second

    from work_card_ledger import WorkCardLedger,linked_project_refresh_request_id
    from server import WsBridge,_resume_linked_project_session_refreshes
    ledger_store=WorkRecordStore(parent)
    events=[]
    bridge=WsBridge(emit=events.append,paths=parent.paths)
    repl=SimpleNamespace(session=parent,agent=SimpleNamespace(session=parent))
    monkeypatch.setattr(bindings,'_reader_for',lambda *_: (_ for _ in ()).throw(AssertionError('source read forbidden')))
    assert _resume_linked_project_session_refreshes(
        repl,bridge,parent,ledger_store,binding_ids=[rows[0]['binding_id']],emit_event=True,
    )==1
    recovered=ConversationWorkRecordBindings(parent)
    committed=recovered._read()
    assert committed.get('refresh_prepared') is None
    assert len(committed['refresh_segments'])==1
    rows_after=recovered.list_project_session_refreshes()
    assert len(rows_after)==101 and rows_after[-1]['refresh_id']==opts2['refresh_id']
    assert rows_after[-1]['copy']['intent']['state']=='reference_queued'

    ledger=WorkCardLedger(parent,ledger_store)
    cards=ledger.list_cards()
    assert len(cards)==1
    assert cards[0]['request_id']==linked_project_refresh_request_id(parent.conversation_id,second['refresh_id'])
    assert cards[0]['snapshot']['source']==second['copy']['snapshot']
    present=[event for event in events if event.get('type')=='work-card.present']
    assert len(present)==1 and present[0]['card_id']==cards[0]['card_id']
    assert len([message for message in parent.messages if message.get('role')=='work_card_presentation'
                and message.get('card_id')==cards[0]['card_id']])==1
    # Re-running recovery must neither recreate the intent/card nor enqueue a duplicate reference.
    assert _resume_linked_project_session_refreshes(
        repl,bridge,parent,ledger_store,binding_ids=[rows[0]['binding_id']],emit_event=True,
    )==0
    assert len(ledger.list_cards())==1
    assert len([event for event in events if event.get('type')=='work-card.present'
                and event.get('card_id')==cards[0]['card_id']])==1
    assert len([message for message in parent.messages if message.get('role')=='work_card_presentation'
                and message.get('card_id')==cards[0]['card_id']])==1
