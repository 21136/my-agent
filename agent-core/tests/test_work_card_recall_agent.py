from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import Agent, _is_explicit_work_card_lookup
from llm_client import LLMClient, LLMResponse
from work_card_ledger import WorkCardLedger
from test_work_card_ledger import make_independent_session, REQUEST_ID, OTHER_REQUEST_ID
import work_records_luna_recall as luna


@pytest.mark.parametrize('text', ['找回之前的工作卡', '请帮我查找历史工作卡片', '找回之前关于合成需求的工作卡'])
def test_explicit_card_phrases_route(text):
    assert _is_explicit_work_card_lookup(text)


@pytest.mark.parametrize('text', ['\u627e\u56de\u4e4b\u524d\u5173\u4e8e\u767b\u5f55\u8d85\u65f6\u7684\u5de5\u4f5c\u5361\uff0c\u7ed3\u679c\u5982\u4f55\uff1f'])
def test_explicit_result_question_routes_as_one_card_lookup(text):
    assert _is_explicit_work_card_lookup(text)


@pytest.mark.parametrize('text', [
    '刚才我们说了什么', '之前项目A做了什么', '查一下之前的工作记录',
    '不要找回之前的工作卡', '“找回之前的工作卡”是什么意思', '创建之前的工作卡',
    '找回之前的工作卡并删除它', '找回之前的工作卡，然后执行任务',
    '请修改之前的工作卡', '找回之前关于删除文件的工作卡',
    'Recap the requirements recorded for the synthetic Zeta-41 mission-log item.',
    '找回之前的工作卡\n删除文件',
])
def test_other_requests_do_not_enter_card_route(text):
    assert not _is_explicit_work_card_lookup(text)


@pytest.mark.parametrize('text', [
    '\u627e\u56de\u4e4b\u524d\u5173\u4e8e\u767b\u5f55\u8d85\u65f6\u7684\u5de5\u4f5c\u5361\uff0c\u7ed3\u679c\u5982\u4f55\uff0c\u7136\u540e\u6267\u884c\u4efb\u52a1',
    '\u627e\u56de\u4e4b\u524d\u5173\u4e8e\u767b\u5f55\u8d85\u65f6\u7684\u5de5\u4f5c\u5361\uff0c\u7ed3\u679c\u5982\u4f55\uff0c\u7136\u540e\u4fee\u6539\u4ee3\u7801',
])
def test_result_question_with_extra_action_is_not_a_card_lookup(text):
    assert not _is_explicit_work_card_lookup(text)


def setup_case(tmp_path, monkeypatch, *, count=1, confirm=None):
    session, store, snapshot, _, _ = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    first = ledger.save_snapshot(REQUEST_ID, snapshot)
    if count == 2:
        assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _: SimpleNamespace(
        id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=1_000_000,
        chat_completions_url=lambda: 'https://example.com/v1/chat/completions')))
    client = LLMClient(paths=session.paths)
    agent = Agent.create(session, llm=client, confirm_fn=confirm or (lambda *_: 'y'), enable_work_record_recall=True)
    agent.work_record_recall_controller = SimpleNamespace(_store=store, _semantic_matcher=SimpleNamespace(_client=client))
    return agent, session, store, ledger, first.card_id


