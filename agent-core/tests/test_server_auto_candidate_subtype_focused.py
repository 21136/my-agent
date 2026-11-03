from __future__ import annotations
import asyncio
import json
import sys
import uuid
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_server_auto_recognition_ws_chain_focused import harness
from work_records import WorkRecordStore


class Provider:
    paths=None
    def chat(self,*args,**kwargs):
        return '{"action":"record","kind":"requirement","title":"synthetic title","requirement":"synthetic deliverable","scope":"independent","item_id":null}'


def test_ws_structured_candidate_exactly_matches_committed_record(tmp_path,monkeypatch):
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin(); rid=await message(ws,'synthetic task')
        request=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        terminal=held['bridge'].lifecycle.snapshot()
        assert request['confirm_subtype']=='work_record_auto_candidate'
        fields=request['work_record_candidate']
        assert fields=={'title':'synthetic title','requirement':'synthetic deliverable','scope':{'kind':'independent','object_ref':None},
                       'session_id':session.conversation_id,'request_id':rid,'run_id':str(uuid.UUID(terminal.run_id)),
                       'sequence':terminal.sequence,'terminal_state':'settled','finish_reason':terminal.finish_reason}
        assert request['request_id']!=rid
        assert all(value in request['preview'] for value in ('synthetic title','synthetic deliverable',str(uuid.UUID(terminal.run_id)),rid))
        await ws.incoming.put({'type':'confirm.response','request_id':request['request_id'],'choice':'y'})
        done=await wait(ws,lambda e:e.get('type')=='confirm.done' and e.get('request_id')==request['request_id'])
        assert done['confirm_subtype']=='work_record_auto_candidate' and done['work_record_candidate']==fields
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']=='recorded'
        item=WorkRecordStore(session).list_items()[0]
        assert item['title']==fields['title'] and item['scope']==fields['scope']
        assert item['requirements'][0]['text']==fields['requirement']
        await finish(ws,task)
    asyncio.run(run())


@pytest.mark.parametrize('field,value',[('title','forged title'),('requirement','forged requirement'),('scope_json','{"kind":"project","project_id":"forged"}')])
def test_changed_prepared_fields_after_confirmation_never_commit(tmp_path,monkeypatch,field,value):
    from work_records_service import WorkRecordCaptureService
    original=WorkRecordCaptureService.commit_prepared_capture
    def tamper(self,prepared,**kwargs):
        object.__setattr__(prepared,field,value)
        return original(self,prepared,**kwargs)
    monkeypatch.setattr(WorkRecordCaptureService,'commit_prepared_capture',tamper)
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic task')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        assert confirm['confirm_subtype']=='work_record_auto_candidate'
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']!='recorded'
        assert WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('run_id',None),('sequence',999),('finish_reason','stop')])
def test_changed_receipt_after_confirmation_never_commits(tmp_path,monkeypatch,field,value):
    from dataclasses import replace
    from work_records_service import WorkRecordCaptureService
    original=WorkRecordCaptureService.commit_prepared_capture
    def tamper(self,prepared,**kwargs):
        kwargs['receipt']=replace(kwargs['receipt'],**{field:uuid.uuid4().hex if value is None else value})
        return original(self,prepared,**kwargs)
    monkeypatch.setattr(WorkRecordCaptureService,'commit_prepared_capture',tamper)
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic task')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']!='recorded' and WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    asyncio.run(run())


def test_project_scope_structured_fields_match_actual_storage(tmp_path,monkeypatch):
    class ProjectProvider:
        paths=None
        def chat(self,*a,**k):
            return '{"action":"record","kind":"requirement","title":"project title","requirement":"project deliverable","scope":"current_project","item_id":null}'
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,ProjectProvider())
    session.meta.project_id='synthetic-project'
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic current project')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        fields=confirm['work_record_candidate']
        assert fields['scope']=={'kind':'project','project_id':'synthetic-project'}
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        result=await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert result['status']=='recorded'
        assert WorkRecordStore(session).list_items()[0]['scope']==fields['scope']
        await finish(ws,task)
    asyncio.run(run())


def test_revocation_done_keeps_subtype_and_cannot_commit(tmp_path,monkeypatch):
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,Provider())
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic task')
        confirm=await wait(ws,lambda e:e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn')
        await ws.incoming.put({'type':'work-record.auto-recognition.set','session_id':session.conversation_id,'request_id':str(uuid.uuid4()),'action':'revoke'})
        await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.done' and e.get('action')=='revoke')
        done=await wait(ws,lambda e:e.get('type')=='confirm.done' and e.get('request_id')==confirm['request_id'])
        assert done['confirm_subtype']=='work_record_auto_candidate' and done['session_id']==session.conversation_id
        await ws.incoming.put({'type':'confirm.response','request_id':confirm['request_id'],'choice':'y'})
        await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid)
        assert WorkRecordStore(session).list_items()==[]
        await finish(ws,task)
    asyncio.run(run())
