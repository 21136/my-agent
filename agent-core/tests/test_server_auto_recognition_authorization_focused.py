from __future__ import annotations
import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from paths import AgentPaths
from session import Session, SessionMeta
from work_record_recognition_queue import QueueStatus


class FakeSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
    def __aiter__(self): return self
    async def __anext__(self):
        value = await self.incoming.get()
        if value is None: raise StopAsyncIteration
        return json.dumps(value)
    async def send(self, data): await self.outgoing.put(json.loads(data))
    async def send_message(self, message): await self.incoming.put(message)
    async def next_done(self):
        while True:
            event = await asyncio.wait_for(self.outgoing.get(), 2)
            if event.get('type') == 'work-record.auto-recognition.done': return event


def request(session, action, request_id=None, **extra):
    return {'type': 'work-record.auto-recognition.set', 'session_id': session.conversation_id,
            'request_id': request_id or str(uuid.uuid4()), 'action': action, **extra}


def prepare(tmp_path, monkeypatch):
    paths = AgentPaths(tmp_path, tmp_path/'workspace', tmp_path/'data', tmp_path/'evolve')
    def make_session(name):
        directory = paths.data/'sessions'/name
        directory.mkdir(parents=True)
        return Session(name, directory, '', SessionMeta(), [], paths)
    first, second = make_session('synthetic-a'), make_session('synthetic-b')
    held = {}
    monkeypatch.setattr(server, 'resume_desktop_or_create', lambda _: first)
    def build(session, paths, bridge, **kwargs):
        repl = SimpleNamespace(session=session)
        bridge._session_provider = lambda: repl.session
        held.update(bridge=bridge, repl=repl)
        return repl
    monkeypatch.setattr(server, '_build_repl', build)
    monkeypatch.setattr(server.WsBridge, 'emit_session_state', lambda *args: None)
    monkeypatch.setattr(server, 'emit_corruption_notices', lambda *args: None)
    monkeypatch.setattr(server, '_emit_pending_work_card_references', lambda *args: None)
    handler = server.WsSessionHandler(paths)
    async def dispatch(message, repl, bridge, **kwargs):
        if message['type'] == 'session.open': repl.session = second
    monkeypatch.setattr(handler, '_dispatch', dispatch)
    return handler, first, second, held


async def start(handler):
    ws = FakeSocket(); task = asyncio.create_task(handler.handle(ws))
    await asyncio.sleep(0)
    return ws, task


def test_ws_enable_revoke_replay_and_malformed(tmp_path, monkeypatch):
    handler, first, _, held = prepare(tmp_path, monkeypatch)
    async def scenario():
        ws, task = await start(handler)
        bridge = held['bridge']
        msg = request(first, 'enable')
        await ws.send_message(msg); done = await ws.next_done()
        assert done == {'type':'work-record.auto-recognition.done','session_id':first.conversation_id,
                        'request_id':msg['request_id'],'action':'enable','status':'ok','enabled':True}
        q = bridge._auto_recognition_queue
        admission = q.enqueue(bridge, first)
        assert admission.status is QueueStatus.ENQUEUED
        await ws.send_message(request(first, 'enable')); assert (await ws.next_done())['enabled'] is True
        assert q.claim_next().request_token == admission.request_token
        await ws.send_message({**msg, 'action':'revoke'}); assert (await ws.next_done())['status'] == 'rejected'
        assert q.enqueue(bridge, first).status is QueueStatus.ENQUEUED
        revoke = request(first, 'revoke')
        await ws.send_message(revoke); done = await ws.next_done()
        assert done['status'] == 'ok' and done['enabled'] is False
        await ws.send_message(msg); assert (await ws.next_done())['status'] == 'rejected'
        assert q.enqueue(bridge, first).status is QueueStatus.UNAUTHORIZED
        for bad in [request(first, 'enable', 'bad'), request(first, 'enable', str(uuid.uuid4()).upper()),
                    request(first, 'enable', extra='x'), request(first, 'nope'),
                    {**request(first, 'enable'), 'session_id':'forged-session'}]:
            await ws.send_message(bad)
            assert (await ws.next_done())['status'] == 'rejected'
            assert q.enqueue(bridge, first).status is QueueStatus.UNAUTHORIZED
        await ws.incoming.put(None); await task
    asyncio.run(scenario())