def response(ids):
    content = json.dumps({'status': 'match' if len(ids) == 1 else 'ambiguous', 'candidate_ids': ids})
    raw = {'model': 'gpt-6-luna', 'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}]}
    return LLMResponse(model='gpt-6-luna', content=content, tool_calls=[], finish_reason='stop', usage=None, raw=raw)


def install_client(monkeypatch, ids, calls, *, after=None):
    def fake(self, messages, *, model_id, session_id, expected_preview_json, confirm_fn):
        calls.append((messages, model_id, session_id, expected_preview_json))
        if confirm_fn(expected_preview_json, False) != 'y':
            raise RuntimeError('not approved')
        if after:
            after()
        return response(ids)
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', fake)
    monkeypatch.setattr(LLMClient, 'chat', lambda *a, **kw: pytest.fail('ordinary chat not allowed'))


def test_card_recall_returns_local_complete_record_without_ack(tmp_path, monkeypatch):
    agent, session, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    calls = []
    install_client(monkeypatch, [card_id], calls)
    before = ledger.list_cards()
    branch_calls = []
    original = Agent._run_work_card_recall
    def observe_branch(self, text):
        branch_calls.append(text)
        return original(self, text)
    monkeypatch.setattr(Agent, '_run_work_card_recall', observe_branch)
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert branch_calls == ['找回之前的工作卡']
    assert result.finish_reason == 'work_card_recall' and result.tool_rounds == 0
    assert '可能' in result.assistant_text and '历史快照' in result.assistant_text
    assert 'Synthetic résumé requirement' in result.assistant_text
    assert 'synthetic old 1' in result.assistant_text and '不属于当前运行' in result.assistant_text
    assert 'not_verified' in result.assistant_text and '不代表已读' in result.assistant_text
    assert before[0]['request_id'] not in result.assistant_text
    assert before[0]['snapshot_sha256'] not in result.assistant_text
    assert json.dumps(before[0], ensure_ascii=False, sort_keys=True) not in result.assistant_text
    assert ledger.list_cards() == before and before[0]['viewed_at'] is None
    assert len(calls) == 1 and len(session.messages) == 2
    assert calls[0][0] == json.loads(calls[0][3])['provider_payload']['messages']


def test_result_question_recall_preserves_original_query_and_shows_saved_result_boundary(tmp_path, monkeypatch):
    agent, _, _, ledger, card_id = setup_case(tmp_path, monkeypatch)
    calls = []
    install_client(monkeypatch, [card_id], calls)
    query = '\u627e\u56de\u4e4b\u524d\u5173\u4e8e\u767b\u5f55\u8d85\u65f6\u7684\u5de5\u4f5c\u5361\uff0c\u7ed3\u679c\u5982\u4f55\uff1f'

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'work_card_recall' and result.tool_rounds == 0
    assert query in json.dumps(calls[0][0], ensure_ascii=False)
    assert calls[0][0] == json.loads(calls[0][3])['provider_payload']['messages']
    assert '\u5386\u53f2\u5feb\u7167\u5019\u9009' in result.assistant_text
    assert '\u7ed3\u679c\u6458\u8981' in result.assistant_text
    assert 'synthetic old 1' in result.assistant_text
    assert 'not_verified' in result.assistant_text
    assert '\u4e0d\u5c5e\u4e8e\u5f53\u524d\u8fd0\u884c' in result.assistant_text
    assert '\u4e0d\u4ee3\u8868\u5df2\u8bfb' in result.assistant_text
    assert ledger.get_card(card_id)['viewed_at'] is None

def test_ambiguous_returns_only_candidate_metadata_without_selection_claim(tmp_path, monkeypatch):
    agent, _, _, ledger, _ = setup_case(tmp_path, monkeypatch, count=2)
    records = ledger.list_cards()
    calls = []
    install_client(monkeypatch, [r['card_id'] for r in records], calls)
    result = agent.run_turn('查找之前的工作卡', spawn_explore=False)
    assert '不能确定唯一' in result.assistant_text and '重新发起' in result.assistant_text
    for record in records:
        assert record['card_id'] in result.assistant_text
        assert json.dumps(record, ensure_ascii=False, sort_keys=True) not in result.assistant_text
    assert 'Synthetic résumé requirement' not in result.assistant_text
    assert ledger.list_cards() == records and len(calls) == 1


def test_confirmation_session_switch_blocks_request_and_result(tmp_path, monkeypatch):
    agent, original, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    calls = []
    sent = []
    def confirm(*_):
        agent.session = object()
        return 'y'
    agent.executor.confirm_fn = confirm
    install_client(monkeypatch, [card_id], calls, after=lambda: sent.append(True))
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert not sent and result.finish_reason == 'work_card_recall'
    assert card_id not in result.assistant_text and len(calls) == 1
    assert ledger.get_card(card_id)['viewed_at'] is None


def test_refusal_and_network_error_do_not_claim_absence_or_retry(tmp_path, monkeypatch):
    agent, _, _, ledger, card_id = setup_case(tmp_path, monkeypatch, confirm=lambda *_: 'n')
    calls = []
    install_client(monkeypatch, [card_id], calls)
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and len(calls) == 1
    agent.executor.confirm_fn = lambda *_: 'y'
    def failing(self, messages, **kwargs):
        calls.append('error')
        assert kwargs['confirm_fn'](kwargs['expected_preview_json'], False) == 'y'
        raise RuntimeError('private')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', failing)
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and '未发送' not in result.assistant_text
    assert calls.count('error') == 1 and ledger.get_card(card_id)['viewed_at'] is None

@pytest.mark.parametrize('text', ['找回之前的工作卡？', '请帮我查找历史工作卡片。'])
def test_card_lookup_accepts_sentence_terminal_punctuation(text):
    assert _is_explicit_work_card_lookup(text)


def test_unbound_store_or_non_llm_client_fails_closed_before_confirmation(tmp_path, monkeypatch):
    agent, _, store, ledger, card_id = setup_case(tmp_path, monkeypatch)
    asked = []
    agent.executor.confirm_fn = lambda *args: asked.append(args) or 'y'
    agent.work_record_recall_controller._semantic_matcher._client = SimpleNamespace(
        chat_confirmed_json=lambda *a, **kw: pytest.fail('untrusted client'))
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and not asked
    assert ledger.get_card(card_id)['viewed_at'] is None


def test_ledger_change_during_confirmation_stops_before_send(tmp_path, monkeypatch):
    agent, _, _, ledger, card_id = setup_case(tmp_path, monkeypatch)
    # Change the viewed metadata through the public separate acknowledgement path,
    # solely as a concurrent actor in this test, not as an action by recall.
    sent, calls = [], []
    def confirm(*_):
        assert ledger.acknowledge_viewed(card_id).status == 'acknowledged'
        return 'y'
    agent.executor.confirm_fn = confirm
    install_client(monkeypatch, [card_id], calls, after=lambda: sent.append(True))
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and not sent and len(calls) == 1


def test_session_switch_after_provider_response_discards_card(tmp_path, monkeypatch):
    agent, _, _, ledger, card_id = setup_case(tmp_path, monkeypatch)
    calls = []
    install_client(monkeypatch, [card_id], calls, after=lambda: setattr(agent, 'session', object()))
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and card_id not in result.assistant_text
    assert ledger.get_card(card_id)['viewed_at'] is None


def test_invalid_raw_response_fails_without_exposing_card(tmp_path, monkeypatch):
    agent, _, _, ledger, card_id = setup_case(tmp_path, monkeypatch)
    def invalid(self, messages, **kwargs):
        assert kwargs['confirm_fn'](kwargs['expected_preview_json'], False) == 'y'
        r = response([card_id])
        r.raw.pop('model')
        return r
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', invalid)
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and card_id not in result.assistant_text
    assert '未发送' not in result.assistant_text and ledger.get_card(card_id)['viewed_at'] is None

@pytest.mark.parametrize('text', [
    '找回之前关于备份并上传的工作卡',
    '找回之前关于顺便发布的工作卡',
    '找回之前关于启动服务的工作卡',
])
def test_mixed_action_words_in_description_fail_closed(text):
    assert not _is_explicit_work_card_lookup(text)


def test_project_snapshot_keeps_incomplete_history_and_verification_facts(tmp_path, monkeypatch):
    from test_work_card_ledger import make_session
    from work_record_cards import build_project_work_record_cards
    session, store, _ = make_session(tmp_path)
    first = store.list_items()[0]
    store.add_requirement(first['item_id'], 'Another project requirement', event_id='req-extra')
    second = store.create_item(title='Second project item', scope={'kind': 'project', 'project_id': 'synthetic-project'})
    store.add_requirement(second['item_id'], 'Second item requirement', event_id='req-second')
    snapshot = build_project_work_record_cards(store, 'synthetic-project')
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == 'saved'
    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _: SimpleNamespace(
        id='0x567-flash', vendor='0x567', provider_model='gpt-6-luna', max_input_tokens=1_000_000,
        chat_completions_url=lambda: 'https://example.com/v1/chat/completions')))
    client = LLMClient(paths=session.paths)
    agent = Agent.create(session, llm=client, confirm_fn=lambda *_: 'y', enable_work_record_recall=True)
    agent.work_record_recall_controller = SimpleNamespace(_store=store, _semantic_matcher=SimpleNamespace(_client=client))
    calls = []
    install_client(monkeypatch, [saved.card_id], calls)
    record = ledger.get_card(saved.card_id)
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    for text in ('Synthetic task', 'Synthetic requirement', 'Another project requirement',
                 'Second project item', 'Second item requirement', 'latest stored summary',
                 'run-current', 'run-old-2', 'not_verified', '不属于当前运行', '省略 1 条历史结果'):
        assert text in result.assistant_text
    assert record['request_id'] not in result.assistant_text
    assert record['snapshot_sha256'] not in result.assistant_text
    assert json.dumps(record, ensure_ascii=False, sort_keys=True) not in result.assistant_text
    assert 'latest stored summary' not in calls[0][3]
    assert ledger.get_card(saved.card_id)['viewed_at'] is None


