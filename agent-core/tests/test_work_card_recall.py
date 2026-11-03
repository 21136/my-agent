from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import work_card_recall as recall
import work_records_luna_recall as luna
from work_card_ledger import WorkCardLedger
from test_work_card_ledger import make_independent_session, make_session, REQUEST_ID, OTHER_REQUEST_ID
from work_record_cards import build_independent_work_record_card, build_project_work_record_cards


def make_ledger(tmp_path, monkeypatch):
    session, store, snapshot, _, _ = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == "saved"
    monkeypatch.setattr(luna, "get_registry", lambda *_: SimpleNamespace(get=lambda _id: SimpleNamespace(
        id=luna.MODEL_ID, vendor=luna.EXPECTED_VENDOR,
        provider_model=luna.EXPECTED_PROVIDER_MODEL, max_input_tokens=1_000_000,
        chat_completions_url=lambda: "https://api-cdn.0x567.com/v1/chat/completions",
    )))
    return ledger, saved.card_id, snapshot


def test_preview_contains_only_complete_compact_candidates_and_resolves_from_ledger(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    entries = ledger.list_cards()
    before = (ledger._session_dir / 'work-card-ledger.v1.json').read_bytes()
    prepared = recall.prepare_recall(ledger, entries, 'Find my previous work card')
    assert prepared.status == 'ready'
    preview = json.loads(prepared.preview_json)
    assert preview['model_id'] == '0x567-flash'
    request = preview['provider_payload']
    assert request['model'] == 'gpt-6-luna' and request['stream'] is False
    assert set(request) == {'model', 'messages', 'temperature', 'stream', 'response_format'}
    assert preview['session_id'] == ledger._session.conversation_id
    assert preview['endpoint'] == 'https://api-cdn.0x567.com/v1/chat/completions'
    payload = json.loads(request['messages'][1]['content'])
    assert payload['question'] == 'Find my previous work card'
    candidate = payload['candidates'][0]
    assert candidate['id'] == card_id and candidate['title'] == 'Synthetic résumé'
    assert json.loads(candidate['excerpt']) == {
        'scope': 'independent', 'items': [{'title': 'Synthetic résumé', 'requirements': ['Synthetic résumé requirement']}]
    }
    assert 'Synthetic résumé requirement' in prepared.preview_json
    assert 'synthetic old' not in prepared.preview_json
    result = recall.resolve_recall(ledger, prepared, json.dumps({'status': 'match', 'candidate_ids': [card_id]}), finish_reason='stop')
    assert result.status == 'match' and result.records[0] == ledger.get_card(card_id)
    assert result.records[0]['viewed_at'] is None
    assert (ledger._session_dir / 'work-card-ledger.v1.json').read_bytes() == before


def test_partial_or_forged_entries_and_budget_are_fail_closed(tmp_path, monkeypatch):
    ledger, card_id, snapshot = make_ledger(tmp_path, monkeypatch)
    assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
    entries = ledger.list_cards()
    assert recall.prepare_recall(ledger, entries[:1], 'find card').status == 'incomplete'
    altered = json.loads(json.dumps(entries))
    altered[0]['snapshot']['card']['title'] = 'forged'
    assert recall.prepare_recall(ledger, altered, 'find card').status == 'incomplete'
    assert recall.prepare_recall(ledger, entries, 'find card', max_input_tokens=1_000).status == 'incomplete'


def test_invalid_or_negative_model_response_never_asserts_absence_or_unique_match(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    for response in ('bad json', json.dumps({'status':'match','candidate_ids':['wc-' + 'f' * 64]}),
                     json.dumps({'status':'not_found','candidate_ids':[]})):
        result = recall.resolve_recall(ledger, prepared, response, finish_reason='stop')
        assert result.status in ('uncertain', 'error', 'incomplete')
        assert result.records == ()


def test_ledger_mutation_after_preview_invalidates_even_valid_model_choice(tmp_path, monkeypatch):
    ledger, card_id, snapshot = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
    result = recall.resolve_recall(ledger, prepared, json.dumps({'status':'match','candidate_ids':[card_id]}), finish_reason='stop')
    assert result.status == 'incomplete' and result.records == ()


def test_oversized_titles_are_not_silently_truncated(tmp_path, monkeypatch):
    ledger, _, _ = make_ledger(tmp_path, monkeypatch)
    entries = ledger.list_cards()
    # A small explicit caller cap must not turn the title into a misleading prefix.
    prepared = recall.prepare_recall(ledger, entries, 'find card', max_title_bytes=4)
    assert prepared.status == 'incomplete' and not prepared.preview_json


def test_truncated_or_missing_finish_reason_never_yields_unique_match(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    response = json.dumps({'status': 'match', 'candidate_ids': [card_id]})
    assert recall.resolve_recall(ledger, prepared, response, finish_reason='length').status == 'incomplete'
    assert recall.resolve_recall(ledger, prepared, response).status == 'incomplete'


def test_revalidate_preview_before_send_detects_new_ledger_entries(tmp_path, monkeypatch):
    ledger, _, snapshot = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    assert recall.revalidate_preparation(ledger, prepared) is True
    assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
    assert recall.revalidate_preparation(ledger, prepared) is False


def test_ambiguous_model_result_reloads_all_whitelisted_cards(tmp_path, monkeypatch):
    ledger, first, snapshot = make_ledger(tmp_path, monkeypatch)
    second = ledger.save_snapshot(OTHER_REQUEST_ID, snapshot)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    response = json.dumps({'status': 'ambiguous', 'candidate_ids': [second.card_id, first]})
    result = recall.resolve_recall(ledger, prepared, response, finish_reason='stop')
    assert result.status == 'ambiguous'
    assert [row['card_id'] for row in result.records] == [second.card_id, first]
    assert all(row['viewed_at'] is None for row in result.records)


def test_different_ledger_cannot_resolve_prepared_selection(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path / 'first', monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    other, _, _ = make_ledger(tmp_path / 'other', monkeypatch)
    response = json.dumps({'status': 'match', 'candidate_ids': [card_id]})
    result = recall.resolve_recall(other, prepared, response, finish_reason='stop')
    assert result.status == 'incomplete' and result.records == ()


def test_provider_destination_change_invalidates_existing_preview(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _id: SimpleNamespace(
        id=luna.MODEL_ID, vendor=luna.EXPECTED_VENDOR, provider_model=luna.EXPECTED_PROVIDER_MODEL,
        max_input_tokens=1_000_000, chat_completions_url=lambda: 'https://other.example/v1/chat/completions',
    )))
    assert recall.revalidate_preparation(ledger, prepared) is False
    response = json.dumps({'status': 'match', 'candidate_ids': [card_id]})
    assert recall.resolve_recall(ledger, prepared, response, finish_reason='stop').status == 'incomplete'


def test_invalid_endpoint_with_embedded_credentials_has_no_preview(tmp_path, monkeypatch):
    ledger, _, _ = make_ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _id: SimpleNamespace(
        id=luna.MODEL_ID, vendor=luna.EXPECTED_VENDOR, provider_model=luna.EXPECTED_PROVIDER_MODEL,
        max_input_tokens=1_000_000, chat_completions_url=lambda: 'https://user:password@example.com/v1/chat/completions',
    )))
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    assert prepared.status == 'error' and not prepared.preview_json


def test_project_preview_contains_every_requirement_for_each_item_without_results(tmp_path, monkeypatch):
    session, store, _ = make_session(tmp_path)
    items = store.list_items()
    first = items[0]
    store.add_requirement(first['item_id'], 'Additional synthetic requirement', event_id='extra-req')
    second = store.create_item(title='Second task', scope={'kind': 'project', 'project_id': 'synthetic-project'})
    store.add_requirement(second['item_id'], 'Second task requirement A', event_id='second-req-a')
    store.add_requirement(second['item_id'], 'Second task requirement B', event_id='second-req-b')
    snapshot = build_project_work_record_cards(store, 'synthetic-project')
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == 'saved'
    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _id: SimpleNamespace(
        id=luna.MODEL_ID, vendor=luna.EXPECTED_VENDOR, provider_model=luna.EXPECTED_PROVIDER_MODEL,
        max_input_tokens=1_000_000, chat_completions_url=lambda: 'https://api-cdn.0x567.com/v1/chat/completions',
    )))
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'Find the second task requirement')
    assert prepared.status == 'ready'
    request = json.loads(prepared.preview_json)['provider_payload']
    candidate = json.loads(request['messages'][1]['content'])['candidates'][0]
    excerpt = json.loads(candidate['excerpt'])
    assert excerpt['scope'] == 'project'
    assert {item['title']: item['requirements'] for item in excerpt['items']} == {
        'Synthetic task': ['Synthetic requirement', 'Additional synthetic requirement'],
        'Second task': ['Second task requirement A', 'Second task requirement B'],
    }
    assert 'latest stored summary' not in prepared.preview_json
    assert 'RAW_TOOL_BODY_DO_NOT_COPY' not in prepared.preview_json


