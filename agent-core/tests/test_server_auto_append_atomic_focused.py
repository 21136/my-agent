from __future__ import annotations
import asyncio, json, sys, uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_server_auto_recognition_ws_chain_focused import harness
from work_records import WorkRecordStore

class Provider:
    paths=None
    def __init__(self): self.target=None
    def chat(self,*a,**k):
        return json.dumps({'action':'record','kind':'requirement','title':'model title not target',
                           'requirement':'synthetic new requirement','scope':'current_project','item_id':self.target})

def test_ws_append_exact_item_and_bind_actual_run(tmp_path,monkeypatch):
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    session.meta.project_id='synthetic-project';store=WorkRecordStore(session)
    existing=store.create_item_with_requirement(title='synthetic existing',scope={'kind':'project','project_id':'synthetic-project'},
                 requirement='synthetic prior requirement',event_id=str(uuid.uuid4()),idempotency_key='synthetic-prior')
    provider.target=existing['item_id'];before=store.get_item(provider.target)
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic task')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        fields=confirm['work_record_candidate']
        assert fields['operation']=='append_requirement' and fields['item_id']==provider.target
        assert fields['title']=='synthetic existing' and fields['requirement']=='synthetic new requirement'
        assert provider.target in confirm['preview'] and 'synthetic existing' in confirm['preview']
        terminal=held['bridge'].lifecycle.snapshot()
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']=='recorded'
        saved=store.get_item(provider.target)
        assert saved['requirements'][0]==before['requirements'][0]
        assert saved['requirements'][1]['text']=='synthetic new requirement'
        assert saved['history'][:len(before['history'])]==before['history']
        run=saved['runs'][-1]
        assert uuid.UUID(run['run_id'])==uuid.UUID(terminal.run_id)
        assert run['requirement_id']==saved['requirements'][1]['requirement_id']
        assert run['status']=='unknown' and run['verification']['status']=='not_verified'
        await finish(ws,task)
    asyncio.run(run())


def test_ws_append_refusal_preserves_history_without_second_execution(tmp_path,monkeypatch):
    provider=Provider(); provider.calls=0
    original=provider.chat
    def call(*a,**k): provider.calls+=1;return original(*a,**k)
    provider.chat=call
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    session.meta.project_id='synthetic-project';store=WorkRecordStore(session)
    target=store.create_item_with_requirement(title='target',scope={'kind':'project','project_id':'synthetic-project'},
          requirement='old',event_id=str(uuid.uuid4()),idempotency_key='prior');provider.target=target['item_id']
    before=store._read()
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic work')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'n'})
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']=='storage_not_authorized' and store._read()==before and provider.calls==1
        assert len([e for e in ws.events if e.get('type')=='turn.end'])==1
        await finish(ws,task)
    asyncio.run(run())


def test_ws_forged_cross_session_and_wrong_scope_never_prompt(tmp_path,monkeypatch):
    from session import Session, SessionMeta
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    session.meta.project_id='synthetic-project';store=WorkRecordStore(session)
    other_dir=session.session_dir.parent/'foreign';other_dir.mkdir()
    other=Session('foreign',other_dir,'',SessionMeta(project_id='synthetic-project'),[],session.paths)
    foreign=WorkRecordStore(other).create_item_with_requirement(title='foreign',scope={'kind':'project','project_id':'synthetic-project'},
        requirement='foreign',event_id=str(uuid.uuid4()),idempotency_key='foreign')
    wrong_scope=store.create_item_with_requirement(title='independent',scope={'kind':'independent'},
        requirement='old',event_id=str(uuid.uuid4()),idempotency_key='independent')
    before=store._read();other_before=WorkRecordStore(other)._read()
    async def run():
        ws,task=await begin()
        for target in (foreign['item_id'],wrong_scope['item_id'],str(uuid.uuid4())):
            provider.target=target;rid=await message(ws,'synthetic text '+target)
            result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
            assert result['status']=='not_recorded'
        assert not [e for e in ws.events if e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn']
        assert store._read()==before and WorkRecordStore(other)._read()==other_before
        await finish(ws,task)
    asyncio.run(run())


def test_ws_concurrent_confirmations_one_stale_without_partial_append(tmp_path,monkeypatch):
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    session.meta.project_id='synthetic-project';store=WorkRecordStore(session)
    target=store.create_item_with_requirement(title='target',scope={'kind':'project','project_id':'synthetic-project'},
          requirement='old',event_id=str(uuid.uuid4()),idempotency_key='prior');provider.target=target['item_id']
    async def run():
        ws,task=await begin();one=await message(ws,'synthetic one')
        first=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        two=await message(ws,'synthetic two')
        second=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' and e.get('request_id')!=first['request_id'])
        await ws.incoming.put({'type':'confirm.response','request_id':first['request_id'],'choice':'y'})
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==one))['status']=='recorded'
        await ws.incoming.put({'type':'confirm.response','request_id':second['request_id'],'choice':'y'})
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==two))['status']=='stale_context'
        saved=store.get_item(provider.target)
        assert len(saved['requirements'])==2 and len(saved['runs'])==1
        await finish(ws,task)
    asyncio.run(run())


