from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_card_text import render_work_card_text, WorkCardTextError
from work_card_ledger import WorkCardLedger
from work_record_cards import build_project_work_record_cards
from test_work_card_ledger import make_independent_session, make_session, REQUEST_ID


def independent_record(tmp_path):
    session, store, snapshot, _, _ = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    assert ledger.save_snapshot(REQUEST_ID, snapshot).status == 'saved'
    return ledger.list_cards()[0]


def test_independent_renders_complete_snapshot_without_internal_metadata(tmp_path):
    record = independent_record(tmp_path)
    text = render_work_card_text(record)
    assert '历史快照' in text and '不代表当前状态' in text and '不代表已读' in text
    assert '独立事项' in text and 'Synthetic résumé' in text
    assert 'Synthetic résumé requirement' in text
    assert record['card_id'] in text
    assert record['request_id'] not in text and record['snapshot_sha256'] not in text
    assert '状态' in text and '验证' in text and '来源' in text


def test_project_preserves_all_items_requirements_result_and_provenance(tmp_path):
    session, store, _ = make_session(tmp_path)
    first = store.list_items()[0]
    store.add_requirement(first['item_id'], 'Requirement second', event_id='another-req')
    second = store.create_item(title='Other item', scope={'kind':'project', 'project_id':'synthetic-project'})
    store.add_requirement(second['item_id'], 'Other requirement', event_id='third-req')
    snapshot = build_project_work_record_cards(store, 'synthetic-project')
    ledger = WorkCardLedger(session, store)
    assert ledger.save_snapshot(REQUEST_ID, snapshot).status == 'saved'
    text = render_work_card_text(ledger.list_cards()[0])
    for value in ('Synthetic task', 'Synthetic requirement', 'Requirement second', 'Other item', 'Other requirement',
                  'latest stored summary', 'run-current', 'run-old-2', 'project-source', 'requirement-source',
                  'old-2-source', 'not_verified'):
        assert value in text
    assert '历史结果' in text and '省略' in text and '不属于当前运行' in text
    assert '无已保存结果' in text and '未提供' in text
    assert 'RAW_TOOL_BODY_DO_NOT_COPY' not in text


def test_missing_verification_and_empty_sources_are_explicit(tmp_path):
    record = independent_record(tmp_path)
    req = record['snapshot']['card']['requirements'][0]
    if req['latest_result'] is not None:
        req['latest_result']['verification_status'] = None
        req['latest_result']['sources'] = []
    req['source_ref'] = None
    record['snapshot']['card']['source_refs'] = []
    text = render_work_card_text(record)
    assert '来源：未提供' in text
    if req['latest_result'] is not None:
        assert '验证状态：未提供' in text


@pytest.mark.parametrize('change', ['extra_snapshot', 'extra_card', 'extra_requirement', 'extra_result',
                                   'missing_required', 'incorrect_counts', 'bad_source', 'incomplete'])
def test_unknown_or_inconsistent_fields_never_silently_fabricate(tmp_path, change):
    record = independent_record(tmp_path)
    snap = record['snapshot']
    card = snap['card']
    req = card['requirements'][0]
    if change == 'extra_snapshot': snap['future_field'] = 'unknown'
    elif change == 'extra_card': card['future_field'] = 'unknown'
    elif change == 'extra_requirement': req['future_field'] = 'unknown'
    elif change == 'extra_result':
        if req['latest_result'] is None:
            pytest.skip('fixture has no result')
        req['latest_result']['future_field'] = 'unknown'
    elif change == 'missing_required': del req['status']
    elif change == 'incorrect_counts': snap['requirement_count'] += 1
    elif change == 'bad_source': card['source_refs'] = [{'kind':'result', 'id':'x', 'future':'unknown'}]
    else: snap['completeness'] = 'incomplete'
    with pytest.raises(WorkCardTextError):
        render_work_card_text(record)


def test_unknown_status_is_preserved_not_interpreted_as_success(tmp_path):
    record = independent_record(tmp_path)
    record['snapshot']['card']['status'] = 'future-status'
    text = render_work_card_text(record)
    assert 'future-status' in text and '未知' in text


def test_render_is_read_only_and_does_not_truncate_text(tmp_path):
    record = independent_record(tmp_path)
    req = record['snapshot']['card']['requirements'][0]
    req['text'] = 'BEGIN' + '长' * 4000 + 'END'
    before = copy.deepcopy(record)
    text = render_work_card_text(record)
    assert req['text'] in text and record == before


