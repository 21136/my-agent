"""Render a locally re-read work-card ledger record as transitional text.

This is not a card UI, a viewed acknowledgement, source authentication, or a
validator for untrusted model output. Callers must supply a ledger-validated
record from the current Session. Unknown schema or inconsistent facts fail closed.
"""
from __future__ import annotations

import json
from dataclasses import asdict
import re
from typing import Any

from work_record_cards import ProjectWorkRecordCardsSnapshot, IndependentWorkRecordCardsSnapshot

from work_card_ledger import (
    _RECORD_KEYS, _SNAPSHOT_KEYS, _INDEPENDENT_SNAPSHOT_KEYS,
    _PROJECT_CARD_KEYS, _INDEPENDENT_CARD_KEYS,
    _REQUIREMENT_CARD_KEYS, _INDEPENDENT_REQUIREMENT_KEYS,
    _RESULT_CARD_KEYS, _INDEPENDENT_RESULT_KEYS,
)


class WorkCardTextError(ValueError):
    """The record cannot be rendered faithfully under the known schema."""


def _check(condition: bool) -> None:
    if not condition:
        raise WorkCardTextError('工作卡字段未知或不一致，无法安全呈现')


def _shape(value: Any, keys: frozenset[str]) -> None:
    _check(type(value) is dict and set(value) == keys)


def _text(value: Any, *, empty: bool = False) -> str:
    _check(type(value) is str and (empty or bool(value)))
    return json.dumps(value, ensure_ascii=False)


def _count(value: Any) -> int:
    _check(type(value) is int and value >= 0)
    return value


def _status(value: Any) -> str:
    _text(value)
    labels = {'active': '活跃', 'pending': '待处理', 'completed': '已记录为完成',
              'in_progress': '进行中', 'unknown': '未知', 'blocked': '受阻', 'stopped': '已停止'}
    return labels.get(value, '未知状态') + '（原值：' + _text(value) + '）'


def _refs(value: Any) -> str:
    _check(type(value) is list)
    if not value:
        return '未提供'
    parts = []
    for ref in value:
        _shape(ref, frozenset({'kind', 'id'}))
        parts.append(_text(ref['kind']) + ':' + _text(ref['id']))
    return '、'.join(parts)


def render_work_card_text(record: dict[str, Any]) -> str:
    """Return all known snapshot facts without displaying ledger request ID/hash.

    Inputs are the complete dictionary returned by WorkCardLedger.get_card or
    resolve_recall, not a provider response. No I/O, mutation, truncation, ACK,
    delivery or verification is performed. Raises WorkCardTextError if the
    schema cannot be represented safely; callers must not fall back to raw JSON.
    """
    _shape(record, _RECORD_KEYS)
    _check(type(record['card_id']) is str and re.fullmatch(r'wc-[0-9a-f]{64}', record['card_id']) is not None)
    _text(record['saved_at'])
    return _render_snapshot(record['snapshot'], card_id=record['card_id'], saved_at=record['saved_at'],
                            allow_incomplete=False)


def render_work_card_snapshot_text(snapshot: Any) -> str:
    """Render a complete-scope builder snapshot, including an unsaved incomplete one.

    This dataclass type is not authentication; callers must bind it to their trusted
    current Session. Scope-limit snapshots with unknown counts or omitted items are
    refused, not presented as complete. This function does not save the snapshot.
    """
    _check(type(snapshot) in (ProjectWorkRecordCardsSnapshot, IndependentWorkRecordCardsSnapshot))
    return _render_snapshot(json.loads(json.dumps(asdict(snapshot), ensure_ascii=False, allow_nan=False)),
                            card_id=None, saved_at=None, allow_incomplete=True)


