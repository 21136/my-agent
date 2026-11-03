from __future__ import annotations
import asyncio, sys, uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from agent import Agent
from llm_client import LLMResponse
from main import ConversationRepl
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore

class FakeLLM:
    def chat(self,*args,**kwargs):
        return LLMResponse(model='synthetic',content='Synthetic explicit result',tool_calls=[],finish_reason='stop',usage=None,raw={})

@pytest.fixture(autouse=True)
def isolated_lock(monkeypatch): monkeypatch.setattr(server,'TURN_LOCK',asyncio.Lock())

def fixture(tmp_path,monkeypatch):
    root=tmp_path/'root';paths=AgentPaths(root,root/'workspace',root/'data',root/'evolve')
    d=paths.data/'sessions'/'synthetic';d.mkdir(parents=True)
    session=Session('synthetic',d,'',SessionMeta(turn_mode='ask'),[],paths);session.save()
    agent=Agent.create(session,llm=FakeLLM(),confirm_fn=lambda *_:'n')
    events=[];bridge=server.WsBridge(emit=events.append,paths=paths)
    repl=ConversationRepl(paths,session,agent,input_fn=lambda _:'',output_fn=lambda _:None,assistant_output_fn=bridge.emit_assistant)
    bridge._session_provider=lambda:repl.session;bridge.init_post_turn_send_gate(session)
    request=str(uuid.uuid4());store=WorkRecordStore(session)
    item=store.create_item_with_requirement(title='synthetic item',scope={'kind':'independent'},requirement='synthetic requirement',event_id=request,idempotency_key=f'desktop:user.message:{request}:synthetic')
    req=item['requirements'][0]['requirement_id']
    run_id=server._begin_work_record_capture_turn(repl,bridge)
    binding=server._begin_work_record_run_binding(session=session,capture=SimpleNamespace(status='recorded',item_id=item['item_id'],requirement_id=req),run_id=run_id,current_session=session,bridge=bridge,emit=events.append,request_id=request)
    assert type(binding) is tuple
    async def run(enabled):
        async with server.TURN_LOCK:
            return await server._run_line(repl,bridge,'synthetic question',paths,lock_held=True,prestarted_run_id=run_id,work_record_binding=binding,explicit_result_capture=enabled)
    return session,store,item,req,run_id,request,bridge,events,run