@pytest.mark.parametrize('change', ['result_count', 'wrong_item', 'wrong_run_relation', 'unknown_source_field',
                                   'unknown_record_field', 'history_inconsistent'])
def test_inconsistent_nested_identity_and_history_fail_closed(tmp_path, change):
    record = independent_record(tmp_path)
    snapshot = record['snapshot']
    req = snapshot['card']['requirements'][0]
    if change == 'result_count':
        req['result_count'] += 1
    elif change == 'wrong_item':
        req['item_id'] = 'different-item'
    elif change == 'wrong_run_relation':
        req['latest_result_is_current'] = not req['latest_result_is_current']
    elif change == 'unknown_source_field':
        req['source_ref'] = {'kind':'result', 'id':'x', 'extra': True}
    elif change == 'unknown_record_field':
        record['future_field'] = True
    else:
        snapshot['history_complete'] = not snapshot['history_complete']
    with pytest.raises(WorkCardTextError):
        render_work_card_text(record)


def test_empty_result_history_does_not_claim_success(tmp_path):
    record = independent_record(tmp_path)
    snap = record['snapshot']
    req = snap['card']['requirements'][0]
    req.update(latest_result=None, result_count=0, omitted_result_count=0,
               current_run_id='run-without-result', latest_result_is_current=False)
    snap.update(result_count=0, omitted_result_count=0, history_complete=True)
    text = render_work_card_text(record)
    assert 'run-without-result' in text and '无已保存结果' in text
    assert '验证状态：未提供' in text and '验证通过' in text
    assert '结果状态：已记录为完成' not in text


def test_real_complete_builder_old_result_is_not_a_current_result(tmp_path):
    session, store, snapshot, _, _ = make_independent_session(tmp_path, False)
    req = snapshot.card.requirements[0]
    assert snapshot.completeness == 'complete' and snapshot.missing_current_result_count == 0
    assert req.current_result_missing is False and req.latest_result_is_current is False
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    text = render_work_card_text(saved.record)
    assert '当前运行的结果未在最新结果中体现' in text
    assert '无法确认历史中是否有当前运行结果' in text
    assert 'synthetic old 1' in text and '不属于当前运行' in text