def _render_snapshot(snapshot: Any, *, card_id: str | None, saved_at: str | None,
                     allow_incomplete: bool) -> str:
    _check(type(snapshot) is dict)
    independent = snapshot.get('kind') == 'independent'
    project_scoped = snapshot.get('kind') == 'project_scoped'
    if project_scoped:
        scoped_keys = _SNAPSHOT_KEYS | {'kind'}
        _check(set(snapshot) in (scoped_keys, scoped_keys | {'change_focus'}))
    else:
        _shape(snapshot, _INDEPENDENT_SNAPSHOT_KEYS if independent else _SNAPSHOT_KEYS)
    session_id = snapshot['session_id']
    _text(session_id)
    _text(snapshot['captured_at'])
    _check(snapshot['persisted'] is False and type(snapshot['history_complete']) is bool)
    complete = snapshot['completeness'] == 'complete'
    _check((complete and snapshot['incomplete_reason'] is None) or
           (allow_incomplete and snapshot['completeness'] == 'incomplete'
            and snapshot['incomplete_reason'] == 'current_result_missing'))
    project_id = snapshot['project_id']
    if independent:
        _check(project_id is None)
        _text(snapshot['item_id'])
        cards = [snapshot['card']]
    else:
        _text(project_id)
        cards = snapshot['cards']
        _check(type(cards) is list)
    counts = {key: _count(snapshot[key]) for key in ('item_count', 'requirement_count', 'result_count',
              'omitted_item_count', 'omitted_requirement_count', 'omitted_result_count', 'missing_current_result_count')}
    _check(counts['item_count'] == len(cards) and counts['omitted_item_count'] == 0
           and counts['omitted_requirement_count'] == 0
           and (counts['missing_current_result_count'] == 0 if complete else counts['missing_current_result_count'] > 0)
           and snapshot['history_complete'] == (counts['omitted_result_count'] == 0))
    lines = ['【历史工作卡快照】', '这是历史快照，不代表当前状态，不代表已读；记录状态不等于验证通过。',
             ('卡片 ID：' + card_id if card_id is not None else '此 builder 快照未保存为工作卡。'),
             '范围：' + ('独立事项' if independent else '项目 ' + _text(project_id)),
             '会话：' + _text(session_id), '快照时间：' + _text(snapshot['captured_at']),
             ('保存时间：' + _text(saved_at) if saved_at is not None else '保存时间：未保存'),
             ('快照完整性：完整' if complete else
              f"快照不完整：{counts['missing_current_result_count']} 项终态要求当前结果缺失。"),
             f"事项 {counts['item_count']} 项，要求 {counts['requirement_count']} 项，已保存结果 {counts['result_count']} 条。",
             f"历史结果：{'完整' if snapshot['history_complete'] else '不完整'}；省略 {counts['omitted_result_count']} 条历史结果。"]
    seen_items: set[str] = set()
    seen_requirements: set[str] = set()
    total_req = total_results = total_omitted = total_missing = 0
    for index, card in enumerate(cards, 1):
        _shape(card, _INDEPENDENT_CARD_KEYS if independent else _PROJECT_CARD_KEYS)
        item_id = card['item_id']
        _text(item_id)
        _check(item_id not in seen_items and card['session_id'] == session_id and card['project_id'] == project_id)
        if independent:
            _check(card['kind'] == 'independent' and item_id == snapshot['item_id'])
        seen_items.add(item_id)
        lines.extend([f"\n事项 {index}：" + _text(card['title']), '事项 ID：' + _text(item_id),
                      '事项状态：' + _status(card['status']), '事项来源：' + _refs(card['source_refs'])])
        requirements = card['requirements']
        _check(type(requirements) is list)
        total_req += len(requirements)
        if not requirements:
            lines.append('要求：无已记录要求')
        for req_index, req in enumerate(requirements, 1):
            _shape(req, _INDEPENDENT_REQUIREMENT_KEYS if independent else _REQUIREMENT_CARD_KEYS)
            req_id = req['requirement_id']
            _text(req_id)
            _check(req_id not in seen_requirements and req['session_id'] == session_id and req['item_id'] == item_id)
            if not independent:
                _check(req['project_id'] == project_id)
            seen_requirements.add(req_id)
            source = req['source_ref']
            lines.extend([f"  要求 {req_index}：" + _text(req['text']), '  要求 ID：' + _text(req_id),
                          '  要求状态：' + _status(req['status']),
                          '  要求来源：' + _refs([] if source is None else [source])])
            result_count, omitted = _count(req['result_count']), _count(req['omitted_result_count'])
            total_results += result_count
            total_omitted += omitted
            _check(type(req['current_result_missing']) is bool)
            total_missing += int(req['current_result_missing'])
            current_run = req['current_run_id']
            if current_run is not None:
                _text(current_run)
            current = req['latest_result_is_current']
            _check(current is None or type(current) is bool)
            lines.append('  当前运行 ID：' + ('未提供' if current_run is None else _text(current_run)))
            lines.append(f'  已保存结果 {result_count} 条，省略历史结果 {omitted} 条。')
            latest = req['latest_result']
            terminal = req['status'] in {'completed', 'blocked', 'stopped', 'unknown'}
            expected_missing = terminal and (latest is None or (current_run is not None and current is False))
            _check(req['current_result_missing'] is expected_missing)
            if latest is None:
                _check(result_count == 0 and omitted == 0 and current is (False if current_run is not None else None))
                if current_run is not None:
                    lines.append('  当前运行尚无已保存结果。')
                lines.append('  最新结果：无已保存结果')
                lines.append('  验证状态：未提供')
            else:
                _shape(latest, _INDEPENDENT_RESULT_KEYS if independent else _RESULT_CARD_KEYS)
                _check(result_count > 0 and omitted == result_count - 1
                       and latest['session_id'] == session_id and latest['item_id'] == item_id
                       and latest['requirement_id'] == req_id)
                if not independent:
                    _check(latest['project_id'] == project_id)
                _text(latest['result_id'])
                _text(latest['run_id'])
                expected = None if current_run is None else latest['run_id'] == current_run
                _check(current is expected)
                relation = ('未提供当前运行，无法比较' if current is None else
                            '属于当前运行' if current else '不属于当前运行')
                if current is False:
                    lines.append('  当前运行的结果未在最新结果中体现；无法确认历史中是否有当前运行结果。')
                verification = latest['verification_status']
                lines.extend(['  最新结果 ID：' + _text(latest['result_id']),
                              '  最新结果运行 ID：' + _text(latest['run_id']),
                              '  最新结果与当前运行：' + relation,
                              '  结果状态：' + _status(latest['status']),
                              '  结果摘要：' + _text(latest['summary'], empty=True),
                              '  验证状态：' + ('未提供' if verification is None else _text(verification)),
                              '  结果来源：' + _refs(latest['sources'])])
            if req['current_result_missing']:
                lines.append('  当前结果缺失：builder 标记此终态要求缺少当前结果。')
    _check(total_req == counts['requirement_count'] and total_results == counts['result_count']
           and total_omitted == counts['omitted_result_count']
           and total_missing == counts['missing_current_result_count'])
    if project_scoped and snapshot.get('change_focus') is not None:
        focus = snapshot['change_focus']
        _shape(focus, frozenset({'item_id', 'requirement_id', 'result_id'}))
        for value in focus.values():
            _text(value)
        matches = [req for card in cards if card['item_id'] == focus['item_id']
                   for req in card['requirements']
                   if req['requirement_id'] == focus['requirement_id']
                   and type(req['latest_result']) is dict
                   and req['latest_result']['result_id'] == focus['result_id']
                   and req['latest_result']['run_id'] == req['current_run_id']
                   and req['latest_result_is_current'] is True]
        _check(len(matches) == 1)
        lines.append('\u672c\u6b21\u66f4\u65b0\uff1a' + _text(focus['item_id']) + ' / '
                     + _text(focus['requirement_id']) + ' / ' + _text(focus['result_id']))
    return '\n'.join(lines)