@pytest.mark.parametrize('limit,value', [
    ('MAX_REQUIREMENT_BYTES', 4),
    ('MAX_REQUIREMENTS_PER_CARD', 0),
    ('MAX_REQUIREMENT_TOTAL_BYTES', 4),
])
def test_excess_requirements_refuse_whole_preview_without_truncation(tmp_path, monkeypatch, limit, value):
    ledger, _, _ = make_ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(recall, limit, value)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find requirement')
    assert prepared.status == 'incomplete' and not prepared.preview_json


def test_independent_cards_retain_every_requirement_and_all_ledger_candidates(tmp_path, monkeypatch):
    ledger, first_id, _ = make_ledger(tmp_path, monkeypatch)
    item_id = ledger._store.list_items()[0]['item_id']
    extra = 'Second requirement: line one\nline two, "quoted".'
    ledger._store.add_requirement(item_id, extra, event_id='independent-second-requirement')
    snapshot = build_independent_work_record_card(ledger._store, item_id)
    saved = ledger.save_snapshot(OTHER_REQUEST_ID, snapshot)
    assert saved.status == 'saved'
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find the second requirement')
    assert prepared.status == 'ready'
    messages = json.loads(prepared.preview_json)['provider_payload']['messages']
    candidates = json.loads(messages[1]['content'])['candidates']
    assert {candidate['id'] for candidate in candidates} == {first_id, saved.card_id}
    second = next(candidate for candidate in candidates if candidate['id'] == saved.card_id)
    requirements = json.loads(second['excerpt'])['items'][0]['requirements']
    assert len(requirements) == 2 and requirements[-1] == extra


