from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_server_auto_recognition_ws_chain_focused import harness
from work_records import WorkRecordStore

@pytest.mark.parametrize('bound', [True, False])
def test_auto_project_prompt_matches_sent_payload_and_host_binding(tmp_path, monkeypatch, bound):
    class Provider:
        paths = None
        def __init__(self): self.calls = []
        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            system = messages[0]['content']
            assert 'current_project_available' not in system
            payload = json.loads(messages[1]['content'])
            assert set(payload) == {'text', 'recognition_view'}
            assert payload['recognition_view'] == []
            assert 'synthetic-project' not in json.dumps(messages, ensure_ascii=False)
            return json.dumps({'action': 'record', 'kind': 'requirement', 'title': 'synthetic',
                'requirement': 'synthetic deliverable', 'scope': 'current_project', 'item_id': None})
    provider = Provider()
    session, repl, held, handler, wait, begin, message, finish = harness(tmp_path, monkeypatch, provider)
    if bound: session.meta.project_id = 'synthetic-project'
    async def run():
        ws, task = await begin()
        rid = await message(ws, '当前项目合成需求')
        if bound:
            confirm = await wait(ws, lambda e: e.get('type') == 'confirm.request' and e.get('confirm_kind') == 'work_record_post_turn')
            assert confirm['work_record_candidate']['scope'] == {'kind': 'project', 'project_id': 'synthetic-project'}
            await ws.incoming.put({'type': 'confirm.response', 'request_id': confirm['request_id'], 'choice': 'n'})
        result = await wait(ws, lambda e: e.get('type') == 'work-record.auto-recognition.result' and e.get('request_id') == rid)
        assert result['status'] == ('storage_not_authorized' if bound else 'not_recorded')
        assert len(provider.calls) == 1
        assert WorkRecordStore(session).list_items() == []
        await finish(ws, task)
    asyncio.run(run())
