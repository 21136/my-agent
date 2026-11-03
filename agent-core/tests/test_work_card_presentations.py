import concurrent.futures
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_card_ledger import WorkCardLedger
from test_work_card_ledger import make_independent_session, REQUEST_ID, OTHER_REQUEST_ID, _reopen
from work_card_presentations import WorkCardPresentations, PRESENTATIONS_FILENAME


def setup_case(tmp_path):
    session, store, snapshot, _, _ = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    first = ledger.save_snapshot(REQUEST_ID, snapshot)
    second = ledger.save_snapshot(OTHER_REQUEST_ID, snapshot)
    assert first.status == second.status == 'saved'
    return session, store, ledger, first.card_id, second.card_id


def test_retries_explicit_ack_and_new_presentation_do_not_inherit(tmp_path):
    session, store, ledger, card, other = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    one, two = str(uuid.uuid4()), str(uuid.uuid4())
    first = p.create(one, card)
    assert first.status == 'created' and first.fact.acknowledged_at is None
    assert p.create(one, card).status == 'already_created'
    assert p.create(one, other).status == 'conflict'
    assert p.acknowledge(one, card, explicit_user_action=False).status == 'rejected'
    assert p.get(one).fact.acknowledged_at is None
    ack = p.acknowledge(one, card, explicit_user_action=True)
    assert ack.status == 'acknowledged' and ack.fact.acknowledged_at
    again = p.acknowledge(one, card, explicit_user_action=True)
    assert again.status == 'already_acknowledged' and again.fact == ack.fact
    assert p.create(two, card).fact.acknowledged_at is None
    assert ledger.get_card(card)['viewed_at'] is None
    reopened, reopened_store = _reopen(session)
    reopened_ledger = WorkCardLedger(reopened, reopened_store)
    pp = WorkCardPresentations(reopened, reopened_store, reopened_ledger)
    assert pp.get(one).fact == ack.fact and pp.get(two).fact.acknowledged_at is None


def test_invalid_unknown_and_session_binding_fail_closed(tmp_path):
    session, store, ledger, card, other = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    assert p.create('bad', card).status == 'rejected'
    assert p.create(pid, 'wc-'+'0'*64).status == 'rejected'
    assert p.acknowledge(pid, card, explicit_user_action=True).status == 'rejected'
    assert p.create(pid, card).status == 'created'
    assert p.acknowledge(pid, other, explicit_user_action=True).status == 'conflict'
    session.meta.project_id = 'changed'
    assert p.acknowledge(pid, card, explicit_user_action=True).status != 'acknowledged'
    assert p.get(pid).status != 'found'


def test_corruption_does_not_overwrite_and_duplicate_keys_rejected(tmp_path):
    session, store, ledger, card, _ = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    assert p.create(pid, card).status == 'created'
    path = session.session_dir / PRESENTATIONS_FILENAME
    for bad in (b'{bad', b'{"schema_version":1,"schema_version":1}'):
        path.write_bytes(bad)
        assert p.get(pid).status == 'corrupt'
        assert p.create(str(uuid.uuid4()), card).status == 'corrupt'
        assert path.read_bytes() == bad


def test_uncertain_publish_no_false_success_retry_same_id(tmp_path, monkeypatch):
    session, store, ledger, card, _ = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    original = p._presentations_store._publish
    def published_then_error(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('private')
    monkeypatch.setattr(p._presentations_store, '_publish', published_then_error)
    assert p.create(pid, card).status == 'uncertain'
    monkeypatch.setattr(p._presentations_store, '_publish', original)
    assert p.create(pid, card).status == 'already_created'
    assert p.get(pid).fact.acknowledged_at is None


def test_concurrent_create_and_ack_same_identity(tmp_path):
    session, store, ledger, card, _ = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: p.create(pid, card), range(6)))
    assert sorted(r.status for r in results) == ['already_created'] * 5 + ['created']
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        acks = list(pool.map(lambda _: p.acknowledge(pid, card, explicit_user_action=True), range(6)))
    assert sorted(r.status for r in acks) == ['acknowledged'] + ['already_acknowledged'] * 5
    assert len({r.fact.acknowledged_at for r in acks}) == 1


