from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import work_record_visibility_clock as clock_module
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_record_cards import build_project_work_record_cards
from work_record_cards import build_independent_work_record_card
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_records_overview import render_work_records_overview


@dataclass(frozen=True)
class Sample:
    boot_id: str
    monotonic_ns: int
    wall_utc: str


def setup_completed(tmp_path: Path, *, extra_status: str | None = None, sample=None, monkeypatch=None,
                    ack_indices=None, ack_offsets_ns=None):
    if sample is not None and monkeypatch is not None:
        monkeypatch.setattr(clock_module, "sample_elapsed_clock", lambda: sample[0])
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / 'workspace',
                       data=tmp_path / 'data', evolve=tmp_path / 'evolve')
    sid = '20261030-abcddcba'
    directory = paths.data / 'sessions' / sid
    directory.mkdir(parents=True)
    session = Session(sid, directory, '', SessionMeta(project_id='project-a'), [], paths)
    store = WorkRecordStore(session)
    item = store.create_item(title='completed task', scope={'kind': 'project', 'project_id': 'project-a'})
    requirements = []
    for index, status in enumerate(['completed', *([extra_status] if extra_status else [])]):
        req = store.add_requirement(item['item_id'], f'requirement-{index}', event_id=f'add-{index}')
        run = f'run-{index}'
        if status == 'pending':
            requirements.append(req)
            continue
        store.begin_requirement_run(item['item_id'], req['requirement_id'], run_id=run, expected_current_run_id=None)
        if status == 'in_progress':
            requirements.append(req)
            continue
        result_status = status if status in {'completed', 'blocked', 'stopped', 'unknown'} else 'completed'
        store.record_result_if_current_run(item['item_id'], req['requirement_id'], run_id=run,
            result_id=f'result-{index}', status=result_status, summary=f'summary-{index}', verification={'status': 'not_verified'})
        requirements.append(req)
    ledger = WorkCardLedger(session, store)
    snapshot = build_project_work_record_cards(store, 'project-a')
    saved = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
    assert saved.status == 'saved'
    presentation_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == 'created'
    for index, req in enumerate(requirements):
        if (extra_status is None or extra_status == 'completed' or index == 0) and (ack_indices is None or index in ack_indices):
            if ack_offsets_ns is not None and sample is not None:
                base = sample[0]
                offset = ack_offsets_ns[index]
                base_wall = datetime.fromisoformat(base.wall_utc.replace('Z', '+00:00'))
                sample[0] = Sample(base.boot_id, base.monotonic_ns + offset,
                                   (base_wall + timedelta(microseconds=offset // 1000)).isoformat().replace('+00:00', 'Z'))
            assert presentations.acknowledge_result(presentation_id, saved.card_id, f'result-{index}',
                action='result_detail', trusted_action=True).status == 'acknowledged'
    return session, store, item, requirements, ledger, saved.card_id, presentation_id


def test_overview_uses_persisted_monotonic_time_and_keeps_history(tmp_path, monkeypatch):
    current = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, store, item, _, ledger, card_id, _ = setup_completed(tmp_path, sample=current, monkeypatch=monkeypatch)

    first = render_work_records_overview(session)
    assert 'completed task' in first
    current[0] = Sample('boot-a', 1_000_000_000 + 29 * 60 * 1_000_000_000,
                        '2026-10-30T00:29:00Z')
    assert 'completed task' in render_work_records_overview(session)
    current[0] = Sample('boot-a', 1_000_000_000 + 30 * 60 * 1_000_000_000,
                        '2026-10-30T00:30:00Z')
    collapsed = render_work_records_overview(session)
    assert 'completed task' not in collapsed
    assert '工作记录、历史卡片和原对话仍保留' in collapsed
    assert store.get_item(item['item_id'])['item_id'] == item['item_id']
    assert ledger.get_card(card_id) is not None


def test_missing_result_ack_result_change_and_unfinished_status_are_retained(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 10_000_000_000, '2026-10-30T00:00:00Z')]
    session, store, item, requirements, _, _, presentation_id = setup_completed(tmp_path, sample=clock, monkeypatch=monkeypatch)
    assert 'completed task' in render_work_records_overview(session)  # starts timer
    clock[0] = Sample('boot-a', 10_000_000_000 + 2_000_000_000, '2026-10-30T00:00:02Z')
    store.begin_requirement_run(item['item_id'], requirements[0]['requirement_id'], run_id='new-run',
                                expected_current_run_id=f'run-0')
    store.record_result_if_current_run(item['item_id'], requirements[0]['requirement_id'], run_id='new-run',
                        result_id='new-result', status='completed', summary='changed result',
                        verification={'status': 'not_verified'})
    assert 'completed task' in render_work_records_overview(session)
    assert presentation_id  # old result ACK cannot authorize the changed current result


def test_clock_rollback_or_boot_change_never_uses_wall_clock_to_hide(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, *_ = setup_completed(tmp_path, sample=clock, monkeypatch=monkeypatch)
    assert 'completed task' in render_work_records_overview(session)
    clock[0] = Sample('boot-a', 31 * 60 * 1_000_000_000, '2026-10-29T00:00:00Z')
    assert 'completed task' in render_work_records_overview(session)
    clock[0] = Sample('boot-b', 60 * 60 * 1_000_000_000, '2026-10-30T01:00:00Z')
    assert 'completed task' in render_work_records_overview(session)


def test_unavailable_clock_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(clock_module, 'sample_elapsed_clock', lambda: None)
    session, *_ = setup_completed(tmp_path)
    assert 'completed task' in render_work_records_overview(session)


def test_restart_accumulates_proven_uptime_without_counting_unknown_reboot_gap(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 2_000_000_000, '2026-10-30T00:00:00Z')]
    session, *_ = setup_completed(tmp_path, sample=clock, monkeypatch=monkeypatch)
    clock[0] = Sample('boot-a', 2_000_000_000 + 20 * 60 * 1_000_000_000, '2026-10-30T00:20:00Z')
    assert 'completed task' in render_work_records_overview(session)
    clock[0] = Sample('boot-b', 5 * 60 * 1_000_000_000, '2026-10-30T03:00:00Z')
    assert 'completed task' in render_work_records_overview(session)
    clock[0] = Sample('boot-c', 1 * 60 * 1_000_000_000, '2026-10-30T05:00:00Z')
    assert 'completed task' in render_work_records_overview(session)  # reboot retains 20 proven minutes
    clock[0] = Sample('boot-c', 11 * 60 * 1_000_000_000, '2026-10-30T05:10:00Z')
    assert 'completed task' not in render_work_records_overview(session)


@pytest.mark.parametrize("status", ["pending", "in_progress", "blocked", "unknown", "stopped"])
def test_noncompleted_requirement_keeps_item_even_with_completed_result_ack(tmp_path, monkeypatch, status):
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, *_ = setup_completed(tmp_path, extra_status=status, sample=clock, monkeypatch=monkeypatch)
    clock[0] = Sample('boot-a', 40 * 60 * 1_000_000_000, '2026-10-30T00:40:00Z')
    assert 'completed task' in render_work_records_overview(session)


def test_legacy_wall_only_result_view_never_becomes_elapsed_proof(tmp_path, monkeypatch):
    import json
    from work_card_presentations import PRESENTATIONS_FILENAME
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, *_ = setup_completed(tmp_path, sample=clock, monkeypatch=monkeypatch)
    path = session.session_dir / PRESENTATIONS_FILENAME
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['schema_version'] = 2
    for row in payload['items']:
        for view in row['result_views']:
            view.pop('elapsed_evidence')
        for key in ('run_id', 'receipt_state', 'receipt_attempts', 'last_offer_at', 'received_at'):
            row.pop(key, None)
    path.write_text(json.dumps(payload), encoding='utf-8')
    clock[0] = Sample('boot-a', 4 * 60 * 60 * 1_000_000_000, '2026-10-30T04:00:00Z')
    assert 'completed task' in render_work_records_overview(session)
    upgraded = json.loads(path.read_text(encoding='utf-8'))
    assert upgraded['schema_version'] == 4
    assert upgraded['items'][0]['result_views'][0]['elapsed_evidence'] is None


def test_project_group_waits_for_every_item_before_hiding_any_of_it(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, store, *_ = setup_completed(tmp_path, sample=clock, monkeypatch=monkeypatch)
    pending = store.create_item(title='still active in project', scope={'kind': 'project', 'project_id': 'project-a'})
    store.add_requirement(pending['item_id'], 'not finished', event_id='pending-req')
    clock[0] = Sample('boot-a', 41 * 60 * 1_000_000_000, '2026-10-30T00:40:00Z')
    overview = render_work_records_overview(session)
    assert 'completed task' in overview
    assert 'still active in project' in overview


def test_duplicate_presentation_of_same_result_does_not_restart_read_or_wait(tmp_path, monkeypatch):
    import uuid
    from work_card_presentations import WorkCardPresentations
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, store, _, _, ledger, card_id, _ = setup_completed(tmp_path, sample=clock, monkeypatch=monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)
    second_id = str(uuid.uuid4())
    assert presentations.create(second_id, card_id).status == 'created'
    clock[0] = Sample('boot-a', 1_000_000_000 + 30 * 60 * 1_000_000_000, '2026-10-30T00:30:00Z')
    assert 'completed task' not in render_work_records_overview(session)  # original explicit ACK remains authoritative
    assert presentations.acknowledge_result(second_id, card_id, 'result-0', action='result_detail', trusted_action=True).status == 'acknowledged'
    assert 'completed task' not in render_work_records_overview(session)


def test_each_result_keeps_its_own_ack_clock_across_duplicate_presentations(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, store, _, _, ledger, card_id, first_id = setup_completed(
        tmp_path, extra_status='completed', sample=clock, monkeypatch=monkeypatch,
        ack_indices={0}, ack_offsets_ns=(0, 0))
    presentations = WorkCardPresentations(session, store, ledger)
    second_id = str(uuid.uuid4())
    assert presentations.create(second_id, card_id).status == 'created'
    clock[0] = Sample('boot-a', 1_000_000_000 + 1 * 60 * 1_000_000_000, '2026-10-30T00:01:00Z')
    assert presentations.acknowledge_result(second_id, card_id, 'result-1',
        action='result_detail', trusted_action=True).status == 'acknowledged'
    clock[0] = Sample('boot-a', 1_000_000_000 + 30 * 60 * 1_000_000_000, '2026-10-30T00:30:00Z')
    assert 'completed task' in render_work_records_overview(session)
    clock[0] = Sample('boot-a', 1_000_000_000 + 31 * 60 * 1_000_000_000, '2026-10-30T00:31:00Z')
    assert 'completed task' not in render_work_records_overview(session)
    assert first_id != second_id


def test_multi_requirement_ack_times_do_not_share_maturity_within_one_presentation(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, *_ = setup_completed(tmp_path, extra_status='completed', sample=clock, monkeypatch=monkeypatch,
                                  ack_offsets_ns=(0, 29 * 60 * 1_000_000_000))
    clock[0] = Sample('boot-a', 1_000_000_000 + 30 * 60 * 1_000_000_000, '2026-10-30T00:30:00Z')
    assert 'completed task' in render_work_records_overview(session)


def _worker_batch_with_project_tasks(store, *, task_specs):
    digest = 'b' * 64
    created = store.create_item_with_requirement(
        title='多项目任务批次', scope={'kind': 'independent'},
        requirement='原始请求：协调所选项目', event_id=f'worker-batch-original-{digest}',
        idempotency_key=f'worker-batch-original:{digest}',
    )
    item = created[0] if isinstance(created, tuple) else created
    tasks = []
    for index, (project_id, text, status) in enumerate(task_specs):
        req = store.add_requirement(
            item['item_id'], text, event_id=f'worker-batch-task-{digest}-{index}',
            source_ref={'kind': 'project', 'id': project_id},
            idempotency_key=f'worker-batch-task:{digest}:{index}',
        )
        result_id = None
        if status == 'completed':
            run_id = f'batch-run-{index}'
            result_id = f'batch-result-{index}'
            store.begin_requirement_run(item['item_id'], req['requirement_id'], run_id=run_id,
                                        expected_current_run_id=None)
            store.record_result_if_current_run(
                item['item_id'], req['requirement_id'], run_id=run_id, result_id=result_id,
                status='completed', summary=f'result for {text}',
                verification={'status': 'not_verified'},
            )
        tasks.append((req, result_id))
    return item, tasks


def test_worker_batch_collapses_only_mature_project_requirements(tmp_path, monkeypatch):
    import uuid

    clock = [Sample('boot-batch', 2_000_000_000, '2026-10-30T00:00:00Z')]
    monkeypatch.setattr(clock_module, 'sample_elapsed_clock', lambda: clock[0])
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / 'workspace',
                       data=tmp_path / 'data', evolve=tmp_path / 'evolve')
    sid = '20261030-baa01234'
    directory = paths.data / 'sessions' / sid
    directory.mkdir(parents=True)
    session = Session(sid, directory, '', SessionMeta(), [], paths)
    store = WorkRecordStore(session)
    item, tasks = _worker_batch_with_project_tasks(store, task_specs=[
        ('project-a', 'project-a completed one', 'completed'),
        ('project-a', 'project-a completed two', 'completed'),
        ('project-b', 'project-b still pending', 'pending'),
    ])
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, item['item_id']))
    assert saved.status == 'saved'
    presentation_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == 'created'
    for _req, result_id in tasks:
        if result_id is not None:
            assert presentations.acknowledge_result(
                presentation_id, saved.card_id, result_id,
                action='result_detail', trusted_action=True,
            ).status == 'acknowledged'

    assert 'project-a completed one' in render_work_records_overview(session)
    clock[0] = Sample('boot-batch', 2_000_000_000 + 30 * 60 * 1_000_000_000, '2026-10-30T00:30:00Z')
    overview = render_work_records_overview(session)

    assert 'project-a completed one' not in overview
    assert 'project-a completed two' not in overview
    assert '项目 project-a（关联' not in overview
    assert 'project-b still pending' in overview
    assert '项目 project-b（关联' in overview
    assert '原始请求（仅追溯，不计入事项/要求）：原始请求：协调所选项目' in overview
    assert store.get_item(item['item_id'])['item_id'] == item['item_id']


def test_worker_batch_unread_other_project_does_not_inherit_mature_ack(tmp_path, monkeypatch):
    import uuid

    clock = [Sample('boot-batch-unread', 3_000_000_000, '2026-10-30T00:00:00Z')]
    monkeypatch.setattr(clock_module, 'sample_elapsed_clock', lambda: clock[0])
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / 'workspace',
                       data=tmp_path / 'data', evolve=tmp_path / 'evolve')
    sid = '20261030-baa05678'
    directory = paths.data / 'sessions' / sid
    directory.mkdir(parents=True)
    session = Session(sid, directory, '', SessionMeta(), [], paths)
    store = WorkRecordStore(session)
    item, tasks = _worker_batch_with_project_tasks(store, task_specs=[
        ('project-a', 'acknowledged project result', 'completed'),
        ('project-b', 'unread project result', 'completed'),
    ])
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, item['item_id']))
    assert saved.status == 'saved'
    presentation_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == 'created'
    assert presentations.acknowledge_result(
        presentation_id, saved.card_id, tasks[0][1], action='result_detail', trusted_action=True,
    ).status == 'acknowledged'

    assert 'acknowledged project result' in render_work_records_overview(session)
    clock[0] = Sample('boot-batch-unread', 3_000_000_000 + 30 * 60 * 1_000_000_000,
                      '2026-10-30T00:30:00Z')
    overview = render_work_records_overview(session)

    assert 'acknowledged project result' not in overview
    assert 'unread project result' in overview
    assert '项目 project-b（关联' in overview
    assert '原始请求（仅追溯，不计入事项/要求）：原始请求：协调所选项目' in overview


