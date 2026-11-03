"""Synthetic cross-layer batch recall; mocked HTTP, no GUI or network."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import Agent
from llm_client import LLMClient
from llm_models import ModelEntry, ModelRegistry
from server import WsBridge, _patch_repl
from test_confirmed_llm_request import make_registry, install_transport
from test_work_card_recall_live_optin import _fixture
import work_card_recall as recall
import work_records_luna_recall as luna


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('mode', ['success', 'switch_before', 'cancel_before', 'switch_after_one', 'cancel_after_one'])
def test_batch_bridge_binding_and_exact_mock_requests(tmp_path, monkeypatch, mode):
    ledger, project_id, independent_id = _fixture(tmp_path)
    session = ledger._session
    store = ledger._store
    client = LLMClient(paths=session.paths)
    agent = Agent.create(session, llm=client, enable_work_record_recall=True, confirm_fn=lambda *_: 'n')
    agent.work_record_recall_controller = SimpleNamespace(_store=store, _semantic_matcher=SimpleNamespace(_client=client))
    repl = SimpleNamespace(session=session, agent=agent, _rebind_agent=lambda: None)
    events, deliveries = [], []
    bridge = WsBridge(emit=lambda event: None, paths=session.paths)
    ordinary = []
    # Install the spy on the bridge before actual REPL wiring.
    def ordinary_confirm(*_args):
        ordinary.append(True)
        return 'n'
    bridge.confirm_fn = ordinary_confirm
    _patch_repl(repl, bridge)
    assert agent.executor.confirm_fn is bridge.confirm_fn
    assert agent.work_card_batch_confirm_fn == bridge.confirm_work_card_batch
    question = '请找回之前关于Kestrel741的工作卡'
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                       base_url='https://example.test', provider_model='gpt-6-luna',
                       max_input_tokens=128000, api_key='synthetic-key')
    registry = make_registry(entry)
    monkeypatch.setattr(luna, 'get_registry', lambda *_: registry)
    monkeypatch.setattr(recall, 'MAX_CONFIRM_EVENT_BYTES', 1)
    real_prepare = recall.prepare_batch_recall
    def small_batch(ledger_arg, entries, text, **kwargs):
        if 'max_batch_bytes' in kwargs:
            return real_prepare(ledger_arg, entries, text, **kwargs)
        full = real_prepare(ledger_arg, entries, text, **kwargs)
        assert full.status == 'ready'
        full_payloads = json.loads(full.preview_json)['provider_payloads']
        assert len(full_payloads) == 1
        rows = json.loads(full_payloads[0]['messages'][1]['content'])['candidates']
        assert len(rows) == 2
        limit = max(len(canonical(recall._batch_payload(text, [r]))) for r in rows)
        assert limit < len(canonical(recall._batch_payload(text, rows)))
        return real_prepare(ledger_arg, entries, text, max_batch_bytes=limit, **kwargs)
    monkeypatch.setattr(recall, 'prepare_batch_recall', small_batch)

    def emit(event):
        events.append(event)
        if event.get('type') != 'confirm.request':
            return
        data = json.loads(event['preview'])
        assert len(data['provider_payloads']) == 2
        assert data['session_id'] == session.conversation_id and event['allow_approve_all'] is False
        assert {json.loads(p['messages'][1]['content'])['candidates'][0]['id'] for p in data['provider_payloads']} == {project_id, independent_id}
        assert all(len(json.loads(p['messages'][1]['content'])['candidates']) == 1 for p in data['provider_payloads'])
        if mode == 'switch_before':
            repl.session = object()
        elif mode == 'cancel_before':
            bridge.cancel_event.set()
        deliveries.append(bridge.deliver_confirm(event['request_id'], 'y'))
    bridge.emit = emit
    requests = []
    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        ids = [row['id'] for row in json.loads(payload['messages'][1]['content'])['candidates']]
        if mode == 'switch_after_one':
            repl.session = object()
            agent.session = repl.session
        elif mode == 'cancel_after_one':
            bridge.cancel_event.set()
        selected = [project_id] if project_id in ids else []
        content = json.dumps({'status': 'match' if selected else 'not_found', 'candidate_ids': selected})
        return httpx.Response(200, json={'model': 'gpt-6-luna', 'choices': [{'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': content}}]})
    install_transport(monkeypatch, handler)
    from work_card_ledger import LEDGER_FILENAME
    before = (ledger._session_dir / LEDGER_FILENAME).read_bytes()
    with patch.object(ModelRegistry, 'load', return_value=registry):
        turn = agent.run_turn(question, spawn_explore=False)
    confirmations = [event for event in events if event.get('type') == 'confirm.request']
    assert len(confirmations) == 1 and not ordinary
    assert deliveries == ([False] if mode == 'cancel_before' else [True])
    preview = json.loads(confirmations[0]['preview'])
    expected = [canonical(payload) for payload in preview['provider_payloads']]
    expected_count = 2 if mode == 'success' else (1 if mode.endswith('after_one') else 0)
    assert len(requests) == expected_count
    assert [r.content for r in requests] == expected[:expected_count]
    assert all(str(r.url) == entry.chat_completions_url() for r in requests)
    assert turn.finish_reason == 'work_card_recall'
    if mode == 'success':
        assert project_id in turn.assistant_text and independent_id not in turn.assistant_text
        assert '可能相关' in turn.assistant_text
    else:
        assert '未完成' in turn.assistant_text and project_id not in turn.assistant_text
    assert (ledger._session_dir / LEDGER_FILENAME).read_bytes() == before
    assert all(record['viewed_at'] is None for record in ledger.list_cards())