def test_cross_session_and_corrupt_ledger_fail_closed(tmp_path):
    session, store, ledger, card, _ = setup_case(tmp_path / 'first')
    other_session, other_store, other_ledger, _, _ = setup_case(tmp_path / 'second')
    with pytest.raises(ValueError):
        WorkCardPresentations(session, store, other_ledger)
    presentation = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    assert presentation.create(pid, card).status == 'created'
    other = WorkCardPresentations(other_session, other_store, other_ledger)
    assert other.get(pid).status == 'not_found'
    assert other.acknowledge(pid, card, explicit_user_action=True).status == 'rejected'
    ledger._ledger_store._path.write_text('{bad', encoding='utf-8')
    assert presentation.get(pid).status == 'corrupt'
    assert presentation.get(str(uuid.uuid4())).status == 'corrupt'
    assert presentation.acknowledge(pid, card, explicit_user_action=True).status == 'corrupt'


def test_cross_process_same_id_create_and_ack(tmp_path):
    import subprocess
    session, store, ledger, card, _ = setup_case(tmp_path)
    pid = str(uuid.uuid4())
    script = r'''
import sys
from pathlib import Path
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
root=Path(sys.argv[1]); sid=sys.argv[2]; pid=sys.argv[3]; card=sys.argv[4]; action=sys.argv[5]
paths=AgentPaths(agent_root=root,workspace=root/'workspace',data=root/'data',evolve=root/'evolve')
s=Session(sid,paths.data/'sessions'/sid,'synthetic',SessionMeta(project_id=''),[],paths)
st=WorkRecordStore(s,lock_timeout=15); p=WorkCardPresentations(s,st,WorkCardLedger(s,st))
r=p.create(pid,card) if action=='create' else p.acknowledge(pid,card,explicit_user_action=True)
print(r.status)
'''
    def run(action):
        procs = [subprocess.Popen([sys.executable, '-c', script, str(session.paths.agent_root),
                  session.conversation_id, pid, card, action], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  text=True, cwd=str(Path(__file__).resolve().parents[1])) for _ in range(4)]
        values = []
        for proc in procs:
            out, err = proc.communicate(timeout=30)
            assert proc.returncode == 0, err
            values.append(out.strip())
        return sorted(values)
    assert run('create') == ['already_created'] * 3 + ['created']
    assert run('ack') == ['acknowledged'] + ['already_acknowledged'] * 3
    p = WorkCardPresentations(session, store, ledger)
    assert p.get(pid).fact.acknowledged_at


def test_ack_uncertain_after_commit_recovers_original_fact_without_rewrite(tmp_path, monkeypatch):
    session, store, ledger, card, _ = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    assert p.create(pid, card).status == 'created'
    original = p._presentations_store._publish
    def after_publish(*a, **kw):
        original(*a, **kw)
        raise RuntimeError('PRIVATE')
    monkeypatch.setattr(p._presentations_store, '_publish', after_publish)
    uncertain = p.acknowledge(pid, card, explicit_user_action=True)
    assert uncertain.status == 'uncertain' and uncertain.fact is None and 'PRIVATE' not in repr(uncertain)
    monkeypatch.setattr(p._presentations_store, '_publish', original)
    fact = p.get(pid).fact
    before = p._presentations_store._path.read_bytes()
    again = p.acknowledge(pid, card, explicit_user_action=True)
    assert again.status == 'already_acknowledged' and again.fact == fact
    assert p._presentations_store._path.read_bytes() == before


def test_foreign_session_presentation_file_rejected(tmp_path):
    session, store, ledger, card, _ = setup_case(tmp_path / 'source')
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    assert p.create(pid, card).status == 'created'
    from test_work_card_ledger import make_independent_session
    other, other_store, snapshot, _, _ = make_independent_session(tmp_path / 'target', False, '20261101-dddddddd')
    other_ledger = WorkCardLedger(other, other_store)
    assert other_ledger.save_snapshot(REQUEST_ID, snapshot).status == 'saved'
    target = other.session_dir / PRESENTATIONS_FILENAME
    target.write_bytes((session.session_dir / PRESENTATIONS_FILENAME).read_bytes())
    foreign = WorkCardPresentations(other, other_store, other_ledger)
    assert foreign.get(pid).status == 'corrupt'
    assert foreign.create(pid, card).status == 'corrupt'