def test_requirement_content_is_counted_in_complete_request_budget(tmp_path, monkeypatch):
    ledger, _, _ = make_ledger(tmp_path, monkeypatch)
    entries = ledger.list_cards()
    prepared = recall.prepare_recall(ledger, entries, 'find requirement')
    messages = json.loads(prepared.preview_json)['provider_payload']['messages']
    payload = json.loads(messages[1]['content'])
    payload['candidates'][0]['excerpt'] = 'independent'
    messages[1]['content'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    title_only_bytes = len(json.dumps(messages, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    budget = title_only_bytes + luna.DEFAULT_OUTPUT_RESERVE + 512
    refused = recall.prepare_recall(ledger, entries, 'find requirement', max_input_tokens=budget)
    assert refused.status == 'incomplete' and refused.reason == 'budget_exceeded'
    assert refused.preview_json == ''

class ConfirmedClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.sent = 0

    def chat(self, *args, **kwargs):
        raise AssertionError('ordinary chat must not be used')

    def chat_confirmed_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if kwargs['confirm_fn'](kwargs['expected_preview_json'], False) != 'y':
            raise RuntimeError('not approved')
        self.sent += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def completion(card_id):
    content = json.dumps({'status': 'match', 'candidate_ids': [card_id]})
    raw = {'model': 'gpt-6-luna', 'choices': [{'finish_reason': 'stop',
           'message': {'role': 'assistant', 'content': content}}]}
    return SimpleNamespace(model='gpt-6-luna', content=content, tool_calls=[],
                           finish_reason='stop', raw=raw)


def test_execute_exact_preview_approval_and_read_only_resolution(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    before = (ledger._session_dir / 'work-card-ledger.v1.json').read_bytes()
    client = ConfirmedClient(completion(card_id))
    seen = []
    result = recall.execute_recall(ledger, prepared, client, lambda p, a: seen.append((p, a)) or 'y')
    assert result.status == 'match' and result.records[0]['card_id'] == card_id
    assert result.records[0]['viewed_at'] is None
    assert (ledger._session_dir / 'work-card-ledger.v1.json').read_bytes() == before
    messages, args = client.calls[0]
    assert messages == json.loads(prepared.preview_json)['provider_payload']['messages']
    assert args['model_id'] == '0x567-flash'
    assert args['session_id'] == ledger._session.conversation_id
    assert args['expected_preview_json'] == prepared.preview_json
    assert seen == [(prepared.preview_json, False)] and client.sent == 1


@pytest.mark.parametrize('choice', ['n', 'yes', 'a', None, RuntimeError('private')])
def test_execute_rejected_or_exception_never_sends(tmp_path, monkeypatch, choice):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    client = ConfirmedClient(completion(card_id))
    def confirm(*_):
        if isinstance(choice, Exception):
            raise choice
        return choice
    result = recall.execute_recall(ledger, prepared, client, confirm)
    assert result.status == 'incomplete' and result.records == () and client.sent == 0


@pytest.mark.parametrize('change', ['ledger', 'store', 'session', 'endpoint'])
def test_execute_change_during_confirmation_blocks_send(tmp_path, monkeypatch, change):
    ledger, card_id, snapshot = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    client = ConfirmedClient(completion(card_id))
    def confirm(*_):
        if change == 'ledger':
            assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
        elif change == 'store':
            ledger._store._session = object()
        elif change == 'session':
            ledger._session = object()
        else:
            monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _: None))
        return 'y'
    result = recall.execute_recall(ledger, prepared, client, confirm)
    assert result.status == 'incomplete' and result.records == () and client.sent == 0


@pytest.mark.parametrize('invalid', ['model_missing', 'model_wrong', 'finish_missing', 'finish_length',
                                     'tools', 'malformed_tools', 'function_call', 'content_mismatch', 'foreign_id'])
def test_execute_rejects_untrusted_raw_response(tmp_path, monkeypatch, invalid):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    response = completion(card_id)
    raw, choice = response.raw, response.raw['choices'][0]
    if invalid == 'model_missing': del raw['model']
    elif invalid == 'model_wrong': raw['model'] = 'other'
    elif invalid == 'finish_missing': del choice['finish_reason']
    elif invalid == 'finish_length': choice['finish_reason'] = 'length'
    elif invalid == 'tools': choice['message']['tool_calls'] = [{'id': 'x'}]
    elif invalid == 'malformed_tools': choice['message']['tool_calls'] = ['bad']
    elif invalid == 'function_call': choice['message']['function_call'] = {'name': 'x'}
    elif invalid == 'content_mismatch': choice['message']['content'] = 'different'
    else:
        response = completion('wc-' + 'f' * 64)
    client = ConfirmedClient(response)
    result = recall.execute_recall(ledger, prepared, client, lambda *_: 'y')
    assert result.status in ('incomplete', 'uncertain', 'error') and result.records == ()
    assert client.sent == 1


def test_execute_network_failure_is_not_reported_as_unsent(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    client = ConfirmedClient(RuntimeError('private provider body'))
    result = recall.execute_recall(ledger, prepared, client, lambda *_: 'y')
    assert result.status == 'incomplete' and result.reason == 'request_outcome_unknown'
    assert client.sent == 1 and len(client.calls) == 1


def test_execute_refuses_oversized_confirmation_and_stale_preview(tmp_path, monkeypatch):
    ledger, card_id, snapshot = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    client = ConfirmedClient(completion(card_id))
    monkeypatch.setattr(recall, 'MAX_CONFIRM_EVENT_BYTES', 1)
    assert recall.execute_recall(ledger, prepared, client, lambda *_: 'y').status == 'incomplete'
    assert not client.calls
    monkeypatch.setattr(recall, 'MAX_CONFIRM_EVENT_BYTES', 65536)
    assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
    assert recall.execute_recall(ledger, prepared, client, lambda *_: 'y').status == 'incomplete'
    assert not client.calls

@pytest.mark.parametrize('mode', ['wrong_preview', 'allow_all', 'no_callback', 'twice'])
def test_execute_requires_exact_single_confirmation_callback(tmp_path, monkeypatch, mode):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    asked = []
    class BadClient:
        def chat_confirmed_json(self, messages, **kwargs):
            callback = kwargs['confirm_fn']
            preview = kwargs['expected_preview_json']
            if mode == 'wrong_preview':
                assert callback(preview + 'x', False) == 'n'
            elif mode == 'allow_all':
                assert callback(preview, True) == 'n'
            elif mode == 'twice':
                assert callback(preview, False) == 'y'
                assert callback(preview, False) == 'n'
            return completion(card_id)
    result = recall.execute_recall(ledger, prepared, BadClient(), lambda *args: asked.append(args) or 'y')
    assert result.status == 'incomplete' and result.records == ()
    assert len(asked) == (1 if mode == 'twice' else 0)


def test_execute_discards_response_when_ledger_changes_after_send(tmp_path, monkeypatch):
    ledger, card_id, snapshot = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    class ChangedClient(ConfirmedClient):
        def chat_confirmed_json(self, messages, **kwargs):
            result = super().chat_confirmed_json(messages, **kwargs)
            assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
            return result
    client = ChangedClient(completion(card_id))
    result = recall.execute_recall(ledger, prepared, client, lambda *_: 'y')
    assert result.status == 'incomplete' and result.records == () and client.sent == 1


def test_execute_configuration_error_before_confirmation_does_not_ask_or_retry(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    class ConfigChanged:
        def chat_confirmed_json(self, *args, **kwargs):
            raise RuntimeError('configuration changed')
    asked = []
    result = recall.execute_recall(ledger, prepared, ConfigChanged(), lambda *a: asked.append(a) or 'y')
    assert result.status == 'incomplete' and result.records == () and not asked


def test_execute_does_not_accept_another_ledger_or_uncertain_negative(tmp_path, monkeypatch):
    ledger, card_id, _ = make_ledger(tmp_path / 'one', monkeypatch)
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), 'find card')
    other, _, _ = make_ledger(tmp_path / 'two', monkeypatch)
    client = ConfirmedClient(completion(card_id))
    assert recall.execute_recall(other, prepared, client, lambda *_: 'y').status == 'incomplete'
    assert not client.calls
    response = completion(card_id)
    content = json.dumps({'status': 'not_found', 'candidate_ids': []})
    response.content = content
    response.raw['choices'][0]['message']['content'] = content
    result = recall.execute_recall(ledger, prepared, ConfirmedClient(response), lambda *_: 'y')
    assert result.status == 'uncertain' and result.records == ()


def synthetic_batch_ledger(tmp_path, monkeypatch, count=999):
    ledger, _, _ = make_ledger(tmp_path, monkeypatch)
    base = ledger.list_cards()[0]
    rows = []
    for n in range(count):
        row = json.loads(json.dumps(base, ensure_ascii=False))
        row['card_id'] = 'wc-' + format(n, '064x')
        row['snapshot']['card']['title'] = f'Synthetic task {n}'
        row['snapshot']['card']['requirements'][0]['text'] = f'Synthetic requirement {n}'
        rows.append(row)
    monkeypatch.setattr(ledger, 'list_cards', lambda: json.loads(json.dumps(rows, ensure_ascii=False)))
    monkeypatch.setattr(ledger, 'get_card', lambda card_id: next((json.loads(json.dumps(r)) for r in rows if r['card_id'] == card_id), None))
    return ledger, rows


def batch_response(payload, selected=None, *, status=None):
    ids = [row['id'] for row in json.loads(payload['messages'][1]['content'])['candidates']]
    selected = [] if selected is None else selected
    status = status or ('match' if len(selected) == 1 else 'ambiguous' if selected else 'not_found')
    content = json.dumps({'status': status, 'candidate_ids': selected})
    return SimpleNamespace(model='gpt-6-luna', content=content, tool_calls=[], finish_reason='stop',
        raw={'model': 'gpt-6-luna', 'choices': [{'finish_reason':'stop', 'message':{'role':'assistant', 'content':content}}]})


def test_batch_999_preview_complete_and_merge_readonly(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find synthetic work card')
    assert prepared.status == 'ready'
    preview = json.loads(prepared.preview_json)
    assert prepared.preview_json == json.dumps(preview, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert set(preview) == {'model_id', 'vendor', 'endpoint', 'session_id', 'provider_payloads'}
    assert 2 <= len(preview['provider_payloads']) <= 32
    seen = []
    for payload in preview['provider_payloads']:
        assert set(payload) == {'model', 'messages', 'temperature', 'stream', 'response_format'}
        assert payload['model'] == 'gpt-6-luna' and payload['temperature'] == 0 and payload['stream'] is False
        assert len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')) <= 48 * 1024
        seen.extend(json.loads(payload['messages'][1]['content'])['candidates'])
    assert [v['id'] for v in seen] == [v['card_id'] for v in rows]
    assert 'Synthetic requirement 998' in prepared.preview_json
    assert recall.revalidate_batch_preparation(ledger, prepared)
    target = rows[-1]['card_id']
    replies = [batch_response(p, [target] if target in {x['id'] for x in json.loads(p['messages'][1]['content'])['candidates']} else []) for p in preview['provider_payloads']]
    result = recall.resolve_batch_recall(ledger, prepared, replies)
    assert result.status == 'candidates' and [r['card_id'] for r in result.records] == [target]
    assert result.records[0]['viewed_at'] is None


def test_batch_rejects_partial_forged_or_invalid_responses(tmp_path, monkeypatch):
    ledger, _ = synthetic_batch_ledger(tmp_path, monkeypatch, 20)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    assert prepared.status == 'ready'
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    assert len(payloads) > 1
    replies = [batch_response(p) for p in payloads]
    assert recall.resolve_batch_recall(ledger, prepared, replies[:-1]).status == 'incomplete'
    foreign = json.loads(payloads[1]['messages'][1]['content'])['candidates'][0]['id']
    forged = list(replies)
    forged[0] = batch_response(payloads[0], [foreign])
    assert recall.resolve_batch_recall(ledger, prepared, forged).status == 'incomplete'
    bad = list(replies)
    bad[0] = batch_response(payloads[0])
    bad[0].raw['choices'][0]['finish_reason'] = 'length'
    assert recall.resolve_batch_recall(ledger, prepared, bad).status == 'incomplete'
    assert recall.resolve_batch_recall(ledger, prepared, replies).status == 'uncertain'


def test_batch_boundaries_never_return_partial_preview(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 20)
    for kwargs in ({'max_batch_bytes': 1}, {'max_batches': 1, 'max_batch_bytes': 2200},
                   {'max_preview_bytes': 100}, {'max_input_tokens': 1000}, {'max_batches': 33},
                   {'max_preview_bytes': 2 * 1024 * 1024 + 1}, {'max_batch_bytes': 48 * 1024 + 1}):
        prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', **kwargs)
        assert prepared.status == 'incomplete' and not prepared.preview_json
    assert recall.prepare_batch_recall(ledger, ledger.list_cards()[:-1], 'find').status == 'incomplete'


def test_batch_revalidation_refuses_ledger_and_config_changes(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 10)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find')
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    replies = [batch_response(p) for p in payloads]
    rows[0]['snapshot']['card']['title'] = 'Changed'
    assert not recall.revalidate_batch_preparation(ledger, prepared)
    assert recall.resolve_batch_recall(ledger, prepared, replies).status == 'incomplete'
    rows[0]['snapshot']['card']['title'] = 'Synthetic task 0'
    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _: None))
    assert not recall.revalidate_batch_preparation(ledger, prepared)


def test_batch_execute_uses_one_confirmation_and_before_each_guard(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 30)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    assert len(payloads) > 1
    confirms, sends, checks = [], [], []
    class Client:
        def chat_confirmed_json_batch(self, messages_batches, **kwargs):
            assert messages_batches == [p['messages'] for p in payloads]
            assert kwargs['model_id'] == '0x567-flash' and kwargs['session_id'] == ledger._session.conversation_id
            assert kwargs['expected_preview_json'] == prepared.preview_json
            assert kwargs['confirm_fn'](prepared.preview_json, False) == 'y'
            checks.append(kwargs['before_each']())
            responses = []
            for i, payload in enumerate(payloads):
                checks.append(kwargs['before_each']())
                sends.append(i)
                response = batch_response(payload)
                assert kwargs['response_validator'](i, response) is True
                responses.append(response)
            return tuple(responses)
    result = recall.execute_batch_recall(ledger, prepared, Client(),
                                         lambda p, a: confirms.append((p, a)) or 'y')
    assert result.status == 'uncertain' and confirms == [(prepared.preview_json, False)]
    assert len(sends) == len(payloads) and all(checks) and len(checks) == len(payloads) + 1


def test_batch_execute_stops_on_ledger_change_between_sends(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 30)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    sent = []
    class Client:
        def chat_confirmed_json_batch(self, messages_batches, **kwargs):
            assert kwargs['confirm_fn'](prepared.preview_json, False) == 'y'
            assert kwargs['before_each']() is True
            assert kwargs['before_each']() is True
            sent.append(0)
            payload = json.loads(prepared.preview_json)['provider_payloads'][0]
            assert kwargs['response_validator'](0, batch_response(payload)) is True
            rows[0]['snapshot']['card']['title'] = 'changed'
            assert kwargs['before_each']() is False
            raise RuntimeError('partial private response')
    result = recall.execute_batch_recall(ledger, prepared, Client(), lambda *_: 'y')
    assert result.status == 'incomplete' and not result.records and sent == [0]
    assert result.reason == 'batch_outcome_unknown'


@pytest.mark.parametrize('mode', ['reject', 'confirm_raises', 'session_changed'])
def test_batch_execute_denial_or_binding_change_blocks_send(tmp_path, monkeypatch, mode):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 3)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find')
    sends = []
    def confirm(*_):
        if mode == 'confirm_raises':
            raise RuntimeError('private')
        if mode == 'session_changed':
            ledger._store._session = object()
        return 'n' if mode == 'reject' else 'y'
    class Client:
        def chat_confirmed_json_batch(self, messages_batches, **kwargs):
            if kwargs['confirm_fn'](prepared.preview_json, False) != 'y':
                raise RuntimeError('not approved')
            sends.append(True)
    result = recall.execute_batch_recall(ledger, prepared, Client(), confirm)
    assert result.status == 'incomplete' and not result.records and not sends


@pytest.mark.parametrize('bad', ['wrong_batch_id', 'invalid_json', 'model', 'finish', 'tools'])
def test_batch_execute_invalid_response_stops_before_next_send(tmp_path, monkeypatch, bad):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 30)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    assert len(payloads) > 1
    sends, validated = [], []
    class Client:
        def chat_confirmed_json_batch(self, messages_batches, **kwargs):
            assert kwargs['confirm_fn'](prepared.preview_json, False) == 'y'
            assert kwargs['before_each']() is True and kwargs['before_each']() is True
            sends.append(0)
            if bad == 'wrong_batch_id':
                foreign = json.loads(payloads[1]['messages'][1]['content'])['candidates'][0]['id']
                r = batch_response(payloads[0], [foreign])
            else:
                r = batch_response(payloads[0])
                if bad == 'invalid_json':
                    r.content = 'not json'; r.raw['choices'][0]['message']['content'] = r.content
                elif bad == 'model': r.raw.pop('model')
                elif bad == 'finish': r.raw['choices'][0]['finish_reason'] = 'length'
                else: r.raw['choices'][0]['message']['tool_calls'] = ['malformed']
            validated.append(kwargs['response_validator'](0, r))
            raise RuntimeError('provider response invalid')
    result = recall.execute_batch_recall(ledger, prepared, Client(), lambda *_: 'y')
    assert result.status == 'incomplete' and not result.records and sends == [0]
    assert validated == [False]


