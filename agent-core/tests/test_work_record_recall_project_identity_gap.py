from __future__ import annotations

import json
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_records_confirm_bridge import _make_case
from work_card_ledger import WorkCardLedger


def test_same_title_and_requirement_different_project_refs_are_indistinguishable_to_recall(tmp_path, monkeypatch):
    agent, ordinary, provider, _bridge, _events, _handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    ids = []
    for project in ('project-alpha', 'project-beta'):
        item = store.create_item(title='合成同名事项', scope={'kind': 'independent'})
        req = store.add_requirement(item['item_id'], '合成同名要求', event_id='req-' + project,
                                    source_ref={'kind': 'project', 'id': project})
        store.record_result(item['item_id'], req['requirement_id'], run_id='run-' + project,
                            result_id='result-' + project, status='unknown',
                            summary='合成结果-' + project, verification={'status': 'not_verified'})
        ids.append(item['item_id'])
    before = store.list_items()
    cards_before = WorkCardLedger(agent.session, store).list_cards()
    previews = []
    agent.executor.confirm_fn = lambda preview, allow: previews.append(json.loads(preview)) or 'y'

    def choose_wrong_candidate(messages, **kwargs):
        provider.calls.append((messages, kwargs))
        return SimpleNamespace(content=json.dumps({'status': 'match', 'candidate_ids': [ids[1]]}), tool_calls=[])
    provider.chat = choose_wrong_candidate

    result = agent.run_turn('项目project-alpha之前做了什么？', spawn_explore=False)
    assert result.finish_reason == 'work_record_recall' and not ordinary.calls
    assert len(provider.calls) == len(previews) == 1
    messages = provider.calls[0][0]
    assert messages == previews[0]['exact_provider_messages']
    assert store.list_items() == before and WorkCardLedger(agent.session, store).list_cards() == cards_before
    # Desired safety boundary: a deliberately wrong model ID must not select beta.
    assert '合成结果&#45;project&#45;beta' not in result.assistant_text
    assert '合成结果&#45;project&#45;alpha' not in result.assistant_text
    assert '合成同名要求' not in result.assistant_text
    assert '无法区分' in result.assistant_text and '对象或来源' in result.assistant_text


@pytest.mark.parametrize('add_distinct', [False, True])
def test_unique_or_distinguishable_candidate_still_returns_selected_record(tmp_path, monkeypatch, add_distinct):
    agent, ordinary, provider, _bridge, _events, _handler, _paths = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    if add_distinct:
        item = store.create_item(title='合成另一项目', scope={'kind': 'independent'})
        req = store.add_requirement(item['item_id'], '独特任务要求', event_id='distinct-req')
        expected = '独特任务要求'
        selected = item['item_id']
    else:
        item = store.list_items()[0]
        selected = item['item_id']
        expected = item['requirements'][0]['text']
    previews = []
    agent.executor.confirm_fn = lambda preview, allow: previews.append(json.loads(preview)) or 'y'
    def choose(messages, **kwargs):
        provider.calls.append((messages, kwargs))
        return SimpleNamespace(content=json.dumps({'status': 'match', 'candidate_ids': [selected]}), tool_calls=[])
    provider.chat = choose
    before = store.list_items()
    result = agent.run_turn('项目A之前做了什么？', spawn_explore=False)
    assert result.finish_reason == 'work_record_recall' and expected in result.assistant_text
    assert len(provider.calls) == len(previews) == 1 and not ordinary.calls
    assert provider.calls[0][0] == previews[0]['exact_provider_messages']
    assert store.list_items() == before


def _install_choice(agent, provider, selected):
    previews = []
    agent.executor.confirm_fn = lambda preview, allow: previews.append(json.loads(preview)) or 'y'
    def choose(messages, **kwargs):
        provider.calls.append((messages, kwargs))
        return SimpleNamespace(content=json.dumps({'status': 'match', 'candidate_ids': [selected]}), tool_calls=[])
    provider.chat = choose
    return previews


def test_empty_semantic_material_cannot_authorize_result_disclosure(tmp_path, monkeypatch):
    agent, ordinary, provider, *_ = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    item = store.list_items()[0]
    selected = item['item_id']
    store.record_result(selected, item['requirements'][0]['requirement_id'], run_id='blank-run',
                        result_id='blank-result', status='unknown', summary='秘密结果', verification={})
    def blank(data):
        data['items'][0]['title'] = '   '
        return None, True
    store._mutate(blank)
    agent.work_record_recall_controller._semantic_excerpt_policy = None
    _install_choice(agent, provider, selected)
    result = agent.run_turn('项目A之前做了什么？', spawn_explore=False)
    assert '秘密结果' not in result.assistant_text
    assert '无法' in result.assistant_text or '补充' in result.assistant_text
    assert not ordinary.calls


def test_changed_get_item_same_id_is_not_disclosed_even_if_list_restored(tmp_path, monkeypatch):
    agent, ordinary, provider, *_ = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    item = store.list_items()[0]
    selected = item['item_id']
    before = store.list_items()
    real_get = store.get_item
    def substituted(identity):
        original = real_get(identity)
        changed = copy.deepcopy(original)
        changed['requirements'][0]['text'] = '临时替换的保密详情'
        return changed
    monkeypatch.setattr(store, 'get_item', substituted)
    _install_choice(agent, provider, selected)
    result = agent.run_turn('项目A之前做了什么？', spawn_explore=False)
    assert '临时替换的保密详情' not in result.assistant_text
    assert store.list_items() == before and len(provider.calls) == 1 and not ordinary.calls
    assert '无法' in result.assistant_text or '没查清' in result.assistant_text


def test_case_only_distinct_candidates_require_clarification(tmp_path, monkeypatch):
    agent, ordinary, provider, *_ = _make_case(tmp_path, monkeypatch)
    store = agent.work_record_recall_controller._store
    ids = []
    for title, requirement in [('Project ALPHA', 'Same Task'), ('project alpha', 'same task')]:
        item = store.create_item(title=title, scope={'kind':'independent'})
        req = store.add_requirement(item['item_id'], requirement, event_id='case-req-'+str(len(ids)))
        store.record_result(item['item_id'], req['requirement_id'], run_id='case-run-'+str(len(ids)),
                            result_id='case-result-'+str(len(ids)), status='unknown',
                            summary='CASE SECRET '+str(len(ids)), verification={})
        ids.append(item['item_id'])
    _install_choice(agent, provider, ids[1])
    result = agent.run_turn('项目A之前做了什么？', spawn_explore=False)
    assert '无法区分' in result.assistant_text and '对象或来源' in result.assistant_text
    assert 'CASE SECRET' not in result.assistant_text and 'same task' not in result.assistant_text
    assert len(provider.calls) == 1 and not ordinary.calls
