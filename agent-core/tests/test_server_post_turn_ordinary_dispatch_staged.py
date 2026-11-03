from __future__ import annotations
import asyncio
import sys
import uuid
import pytest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_client
import server
from paths import AgentPaths
from session import Session, SessionMeta


def test_opt_in_ordinary_dispatch_schedules_only_after_lock_release(tmp_path, monkeypatch):
    paths=AgentPaths(tmp_path, tmp_path/'workspace', tmp_path/'data', tmp_path/'evolve')
    session_dir=paths.data/'sessions'/'synthetic'; session_dir.mkdir(parents=True)
    session=Session('synthetic', session_dir, '', SessionMeta(), [], paths)
    bridge=server.WsBridge(emit=lambda e: None, paths=paths)
    bridge._session_provider=lambda: session
    bridge.init_post_turn_send_gate(session)
    repl=SimpleNamespace(session=session, agent=SimpleNamespace(executor=SimpleNamespace(session=SimpleNamespace())), last_turn_finish_reason=None)
    handler=object.__new__(server.WsSessionHandler)
    handler.paths=paths; handler.managed_readonly_context=None; handler._file_stage=SimpleNamespace(take=lambda *_: [])
    handler._post_turn_commit_ready=True
    request=str(uuid.uuid4()); captured=[]; observed=[]
    candidate=server._FrozenPostTurnCandidate(session, 'synthetic', request, uuid.uuid4().hex, 3, 'completed', (('run','call','read_file',True),))
    async def fake_run(*args, **kwargs):
        captured.append(kwargs.get('post_turn_request_id'))
        return candidate
    monkeypatch.setattr(server, '_run_line', fake_run)
    monkeypatch.setattr(llm_client, 'LLMClient', lambda **kwargs: object())
    def fake_execute(c,b,client):
        observed.append((c, server.TURN_LOCK.locked()))
        return SimpleNamespace(status='not_recorded')
    monkeypatch.setattr(server, '_execute_post_turn_candidate', fake_execute)
    async def run():
        await handler._handle_incoming({'type':'user.message','text':'synthetic','request_id':request}, repl, bridge)
        for _ in range(100):
            if observed: break
            await asyncio.sleep(.01)
    asyncio.run(run())
    assert captured == [request] and observed == [(candidate, False)]


@pytest.mark.parametrize('perform_tool', [True, False])
def test_synthetic_ws_ordinary_turn_to_atomic_record(tmp_path, monkeypatch, perform_tool):
    import json
    import pytest
    from tests.test_server_post_turn_freeze_focused import make_fixture
    from work_records import WorkRecordStore
    paths, session, repl, unused_bridge, unused_events = make_fixture(tmp_path, perform_tool)
    monkeypatch.setattr(server, 'resume_desktop_or_create', lambda paths: session)
    def build(session_arg, paths_arg, bridge, **kwargs):
        bridge._session_provider=lambda: repl.session
        bridge.init_post_turn_send_gate(repl.session)
        return repl
    monkeypatch.setattr(server, '_build_repl', build)
    monkeypatch.setattr(server.WsBridge, 'emit_session_state', lambda *args: None)
    monkeypatch.setattr(server, 'emit_corruption_notices', lambda *args: None)
    monkeypatch.setattr(server, '_emit_pending_work_card_references', lambda *args: None)
    class Registry:
        def get(self, _id):
            return SimpleNamespace(id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=96000)
    monkeypatch.setattr('work_records_service.get_registry', lambda *a, **k: Registry())
    monkeypatch.setattr('work_records_luna_intent.get_registry', lambda *a, **k: Registry())
    class Provider:
        paths=None
        calls=0
        def chat(self, messages, **kwargs):
            self.calls+=1
            return '{"action":"record","kind":"requirement","title":"synthetic","requirement":"synthetic deliverable","scope":"independent","item_id":null}'
    provider=Provider()
    monkeypatch.setattr(llm_client, 'LLMClient', lambda **kwargs: provider)
    errors=[]; original=WorkRecordStore.create_item_with_requirement
    def trace(self, **kwargs):
        try: return original(self, **kwargs)
        except Exception as exc: errors.append(str(exc)); raise
    monkeypatch.setattr(WorkRecordStore, 'create_item_with_requirement', trace)
    class Transport:
        def __init__(self): self.incoming=asyncio.Queue(); self.events=[]
        def __aiter__(self): return self
        async def __anext__(self):
            value=await self.incoming.get()
            if value is None: raise StopAsyncIteration
            return value
        async def send(self, raw): self.events.append(json.loads(raw))
    handler=server.WsSessionHandler(paths)
    handler._post_turn_commit_ready=True
    request=str(uuid.uuid4())
    async def run():
        transport=Transport(); task=asyncio.create_task(handler.handle(transport))
        await transport.incoming.put(json.dumps({'type':'user.message','text':'synthetic task','request_id':request}))
        approved=set()
        for _ in range(400):
            for event in list(transport.events):
                if (event.get('type')=='confirm.request' and event.get('confirm_kind')=='work_record_post_turn'
                        and event['request_id'] not in approved):
                    approved.add(event['request_id'])
                    await transport.incoming.put(json.dumps({'type':'confirm.response','request_id':event['request_id'],'choice':'y'}))
            if len(approved)==2 and (errors or WorkRecordStore(session).list_items()): break
            await asyncio.sleep(.01)
        await transport.incoming.put(None)
        await task
        return transport.events, approved
    events, approved = asyncio.run(run())
    assert len(approved)==2 and provider.calls==1
    assert errors == []
    items=WorkRecordStore(session).list_items()
    assert len(items)==1 and items[0]['requirements'][0]['status']=='unknown'
    end=next(e for e in events if e.get('type')=='turn.end')
    assert uuid.UUID(items[0]['runs'][0]['run_id'])==uuid.UUID(end['run_id'])
    assert items[0]['runs'][0]['verification']['status']=='not_verified'


