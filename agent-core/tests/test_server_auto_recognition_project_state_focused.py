from __future__ import annotations
import asyncio
import json
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_server_auto_recognition_ws_chain_focused import harness
from work_records import WorkRecordStore

class PausedProvider:
    paths = None
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
    def chat(self, messages, **kwargs):
        self.entered.set()
        if not self.release.wait(8):
            raise TimeoutError('synthetic provider timed out')
        return json.dumps({'action':'record', 'kind':'requirement', 'title':'synthetic item',
            'requirement':'synthetic output', 'scope':'independent', 'item_id':None})

def test_project_state_during_recognition_keeps_authorization_and_allows_confirmation(tmp_path, monkeypatch):
    provider = PausedProvider()
    session, repl, held, handler, wait, begin, message, finish = harness(tmp_path, monkeypatch, provider)
    async def run():
        ws, task = await begin()
        rid = await message(ws, 'synthetic task')
        assert await asyncio.to_thread(provider.entered.wait, 3)
        bridge = held['bridge']
        generation = bridge._post_turn_generation
        await ws.incoming.put({'type':'project.state'})
        state = await wait(ws, lambda e:e.get('type') == 'project.state')
        assert state['project_id'] is None
        assert bridge._post_turn_generation == generation
        assert bridge._auto_recognition_session is session
        assert not bridge._auto_recognition_active['cancel_event'].is_set()
        provider.release.set()
        confirm = await wait(ws, lambda e:e.get('type') == 'confirm.request' and e.get('confirm_kind') == 'work_record_post_turn')
        await ws.incoming.put({'type':'confirm.response', 'request_id':confirm['request_id'], 'choice':'y'})
        result = await wait(ws, lambda e:e.get('type') == 'work-record.auto-recognition.result' and e.get('request_id') == rid)
        assert result['status'] == 'recorded'
        assert len(WorkRecordStore(session).list_items()) == 1
        await finish(ws, task)
    try:
        asyncio.run(run())
    finally:
        provider.release.set()

def test_real_project_switch_ingress_still_cancels_and_revokes(tmp_path, monkeypatch):
    provider = PausedProvider()
    session, repl, held, handler, wait, begin, message, finish = harness(tmp_path, monkeypatch, provider)
    original = handler._dispatch
    async def dispatch(msg, repl, bridge, **kwargs):
        if msg['type'] == 'project.switch':
            repl.session.meta.project_id = 'synthetic-new-project'
            bridge.emit({'type':'project.switch.done','applied':True})
            return None
        return await original(msg, repl, bridge, **kwargs)
    monkeypatch.setattr(handler, '_dispatch', dispatch)
    async def run():
        ws, task = await begin()
        rid = await message(ws, 'synthetic task')
        assert await asyncio.to_thread(provider.entered.wait, 3)
        await ws.incoming.put({'type':'project.switch', 'project_id':'synthetic-new-project'})
        await wait(ws, lambda e:e.get('type') == 'project.switch.done')
        assert held['bridge']._auto_recognition_session is None
        provider.release.set()
        result = await wait(ws, lambda e:e.get('type') == 'work-record.auto-recognition.result' and e.get('request_id') == rid)
        assert result['status'] == 'cancelled'
        assert not [e for e in ws.events if e.get('type') == 'confirm.request' and e.get('confirm_kind') == 'work_record_post_turn']
        assert WorkRecordStore(session).list_items() == []
        await finish(ws, task)
    try:
        asyncio.run(run())
    finally:
        provider.release.set()

def test_project_state_keeps_pending_confirmation_valid(tmp_path, monkeypatch):
    provider = PausedProvider()
    provider.release.set()
    session, repl, held, handler, wait, begin, message, finish = harness(tmp_path, monkeypatch, provider)
    async def run():
        ws, task = await begin()
        rid = await message(ws, 'synthetic task')
        confirm = await wait(ws, lambda e:e.get('type') == 'confirm.request' and e.get('confirm_kind') == 'work_record_post_turn')
        generation = held['bridge']._post_turn_generation
        await ws.incoming.put({'type':'project.state'})
        await wait(ws, lambda e:e.get('type') == 'project.state')
        assert held['bridge']._post_turn_generation == generation
        await ws.incoming.put({'type':'confirm.response', 'request_id':confirm['request_id'], 'choice':'y'})
        result = await wait(ws, lambda e:e.get('type') == 'work-record.auto-recognition.result' and e.get('request_id') == rid)
        assert result['status'] == 'recorded'
        assert len(WorkRecordStore(session).list_items()) == 1
        await finish(ws, task)
    asyncio.run(run())