def test_many_ambiguous_candidates_are_bounded_and_never_include_snapshots(tmp_path, monkeypatch):
    import work_card_recall
    agent, _, _, ledger, card_id = setup_case(tmp_path, monkeypatch)
    original = ledger.list_cards()[0]
    records = []
    for index in range(999):
        record = json.loads(json.dumps(original))
        record['card_id'] = 'wc-' + format(index, '064x')
        record['snapshot']['card']['requirements'][0]['text'] = 'PRIVATE_SNAPSHOT_REQUIREMENT'
        records.append(record)
    monkeypatch.setattr(work_card_recall, 'execute_recall', lambda *a, **kw:
                        work_card_recall.RecallResolution('ambiguous', tuple(records)))
    before = ledger.list_cards()
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '999' in result.assistant_text and '仅展示 5 张' in result.assistant_text
    assert '未展示 994 张' in result.assistant_text
    assert sum(record['card_id'] in result.assistant_text for record in records) == 5
    assert '不能确定唯一' in result.assistant_text and '重新发起' in result.assistant_text
    assert 'PRIVATE_SNAPSHOT_REQUIREMENT' not in result.assistant_text
    assert '"snapshot"' not in result.assistant_text and len(result.assistant_text) < 2500
    assert records[0]['card_id'] in result.assistant_text
    assert records[-1]['card_id'] not in result.assistant_text
    assert ledger.list_cards() == before and ledger.get_card(card_id)['viewed_at'] is None


@pytest.mark.parametrize('mode', ['exception', 'empty', 'invalid_type'])
def test_renderer_failure_does_not_fallback_or_ack(tmp_path, monkeypatch, mode):
    import work_card_text
    agent, _, _, ledger, card_id = setup_case(tmp_path, monkeypatch)
    record = ledger.get_card(card_id)
    calls = []
    install_client(monkeypatch, [card_id], calls)
    def fail(_record):
        if mode == 'exception':
            raise work_card_text.WorkCardTextError('PRIVATE DIAGNOSTIC')
        return '' if mode == 'empty' else None
    monkeypatch.setattr(work_card_text, 'render_work_card_text', fail)
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '无法安全呈现' in result.assistant_text
    assert 'PRIVATE DIAGNOSTIC' not in result.assistant_text
    assert card_id not in result.assistant_text and record['request_id'] not in result.assistant_text
    assert record['snapshot_sha256'] not in result.assistant_text
    assert '未发送' not in result.assistant_text and ledger.get_card(card_id) == record
    assert record['viewed_at'] is None and len(calls) == 1


def test_ambiguous_branch_does_not_call_single_card_renderer(tmp_path, monkeypatch):
    import work_card_text
    agent, _, _, ledger, _ = setup_case(tmp_path, monkeypatch, count=2)
    records = ledger.list_cards()
    calls = []
    install_client(monkeypatch, [record['card_id'] for record in records], calls)
    monkeypatch.setattr(work_card_text, 'render_work_card_text',
                        lambda *_: pytest.fail('ambiguous branch must not render snapshots'))
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '不能确定唯一' in result.assistant_text and '仅展示 2 张' in result.assistant_text
    assert all(record['card_id'] in result.assistant_text for record in records)
    assert ledger.list_cards() == records