def test_synthetic_ws_reject_cancel_switch_and_next_turn_do_not_block_or_write(tmp_path, monkeypatch):
    import json
    from tests.test_server_post_turn_freeze_focused import make_fixture
    from work_records import WorkRecordStore
    for action in ('reject', 'cancel', 'switch', 'next_turn', 'disconnect'):
        root=tmp_path/action
        paths, session, repl, unused_bridge, unused_events=make_fixture(root, True)
        monkeypatch.setattr(server, 'resume_desktop_or_create', lambda _paths, s=session: s)
        repl._rebind_agent=lambda: None
        def build(_session, _paths, bridge, **kwargs):
            bridge._session_provider=lambda: repl.session
            bridge.init_post_turn_send_gate(repl.session)
            return repl
        monkeypatch.setattr(server, '_build_repl', build)
        monkeypatch.setattr(server.WsBridge, 'emit_session_state', lambda *args: None)
        monkeypatch.setattr(server, 'emit_corruption_notices', lambda *args: None)
        monkeypatch.setattr(server, '_emit_pending_work_card_references', lambda *args: None)
        other_dir=paths.data/'sessions'/'other-synthetic';other_dir.mkdir(parents=True)
        other=Session('other-synthetic', other_dir, '', SessionMeta(), [], paths)
        monkeypatch.setattr(server, 'load_session_for_harness', lambda *a, **k: other)
        class Provider:
            calls=0
            def chat(self, *args, **kwargs): self.calls+=1
        provider=Provider()
        monkeypatch.setattr(llm_client, 'LLMClient', lambda **kwargs: provider)
        class Transport:
            def __init__(self): self.incoming=asyncio.Queue(); self.events=[]
            def __aiter__(self): return self
            async def __anext__(self):
                value=await self.incoming.get()
                if value is None: raise StopAsyncIteration
                return value
            async def send(self, raw): self.events.append(json.loads(raw))
        handler=server.WsSessionHandler(paths); handler._post_turn_commit_ready=True
        async def run():
            transport=Transport(); task=asyncio.create_task(handler.handle(transport))
            async def send(payload): await transport.incoming.put(json.dumps(payload))
            await send({'type':'user.message','text':'synthetic task','request_id':str(uuid.uuid4())})
            first=None
            for _ in range(300):
                first=next((e for e in transport.events if e.get('type')=='confirm.request' and e.get('confirm_kind')=='work_record_post_turn'), None)
                if first: break
                await asyncio.sleep(.01)
            assert first is not None
            if action == 'reject':
                await send({'type':'confirm.response','request_id':first['request_id'],'choice':'n'})
            elif action == 'cancel':
                await send({'type':'turn.cancel'})
            elif action == 'switch':
                await send({'type':'session.open','session_id':other.conversation_id})
            elif action == 'disconnect':
                await transport.incoming.put(None)
                await task
                await asyncio.sleep(.2)
                return
            else:
                await send({'type':'user.message','text':'synthetic followup','request_id':str(uuid.uuid4())})
                for _ in range(300):
                    if sum(e.get('type')=='turn.end' for e in transport.events)>=2: break
                    await asyncio.sleep(.01)
                assert sum(e.get('type')=='turn.end' for e in transport.events)>=2
                await send({'type':'confirm.response','request_id':first['request_id'],'choice':'n'})
            for _ in range(300):
                if any(e.get('type')=='confirm.done' and e.get('request_id')==first['request_id'] for e in transport.events): break
                await asyncio.sleep(.01)
            assert any(e.get('type')=='confirm.done' and e.get('request_id')==first['request_id'] for e in transport.events)
            await transport.incoming.put(None); await task
        asyncio.run(run())
        assert provider.calls==0 and WorkRecordStore(session).list_items()==[]
