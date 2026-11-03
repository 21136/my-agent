from __future__ import annotations
import asyncio, sys, uuid
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import Agent
from llm_client import LLMResponse
from main import ConversationRepl
from paths import AgentPaths
from session import Session, SessionMeta
import server

class FakeLLM:
    def __init__(self,text): self.text=text
    def chat(self,*args,**kwargs):
        return LLMResponse(model='synthetic',content=self.text,tool_calls=[],finish_reason='completed',usage=None,raw={})

@pytest.fixture(autouse=True)
def isolated_lock(monkeypatch): monkeypatch.setattr(server,'TURN_LOCK',asyncio.Lock())

def setup(tmp_path,text):
    root=tmp_path/'agent-root';paths=AgentPaths(root,root/'workspace',root/'data',root/'evolve')
    d=paths.data/'sessions'/'synthetic';d.mkdir(parents=True)
    session=Session('synthetic',d,'',SessionMeta(turn_mode='ask'),[],paths);session.save()
    agent=Agent.create(session,llm=FakeLLM(text),confirm_fn=lambda *_:'n')
    events=[];bridge=server.WsBridge(emit=events.append,paths=paths)
    repl=ConversationRepl(paths,session,agent,input_fn=lambda _:'',output_fn=lambda _:None,assistant_output_fn=bridge.emit_assistant)
    bridge._session_provider=lambda:repl.session;bridge.init_post_turn_send_gate(session)
    async def freeze():
        async with server.TURN_LOCK:
            return await server._run_line(repl,bridge,'synthetic question',paths,lock_held=True,post_turn_request_id=str(uuid.uuid4()))
    return session,agent,bridge,events,freeze

def test_real_repl_turn_result_captured_without_message_matching(tmp_path):
    session,agent,bridge,events,freeze=setup(tmp_path,'Synthetic answer: important detail')
    candidate=asyncio.run(freeze())
    assert candidate is not None
    assert 'Synthetic answer: important detail' in candidate.result_excerpt
    assert '未核验' in candidate.result_excerpt and '不可定位' in candidate.result_excerpt
    assert 'run_turn' not in agent.__dict__

@pytest.mark.parametrize('text', ['甲'*2000, ''])
def test_truncation_and_empty_are_explicit_and_bounded(tmp_path,text):
    if not text:
        assert '缺失' in server._post_turn_result_excerpt(None)
        return
    _,_,_,_,freeze=setup(tmp_path,text)
    candidate=asyncio.run(freeze());assert candidate is not None
    assert len(candidate.result_excerpt.encode('utf-8'))<=4096
    assert '已截断' in candidate.result_excerpt

@pytest.mark.parametrize('approve_excerpt',[True,False])
def test_synthetic_ws_real_repl_excerpt_preview_and_atomic_store(tmp_path,monkeypatch,approve_excerpt):
    import json
    import llm_client
    from work_records import WorkRecordStore
    text='Synthetic private assistant result - UNIQUE_9f42'
    session,agent,_,_,_=setup(tmp_path,text);paths=session.paths
    def stop_reply(*args, **kwargs):
        return LLMResponse(model='synthetic',content=text,tool_calls=[],finish_reason='stop',usage=None,raw={})
    agent.llm.chat=stop_reply
    monkeypatch.setattr(server,'resume_desktop_or_create',lambda _:session)
    def build(_session,_paths,bridge,**kwargs):
        bridge._session_provider=lambda:session;bridge.init_post_turn_send_gate(session)
        return ConversationRepl(paths,session,agent,input_fn=lambda _:'',output_fn=lambda _:None,assistant_output_fn=bridge.emit_assistant)
    monkeypatch.setattr(server,'_build_repl',build)
    monkeypatch.setattr(server.WsBridge,'emit_session_state',lambda *args:None)
    monkeypatch.setattr(server,'emit_corruption_notices',lambda *args:None)
    monkeypatch.setattr(server,'_emit_pending_work_card_references',lambda *args:None)
    class Registry:
        def get(self,_id): return type('Entry',(),{'id':'0x567-flash','vendor':'0x567','provider_model':'gpt-6-luna','max_input_tokens':96000})()
    monkeypatch.setattr('work_records_service.get_registry',lambda *a,**k:Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry',lambda *a,**k:Registry())
    class Provider:
        paths=None
        calls=[]
        def chat(self,messages,**kwargs):
            self.calls.append(messages)
            return '{"action":"record","kind":"requirement","title":"synthetic","requirement":"synthetic deliverable","scope":"independent","item_id":null}'
    provider=Provider();monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs:provider)
    class Transport:
        def __init__(self): self.incoming=asyncio.Queue();self.events=[]
        def __aiter__(self): return self
        async def __anext__(self):
            value=await self.incoming.get()
            if value is None: raise StopAsyncIteration
            return value
        async def send(self,raw): self.events.append(json.loads(raw))
    handler=server.WsSessionHandler(paths);handler._post_turn_commit_ready=True
    async def run():
        ws=Transport();task=asyncio.create_task(handler.handle(ws));approved=set()
        await ws.incoming.put(json.dumps({'type':'user.message','text':'synthetic question','request_id':str(uuid.uuid4())}))
        for _ in range(500):
            for event in list(ws.events):
                if event.get('type')=='confirm.request' and event.get('confirm_kind')=='work_record_post_turn' and event['request_id'] not in approved:
                    approved.add(event['request_id'])
                    is_excerpt=event['preview'].startswith('工作记录回合摘录与来源状态本地保存确认\n')
                    await ws.incoming.put(json.dumps({'type':'confirm.response','request_id':event['request_id'],'choice':'y' if approve_excerpt or not is_excerpt else 'n'}))
            if any(e.get('type')=='work-record.post-turn.result' for e in ws.events): break
            await asyncio.sleep(.01)
        await ws.incoming.put(None);await task
        return ws.events,approved
    events,approved=asyncio.run(run())
    assert len(approved)==3
    preview=next(e['preview'] for e in events if e.get('type')=='confirm.request' and e['preview'].startswith('工作记录回合摘录与来源状态'))
    assert text in preview and '未核验' in preview and '不可定位' in preview
    assert len(provider.calls)==1 and text not in json.dumps(provider.calls,ensure_ascii=False)
    items=WorkRecordStore(session).list_items()
    if approve_excerpt:
        assert len(items)==1
        run=items[0]['runs'][0]
        assert text in run['result_summary'] and '未核验' in run['result_summary'] and '不可定位' in run['result_summary']
        shown=json.JSONDecoder().raw_decode(preview[preview.index('{'):])[0]
        assert shown['run']['result_summary']==run['result_summary']
        assert run['verification']['status']=='not_verified' and run['status']=='unknown'
        assert run['verification']['host_execution']['source_availability']=='unavailable'
        assert run['verification']['host_execution']['finish_reason']=='stop'
        assert any(e.get('type')=='turn.end' and e.get('finish_reason')=='stop' for e in events)
    else:
        assert items==[]