def large_real_ledger_agent(tmp_path, monkeypatch):
    import uuid
    from work_card_ledger import _stable_card_id
    from llm_models import ModelEntry
    from test_confirmed_llm_request import make_registry
    agent, session, store, ledger, _ = setup_case(tmp_path, monkeypatch)
    original = ledger.list_cards()[0]
    original_card_id = original['card_id']
    def bulk(data):
        assert len(data['items']) == 1
        for index in range(998):
            record = json.loads(json.dumps(original, ensure_ascii=False))
            request = str(uuid.UUID(int=index + 1))
            record['request_id'] = request
            record['card_id'] = _stable_card_id(session.conversation_id, request)
            data['items'].append(record)
        return None, True
    ledger._ledger_store._mutate(bulk)
    records = ledger.list_cards()
    assert len(records) == 999
    assert any(record['card_id'] == original_card_id for record in records)
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                       base_url='https://example.com', provider_model='gpt-6-luna',
                       max_input_tokens=1_000_000, api_key='synthetic-key')
    registry = make_registry(entry)
    monkeypatch.setattr(luna, 'get_registry', lambda *_: registry)
    return agent, ledger, records, registry


@pytest.mark.parametrize('switch_after_first', [False, True])
def test_real_999_ledger_agent_batch_path_with_mock_transport(tmp_path, monkeypatch, switch_after_first):
    import httpx
    from unittest.mock import patch
    from llm_models import ModelRegistry
    from test_confirmed_llm_request import install_transport
    agent, ledger, records, registry = large_real_ledger_agent(tmp_path, monkeypatch)
    original_bytes = ledger._ledger_store._path.read_bytes()
    target = records[-1]['card_id']
    confirms, notices, sent_ids = [], [], []
    agent.executor.confirm_fn = lambda preview, allow: confirms.append((preview, allow)) or 'y'
    agent.on_turn_event = notices.append
    def handler(request):
        payload = json.loads(request.content)
        ids = [row['id'] for row in json.loads(payload['messages'][1]['content'])['candidates']]
        sent_ids.extend(ids)
        if switch_after_first and len(sent_ids) == len(ids):
            agent.session = object()
        decision = {'status':'match', 'candidate_ids':[target]} if target in ids else {'status':'not_found', 'candidate_ids':[]}
        return httpx.Response(200, json={'model':'gpt-6-luna', 'choices':[{'finish_reason':'stop',
                              'message':{'role':'assistant', 'content':json.dumps(decision)}}]})
    requests, _ = install_transport(monkeypatch, handler)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert result.tool_rounds == 0 and result.finish_reason == 'work_card_recall'
    assert len(confirms) == 1 and confirms[0][1] is False
    preview = json.loads(confirms[0][0])
    payloads = preview['provider_payloads']
    assert len(payloads) > 1
    assert any('批' in event.get('text', '') and '费用' in event.get('text', '') for event in notices)
    if switch_after_first:
        assert len(requests) == 1 and '未完成' in result.assistant_text and target not in result.assistant_text
        assert '未发送' not in result.assistant_text
        assert '已尝试 1 批' in result.assistant_text and f'未尝试 {len(payloads) - 1} 批' in result.assistant_text
        assert '可能已发送' in result.assistant_text
    else:
        assert len(requests) == len(payloads)
        assert sent_ids == [r['card_id'] for r in records]
        assert [r.content for r in requests] == [json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8') for p in payloads]
        assert target in result.assistant_text and '可能相关' in result.assistant_text
        assert records[-1]['request_id'] not in result.assistant_text
        assert records[-1]['snapshot_sha256'] not in result.assistant_text
    assert ledger._ledger_store._path.read_bytes() == original_bytes


@pytest.mark.parametrize('reason', ['model_profile_invalid', 'candidate_unrepresentable', 'ledger_or_request_invalid'])
def test_single_non_budget_errors_never_trigger_batch(tmp_path, monkeypatch, reason):
    import work_card_recall as recall
    agent, _, _, _, _ = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr(recall, 'prepare_recall', lambda *_: recall.RecallPreparation('incomplete', reason=reason))
    called = []
    monkeypatch.setattr(recall, 'prepare_batch_recall', lambda *_: called.append(True))
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and not called


@pytest.mark.parametrize('mode', ['multiple', 'invalid_first', 'budget'])
def test_batch_small_real_transport_output_and_failure(tmp_path, monkeypatch, mode):
    import httpx
    from unittest.mock import patch
    import work_card_recall as recall
    from llm_models import ModelEntry, ModelRegistry
    from test_confirmed_llm_request import make_registry, install_transport
    agent, _, _, ledger, _ = setup_case(tmp_path, monkeypatch, count=2)
    records = ledger.list_cards()
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                       base_url='https://example.com', provider_model='gpt-6-luna',
                       max_input_tokens=1_000_000, api_key='synthetic-key')
    registry = make_registry(entry)
    monkeypatch.setattr(luna, 'get_registry', lambda *_: registry)
    if mode == 'budget':
        monkeypatch.setattr(recall, 'prepare_recall', lambda *_: recall.RecallPreparation('incomplete', reason='budget_exceeded'))
    else:
        monkeypatch.setattr(recall, 'MAX_CONFIRM_EVENT_BYTES', 1)
    confirms, notices = [], []
    agent.executor.confirm_fn = lambda preview, allow: confirms.append((preview, allow)) or 'y'
    agent.on_turn_event = notices.append
    def handler(request):
        ids = [r['id'] for r in json.loads(json.loads(request.content)['messages'][1]['content'])['candidates']]
        decision = ({'status':'match', 'candidate_ids':['wc-' + 'f' * 64]} if mode == 'invalid_first' else
                    {'status':'ambiguous', 'candidate_ids':ids} if len(ids) > 1 else
                    {'status':'match', 'candidate_ids':ids})
        return httpx.Response(200, json={'model':'gpt-6-luna', 'choices':[{'finish_reason':'stop',
                              'message':{'role':'assistant','content':json.dumps(decision)}}]})
    requests, _ = install_transport(monkeypatch, handler)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert len(confirms) == 1 and len(requests) == 1 and len(notices) == 1
    assert '费用' in notices[0]['text'] and '最多 1 次' in notices[0]['text']
    if mode == 'invalid_first':
        assert '未完成' in result.assistant_text and '未发送' not in result.assistant_text
        assert '已尝试 1 批' in result.assistant_text and '未尝试 0 批' in result.assistant_text
    else:
        assert '不能确定唯一' in result.assistant_text and '语义查全' in result.assistant_text
        assert all(record['card_id'] in result.assistant_text for record in records)
        assert 'Synthetic résumé requirement' not in result.assistant_text
    assert ledger.list_cards() == records