def test_hidden_batch_project_keeps_unmarked_requirement_and_trace_only_source(tmp_path, monkeypatch):
    import uuid

    clock = [Sample('boot-batch-residual', 4_000_000_000, '2026-10-30T00:00:00Z')]
    monkeypatch.setattr(clock_module, 'sample_elapsed_clock', lambda: clock[0])
    paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / 'workspace',
                       data=tmp_path / 'data', evolve=tmp_path / 'evolve')
    sid = '20261030-baa09876'
    directory = paths.data / 'sessions' / sid
    directory.mkdir(parents=True)
    session = Session(sid, directory, '', SessionMeta(), [], paths)
    store = WorkRecordStore(session)
    item, tasks = _worker_batch_with_project_tasks(store, task_specs=[
        ('project-a', 'finished project requirement', 'completed'),
    ])
    store.add_requirement(item['item_id'], 'unmarked follow-up still pending',
                          event_id='unmarked-follow-up', idempotency_key='unmarked-follow-up-key')
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, item['item_id']))
    assert saved.status == 'saved'
    presentation_id = str(uuid.uuid4())
    presentations = WorkCardPresentations(session, store, ledger)
    assert presentations.create(presentation_id, saved.card_id).status == 'created'
    assert presentations.acknowledge_result(
        presentation_id, saved.card_id, tasks[0][1], action='result_detail', trusted_action=True,
    ).status == 'acknowledged'

    clock[0] = Sample('boot-batch-residual', 4_000_000_000 + 30 * 60 * 1_000_000_000,
                      '2026-10-30T00:30:00Z')
    overview = render_work_records_overview(session)

    assert 'finished project requirement' not in overview
    assert 'unmarked follow-up still pending' in overview
    assert '原始请求（仅追溯，不计入事项/要求）：原始请求：协调所选项目' in overview
    assert '项目 project-a（关联' not in overview