def test_session_save_failure_never_produces_candidate(tmp_path,monkeypatch):
    session,agent,bridge,events,freeze=setup(tmp_path,'Synthetic unsaved answer')
    def fail(): raise OSError('synthetic save failure')
    monkeypatch.setattr(session,'save',fail)
    assert asyncio.run(freeze()) is None
    assert 'run_turn' not in agent.__dict__
    assert not any(e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn' for e in events)


def test_same_content_old_turn_not_used_for_binding(tmp_path):
    session,agent,bridge,events,freeze=setup(tmp_path,'Identical synthetic reply')
    session.append_message({'role':'assistant','content':'Identical synthetic reply'});session.save()
    candidate=asyncio.run(freeze())
    assert candidate is not None and 'Identical synthetic reply' in candidate.result_excerpt
    assert len([m for m in session.messages if m.get('role')=='assistant' and m.get('content')=='Identical synthetic reply'])==2


def test_no_host_bound_result_has_explicit_missing_not_guessed(tmp_path):
    from tests.test_server_post_turn_freeze_focused import make_fixture
    paths,session,repl,bridge,_=make_fixture(tmp_path,True)
    async def freeze():
        async with server.TURN_LOCK:
            return await server._run_line(repl,bridge,'synthetic',paths,lock_held=True,post_turn_request_id=str(uuid.uuid4()))
    candidate=asyncio.run(freeze())
    assert candidate is not None and candidate.result_excerpt is None
    assert '缺失' in server._post_turn_result_excerpt(candidate.result_excerpt)


def test_provider_stop_preserved_in_frozen_host_evidence(tmp_path):
    session,agent,bridge,events,freeze=setup(tmp_path,'synthetic answer')
    def stop(*args,**kwargs): return LLMResponse(model='synthetic',content='synthetic answer',tool_calls=[],finish_reason='stop',usage=None,raw={})
    agent.llm.chat=stop
    candidate=asyncio.run(freeze())
    assert candidate is not None and candidate.finish_reason == 'stop'
    assert 'synthetic answer' in candidate.result_excerpt
    assert any(e.get('type')=='execution.state' and e.get('state')=='settled' and e.get('finish_reason')=='stop' for e in events)
    assert any(e.get('type')=='turn.end' and e.get('finish_reason')=='stop' for e in events)


@pytest.mark.parametrize('reason',['length','tool_calls','bogus','cancelled','stopped','timeout'])
def test_ineligible_terminal_reasons_never_form_candidate(tmp_path,reason):
    _,agent,_,events,freeze=setup(tmp_path,'synthetic answer')
    def response(*args,**kwargs): return LLMResponse(model='synthetic',content='synthetic answer',tool_calls=[],finish_reason=reason,usage=None,raw={})
    agent.llm.chat=response
    assert asyncio.run(freeze()) is None