def test_single_confirmation_sizing_error_does_not_fallback_to_batch(tmp_path, monkeypatch):
    import work_card_recall as recall
    agent, _, _, _, _ = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr(recall, 'single_confirmation_event_bytes', lambda _: None)
    called = []
    monkeypatch.setattr(recall, 'prepare_batch_recall', lambda *_: called.append(True))
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and not called


@pytest.mark.parametrize('mode', ['denied', 'configuration', 'partial', 'unknown'])
def test_batch_failure_reports_send_attempts_without_secrets(tmp_path, monkeypatch, mode):
    import work_card_recall as recall
    from llm_client import LLMConfirmedBatchError
    agent, _, _, ledger, _ = setup_case(tmp_path, monkeypatch, count=2)
    monkeypatch.setattr(recall, 'MAX_CONFIRM_EVENT_BYTES', 1)
    def sent_state(self, *args, **kwargs):
        if mode == 'unknown':
            raise RuntimeError('SECRET_DIAGNOSTIC')
        raise LLMConfirmedBatchError(attempted=1 if mode == 'partial' else 0,
                                     remaining=0 if mode == 'partial' else 1)
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json_batch', sent_state)
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert '未完成' in result.assistant_text and '不会自动重试' in result.assistant_text
    assert 'SECRET_DIAGNOSTIC' not in result.assistant_text
    if mode == 'partial':
        assert '已尝试 1 批' in result.assistant_text and '可能已发送' in result.assistant_text
        assert '未尝试 0 批' in result.assistant_text
    elif mode == 'unknown':
        assert '发送情况无法确认' in result.assistant_text
    else:
        assert '已尝试 0 批' in result.assistant_text and '未尝试 1 批' in result.assistant_text
        assert '可能已发送' not in result.assistant_text
    assert all(row['viewed_at'] is None for row in ledger.list_cards())


@pytest.mark.parametrize('mode', ['reject', 'config_mismatch'])
def test_real_batch_client_zero_attempt_failure_never_claims_sent(tmp_path, monkeypatch, mode):
    from unittest.mock import patch
    import work_card_recall as recall
    from llm_models import ModelEntry, ModelRegistry
    from test_confirmed_llm_request import make_registry, install_transport
    agent, _, _, ledger, _ = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr(recall, 'MAX_CONFIRM_EVENT_BYTES', 1)
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                       base_url='https://example.com', provider_model='gpt-6-luna',
                       max_input_tokens=1_000_000, api_key='synthetic-key')
    registry = make_registry(entry)
    monkeypatch.setattr(luna, 'get_registry', lambda *_: registry)
    calls = []
    agent.executor.confirm_fn = lambda *args: calls.append(args) or 'n'
    if mode == 'config_mismatch':
        different = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                               base_url='https://other.example', provider_model='gpt-6-luna',
                               max_input_tokens=1_000_000, api_key='synthetic-key')
        runtime_registry = make_registry(different)
    else:
        runtime_registry = registry
    requests, _ = install_transport(monkeypatch, lambda _: pytest.fail('no request should be sent'))
    with patch.object(ModelRegistry, 'load', return_value=runtime_registry):
        result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert not requests and len(calls) == (1 if mode == 'reject' else 0)
    assert '已尝试 0 批' in result.assistant_text and '未尝试 1 批' in result.assistant_text
    assert '可能已发送' not in result.assistant_text
    assert all(r['viewed_at'] is None for r in ledger.list_cards())