def test_real_complete_builder_current_run_without_any_result(tmp_path):
    from test_work_record_cards import Fixture
    from work_record_cards import build_independent_work_record_card
    f = Fixture(tmp_path)
    item = f.add_item('No result', scope={'kind': 'independent'})
    req = f.add_requirement(item['item_id'], 'New requirement', event_id='req-new')
    f.store.begin_requirement_run(item['item_id'], req['requirement_id'], run_id='new-run', expected_current_run_id=None)
    snapshot = build_independent_work_record_card(f.store, item['item_id'])
    assert snapshot.completeness == 'complete' and snapshot.missing_current_result_count == 0
    assert snapshot.card.requirements[0].current_result_missing is False
    ledger = WorkCardLedger(f.session, f.store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == 'saved'
    text = render_work_card_text(saved.record)
    assert '当前运行尚无已保存结果' in text


def test_real_builder_incomplete_missing_current_result_snapshot(tmp_path):
    import json
    from test_work_record_cards import Fixture
    from work_record_cards import build_project_work_record_cards
    from work_card_text import render_work_card_snapshot_text
    f = Fixture(tmp_path)
    item = f.add_item()
    req = f.add_requirement(item['item_id'], 'Terminal requirement', event_id='req-missing')
    f.store.begin_requirement_run(item['item_id'], req['requirement_id'], run_id='old-run', expected_current_run_id=None)
    f.store.record_result_if_current_run(item['item_id'], req['requirement_id'], run_id='old-run', result_id='old-result',
                                         status='completed', summary='old result only', verification={'status':'not_verified'})
    f.store.begin_requirement_run(item['item_id'], req['requirement_id'], run_id='new-run', expected_current_run_id='old-run')
    payload = json.loads(f.store._path.read_text(encoding='utf-8'))
    payload['items'][0]['requirements'][0]['status'] = 'completed'
    f.store._path.write_text(json.dumps(payload), encoding='utf-8')
    snapshot = build_project_work_record_cards(f.store, f.project_id)
    assert snapshot.completeness == 'incomplete' and snapshot.missing_current_result_count == 1
    assert snapshot.cards[0].requirements[0].current_result_missing is True
    text = render_work_card_snapshot_text(snapshot)
    assert '未保存' in text and '当前结果缺失' in text and '1' in text
    assert 'old result only' in text and '不属于当前运行' in text
    assert WorkCardLedger(f.session, f.store).save_snapshot(REQUEST_ID, snapshot).status == 'rejected'


def test_missing_count_none_and_inconsistent_count_fail_closed(tmp_path):
    from dataclasses import replace
    from work_card_text import render_work_card_snapshot_text
    _, _, snapshot, _, _ = make_independent_session(tmp_path, False)
    for value in (None, 1):
        with pytest.raises(WorkCardTextError):
            render_work_card_snapshot_text(replace(snapshot, missing_current_result_count=value))


def test_builder_count_aggregates_multiple_missing_and_detects_inconsistent_flags(tmp_path):
    import json
    from dataclasses import replace
    from test_work_record_cards import Fixture
    from work_record_cards import build_project_work_record_cards
    from work_card_text import render_work_card_snapshot_text
    f = Fixture(tmp_path)
    item = f.add_item()
    for n in range(2):
        f.add_requirement(item['item_id'], f'missing-{n}', event_id=f'event-{n}')
    payload = json.loads(f.store._path.read_text(encoding='utf-8'))
    for req in payload['items'][0]['requirements']:
        req['status'] = 'completed'
    f.store._path.write_text(json.dumps(payload), encoding='utf-8')
    snapshot = build_project_work_record_cards(f.store, f.project_id)
    assert snapshot.missing_current_result_count == 2
    text = render_work_card_snapshot_text(snapshot)
    assert '2 项终态要求当前结果缺失' in text
    assert text.count('builder 标记此终态要求缺少当前结果') == 2
    with pytest.raises(WorkCardTextError):
        render_work_card_snapshot_text(replace(snapshot, missing_current_result_count=1))


def test_scope_limited_builder_none_count_is_not_rendered_as_complete(tmp_path, monkeypatch):
    from test_work_record_cards import Fixture
    import work_record_cards as cards
    from work_card_text import render_work_card_snapshot_text
    f = Fixture(tmp_path)
    f.add_item()
    monkeypatch.setattr(cards, '_MAX_PROJECT_ITEMS', 0)
    snapshot = cards.build_project_work_record_cards(f.store, f.project_id)
    assert snapshot.completeness == 'incomplete' and snapshot.missing_current_result_count is None
    with pytest.raises(WorkCardTextError):
        render_work_card_snapshot_text(snapshot)


def test_builder_flag_must_match_requirement_state_even_if_count_matches(tmp_path):
    from dataclasses import replace
    from work_card_text import render_work_card_snapshot_text
    _, _, snapshot, _, _ = make_independent_session(tmp_path, False)
    req = snapshot.card.requirements[0]
    forged_card = replace(snapshot.card, requirements=(replace(req, current_result_missing=True),))
    forged = replace(snapshot, card=forged_card, completeness='incomplete',
                     incomplete_reason='current_result_missing', missing_current_result_count=1)
    with pytest.raises(WorkCardTextError):
        render_work_card_snapshot_text(forged)


@pytest.mark.parametrize('with_focus', [False, True])
def test_project_scoped_saved_card_renders_all_requirements_and_exact_focus(tmp_path, with_focus):
    import uuid
    from test_work_card_project_scope import _add_task, _source_fixture
    from work_card_project_scope import build_project_scoped_work_record_cards

    session, store, item_id, _, _ = _source_fixture(tmp_path)
    _add_task(store, item_id, 'synthetic-alpha', 'Alpha second requirement', 'alpha-second')
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_project_scoped_snapshot(
        str(uuid.uuid4()), build_project_scoped_work_record_cards(store, 'synthetic-alpha'))
    assert saved.status == 'saved'
    record = ledger.get_card(saved.card_id)
    snapshot = record['snapshot']
    requirement = snapshot['cards'][0]['requirements'][0]
    if with_focus:
        snapshot['change_focus'] = {
            'item_id': item_id, 'requirement_id': requirement['requirement_id'],
            'result_id': requirement['latest_result']['result_id'],
        }
    text = render_work_card_text(record)
    assert all(part in text for part in ('synthetic-alpha', 'Alpha requirement',
        'Alpha second requirement', snapshot['captured_at'], record['saved_at'], 'not_verified'))
    assert 'Beta requirement' not in text
    if with_focus:
        assert '\u672c\u6b21\u66f4\u65b0' in text
        assert requirement['requirement_id'] in text and requirement['latest_result']['result_id'] in text
        invalid = copy.deepcopy(record)
        invalid['snapshot']['change_focus']['result_id'] = 'not-the-current-result'
        with pytest.raises(WorkCardTextError):
            render_work_card_text(invalid)