@pytest.mark.parametrize('invalid_first', [False, True])
def test_batch_with_real_confirmed_client_and_mock_http_transport(tmp_path, monkeypatch, invalid_first):
    import httpx
    from unittest.mock import patch
    from llm_client import LLMClient
    from llm_models import ModelEntry, ModelRegistry
    from test_confirmed_llm_request import make_registry, install_transport
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 20)
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                       base_url='https://example.com', provider_model='gpt-6-luna',
                       max_input_tokens=1_000_000, api_key='synthetic-test-key')
    registry = make_registry(entry)
    monkeypatch.setattr(luna, 'get_registry', lambda *_: registry)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    assert len(payloads) > 1
    confirmations = []
    def handler(request):
        messages = json.loads(request.content)['messages']
        ids = [row['id'] for row in json.loads(messages[1]['content'])['candidates']]
        content = json.dumps({'status':'match', 'candidate_ids':['wc-' + 'f' * 64]}) if invalid_first else json.dumps({'status':'match','candidate_ids':[ids[0]]})
        return httpx.Response(200, json={'model':'gpt-6-luna','choices':[{'finish_reason':'stop',
                                     'message':{'role':'assistant','content':content}}]})
    requests, _ = install_transport(monkeypatch, handler)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        result = recall.execute_batch_recall(ledger, prepared, LLMClient(paths=ledger._session.paths),
                                             lambda p, a: confirmations.append((p, a)) or 'y')
    assert confirmations == [(prepared.preview_json, False)]
    if invalid_first:
        assert len(requests) == 1 and result.status == 'incomplete' and result.records == ()
    else:
        assert len(requests) == len(payloads) and result.status == 'candidates'
        assert [r.content for r in requests] == [json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8') for p in payloads]
        assert len(result.records) == len(payloads)


def test_batch_merge_reads_multiple_selections_from_one_full_snapshot(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 12)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find')
    assert prepared.status == 'ready'
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    assert len(payloads) == 1
    targets = [rows[0]['card_id'], rows[-1]['card_id']]
    list_reads = []
    original_list = ledger.list_cards
    def counted_list():
        list_reads.append(True)
        return original_list()
    monkeypatch.setattr(ledger, 'list_cards', counted_list)
    def forbidden_per_card_read(_card_id):
        raise AssertionError('batch recall must not re-read the complete ledger per selected card')
    monkeypatch.setattr(ledger, 'get_card', forbidden_per_card_read)
    result = recall.resolve_batch_recall(ledger, prepared, [batch_response(payloads[0], targets)])
    assert result.status == 'candidates'
    assert [record['card_id'] for record in result.records] == targets
    assert len(list_reads) == 8


def test_batch_merge_detects_change_between_full_selection_snapshots(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 10)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find')
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    first = json.loads(payloads[0]['messages'][1]['content'])['candidates'][0]['id']
    replies = [batch_response(payloads[0], [first])] + [batch_response(p) for p in payloads[1:]]
    original_list = ledger.list_cards
    reads = []
    def changing_list():
        result = original_list()
        reads.append(True)
        if len(reads) == 5:
            rows[-1]['snapshot']['card']['title'] = 'changed after first selection snapshot'
        return result
    monkeypatch.setattr(ledger, 'list_cards', changing_list)
    result = recall.resolve_batch_recall(ledger, prepared, replies)
    assert result.status == 'incomplete' and result.records == ()
    assert result.reason == 'ledger_changed'
    assert len(reads) == 6


def test_batch_merge_fails_closed_when_a_shard_corrupts_between_snapshots(tmp_path, monkeypatch):
    ledger, _, snapshot = make_ledger(tmp_path, monkeypatch)
    assert ledger.save_snapshot(OTHER_REQUEST_ID, snapshot).status == 'saved'
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find')
    assert prepared.status == 'ready'
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    first = json.loads(payloads[0]['messages'][1]['content'])['candidates'][0]['id']
    replies = [batch_response(payloads[0], [first])] + [batch_response(p) for p in payloads[1:]]
    original_list = ledger.list_cards
    reads = []
    shards = sorted(ledger._session_dir.glob('work-card-ledger.v1.json.entry-*.json'))
    assert shards
    def corrupt_after_first_selection_snapshot():
        reads.append(True)
        result = original_list()
        if len(reads) == 5:
            shards[0].write_text('{ damaged shard', encoding='utf-8')
        return result
    monkeypatch.setattr(ledger, 'list_cards', corrupt_after_first_selection_snapshot)
    result = recall.resolve_batch_recall(ledger, prepared, replies)
    assert result.status == 'incomplete' and result.records == ()
    assert result.reason == 'ledger_or_response_invalid'
    assert len(reads) == 6


def test_batch_callback_rejects_reordered_or_mutated_responses(tmp_path, monkeypatch):
    ledger, _ = synthetic_batch_ledger(tmp_path, monkeypatch, 20)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    validations = []
    class Client:
        def chat_confirmed_json_batch(self, batches, **kwargs):
            assert kwargs['confirm_fn'](prepared.preview_json, False) == 'y'
            assert kwargs['before_each']() is True and kwargs['before_each']() is True
            validations.append(kwargs['response_validator'](1, batch_response(payloads[1])))
            return ()
    result = recall.execute_batch_recall(ledger, prepared, Client(), lambda *_: 'y')
    assert validations == [False] and result.status == 'incomplete' and result.records == ()


def test_batch_external_host_binding_checked_before_each_send(tmp_path, monkeypatch):
    ledger, rows = synthetic_batch_ledger(tmp_path, monkeypatch, 20)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    payloads = json.loads(prepared.preview_json)['provider_payloads']
    alive, sent = [True], []
    class Client:
        def chat_confirmed_json_batch(self, batches, **kwargs):
            assert kwargs['confirm_fn'](prepared.preview_json, False) == 'y'
            assert kwargs['before_each']() is True and kwargs['before_each']() is True
            sent.append(0)
            assert kwargs['response_validator'](0, batch_response(payloads[0])) is True
            alive[0] = False
            assert kwargs['before_each']() is False
            raise RuntimeError('stopped')
    result = recall.execute_batch_recall(ledger, prepared, Client(), lambda *_: 'y',
                                         host_binding_check=lambda: alive[0])
    assert sent == [0] and result.status == 'incomplete' and result.records == ()


@pytest.mark.parametrize('attempted', [0, 1])
def test_batch_client_failure_returns_only_structured_valid_attempt_counts(tmp_path, monkeypatch, attempted):
    from llm_client import LLMConfirmedBatchError
    ledger, _ = synthetic_batch_ledger(tmp_path, monkeypatch, 20)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find', max_batch_bytes=2200)
    count = len(json.loads(prepared.preview_json)['provider_payloads'])
    class Client:
        def chat_confirmed_json_batch(self, *a, **kw):
            raise LLMConfirmedBatchError(attempted=attempted, remaining=count - attempted)
    result = recall.execute_batch_recall(ledger, prepared, Client(), lambda *_: 'y')
    assert result.status == 'incomplete' and result.records == ()
    assert result.attempted_batches == attempted and result.remaining_batches == count - attempted
    assert prepared.preview_json not in repr(result)


@pytest.mark.parametrize('attempted,remaining', [(-1, 1), (True, 1), (100, 0), (0, 0)])
def test_batch_invalid_exception_counts_are_unknown_not_fabricated(tmp_path, monkeypatch, attempted, remaining):
    from llm_client import LLMConfirmedBatchError
    ledger, _ = synthetic_batch_ledger(tmp_path, monkeypatch, 5)
    prepared = recall.prepare_batch_recall(ledger, ledger.list_cards(), 'find')
    class Client:
        def chat_confirmed_json_batch(self, *a, **kw):
            raise LLMConfirmedBatchError(attempted=attempted, remaining=remaining)
    result = recall.execute_batch_recall(ledger, prepared, Client(), lambda *_: 'y')
    assert result.status == 'incomplete'
    assert result.attempted_batches is None and result.remaining_batches is None


def test_project_worker_cards_recall_preview_retains_exact_project_identity(tmp_path, monkeypatch):
    from test_work_card_project_scope import _source_fixture
    from work_card_project_scope import build_project_scoped_work_record_cards
    session, store, *_ = _source_fixture(tmp_path)
    ledger = WorkCardLedger(session, store)
    for request_id, project in ((REQUEST_ID, 'synthetic-alpha'), (OTHER_REQUEST_ID, 'synthetic-beta')):
        saved = ledger.save_project_scoped_snapshot(request_id, build_project_scoped_work_record_cards(store, project))
        assert saved.status == 'saved'
    monkeypatch.setattr(luna, 'get_registry', lambda *_: SimpleNamespace(get=lambda _id: SimpleNamespace(
        id=luna.MODEL_ID, vendor=luna.EXPECTED_VENDOR, provider_model=luna.EXPECTED_PROVIDER_MODEL,
        max_input_tokens=1_000_000, chat_completions_url=lambda: 'https://api-cdn.0x567.com/v1/chat/completions',
    )))
    prepared = recall.prepare_recall(ledger, ledger.list_cards(), '找 synthetic-beta 的工作卡')
    assert prepared.status == 'ready'
    candidates = json.loads(json.loads(prepared.preview_json)['provider_payload']['messages'][1]['content'])['candidates']
    by_id = {json.loads(candidate['excerpt'])['project_id']: json.loads(candidate['excerpt']) for candidate in candidates}
    assert set(by_id) == {'synthetic-alpha', 'synthetic-beta'}
    assert by_id['synthetic-alpha']['items'][0]['requirements'] == ['Alpha requirement']
    assert by_id['synthetic-beta']['items'][0]['requirements'] == ['Beta requirement']
    assert 'docs/alpha.md' not in prepared.preview_json and 'docs/beta.md' not in prepared.preview_json
    assert recall.revalidate_preparation(ledger, prepared)
