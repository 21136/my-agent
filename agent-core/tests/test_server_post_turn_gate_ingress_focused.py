from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from server import WsBridge, WsSessionHandler


def setup(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'evolve')
    session = SimpleNamespace(conversation_id='synthetic-a')
    current = [session]
    bridge = WsBridge(emit=lambda event: None, paths=paths)
    bridge._session_provider = lambda: current[0]
    bridge.init_post_turn_send_gate(session)
    ticket = bridge._post_turn_send_gate.create(session, 'request-id', 'run-id')
    assert ticket is not None and bridge._post_turn_send_gate.approve(ticket)
    return bridge, current, ticket


def test_inline_cancel_invalidates_post_turn_without_an_active_turn(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    handler = object.__new__(WsSessionHandler)
    assert handler._dispatch_inline({'type': 'turn.cancel'}, bridge)
    assert not bridge._post_turn_send_gate.start_send(ticket)


def test_session_change_invalidates_and_rebinds_gate(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    bridge.begin_session_binding_change()
    assert not bridge._post_turn_send_gate.start_send(ticket)
    current[0] = SimpleNamespace(conversation_id='synthetic-b')
    bridge.end_session_binding_change()
    assert bridge._post_turn_send_gate.create(current[0], 'request-2', 'run-2') is not None


def test_connection_close_invalidates_future_permission(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    bridge.mark_connection_closed()
    assert not bridge._post_turn_send_gate.start_send(ticket)
    assert bridge._connection_closed.is_set()
    assert bridge._post_turn_send_gate.create(current[0], 'request-2', 'run-2') is None


def test_permit_checks_current_session_even_without_transition_notification(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    previous = current[0]
    current[0] = SimpleNamespace(conversation_id='synthetic-a')
    assert not bridge.try_start_post_turn_send(ticket, previous)
    assert not bridge.post_turn_binding_is_valid(ticket, previous)


def test_permit_is_one_shot_and_ended_connection_invalidates_write(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    assert bridge.try_start_post_turn_send(ticket, current[0])
    assert not bridge.try_start_post_turn_send(ticket, current[0])
    assert bridge.post_turn_binding_is_valid(ticket, current[0])
    bridge.mark_connection_closed()
    assert not bridge.post_turn_binding_is_valid(ticket, current[0])
    assert bridge._post_turn_send_gate.was_started(ticket)


def test_raw_cancel_event_rejects_permission_before_watchdog_callback(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    bridge.cancel_event.set()
    assert not bridge.try_start_post_turn_send(ticket, current[0])


def test_repeated_gate_initialization_keeps_existing_ticket(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    bridge.init_post_turn_send_gate(current[0])
    assert bridge.try_start_post_turn_send(ticket, current[0])


def test_capacity_rejection_emits_precise_non_success_notice(tmp_path, monkeypatch):
    bridge, current, ticket = setup(tmp_path)
    events = []
    bridge.emit = events.append
    monkeypatch.setattr(bridge._post_turn_send_gate, '_MAX_TICKETS', 1)
    status, new_ticket = bridge.create_post_turn_ticket(current[0], 'request-2', 'run-2')
    assert (status, new_ticket) == ('capacity_exhausted', None)
    assert len(events) == 1 and events[0]['type'] == 'notice'
    assert '上限' in events[0]['text'] and '未外发' in events[0]['text']
    assert bridge.try_start_post_turn_send(ticket, current[0])


def test_status_for_replay_and_invalid_is_distinct_from_capacity(tmp_path, monkeypatch):
    bridge, current, ticket = setup(tmp_path)
    events = []
    bridge.emit = events.append
    monkeypatch.setattr(bridge._post_turn_send_gate, '_MAX_TICKETS', 1)
    assert bridge.create_post_turn_ticket(current[0], 'request-id', 'other-run') == ('replay', None)
    assert bridge.create_post_turn_ticket(current[0], '', 'run') == ('invalid', None)
    assert events == []
    assert bridge.try_start_post_turn_send(ticket, current[0])


def test_busy_candidate_is_not_replaced_and_has_notice(tmp_path):
    bridge, current, ticket = setup(tmp_path)
    events = []
    bridge.emit = events.append
    assert bridge.create_post_turn_ticket(current[0], 'request-2', 'run-2') == ('busy', None)
    assert len(events) == 1 and '等待' in events[0]['text'] and '未外发' in events[0]['text']
    assert bridge.try_start_post_turn_send(ticket, current[0])
