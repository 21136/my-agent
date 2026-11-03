from __future__ import annotations
import asyncio
import sys
import uuid
import pytest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from tests.test_server_post_turn_freeze_focused import make_fixture

@pytest.fixture(autouse=True)
def isolated_turn_lock(monkeypatch):
    monkeypatch.setattr(server, "TURN_LOCK", asyncio.Lock())


def test_cancel_between_freeze_and_ticket_never_requests_permission_or_sends(tmp_path):
    paths, session, repl, bridge, events = make_fixture(tmp_path, True)
    bridge.init_post_turn_send_gate(session)
    async def freeze():
        async with server.TURN_LOCK:
            return await server._run_line(repl, bridge, 'synthetic task', paths, lock_held=True,
                                          post_turn_request_id=str(uuid.uuid4()))
    candidate=asyncio.run(freeze()); assert candidate is not None
    # No ticket yet: this is the otherwise invisible cancellation interval.
    bridge.request_cancel()
    bridge.confirm_post_turn=lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected confirmation'))
    client=SimpleNamespace(chat=lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected external request')))
    result=server._execute_post_turn_candidate(candidate, bridge, client)
    assert result.status == 'stale_context'
    assert not any(e.get('type')=='confirm.request' for e in events)
    assert not bridge._post_turn_send_gate._states


def test_session_roundtrip_between_freeze_and_ticket_invalidates_candidate(tmp_path):
    paths, session, repl, bridge, events = make_fixture(tmp_path, False)
    bridge.init_post_turn_send_gate(session)
    async def freeze():
        async with server.TURN_LOCK:
            return await server._run_line(repl,bridge,'synthetic',paths,lock_held=True,
                                           post_turn_request_id=str(uuid.uuid4()))
    candidate=asyncio.run(freeze()); assert candidate is not None
    bridge.begin_session_binding_change(); bridge.end_session_binding_change()
    bridge.confirm_post_turn=lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected confirmation'))
    result=server._execute_post_turn_candidate(candidate,bridge,SimpleNamespace(chat=lambda *a,**k: None))
    assert result.status=='stale_context'
    assert not any(e.get('type')=='confirm.request' for e in events)

def test_staged_same_request_is_rejected_before_repeat_dispatch(tmp_path, monkeypatch):
    paths, session, repl, bridge, events = make_fixture(tmp_path, False)
    handler=object.__new__(server.WsSessionHandler)
    handler._post_turn_commit_ready=True
    calls=[]
    async def dispatch(message, *_args):
        calls.append(message['text'])
        await asyncio.sleep(.02)
    monkeypatch.setattr(handler, '_dispatch', dispatch)
    request=str(uuid.uuid4())
    async def run():
        message={'type':'user.message','text':'synthetic side effect','request_id':request}
        await asyncio.gather(handler._handle_incoming(message,repl,bridge),handler._handle_incoming(message,repl,bridge))
    asyncio.run(run())
    assert calls==['synthetic side effect']
    assert any('重复' in e.get('text','') for e in events)


def test_replay_guard_capacity_refuses_instead_of_evicting(tmp_path, monkeypatch):
    paths, session, repl, bridge, events = make_fixture(tmp_path, False)
    handler=object.__new__(server.WsSessionHandler); handler._post_turn_commit_ready=True
    calls=[]
    async def dispatch(message,*args): calls.append(message['request_id'])
    monkeypatch.setattr(handler,'_dispatch',dispatch)
    monkeypatch.setattr(server,'_POST_TURN_REQUEST_CAPACITY',1)
    first=str(uuid.uuid4()); second=str(uuid.uuid4())
    async def run():
        for request in [first,second,first]:
            await handler._handle_incoming({'type':'user.message','text':'synthetic','request_id':request},repl,bridge)
    asyncio.run(run())
    assert calls==[first]
    assert any('上限' in e.get('text','') for e in events)

def test_synthetic_websocket_replay_does_not_repeat_host_tool(tmp_path, monkeypatch):
    import json
    import llm_client
    paths, session, repl, _, _=make_fixture(tmp_path,True)
    executions=[]; original=repl.handle_line
    def handle(line):
        executions.append(line)
        return original(line)
    repl.handle_line=handle
    monkeypatch.setattr(server,'resume_desktop_or_create',lambda _:session)
    def build(_session,_paths,bridge,**kwargs):
        bridge._session_provider=lambda:repl.session
        bridge.init_post_turn_send_gate(repl.session)
        return repl
    monkeypatch.setattr(server,'_build_repl',build)
    monkeypatch.setattr(server.WsBridge,'emit_session_state',lambda *a:None)
    monkeypatch.setattr(server,'emit_corruption_notices',lambda *a:None)
    monkeypatch.setattr(server,'_emit_pending_work_card_references',lambda *a:None)
    monkeypatch.setattr(llm_client,'LLMClient',lambda **kwargs: object())
    monkeypatch.setattr(server,'_execute_post_turn_candidate',lambda *a:SimpleNamespace(status='not_recorded'))
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
        ws=Transport();task=asyncio.create_task(handler.handle(ws));request=str(uuid.uuid4())
        message=json.dumps({'type':'user.message','text':'synthetic work','request_id':request})
        await ws.incoming.put(message);await ws.incoming.put(message)
        for _ in range(400):
            if any('重复请求' in e.get('text','') for e in ws.events):break
            await asyncio.sleep(.01)
        await ws.incoming.put(None);await task
        return ws.events
    events=asyncio.run(run())
    assert executions==['synthetic work']
    assert any('重复请求' in e.get('text','') for e in events)
    assert sum(e.get('type')=='turn.end' for e in events)==1


def test_default_path_does_not_enable_replay_barrier(tmp_path, monkeypatch):
    _, _, repl, bridge, _=make_fixture(tmp_path,False)
    handler=object.__new__(server.WsSessionHandler);handler._post_turn_commit_ready=False
    calls=[]
    async def dispatch(message,*a): calls.append(message)
    monkeypatch.setattr(handler,'_dispatch',dispatch)
    async def run():
        msg={'type':'user.message','text':'synthetic','request_id':str(uuid.uuid4())}
        await handler._handle_incoming(msg,repl,bridge);await handler._handle_incoming(msg,repl,bridge)
    asyncio.run(run())
    assert len(calls)==2

def test_duplicate_request_is_not_delivered_as_active_tool_input(tmp_path,monkeypatch):
    _, session, repl, bridge, events=make_fixture(tmp_path,False)
    handler=object.__new__(server.WsSessionHandler);handler._post_turn_commit_ready=True
    request=str(uuid.uuid4());bridge._post_turn_seen_requests.add((session.conversation_id,request))
    bridge._awaiting_input.set()
    asyncio.run(handler._handle_incoming({'type':'user.message','text':'synthetic replay','request_id':request},repl,bridge))
    assert bridge._input_queue.empty()
    assert any('重复请求' in e.get('text','') for e in events)