def test_identical_result_snapshot_ack_is_shared_across_reposted_cards(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, store, _item, _requirements, ledger, _first_card_id, _first_presentation_id = setup_completed(
        tmp_path, sample=clock, monkeypatch=monkeypatch)
    presentations = WorkCardPresentations(session, store, ledger)

    repost = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, 'project-a'))
    assert repost.status == 'saved'
    assert repost.card_id != _first_card_id
    second_presentation_id = str(uuid.uuid4())
    assert presentations.create(second_presentation_id, repost.card_id).status == 'created'

    clock[0] = Sample('boot-a', 1_000_000_000 + 30 * 60 * 1_000_000_000, '2026-10-30T00:30:00Z')
    overview = render_work_records_overview(session)

    assert 'completed task' not in overview
    assert ledger.get_card(repost.card_id) is not None


def test_only_later_clicked_identical_result_snapshot_starts_its_own_30_minute_wait(tmp_path, monkeypatch):
    clock = [Sample('boot-a', 1_000_000_000, '2026-10-30T00:00:00Z')]
    session, store, _item, _requirements, ledger, first_card_id, _first_presentation_id = setup_completed(
        tmp_path, sample=clock, monkeypatch=monkeypatch, ack_indices=set())
    presentations = WorkCardPresentations(session, store, ledger)
    first_card = ledger.get_card(first_card_id)

    clock[0] = Sample('boot-a', 1_000_000_000 + 5 * 60 * 1_000_000_000, '2026-10-30T00:05:00Z')
    later = ledger.save_snapshot(str(uuid.uuid4()), build_project_work_record_cards(store, 'project-a'))
    assert later.status == 'saved' and later.card_id != first_card_id
    later_card = ledger.get_card(later.card_id)
    first_result = first_card['snapshot']['cards'][0]['requirements'][0]['latest_result']
    later_result = later_card['snapshot']['cards'][0]['requirements'][0]['latest_result']
    assert first_result == later_result
    later_presentation_id = str(uuid.uuid4())
    assert presentations.create(later_presentation_id, later.card_id).status == 'created'
    assert presentations.acknowledge_result(
        later_presentation_id, later.card_id, 'result-0', action='result_detail', trusted_action=True,
    ).status == 'acknowledged'

    clock[0] = Sample('boot-a', 1_000_000_000 + 34 * 60 * 1_000_000_000, '2026-10-30T00:34:00Z')
    assert 'completed task' in render_work_records_overview(session)
    clock[0] = Sample('boot-a', 1_000_000_000 + 35 * 60 * 1_000_000_000, '2026-10-30T00:35:00Z')
    assert 'completed task' not in render_work_records_overview(session)