@pytest.mark.parametrize('mode', ['special', 'reject', 'session_change', 'callback_change', 'fallback_changed'])
def test_batch_confirmation_callback_binding(tmp_path, monkeypatch, mode):
    import httpx
    from unittest.mock import patch
    import work_card_recall as recall
    from llm_models import ModelEntry, ModelRegistry
    from test_confirmed_llm_request import make_registry, install_transport
    agent, session, _, ledger, _ = setup_case(tmp_path, monkeypatch)
    monkeypatch.setattr(recall, 'MAX_CONFIRM_EVENT_BYTES', 1)
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                       base_url='https://example.com', provider_model='gpt-6-luna',
                       max_input_tokens=1_000_000, api_key='synthetic-key')
    registry = make_registry(entry)
    monkeypatch.setattr(luna, 'get_registry', lambda *_: registry)
    calls = []
    def special(preview, allow, *, session):
        calls.append(('special', preview, allow, session))
        if mode == 'session_change': agent.session = object()
        if mode == 'callback_change': agent.work_card_batch_confirm_fn = lambda *a, **k: 'y'
        return 'n' if mode == 'reject' else 'y'
    def ordinary(preview, allow):
        calls.append(('ordinary', preview, allow))
        if mode == 'fallback_changed': agent.work_card_batch_confirm_fn = special
        return 'y'
    agent.executor.confirm_fn = ordinary
    if mode != 'fallback_changed': agent.work_card_batch_confirm_fn = special
    def handler(request):
        ids = [row['id'] for row in json.loads(json.loads(request.content)['messages'][1]['content'])['candidates']]
        return httpx.Response(200, json={'model': 'gpt-6-luna', 'choices': [{'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': json.dumps({'status': 'match', 'candidate_ids': [ids[0]]})}}]})
    requests, _ = install_transport(monkeypatch, handler)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert len(calls) == 1
    assert calls[0][0] == ('ordinary' if mode == 'fallback_changed' else 'special')
    assert calls[0][2] is False
    if mode != 'fallback_changed': assert calls[0][3] is session
    if mode == 'special':
        assert len(requests) == 1 and '可能相关' in result.assistant_text
        payload = json.loads(calls[0][1])['provider_payloads'][0]
        assert json.loads(requests[0].content) == payload
    else:
        assert not requests and '未完成' in result.assistant_text
        assert '已尝试 0 批' in result.assistant_text and '可能已发送' not in result.assistant_text
    assert all(row['viewed_at'] is None for row in ledger.list_cards())


def test_single_confirmation_never_uses_batch_callback(tmp_path, monkeypatch):
    agent, _, _, _, card_id = setup_case(tmp_path, monkeypatch)
    calls = []
    agent.work_card_batch_confirm_fn = lambda *a, **k: pytest.fail('single must not use batch callback')
    agent.executor.confirm_fn = lambda *a: calls.append(a) or 'n'
    install_client(monkeypatch, [card_id], [])
    result = agent.run_turn('找回之前的工作卡', spawn_explore=False)
    assert len(calls) == 1 and '未完成' in result.assistant_text



def _normal_route_once(agent, seen):
    from agent import TurnResult
    def normal(user_text, *, spawn_explore, force_skip_plan_spawn,
               on_external_user_message_persisted=None):
        seen.append(user_text)
        agent._append_user_message_with_result_reply(agent.session, user_text)
        if callable(on_external_user_message_persisted):
            on_external_user_message_persisted(agent.session)
        return TurnResult(assistant_text='normal path', tool_rounds=0,
                          finish_reason='normal_path', total_tool_rounds=0)
    agent._run_turn_without_work_record_recording = normal


def _intent_response(intent):
    from llm_client import LLMResponse
    content = json.dumps({'intent': intent})
    return LLMResponse(model='gpt-6-luna', content=content, tool_calls=[],
                       finish_reason='stop', usage=None,
                       raw={'model':'gpt-6-luna','choices':[{'finish_reason':'stop',
                           'message':{'role':'assistant','content':content}}]})


def test_semantic_pure_recall_requires_separate_intent_and_candidate_approvals(tmp_path, monkeypatch):
    agent, session, _, ledger, card_id = setup_case(tmp_path, monkeypatch)
    approvals, requests = [], []
    agent.executor.confirm_fn = lambda preview, allow: approvals.append((preview, allow)) or 'y'
    def confirmed(self, messages, *, model_id, session_id, expected_preview_json, confirm_fn):
        assert session_id == session.conversation_id
        assert messages == json.loads(expected_preview_json)['provider_payload']['messages']
        requests.append((messages, expected_preview_json))
        assert confirm_fn(expected_preview_json, False) == 'y'
        if len(requests) == 1:
            assert 'candidates' not in messages[-1]['content']
            return _intent_response('recall_only')
        assert 'candidates' in messages[-1]['content']
        return response([card_id])
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    normal = []
    _normal_route_once(agent, normal)

    result = agent.run_turn('能否帮我翻出之前保存的那张工作卡内容？', spawn_explore=False)

    assert result.finish_reason == 'work_card_recall' and normal == []
    assert len(requests) == len(approvals) == 2
    assert requests[0][0][-1]['content'] == '能否帮我翻出之前保存的那张工作卡内容？'
    assert '能否帮我翻出之前保存的那张工作卡内容？' in requests[1][0][-1]['content']
    assert approvals[0][0] != approvals[1][0] and all(x[1] is False for x in approvals)
    assert len([m for m in session.messages if m['role'] == 'user']) == 1
    assert session.messages[0]['content'] == '能否帮我翻出之前保存的那张工作卡内容？'
    assert ledger.get_card(card_id)['viewed_at'] is None


def test_semantic_compound_action_returns_original_once_to_normal_route(tmp_path, monkeypatch):
    agent, session, _, _, _ = setup_case(tmp_path, monkeypatch)
    query = '麻烦找回上次的工作卡，然后继续执行里面的事项'
    approvals, routed, requests = [], [], []
    agent.executor.confirm_fn = lambda preview, allow: approvals.append((preview, allow)) or 'y'
    def confirmed(self, messages, **kwargs):
        requests.append(messages)
        assert kwargs['confirm_fn'](kwargs['expected_preview_json'], False) == 'y'
        return _intent_response('contains_action')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    _normal_route_once(agent, routed)

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'normal_path'
    assert routed == [query] and len(requests) == len(approvals) == 1
    assert [m['content'] for m in session.messages if m['role'] == 'user'] == [query]



