from __future__ import annotations
import asyncio
import sys
import threading
import uuid
from pathlib import Path
import httpx
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server, llm_client
from llm_client import LLMClient, LLMConfig
from llm_models import ModelEntry, ModelRegistry
from tests.test_server_auto_recognition_ws_chain_focused import harness
from work_records import WorkRecordStore


@pytest.mark.parametrize('boundary', ['before_post', 'post_started'])
def test_revoke_around_post_is_best_effort_and_not_shared_with_next_turn(tmp_path,monkeypatch,boundary):
    real_target=server._auto_recognition_target
    entry=ModelEntry(id='0x567-flash',name='synthetic',vendor='0x567',base_url='https://api-cdn.0x567.com',provider_model='gpt-6-luna',api_key='synthetic')
    registry=ModelRegistry((entry,),entry.id,entry.id,{})
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,object())
    monkeypatch.setattr(server,'_auto_recognition_target',real_target)
    monkeypatch.setattr(ModelRegistry,'load',lambda *a,**k:registry)
    config=LLMConfig(api_key=None,base_url='https://unused.invalid',model=entry.id,model_coding=entry.id,timeout_sec=2,context_limit_override=None)
    created=[]
    def make_client(**kwargs):
        client=LLMClient(config,paths=session.paths);created.append(client);return client
    monkeypatch.setattr(llm_client,'LLMClient',make_client)
    reached,release=threading.Event(),threading.Event()
    sends=[]
    class FakeHTTP:
        def __init__(self,**kwargs):pass
        def __enter__(self):
            if boundary=='before_post':
                reached.set(); assert release.wait(4)
            return self
        def __exit__(self,*args):pass
        def close(self):pass
        def post(self,*args,**kwargs):
            sends.append(1)
            if boundary=='post_started':
                reached.set(); assert release.wait(4)
            return httpx.Response(200,json={'model':'gpt-6-luna','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'{"action":"record","kind":"requirement","title":"synthetic","requirement":"synthetic task","scope":"independent","item_id":null}'}}]})
    monkeypatch.setattr(llm_client.httpx,'Client',FakeHTTP)
    async def run():
        ws,task=await begin();rid=await message(ws,'synthetic first')
        assert await asyncio.to_thread(reached.wait,2)
        bridge=held['bridge']; first_run=bridge.lifecycle.snapshot().run_id
        await ws.incoming.put({'type':'work-record.auto-recognition.set','session_id':session.conversation_id,'request_id':str(uuid.uuid4()),'action':'revoke'})
        assert not (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.done' and e.get('action')=='revoke'))['enabled']
        assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='cancelled'
        assert len(created)==1 and created[0]._cancel_event is not bridge.cancel_event
        assert created[0]._cancel_event.is_set()
        bridge.cancel_event.set()  # begin_turn clears only the ordinary-turn flag
        await message(ws,'synthetic next')
        for _ in range(200):
            snap=bridge.lifecycle.snapshot()
            if snap.run_id!=first_run and snap.state=='settled':break
            await asyncio.sleep(.01)
        assert snap.run_id!=first_run and snap.state=='settled'
        assert not bridge.cancel_event.is_set() and created[0]._cancel_event.is_set()
        release.set()
        for _ in range(200):
            if bridge._auto_recognition_active is None:break
            await asyncio.sleep(.01)
        assert sends==([] if boundary=='before_post' else [1])
        assert WorkRecordStore(session).list_items()==[]
        assert not [e for e in ws.events if e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn']
        await finish(ws,task)
    try:asyncio.run(run())
    finally:release.set()


@pytest.mark.parametrize('ingress', ['invalidate_post_turn','request_cancel','begin_session_binding_change','mark_connection_closed'])
def test_all_host_invalidation_ingresses_set_active_client_flag(tmp_path,ingress):
    from types import SimpleNamespace
    from tests.test_server_auto_record_confirm_focused import setup_bridge
    bridge,session,_=setup_bridge(tmp_path)
    flag=threading.Event()
    bridge._auto_recognition_active={'candidate':SimpleNamespace(session=session,request_id=str(uuid.uuid4())),
                                     'reported':False,'cancel_event':flag}
    getattr(bridge,ingress)()
    assert flag.is_set()
    assert flag is not bridge.cancel_event


def test_each_recognition_claim_gets_a_distinct_event(tmp_path,monkeypatch):
    class Provider:
        paths=None
        def chat(self,*a,**k):
            return '{"action":"none","kind":"qa","title":null,"requirement":null,"scope":null,"item_id":null}'
    provider=Provider()
    session,repl,held,handler,wait,begin,message,finish=harness(tmp_path,monkeypatch,provider)
    from tests.test_server_auto_recognition_ws_chain_focused import fake_confirmed
    clients=[]
    def make_client(**kwargs):
        client=fake_confirmed(provider);clients.append(client);return client
    monkeypatch.setattr(llm_client,'LLMClient',make_client)
    async def run():
        ws,task=await begin()
        for text in ('first synthetic','second synthetic'):
            rid=await message(ws,text)
            assert (await wait(ws,lambda e:e.get('type')=='work-record.auto-recognition.result' and e.get('request_id')==rid))['status']=='not_recorded'
        assert len(clients)==2 and clients[0].cancel_event is not clients[1].cancel_event
        assert all(c.cancel_event is not held['bridge'].cancel_event for c in clients)
        await finish(ws,task)
    asyncio.run(run())