def test_ws_switch_and_disconnect_revoke_exact_connection_session(tmp_path, monkeypatch):
    handler, first, second, held = prepare(tmp_path, monkeypatch)
    async def scenario():
        ws, task = await start(handler); bridge = held['bridge']
        await ws.send_message(request(first, 'enable')); assert (await ws.next_done())['enabled']
        q = bridge._auto_recognition_queue
        admit = q.enqueue(bridge, first); lease = q.claim_next()
        assert admit.request_token == lease.request_token
        await ws.send_message({'type':'session.open', 'session_id':second.conversation_id})
        for _ in range(200):
            if held['repl'].session is second: break
            await asyncio.sleep(.005)
        assert held['repl'].session is second
        assert q.finish_recognition(lease, bridge, first, candidate=True).status is QueueStatus.REVOKED
        await ws.send_message(request(first, 'enable')); assert (await ws.next_done())['status'] == 'rejected'
        await ws.send_message(request(second, 'enable')); assert (await ws.next_done())['enabled']
        assert q.enqueue(object(), second).status is QueueStatus.BINDING_MISMATCH
        await ws.incoming.put(None); await task
        assert q.enqueue(bridge, second).status is QueueStatus.UNAUTHORIZED
    asyncio.run(scenario())


def test_ws_revoke_invalidates_candidate_and_old_revoke_cannot_cancel_new_grant(tmp_path, monkeypatch):
    handler, session, _, held = prepare(tmp_path, monkeypatch)
    async def scenario():
        ws, task = await start(handler); bridge = held['bridge']
        await ws.send_message(request(session, 'enable')); assert (await ws.next_done())['enabled']
        q = bridge._auto_recognition_queue
        q.enqueue(bridge, session); lease = q.claim_next()
        ticket = q.finish_recognition(lease, bridge, session, candidate=True).ticket
        revoke = request(session, 'revoke')
        await ws.send_message(revoke); assert not (await ws.next_done())['enabled']
        assert q.ticket_status(ticket, bridge, session) is QueueStatus.REVOKED
        await ws.send_message(request(session, 'enable')); assert (await ws.next_done())['enabled']
        await ws.send_message(revoke); done = await ws.next_done()
        assert done['status'] == 'rejected' and done['enabled']
        assert q.enqueue(bridge, session).status is QueueStatus.ENQUEUED
        await ws.incoming.put(None); await task
    asyncio.run(scenario())


def test_two_ws_connections_do_not_share_authorization(tmp_path, monkeypatch):
    handler, session, _, held = prepare(tmp_path, monkeypatch)
    async def scenario():
        one, task_one = await start(handler); bridge_one = held['bridge']
        two, task_two = await start(handler); bridge_two = held['bridge']
        await one.send_message(request(session, 'enable')); assert (await one.next_done())['enabled']
        assert bridge_one._auto_recognition_queue.enqueue(bridge_one, session).status is QueueStatus.ENQUEUED
        assert bridge_two._auto_recognition_queue is None
        await two.send_message(request(session, 'revoke')); done = await two.next_done()
        assert done['status'] == 'ok' and not done['enabled']
        assert bridge_one._auto_recognition_queue.enqueue(bridge_one, session).status is QueueStatus.ENQUEUED
        await one.incoming.put(None); await two.incoming.put(None)
        await task_one; await task_two
    asyncio.run(scenario())


def test_request_tombstone_capacity_denies_new_enable_but_allows_revoke(tmp_path, monkeypatch):
    handler, session, _, held = prepare(tmp_path, monkeypatch)
    async def scenario():
        ws, task = await start(handler); bridge = held['bridge']
        await ws.send_message(request(session, 'enable')); assert (await ws.next_done())['enabled']
        bridge._auto_recognition_request_ids = {str(uuid.uuid4()) for _ in range(2048)}
        await ws.send_message(request(session, 'enable')); done = await ws.next_done()
        assert done['status'] == 'rejected' and done['enabled']
        await ws.send_message(request(session, 'revoke')); done = await ws.next_done()
        assert done['status'] == 'ok' and not done['enabled']
        await ws.send_message(request(session, 'enable')); done = await ws.next_done()
        assert done['status'] == 'rejected' and not done['enabled']
        await ws.incoming.put(None); await task
    asyncio.run(scenario())