def test_work_card_intent_screen_is_narrow_and_parse_is_fail_closed():
    from work_card_intent import eligible_work_card_intent_question, parse_work_card_intent
    assert eligible_work_card_intent_question('能否翻出之前那张工作卡')
    assert eligible_work_card_intent_question('find my previous work card')
    assert not eligible_work_card_intent_question('你好，帮我看看这个问题')
    assert not eligible_work_card_intent_question('工作卡格式是什么？')
    assert not eligible_work_card_intent_question('“找回之前的工作卡”是什么意思')
    assert parse_work_card_intent('{"intent":"recall_only"}') == 'recall_only'
    assert parse_work_card_intent('{"intent":"recall_only","action":"execute"}') == 'uncertain'
    assert parse_work_card_intent('{"intent":"recall_only","intent":"contains_action"}') == 'uncertain'
    assert parse_work_card_intent('{"intent":"contains_action"}') == 'contains_action'


@pytest.mark.parametrize('mode', ['other', 'unknown', 'declined', 'cancelled'])
def test_semantic_intent_decline_and_unknown_return_original_to_normal_route(tmp_path, monkeypatch, mode):
    from llm_client import LLMCancelledError, LLMOutboundConfirmationError
    agent, session, _, _, _ = setup_case(tmp_path, monkeypatch)
    query = '能不能翻出之前的工作卡'
    approvals, routed, prompts, sent, hook_calls = [], [], [], [], []
    agent.work_card_result_reply_turn = SimpleNamespace(
        before_user_append=lambda *a: hook_calls.append('before') or None,
        after_user_append=lambda *a: hook_calls.append('after'))
    def confirm(preview, allow):
        approvals.append((preview, allow))
        return 'n' if mode == 'declined' else 'y'
    agent.executor.confirm_fn = confirm
    def confirmed(self, messages, **kwargs):
        prompts.append(messages)
        decision = kwargs['confirm_fn'](kwargs['expected_preview_json'], False)
        if decision != 'y':
            raise LLMOutboundConfirmationError('declined')
        if mode == 'cancelled':
            raise LLMCancelledError('cancelled')
        sent.append(True)
        return _intent_response('other' if mode == 'other' else 'not-a-valid-intent')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    _normal_route_once(agent, routed)

    result = agent.run_turn(query, spawn_explore=False)

    if mode in {'declined', 'cancelled'}:
        assert result.finish_reason in {'work_card_intent_declined', 'work_card_intent_cancelled'}
        assert not routed and result.assistant_text.strip() and not hook_calls
    else:
        assert result.finish_reason == 'normal_path' and routed == [query]
    assert [m['content'] for m in session.messages if m['role'] == 'user'] == [query]
    assert len(prompts) == 1 and len(approvals) == 1
    assert sent == ([] if mode in {'declined', 'cancelled'} else [True])


def test_intent_confirmation_session_switch_stops_special_and_preserves_original_session(tmp_path, monkeypatch):
    from llm_client import LLMOutboundConfirmationError
    agent, original_session, _, _, _ = setup_case(tmp_path, monkeypatch)
    query = '能否翻出之前的工作卡并继续处理'
    new_session, requests, routed, hook_calls = object(), [], [], []
    original_session_hook = SimpleNamespace(
        before_user_append=lambda *a: hook_calls.append('before') or None,
        after_user_append=lambda *a: hook_calls.append('after'))
    agent.work_card_result_reply_turn = original_session_hook
    def confirm(preview, allow):
        assert allow is False
        agent.session = new_session
        return 'y'
    agent.executor.confirm_fn = confirm
    def confirmed(self, messages, **kwargs):
        requests.append(messages)
        choice = kwargs['confirm_fn'](kwargs['expected_preview_json'], False)
        assert choice == 'n'
        raise LLMOutboundConfirmationError('binding changed; not sent')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    _normal_route_once(agent, routed)
    agent._run_work_card_recall = lambda *_: pytest.fail('stale intent entered recall')

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'work_card_intent_context_switched'
    assert not routed and len(requests) == 1 and not hook_calls
    assert [m['content'] for m in original_session.messages if m['role'] == 'user'] == [query]
    assert result.assistant_text and not hasattr(new_session, 'messages')


def test_candidate_confirmation_rejection_is_not_reused_as_intent_confirmation(tmp_path, monkeypatch):
    from llm_client import LLMOutboundConfirmationError
    agent, session, _, ledger, _ = setup_case(tmp_path, monkeypatch)
    approvals, calls = [], []
    def confirm(preview, allow):
        approvals.append((preview, allow))
        return 'y' if len(approvals) == 1 else 'n'
    agent.executor.confirm_fn = confirm
    def confirmed(self, messages, *, expected_preview_json, confirm_fn, **kwargs):
        calls.append(messages)
        choice = confirm_fn(expected_preview_json, False)
        if choice != 'y':
            raise LLMOutboundConfirmationError('candidate disclosure declined')
        if len(calls) == 1:
            return _intent_response('recall_only')
        pytest.fail('candidate data sent without its separate approval')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)

    result = agent.run_turn('能不能翻出之前的工作卡', spawn_explore=False)

    assert result.finish_reason == 'work_card_recall'
    assert len(approvals) == len(calls) == 2
    assert approvals[0][1] is approvals[1][1] is False and approvals[0][0] != approvals[1][0]
    assert len([m for m in session.messages if m['role'] == 'user']) == 1
    assert all(row['viewed_at'] is None for row in ledger.list_cards())