def test_freeze_only_after_original_unknown_settlement(tmp_path,monkeypatch):
    session,store,item,req,run_id,request,bridge,events,run=fixture(tmp_path,monkeypatch)
    candidate=asyncio.run(run(True))
    assert type(candidate) is server._FrozenExplicitRunExcerpt
    assert candidate.session is session and candidate.request_id==request and candidate.run_id==run_id
    assert candidate.item_id==item['item_id'] and candidate.requirement_id==req
    assert 'Synthetic explicit result' in candidate.excerpt and '未核验' in candidate.excerpt and '不可定位' in candidate.excerpt
    record=store.get_item(item['item_id']); assert record['requirements'][0]['status']=='unknown'
    assert record['runs'][0]['verification']['status']=='not_verified' and record['runs'][0]['status']=='unknown'
    assert candidate.result_id==record['runs'][0]['result_id']
    assert not any(e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' for e in events)

def test_default_explicit_capture_stays_off_and_unknown_settled(tmp_path,monkeypatch):
    _,store,item,_,_,_,_,events,run=fixture(tmp_path,monkeypatch)
    assert asyncio.run(run(False)) is None
    recorded=store.get_item(item['item_id'])
    assert recorded['runs'][0]['status']=='unknown' and recorded['runs'][0]['verification']['status']=='not_verified'
    assert not any(e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' for e in events)

def test_failed_original_settlement_never_produces_excerpt_candidate(tmp_path,monkeypatch):
    _,_,_,_,_,_,_,_,run=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(server,'_settle_work_record_run',lambda **kwargs: False)
    assert asyncio.run(run(True)) is None

def test_cancel_after_settlement_before_freeze_leaves_unknown(tmp_path,monkeypatch):
    _,store,item,_,_,_,bridge,events,run=fixture(tmp_path,monkeypatch)
    original=server._settle_work_record_run
    def settle_then_cancel(**kwargs):
        ok=original(**kwargs); bridge.request_cancel(); return ok
    monkeypatch.setattr(server,'_settle_work_record_run',settle_then_cancel)
    assert asyncio.run(run(True)) is None
    recorded=store.get_item(item['item_id'])
    assert recorded['runs'][0]['status']=='unknown' and recorded['runs'][0]['verification']['status']=='not_verified'
    assert not any(e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' for e in events)


def test_binding_switch_after_settlement_before_freeze_is_invalid(tmp_path,monkeypatch):
    _,store,item,_,_,_,bridge,events,run=fixture(tmp_path,monkeypatch)
    original=server._settle_work_record_run
    def settle_then_switch(**kwargs):
        ok=original(**kwargs);bridge.begin_session_binding_change();bridge.end_session_binding_change();return ok
    monkeypatch.setattr(server,'_settle_work_record_run',settle_then_switch)
    assert asyncio.run(run(True)) is None
    assert store.get_item(item['item_id'])['runs'][0]['status']=='unknown'

def test_untrusted_instance_run_override_does_not_produce_excerpt(tmp_path,monkeypatch):
    _,store,item,_,_,_,bridge,events,run=fixture(tmp_path,monkeypatch)
    # The capture must come from the bound Agent implementation, not an
    # instance-provided replacement masquerading as the host return.
    import types
    # Retrieve the agent through the provider only via the test's bound session.
    # Override at class-independent level by replacing the live REPL agent method.
    original=Agent.run_turn
    def wrapped(self,*args,**kwargs): return original(self,*args,**kwargs)
    # A class override is still the Agent method; instead mark the exact instance
    # during host binding, before _run_line installs its observer.
    original_bind=Agent.bind_host_run_context
    def bind(self,*args,**kwargs):
        original_bind(self,*args,**kwargs)
        self.run_turn=types.MethodType(wrapped,self)
    monkeypatch.setattr(Agent,'bind_host_run_context',bind)
    assert asyncio.run(run(True)) is None
    assert store.get_item(item['item_id'])['runs'][0]['status']=='unknown'


def test_frozen_candidate_is_private_and_after_turn_end(tmp_path,monkeypatch):
    _,_,_,_,_,_,_,events,run=fixture(tmp_path,monkeypatch)
    candidate=asyncio.run(run(True))
    assert candidate is not None
    assert 'Synthetic explicit result' not in repr(candidate)
    assert any(event.get('type')=='turn.end' for event in events)

def test_execute_frozen_excerpt_requires_bound_approval_and_stores_no_source(tmp_path,monkeypatch):
    session,store,item,_,run_id,_,bridge,events,run=fixture(tmp_path,monkeypatch)
    candidate=asyncio.run(run(True)); previews=[]
    def confirm(preview,**kwargs):
        previews.append(preview)
        return 'y',str(uuid.uuid4())
    monkeypatch.setattr(bridge,'confirm_post_turn',confirm)
    outcome=server._execute_explicit_run_excerpt_candidate(candidate,bridge)
    assert outcome.status=='recorded'
    row=store.get_item(item['item_id'])['runs'][0]
    assert row['assistant_reply_excerpt']['text']==candidate.excerpt
    assert row['assistant_reply_excerpt']['source_availability']=='unavailable' and row['assistant_reply_excerpt']['sources']==[]
    assert row['status']=='unknown' and row['verification']['status']=='not_verified'
    import json
    assert len(previews)==1 and json.loads(previews[0].split("\n",2)[2])["assistant_reply_excerpt"]==candidate.excerpt

@pytest.mark.parametrize('action',['reject','cancel','switch','new_run'])
def test_confirmation_never_commits_after_revocation_or_run_change(tmp_path,monkeypatch,action):
    _,store,item,req,run_id,_,bridge,events,run=fixture(tmp_path,monkeypatch)
    candidate=asyncio.run(run(True))
    def confirm(preview,**kwargs):
        if action=='reject': return 'n',str(uuid.uuid4())
        if action=='cancel': bridge.request_cancel()
        if action=='switch': bridge.begin_session_binding_change();bridge.end_session_binding_change()
        if action=='new_run': store.begin_requirement_run(item['item_id'],req,run_id=uuid.uuid4().hex,expected_current_run_id=run_id)
        return 'y',str(uuid.uuid4())
    monkeypatch.setattr(bridge,'confirm_post_turn',confirm)
    outcome=server._execute_explicit_run_excerpt_candidate(candidate,bridge)
    assert outcome.status!='recorded'
    assert 'assistant_reply_excerpt' not in store.get_item(item['item_id'])['runs'][0]

@pytest.mark.parametrize('action',['approve','reject','cancel','switch','next_turn','new_run','failure'])
def test_explicit_record_synthetic_ws_post_turn_excerpt(tmp_path,monkeypatch,action):
    import json
    import llm_client
    root=tmp_path/'root';paths=AgentPaths(root,root/'workspace',root/'data',root/'evolve')
    d=paths.data/'sessions'/'synthetic';d.mkdir(parents=True)
    session=Session('synthetic',d,'',SessionMeta(turn_mode='ask'),[],paths);session.save()
    other_dir=paths.data/'sessions'/'other';other_dir.mkdir(parents=True)
    other=Session('other',other_dir,'',SessionMeta(turn_mode='ask'),[],paths);other.save()
    agent=Agent.create(session,llm=FakeLLM(),confirm_fn=lambda *_:'n')
    monkeypatch.setattr(server,'resume_desktop_or_create',lambda _:session)
    monkeypatch.setattr(server,'load_session_for_harness',lambda *a,**k:other)
    monkeypatch.setattr(server.WsBridge,'emit_session_state',lambda *a:None)
    monkeypatch.setattr(server,'emit_corruption_notices',lambda *a:None)
    monkeypatch.setattr(server,'_emit_pending_work_card_references',lambda *a:None)
    holder={}
    def build(_session,_paths,bridge,**kwargs):
        repl=ConversationRepl(paths,session,agent,input_fn=lambda _:'',output_fn=lambda _:None,assistant_output_fn=bridge.emit_assistant)
        repl._rebind_agent=lambda:None
        bridge._session_provider=lambda:repl.session;bridge.init_post_turn_send_gate(session)
        holder.update(bridge=bridge,repl=repl)
        return repl
    monkeypatch.setattr(server,'_build_repl',build)
    class Registry:
        def get(self,_id):return SimpleNamespace(id='0x567-flash',vendor='0x567',provider_model='gpt-6-luna',max_input_tokens=96000)
    monkeypatch.setattr('work_records_service.get_registry',lambda *a,**k:Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry',lambda *a,**k:Registry())
    class Provider:
        paths=None
        calls=[]
        def set_cancel_event(self, event): pass
        def chat(self,messages,**kwargs):
            self.calls.append(messages)
            return '{"action":"record","kind":"requirement","title":"Synthetic item","requirement":"Synthetic requirement","scope":"independent","item_id":null}'
    provider=Provider();monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs:provider)
    class Transport:
        def __init__(self):self.incoming=asyncio.Queue();self.events=[]
        def __aiter__(self):return self
        async def __anext__(self):
            value=await self.incoming.get()
            if value is None:raise StopAsyncIteration
            return value
        async def send(self,raw):self.events.append(json.loads(raw))
    handler=server.WsSessionHandler(paths)
    request=str(uuid.uuid4())
    async def run():
        ws=Transport();task=asyncio.create_task(handler.handle(ws));seen=set();post=None;switched=False
        async def send(obj):await ws.incoming.put(json.dumps(obj))
        await send({'type':'user.message','text':'记录一下 synthetic task','request_id':request})
        for _ in range(200):
            for e in list(ws.events):
                if e.get('type')=='confirm.request' and e['request_id'] not in seen:
                    seen.add(e['request_id'])
                    if e.get('confirm_kind')!='work_record_post_turn':
                        await send({'type':'confirm.response','request_id':e['request_id'],'choice':'y'})
                    else:
                        post=e
                        if action=='cancel':await send({'type':'turn.cancel'})
                        elif action=='switch':await send({'type':'session.open','session_id':'other'})
                        elif action=='next_turn':await send({'type':'user.message','text':'synthetic followup','request_id':str(uuid.uuid4())})
                        elif action=='new_run':
                            store=WorkRecordStore(session);item=store.list_items()[0];req=item['requirements'][0]
                            store.begin_requirement_run(item['item_id'],req['requirement_id'],run_id=uuid.uuid4().hex,expected_current_run_id=req['current_run_id'])
                            await send({'type':'confirm.response','request_id':e['request_id'],'choice':'y'})
                        elif action=='failure':
                            def fail_write(*args,**kwargs): raise OSError('synthetic failure')
                            monkeypatch.setattr(WorkRecordStore,'append_assistant_reply_excerpt_if_current_run',fail_write)
                            await send({'type':'confirm.response','request_id':e['request_id'],'choice':'y'})
                        else:await send({'type':'confirm.response','request_id':e['request_id'],'choice':'n' if action=='reject' else 'y'})
                        if action=='approve':await send({'type':'confirm.response','request_id':e['request_id'],'choice':'y'})
            if action=='next_turn' and post and not switched and sum(e.get('type')=='turn.end' for e in ws.events)>=2:
                switched=True;await send({'type':'confirm.response','request_id':post['request_id'],'choice':'y'})
            if post and any(e.get('type')=='work-record.post-turn.result' for e in ws.events):break
            await asyncio.sleep(.01)
        await ws.incoming.put(None);await task
        return ws.events,post
    events,post=asyncio.run(run())
    assert post is not None, [(e.get('type'), e.get('text'), e.get('message')) for e in events]
    assert post['confirm_subtype']=='work_record_reply_excerpt'
    done=[e for e in events if e.get('type')=='confirm.done' and e.get('request_id')==post['request_id']]
    assert len(done)==1 and done[0]['session_id']=='synthetic'
    assert done[0]['confirm_kind']=='work_record_post_turn' and done[0]['confirm_subtype']=='work_record_reply_excerpt'
    preview=json.loads(post['preview'].split('\n',2)[2])
    assert 'Synthetic explicit result' in preview['assistant_reply_excerpt']
    assert preview['source_availability']=='unavailable' and preview['sources']==[]
    results=[e for e in events if e.get('type')=='work-record.post-turn.result']
    assert len(results)==1 and results[0]['session_id']=='synthetic' and results[0]['request_id']==request
    expected_text = ('回复摘录已追加；原工作记录仍为未知、未经验证。' if action in {'approve','next_turn'} else
                     '回复摘录是否追加无法确认；原工作记录此前已保存，请核对，勿重新执行原工作。' if action=='failure' else
                     '回复摘录未追加，原工作记录保留；不会重新执行原工作。')
    assert results[0]['message']==expected_text
    assert 'Synthetic explicit result' not in results[0]['message']
    assert len(provider.calls)==1 and 'Synthetic explicit result' not in json.dumps(provider.calls)
    item=WorkRecordStore(session).list_items()[0];old=item['runs'][0]
    assert old['status']=='unknown' and old['verification']['status']=='not_verified'
    if action in {'approve','next_turn'}:
        assert results[0]['outcome']=='recorded' and old['assistant_reply_excerpt']['text']==preview['assistant_reply_excerpt']
        assert sum(e.get('kind')=='assistant_reply_excerpt_added' for e in item['history'])==1
    else:
        assert results[0]['outcome']==('uncertain' if action=='failure' else 'not_recorded') and 'assistant_reply_excerpt' not in old
    if action=='next_turn':
        assert sum(e.get('type')=='turn.end' for e in events)>=2
        assert sum(e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' for e in events)==1



def test_commit_and_cancel_share_one_linearization_lock(tmp_path,monkeypatch):
    import threading
    _,store,item,_,_,_,bridge,_,run=fixture(tmp_path,monkeypatch)
    candidate=asyncio.run(run(True))
    entered=threading.Event(); release=threading.Event(); cancelled=threading.Event()
    original=candidate.store.append_assistant_reply_excerpt_if_current_run
    def slow_cas(*args,**kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args,**kwargs)
    monkeypatch.setattr(candidate.store,'append_assistant_reply_excerpt_if_current_run',slow_cas)
    monkeypatch.setattr(bridge,'confirm_post_turn',lambda *a,**k:('y',str(uuid.uuid4())))
    outcome=[]
    worker=threading.Thread(target=lambda:outcome.append(server._execute_explicit_run_excerpt_candidate(candidate,bridge)))
    worker.start()
    try:
        assert entered.wait(3), outcome
        def cancel():
            bridge.request_cancel(); cancelled.set()
        canceller=threading.Thread(target=cancel);canceller.start()
        assert not cancelled.wait(.1)
    finally:
        release.set();worker.join(3)
    canceller.join(3)
    assert cancelled.is_set() and outcome[0].status=='recorded'
    assert store.get_item(item['item_id'])['runs'][0]['assistant_reply_excerpt']['text']==candidate.excerpt


def test_subtype_is_host_supplied_not_inferred_and_unknown_is_rejected(tmp_path):
    import threading, time
    session=SimpleNamespace(conversation_id='synthetic')
    paths=AgentPaths(tmp_path,tmp_path/'workspace',tmp_path/'data',tmp_path/'evolve')
    events=[]; bridge=server.WsBridge(emit=events.append,paths=paths,confirm_timeout=.5)
    bridge._session_provider=lambda:session; bridge.init_post_turn_send_gate(session)
    status,ticket=bridge.create_post_turn_ticket(session,str(uuid.uuid4()),uuid.uuid4().hex)
    assert status=='created'
    preview='工作记录助理回复摘录本地保存确认\nsynthetic'
    assert bridge.confirm_post_turn(preview,session=session,ticket=ticket,confirm_subtype='unrecognized')==('n','')
    assert events==[]
    result=[]
    worker=threading.Thread(target=lambda:result.append(bridge.confirm_post_turn(preview,session=session,ticket=ticket)))
    worker.start()
    deadline=time.monotonic()+2
    while not events and time.monotonic()<deadline: time.sleep(.005)
    assert events and events[0]['type']=='confirm.request' and 'confirm_subtype' not in events[0]
    assert bridge.deliver_post_turn_confirm(events[0]['request_id'],'n')
    worker.join(2)
    assert not worker.is_alive() and result[0][0]=='n'
    assert events[-1]['type']=='confirm.done' and 'confirm_subtype' not in events[-1]