def setup_multi_results(tmp_path):
    from test_work_card_ledger import make_session
    from work_record_cards import build_project_work_record_cards
    session, store, _ = make_session(tmp_path)
    first = store.list_items()[0]
    for suffix in ('two', 'three'):
        requirement = store.add_requirement(first['item_id'], 'Synthetic ' + suffix, event_id='event-' + suffix)
        store.record_result(first['item_id'], requirement['requirement_id'], run_id='run-' + suffix,
                            result_id='result-' + suffix, status='completed', summary='summary ' + suffix, verification={'status': 'not_verified'})
    snapshot = build_project_work_record_cards(store, 'synthetic-project')
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(REQUEST_ID, snapshot)
    assert saved.status == 'saved'
    return session, store, ledger, saved.card_id


def test_result_views_separate_actions_idempotent_and_new_instance(tmp_path):
    session, store, ledger, card = setup_multi_results(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    one, two = str(uuid.uuid4()), str(uuid.uuid4())
    assert p.create(one, card).fact.result_views == ()
    assert p.acknowledge(one, card, explicit_user_action=True).fact.result_views == ()
    assert p.acknowledge_result(one, card, 'result-two', action='result_detail', trusted_action=True).status == 'acknowledged'
    first = p.get(one).fact.result_views
    assert len(first) == 1 and first[0].result_id == 'result-two' and first[0].action == 'result_detail'
    duplicate = p.acknowledge_result(one, card, 'result-two', action='reliable_reply', trusted_action=True)
    assert duplicate.status == 'already_acknowledged' and duplicate.fact.result_views == first
    assert p.acknowledge_result(one, card, 'result-three', action='reliable_reply', trusted_action=True).status == 'acknowledged'
    views = p.get(one).fact.result_views
    assert {v.result_id for v in views} == {'result-two', 'result-three'}
    assert len({v.requirement_id for v in views}) == 2
    assert p.create(two, card).fact.result_views == ()
    assert ledger.get_card(card)['viewed_at'] is None
    reopened, rs = _reopen(session)
    assert WorkCardPresentations(reopened, rs, WorkCardLedger(reopened, rs)).get(one).fact.result_views == views


def test_result_ack_rejects_untrusted_wrong_or_unrepresented_result(tmp_path):
    session, store, ledger, card, other = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4())
    assert p.create(pid, card).status == 'created'
    for result_id, action, trusted in [('old-result-1', 'viewport', True), ('old-result-1', 'result_detail', False),
                                       ('old-result-0', 'result_detail', True), ('forged', 'reliable_reply', True)]:
        assert p.acknowledge_result(pid, card, result_id, action=action, trusted_action=trusted).status == 'rejected'
    assert p.acknowledge_result(pid, other, 'old-result-1', action='result_detail', trusted_action=True).status == 'conflict'
    assert p.acknowledge_result(str(uuid.uuid4()), card, 'old-result-1', action='result_detail', trusted_action=True).status == 'rejected'
    assert p.get(pid).fact.result_views == ()


def test_result_ack_concurrent_and_corrupt_forged_view(tmp_path):
    session, store, ledger, card = setup_multi_results(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4()); assert p.create(pid, card).status == 'created'
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: p.acknowledge_result(pid, card, 'result-two', action='result_detail', trusted_action=True), range(6)))
    assert sorted(r.status for r in rows) == ['acknowledged'] + ['already_acknowledged'] * 5
    assert len({r.fact.result_views[0].viewed_at for r in rows}) == 1
    path = session.session_dir / PRESENTATIONS_FILENAME
    data = json.loads(path.read_text(encoding='utf-8'))
    data['items'][0]['result_views'][0]['result_id'] = 'forged'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert p.get(pid).status == 'corrupt'
    assert p.acknowledge_result(pid, card, 'result-three', action='result_detail', trusted_action=True).status == 'corrupt'


def test_schema_v1_migration_does_not_convert_card_ack_to_result_ack(tmp_path):
    session, store, ledger, card, _ = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4()); assert p.create(pid, card).status == 'created'
    assert p.acknowledge(pid, card, explicit_user_action=True).status == 'acknowledged'
    path = session.session_dir / PRESENTATIONS_FILENAME
    data = json.loads(path.read_text(encoding='utf-8'))
    data['schema_version'] = 1
    for row in data['items']:
        row.pop('result_views')
        for key in ('run_id', 'receipt_state', 'receipt_attempts', 'last_offer_at', 'received_at'):
            row.pop(key, None)
    path.write_text(json.dumps(data), encoding='utf-8')
    before = p.get(pid).fact
    assert before.acknowledged_at and before.result_views == ()
    result = p.acknowledge_result(pid, card, 'old-result-1', action='result_detail', trusted_action=True)
    assert result.status == 'acknowledged' and result.fact.acknowledged_at == before.acknowledged_at
    assert json.loads(path.read_text(encoding='utf-8'))['schema_version'] == 4



