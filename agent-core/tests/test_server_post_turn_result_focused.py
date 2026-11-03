from __future__ import annotations
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server, llm_client
from paths import AgentPaths
from session import Session, SessionMeta

@pytest.mark.parametrize('status,expected', [('recorded','recorded'), ('storage_uncertain','uncertain'), ('append_not_allowed','append_not_allowed'), ('not_authorized','not_recorded')])
def test_background_result_has_original_identity_and_no_preview(tmp_path, monkeypatch, status, expected):
    paths=AgentPaths(tmp_path,tmp_path/'workspace',tmp_path/'data',tmp_path/'evolve')
    d=paths.data/'sessions'/'synthetic'; d.mkdir(parents=True)
    session=Session('synthetic',d,'',SessionMeta(),[],paths)
    events=[]; bridge=server.WsBridge(emit=events.append,paths=paths)
    bridge._session_provider=lambda:session; bridge.init_post_turn_send_gate(session)
    repl=SimpleNamespace(session=session,agent=SimpleNamespace(executor=SimpleNamespace(session=SimpleNamespace())),last_turn_finish_reason=None)
    handler=object.__new__(server.WsSessionHandler); handler.paths=paths; handler.managed_readonly_context=None
    handler._file_stage=SimpleNamespace(take=lambda *_: []); handler._post_turn_commit_ready=True
    request=str(uuid.uuid4()); candidate=server._FrozenPostTurnCandidate(session,'private preview',request,uuid.uuid4().hex,3,'completed',())
    async def fake_run(*args, **kwargs): return candidate
    monkeypatch.setattr(server,'_run_line',fake_run)
    monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs: object())
    monkeypatch.setattr(server,'_execute_post_turn_candidate',lambda *args:SimpleNamespace(status=status,message='private preview'))
    async def run():
        await handler._handle_incoming({'type':'user.message','text':'private preview','request_id':request},repl,bridge)
        await asyncio.gather(*list(bridge._post_turn_tasks))
    asyncio.run(run())
    assert [e for e in events if e.get('type')=='work-record.post-turn.result'] == [{'type':'work-record.post-turn.result','session_id':'synthetic','request_id':request,'outcome':expected}]

def test_synthetic_ws_real_capture_emits_scoped_result(tmp_path, monkeypatch):
    import json
    from tests.test_server_post_turn_freeze_focused import make_fixture
    from work_records import WorkRecordStore
    paths, session, repl, _, _ = make_fixture(tmp_path, True)
    monkeypatch.setattr(server, 'resume_desktop_or_create', lambda _: session)
    def build(_session, _paths, bridge, **kwargs):
        bridge._session_provider=lambda: repl.session
        bridge.init_post_turn_send_gate(repl.session)
        return repl
    monkeypatch.setattr(server, '_build_repl', build)
    monkeypatch.setattr(server.WsBridge, 'emit_session_state', lambda *args: None)
    monkeypatch.setattr(server, 'emit_corruption_notices', lambda *args: None)
    monkeypatch.setattr(server, '_emit_pending_work_card_references', lambda *args: None)
    class Registry:
        def get(self, _id): return SimpleNamespace(id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=96000)
    monkeypatch.setattr('work_records_service.get_registry',lambda *a,**k:Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry',lambda *a,**k:Registry())
    class Provider:
        paths=None
        def chat(self,*args,**kwargs): return '{"action":"record","kind":"requirement","title":"synthetic","requirement":"synthetic deliverable","scope":"independent","item_id":null}'
    monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs:Provider())
    class Transport:
        def __init__(self): self.incoming=asyncio.Queue(); self.events=[]
        def __aiter__(self): return self
        async def __anext__(self):
            value=await self.incoming.get()
            if value is None: raise StopAsyncIteration
            return value
        async def send(self,raw): self.events.append(json.loads(raw))
    handler=server.WsSessionHandler(paths); handler._post_turn_commit_ready=True
    request=str(uuid.uuid4())
    async def run():
        ws=Transport(); task=asyncio.create_task(handler.handle(ws)); approved=set()
        await ws.incoming.put(json.dumps({'type':'user.message','text':'synthetic task','request_id':request}))
        for _ in range(500):
            for event in list(ws.events):
                if event.get('type')=='confirm.request' and event.get('confirm_kind')=='work_record_post_turn' and event['request_id'] not in approved:
                    approved.add(event['request_id']); await ws.incoming.put(json.dumps({'type':'confirm.response','request_id':event['request_id'],'choice':'y'}))
            if any(e.get('type')=='work-record.post-turn.result' for e in ws.events): break
            await asyncio.sleep(.01)
        await ws.incoming.put(None); await task
        return ws.events, approved
    events,approved=asyncio.run(run())
    result=[e for e in events if e.get('type')=='work-record.post-turn.result']
    assert result==[{'type':'work-record.post-turn.result','session_id':session.conversation_id,'request_id':request,'outcome':'recorded'}]
    assert len(approved)==2 and len(WorkRecordStore(session).list_items())==1
    assert handler._post_turn_commit_ready is True  # synthetic opt-in only
