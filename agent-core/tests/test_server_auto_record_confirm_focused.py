from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from server import WsBridge, WsSessionHandler, TURN_LOCK


def setup_bridge(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'evolve')
    session = SimpleNamespace(conversation_id='synthetic-session')
    events = []
    bridge = WsBridge(emit=events.append, paths=paths, confirm_timeout=1)
    bridge._session_provider = lambda: session
    return bridge, session, events


def wait_requests(events, count):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        requests = [e for e in events if e.get('type') == 'confirm.request']
        if len(requests) >= count:
            return requests
        time.sleep(.005)
    raise AssertionError('missing confirmation request')


def start(bridge, session, label, *, valid=lambda: True):
    output = []
    thread = threading.Thread(target=lambda: output.append(
        bridge.confirm_auto_record_candidate(label, session=session, binding_is_valid=valid)))
    thread.start()
    return thread, output


def test_independent_candidate_slots_and_normal_confirmation_routing(tmp_path):
    bridge, session, events = setup_bridge(tmp_path)
    bridge._pending_confirm_id = 'ordinary-id'
    one, first = start(bridge, session, 'synthetic 1')
    two, second = start(bridge, session, 'synthetic 2')
    try:
        requests = wait_requests(events, 2)
        by_preview = {e['preview']: e for e in requests}
        a, b = by_preview['synthetic 1'], by_preview['synthetic 2']
        assert a['request_id'] != b['request_id']
        assert all(e['confirm_kind'] == 'work_record_post_turn' and e['session_id'] == session.conversation_id for e in requests)
        handler = object.__new__(WsSessionHandler)
        handler._dispatch_inline({'type':'confirm.response', 'request_id': b['request_id'], 'choice':'n'}, bridge)
        handler._dispatch_inline({'type':'confirm.response', 'request_id': a['request_id'], 'choice':'y'}, bridge)
        one.join(2); two.join(2)
        assert first == [('y', a['request_id'])]
        assert second == [('n', b['request_id'])]
        handler._dispatch_inline({'type':'confirm.response', 'request_id':a['request_id'], 'choice':'y'}, bridge)
        assert bridge._confirm_queue.empty()
        handler._dispatch_inline({'type':'confirm.response', 'request_id':'ordinary-id', 'choice':'y'}, bridge)
        assert bridge._confirm_queue.get_nowait() == ('ordinary-id', 'y')
    finally:
        bridge.mark_connection_closed(); one.join(2); two.join(2)


@pytest.mark.parametrize('invalidate', ['begin_session_binding_change', 'mark_connection_closed', 'invalidate_post_turn'])
def test_all_waiters_invalidated_without_response_leak(tmp_path, invalidate):
    bridge, session, events = setup_bridge(tmp_path)
    one, first = start(bridge, session, 'synthetic 1')
    two, second = start(bridge, session, 'synthetic 2')
    requests = wait_requests(events, 2)
    getattr(bridge, invalidate)()
    handler = object.__new__(WsSessionHandler)
    for request in requests:
        handler._dispatch_inline({'type':'confirm.response', 'request_id':request['request_id'], 'choice':'y'}, bridge)
    one.join(2); two.join(2)
    assert not one.is_alive() and not two.is_alive()
    assert first[0][0] == second[0][0] == 'n'
    assert bridge._confirm_queue.empty()


def test_wait_does_not_hold_turn_lock_and_binding_rechecked(tmp_path):
    bridge, session, events = setup_bridge(tmp_path)
    live = threading.Event(); live.set()
    worker, output = start(bridge, session, 'synthetic', valid=live.is_set)
    request = wait_requests(events, 1)[0]
    async def acquire():
        async with TURN_LOCK:
            return True
    assert asyncio.run(asyncio.wait_for(acquire(), timeout=.5))
    live.clear()
    handler = object.__new__(WsSessionHandler)
    handler._dispatch_inline({'type':'confirm.response', 'request_id':request['request_id'], 'choice':'y'}, bridge)
    worker.join(2)
    assert output[0][0] == 'n'
    assert bridge._confirm_queue.empty()


def test_legacy_post_turn_confirmation_and_auto_slots_are_isolated(tmp_path):
    bridge, session, events = setup_bridge(tmp_path)
    bridge.init_post_turn_send_gate(session)
    status, ticket = bridge.create_post_turn_ticket(session, 'synthetic-request', 'synthetic-run')
    assert status == 'created'
    auto, auto_result = start(bridge, session, 'synthetic auto')
    old_result = []
    old = threading.Thread(target=lambda: old_result.append(
        bridge.confirm_post_turn('synthetic legacy', session=session, ticket=ticket)))
    old.start()
    try:
        requests = wait_requests(events, 2)
        by_preview = {e['preview']: e for e in requests}
        handler = object.__new__(WsSessionHandler)
        for label in ('synthetic legacy', 'synthetic auto'):
            handler._dispatch_inline({'type':'confirm.response', 'request_id':by_preview[label]['request_id'], 'choice':'y'}, bridge)
        auto.join(2); old.join(2)
        assert auto_result == [('y', by_preview['synthetic auto']['request_id'])]
        assert old_result == [('y', by_preview['synthetic legacy']['request_id'])]
    finally:
        bridge.mark_connection_closed(); auto.join(2); old.join(2)


def test_timeout_and_changed_session_responses_cannot_reach_normal_confirm(tmp_path):
    bridge, session, events = setup_bridge(tmp_path)
    bridge.confirm_timeout = .05
    worker, result = start(bridge, session, 'synthetic timeout')
    request = wait_requests(events, 1)[0]
    worker.join(1)
    assert result == [('n', request['request_id'])]
    assert any(e.get('type') == 'confirm.done' and e.get('choice') == 'timeout' for e in events)
    handler = object.__new__(WsSessionHandler)
    handler._dispatch_inline({'type':'confirm.response', 'request_id':request['request_id'], 'choice':'y'}, bridge)
    assert bridge._confirm_queue.empty()
    bridge.confirm_timeout = 1
    worker, result = start(bridge, session, 'synthetic changed')
    request = wait_requests(events, 2)[1]
    bridge._session_provider = lambda: object()
    handler._dispatch_inline({'type':'confirm.response', 'request_id':request['request_id'], 'choice':'y'}, bridge)
    worker.join(1)
    assert result[0][0] == 'n'
    assert bridge._confirm_queue.empty()


def test_capacity_rejects_without_displacing_waiters(tmp_path):
    bridge, session, events = setup_bridge(tmp_path)
    workers = [start(bridge, session, 'synthetic ' + str(i)) for i in range(16)]
    try:
        requests = wait_requests(events, 16)
        assert bridge.confirm_auto_record_candidate('overflow', session=session, binding_is_valid=lambda: True) == ('n', '')
        assert len([e for e in events if e.get('type') == 'confirm.request']) == 16
        handler = object.__new__(WsSessionHandler)
        for request in requests:
            handler._dispatch_inline({'type':'confirm.response', 'request_id':request['request_id'], 'choice':'n'}, bridge)
        for thread, output in workers:
            thread.join(2)
            assert output[0][0] == 'n'
    finally:
        bridge.mark_connection_closed()
        for thread, _ in workers:
            thread.join(2)