def test_result_from_other_card_or_omitted_history_cannot_ack(tmp_path):
    from work_record_cards import build_independent_work_record_card
    session, store, old_snapshot, item, req = make_independent_session(tmp_path, False)
    ledger = WorkCardLedger(session, store)
    old = ledger.save_snapshot(REQUEST_ID, old_snapshot)
    store.record_result_if_current_run(item['item_id'], req['requirement_id'], run_id='current-run', result_id='new-result',
                        status='completed', summary='new synthetic', verification={'status': 'not_verified'})
    new = ledger.save_snapshot(OTHER_REQUEST_ID, build_independent_work_record_card(store, item['item_id']))
    assert old.status == new.status == 'saved'
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4()); assert p.create(pid, old.card_id).status == 'created'
    assert p.acknowledge_result(pid, old.card_id, 'new-result', action='result_detail', trusted_action=True).status == 'rejected'
    assert p.acknowledge_result(pid, old.card_id, 'old-result-0', action='result_detail', trusted_action=True).status == 'rejected'
    assert p.get(pid).fact.result_views == ()


def test_result_ack_uncertain_retry_preserves_first_fact(tmp_path, monkeypatch):
    session, store, ledger, card, _ = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4()); assert p.create(pid, card).status == 'created'
    original = p._presentations_store._publish
    def fail_after(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('PRIVATE')
    monkeypatch.setattr(p._presentations_store, '_publish', fail_after)
    result = p.acknowledge_result(pid, card, 'old-result-1', action='result_detail', trusted_action=True)
    assert result.status == 'uncertain' and result.fact is None and 'PRIVATE' not in repr(result)
    monkeypatch.setattr(p._presentations_store, '_publish', original)
    fact = p.get(pid).fact
    result = p.acknowledge_result(pid, card, 'old-result-1', action='result_detail', trusted_action=True)
    assert result.status == 'already_acknowledged' and result.fact == fact


def test_result_ack_cross_process_idempotency(tmp_path):
    import subprocess
    session, store, ledger, card, _ = setup_case(tmp_path)
    pid = str(uuid.uuid4()); p = WorkCardPresentations(session, store, ledger)
    assert p.create(pid, card).status == 'created'
    script = r'''
import sys
from pathlib import Path
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
root=Path(sys.argv[1]); sid=sys.argv[2]; pid=sys.argv[3]; card=sys.argv[4]
paths=AgentPaths(agent_root=root,workspace=root/'workspace',data=root/'data',evolve=root/'evolve')
s=Session(sid,paths.data/'sessions'/sid,'synthetic',SessionMeta(project_id=''),[],paths)
st=WorkRecordStore(s,lock_timeout=15); p=WorkCardPresentations(s,st,WorkCardLedger(s,st))
r=p.acknowledge_result(pid,card,'old-result-1',action='result_detail',trusted_action=True)
print(r.status)
'''
    procs = [subprocess.Popen([sys.executable, '-c', script, str(session.paths.agent_root),
             session.conversation_id, pid, card], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
             text=True, cwd=str(Path(__file__).resolve().parents[1])) for _ in range(4)]
    statuses = []
    for proc in procs:
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0, err
        statuses.append(out.strip())
    assert sorted(statuses) == ['acknowledged'] + ['already_acknowledged'] * 3
    assert len(p.get(pid).fact.result_views) == 1


def test_lost_card_rejects_existing_result_fact(tmp_path):
    session, store, ledger, card, _ = setup_case(tmp_path)
    p = WorkCardPresentations(session, store, ledger)
    pid = str(uuid.uuid4()); assert p.create(pid, card).status == 'created'
    assert p.acknowledge_result(pid, card, 'old-result-1', action='result_detail', trusted_action=True).status == 'acknowledged'
    # Simulate an externally lost ledger record. Public/private ledger mutations
    # now fail closed on deletion and cannot be used to erase historical cards.
    stored = ledger._ledger_store._read()
    stored['items'] = [row for row in stored['items'] if row['card_id'] != card]
    stored['revision'] += 1
    ledger._ledger_store._path.write_text(
        json.dumps(stored, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert p.get(pid).status == 'corrupt'
    assert p.acknowledge_result(pid, card, 'old-result-1', action='result_detail', trusted_action=True).status == 'corrupt'