def test_store_atomic_append_is_idempotent_and_conflict_safe(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import pytest
    from work_records import WorkRecordConflict
    from tests.test_work_records_service import make_session
    session=make_session(tmp_path);store=WorkRecordStore(session)
    target=store.create_item_with_requirement(title='target',scope={'kind':'independent'},requirement='old',event_id=str(uuid.uuid4()),idempotency_key='prior')
    snapshot=store._read();request=str(uuid.uuid4());run_id=uuid.uuid4().hex
    args={'item_id':target['item_id'],'text':'new','event_id':request,'idempotency_key':'stable-synthetic',
          'expected_snapshot':snapshot,'return_commit_status':True,'expected_scope':{'kind':'independent','object_ref':None},
          'terminal_run':{'run_id':run_id,'sequence':3,'terminal_state':'settled','finish_reason':'stop'}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:store.add_requirement(**args),range(2)))
    assert sorted(changed for _,changed in results)==[False,True]
    assert results[0][0]['requirement_id']==results[1][0]['requirement_id']
    saved=store.get_item(target['item_id']);assert len(saved['requirements'])==2 and len(saved['runs'])==1
    assert saved['runs'][0]['requirement_id']==results[0][0]['requirement_id']
    assert saved['runs'][0]['verification']['host_execution']=={'sequence':3,'finish_reason':'stop'}
    with pytest.raises(WorkRecordConflict):
        store.add_requirement(**{**args,'terminal_run':{**args['terminal_run'],'run_id':uuid.uuid4().hex}})
    with pytest.raises(WorkRecordConflict):
        store.add_requirement(**{**args,'item_id':str(uuid.uuid4())})
    with pytest.raises(WorkRecordConflict):
        store.add_requirement(**{**args,'event_id':str(uuid.uuid4())})
    assert store.get_item(target['item_id'])==saved


def test_store_atomic_append_failed_publication_has_no_partial_state(tmp_path,monkeypatch):
    import pytest
    from tests.test_work_records_service import make_session
    session=make_session(tmp_path);store=WorkRecordStore(session)
    target=store.create_item_with_requirement(title='target',scope={'kind':'independent'},requirement='old',event_id=str(uuid.uuid4()),idempotency_key='prior')
    before=store._read()
    def fail(*a,**k):raise OSError('synthetic')
    monkeypatch.setattr(store,'_publish',fail)
    with pytest.raises(Exception):
        store.add_requirement(target['item_id'],'new',event_id=str(uuid.uuid4()),idempotency_key='new',
            expected_snapshot=before,expected_scope={'kind':'independent','object_ref':None},
            terminal_run={'run_id':uuid.uuid4().hex,'sequence':2,'terminal_state':'settled','finish_reason':'stop'})
    assert store._read()==before


def test_ws_replayed_request_does_not_repeat_append_or_work(tmp_path,monkeypatch):
    provider=Provider();provider.calls=0;chat=provider.chat
    def call(*a,**k):provider.calls+=1;return chat(*a,**k)
    provider.chat=call
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    session.meta.project_id='synthetic-project';store=WorkRecordStore(session)
    target=store.create_item_with_requirement(title='target',scope={'kind':'project','project_id':'synthetic-project'},
          requirement='old',event_id=str(uuid.uuid4()),idempotency_key='prior');provider.target=target['item_id']
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic replay')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='recorded'
        before=store._read();first_run=held['bridge'].lifecycle.snapshot().run_id
        await ws.incoming.put({'type':'user.message','request_id':rid,'text':'synthetic replay'})
        await wait(ws,lambda e:e.get('type')=='notice' and '重复请求' in e.get('text',''))
        assert held['bridge'].lifecycle.snapshot().run_id==first_run
        assert store._read()==before and provider.calls==1
        await finish(ws,task)
    asyncio.run(run())


def test_ws_changed_target_after_confirmation_never_appends(tmp_path,monkeypatch):
    from work_records_service import WorkRecordCaptureService
    original=WorkRecordCaptureService.commit_prepared_capture
    fake_target={'item_id':None}
    def tamper(self,prepared,**kwargs):
        object.__setattr__(prepared,'target_item_id',fake_target['item_id'])
        return original(self,prepared,**kwargs)
    monkeypatch.setattr(WorkRecordCaptureService,'commit_prepared_capture',tamper)
    provider=Provider();session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    session.meta.project_id='synthetic-project';store=WorkRecordStore(session)
    for name in ('first','second'):
        item=store.create_item_with_requirement(title=name,scope={'kind':'project','project_id':'synthetic-project'},
            requirement='old',event_id=str(uuid.uuid4()),idempotency_key=name)
        if name=='first':provider.target=item['item_id']
        else:fake_target['item_id']=item['item_id']
    before=store._read()
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic task')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        assert confirm['work_record_candidate']['item_id']==provider.target
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']!='recorded' and store._read()==before
        await finish(ws,task)
    asyncio.run(run())