def test_ordinary_chat_never_enters_work_card_intent_classifier(tmp_path, monkeypatch):
    agent, session, _, _, _ = setup_case(tmp_path, monkeypatch)
    routed, approvals = [], []
    agent.executor.confirm_fn = lambda *args: approvals.append(args) or 'y'
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', lambda *a, **k: pytest.fail('ordinary chat sent to classifier'))
    _normal_route_once(agent, routed)
    query = '你好，帮我看看这个功能的设计'

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'normal_path' and routed == [query]
    assert not approvals
    assert [m['content'] for m in session.messages if m['role'] == 'user'] == [query]


def test_semantic_recall_only_cannot_override_local_execute_intent(tmp_path, monkeypatch):
    agent, session, _, _, _ = setup_case(tmp_path, monkeypatch)
    query = '找回上次的工作卡，然后继续执行里面的事项'
    routed, requests = [], []
    agent.executor.confirm_fn = lambda *_: 'y'
    def confirmed(self, messages, **kwargs):
        requests.append(messages)
        assert kwargs['confirm_fn'](kwargs['expected_preview_json'], False) == 'y'
        return _intent_response('recall_only')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    _normal_route_once(agent, routed)

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'normal_path' and routed == [query]
    assert len(requests) == 1
    assert [m['content'] for m in session.messages if m['role'] == 'user'] == [query]


@pytest.mark.parametrize('cancel_at', ['before_classifier', 'during_confirmation', 'after_response'])
def test_cancel_event_never_falls_through_or_sends_card_data(tmp_path, monkeypatch, cancel_at):
    from llm_client import LLMCancelledError
    agent, session, _, ledger, _ = setup_case(tmp_path, monkeypatch)
    query = '能否翻出之前的工作卡'
    approvals, requests, routed, hook_calls = [], [], [], []
    agent.work_card_result_reply_turn = SimpleNamespace(
        before_user_append=lambda *a: hook_calls.append('before') or None,
        after_user_append=lambda *a: hook_calls.append('after'))
    if cancel_at == 'before_classifier':
        agent.cancel_event.set()
    def confirm(preview, allow):
        approvals.append((preview, allow))
        if cancel_at == 'during_confirmation':
            agent.cancel_event.set()
        return 'y'
    agent.executor.confirm_fn = confirm
    def confirmed(self, messages, **kwargs):
        requests.append(messages)
        choice = kwargs['confirm_fn'](kwargs['expected_preview_json'], False)
        if agent.cancel_event.is_set() or choice != 'y':
            raise LLMCancelledError('cancelled before send')
        if cancel_at == 'after_response':
            agent.cancel_event.set()
        return _intent_response('recall_only')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    _normal_route_once(agent, routed)
    agent._run_work_card_recall = lambda *_: pytest.fail('cancelled classification entered card recall')

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'work_card_intent_cancelled'
    assert not routed and not hook_calls
    assert len([m for m in session.messages if m['role'] == 'user']) == 1
    assert session.messages[-2]['content'] == query and session.messages[-1]['content'].strip()
    assert ledger.get_card(next(iter(ledger.list_cards()))['card_id'])['viewed_at'] is None
    assert len(requests) == (0 if cancel_at == 'before_classifier' else 1)
    if cancel_at == 'before_classifier':
        assert not approvals



def test_recall_precedence_does_not_swallow_continue_modification(tmp_path, monkeypatch):
    from turn_intent import classify_turn
    agent, session, _, _, _ = setup_case(tmp_path, monkeypatch)
    query = '找回刚才推荐的工作卡，并继续修改它'
    assert classify_turn(query) == 'recall'
    routed = []
    agent.executor.confirm_fn = lambda *_: 'y'
    def confirmed(self, messages, **kwargs):
        assert kwargs['confirm_fn'](kwargs['expected_preview_json'], False) == 'y'
        return _intent_response('recall_only')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    _normal_route_once(agent, routed)

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'normal_path' and routed == [query]
    assert [m['content'] for m in session.messages if m['role'] == 'user'] == [query]


def test_session_switch_between_intent_gate_and_recall_keeps_original_binding(tmp_path, monkeypatch):
    agent, original_session, _, _, _ = setup_case(tmp_path, monkeypatch)
    query = '能不能翻出之前保存的工作卡内容'
    new_session, requests = object(), []
    agent.executor.confirm_fn = lambda *_: 'y'
    def confirmed(self, messages, **kwargs):
        requests.append(messages)
        assert kwargs['confirm_fn'](kwargs['expected_preview_json'], False) == 'y'
        return _intent_response('recall_only')
    monkeypatch.setattr(LLMClient, 'chat_confirmed_json', confirmed)
    original_recall = Agent._run_work_card_recall
    def switch_before_recall(self, text, *, expected_session=None):
        self.session = new_session
        return original_recall(self, text, expected_session=expected_session)
    monkeypatch.setattr(Agent, '_run_work_card_recall', switch_before_recall)

    result = agent.run_turn(query, spawn_explore=False)

    assert result.finish_reason == 'work_card_intent_context_switched'
    assert len(requests) == 1
    assert [m['content'] for m in original_session.messages if m['role'] == 'user'] == [query]
    assert not hasattr(new_session, 'messages')


def test_request_cancel_interrupts_independent_work_card_matcher_client(tmp_path, monkeypatch):
    from llm_client import LLMClient
    agent, session, _, _, _ = setup_case(tmp_path, monkeypatch)
    independent = LLMClient(paths=session.paths)
    agent.work_record_recall_controller._semantic_matcher._client = independent
    agent.llm = SimpleNamespace()
    calls = []
    monkeypatch.setattr(independent, 'cancel_current_request', lambda: calls.append('cancel'))

    agent.request_cancel()

    assert agent.cancel_event.is_set()
    assert independent._cancel_event is agent.cancel_event
    assert calls == ['cancel']
