from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_record_post_turn_gate import PostTurnSendGate


def candidate():
    session = object()
    gate = PostTurnSendGate(session)
    ticket = gate.create(session, 'request-1', 'run-1')
    assert ticket is not None
    return gate, session, ticket


def test_permit_is_one_shot_requires_exact_approved_identity():
    gate, session, ticket = candidate()
    assert not gate.start_send(ticket)
    assert not gate.approve(object())
    assert gate.approve(ticket)
    assert not gate.approve(ticket)
    assert gate.start_send(ticket)
    assert not gate.start_send(ticket)
    assert gate.is_valid(ticket)
    assert gate.was_started(ticket)


@pytest.mark.parametrize('invalidate', ['cancel', 'disconnect', 'switch'])
def test_invalidation_before_permission_never_starts_send(invalidate):
    gate, session, ticket = candidate()
    assert gate.approve(ticket)
    if invalidate == 'switch':
        gate.begin_session_change()
        gate.end_session_change(object())
    else:
        getattr(gate, invalidate)()
    assert not gate.start_send(ticket)
    assert not gate.is_valid(ticket)
    assert not gate.was_started(ticket)


@pytest.mark.parametrize('invalidate', ['cancel', 'disconnect', 'switch'])
def test_invalidation_after_permission_does_not_revoke_historical_start(invalidate):
    gate, session, ticket = candidate()
    assert gate.approve(ticket) and gate.start_send(ticket)
    if invalidate == 'switch':
        gate.begin_session_change()
        gate.end_session_change(object())
    else:
        getattr(gate, invalidate)()
    assert gate.was_started(ticket)
    assert not gate.is_valid(ticket)


def test_session_transition_rejects_new_candidate_and_old_ticket():
    gate, session, ticket = candidate()
    gate.begin_session_change()
    assert gate.create(session, 'request-2', 'run-2') is None
    next_session = object()
    gate.end_session_change(next_session)
    assert gate.create(session, 'request-3', 'run-3') is None
    assert gate.create(next_session, 'request-3', 'run-3') is not None
    assert not gate.approve(ticket)


def test_pending_candidate_is_not_silently_replaced_and_close_is_permanent():
    gate, session, ticket = candidate()
    assert gate.create_checked(session, 'request-2', 'run-2') == ('busy', None)
    assert gate.approve(ticket)
    gate.cancel()
    other = gate.create(session, 'request-2', 'run-2')
    assert other is not None and not gate.approve(ticket)
    gate.disconnect()
    assert not gate.approve(other)
    assert gate.create(session, 'request-3', 'run-3') is None


def test_network_runs_outside_gate_lock_and_switch_can_invalidate():
    gate, session, ticket = candidate()
    assert gate.approve(ticket)
    started, release = threading.Event(), threading.Event()
    def network_worker():
        assert gate.start_send(ticket)
        started.set()
        assert release.wait(2)
    worker = threading.Thread(target=network_worker)
    worker.start()
    try:
        assert started.wait(2)
        switched = threading.Event()
        def do_switch():
            gate.begin_session_change()
            switched.set()
        switcher = threading.Thread(target=do_switch)
        switcher.start()
        assert switched.wait(1), 'network worker held the gate lock'
        switcher.join(1)
        assert not gate.is_valid(ticket)
        assert gate.was_started(ticket)
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_racing_send_and_cancel_has_one_consistent_order():
    for _ in range(100):
        gate, session, ticket = candidate()
        assert gate.approve(ticket)
        barrier = threading.Barrier(3)
        result = []
        def send():
            barrier.wait()
            result.append(gate.start_send(ticket))
        def cancel():
            barrier.wait()
            gate.cancel()
        a, b = threading.Thread(target=send), threading.Thread(target=cancel)
        a.start(); b.start(); barrier.wait(); a.join(2); b.join(2)
        assert not a.is_alive() and not b.is_alive()
        assert result == [gate.was_started(ticket)]
        assert not gate.is_valid(ticket)


def test_replayed_request_cannot_create_another_permit():
    gate, session, ticket = candidate()
    assert gate.approve(ticket) and gate.start_send(ticket)
    gate.cancel()
    assert gate.create(session, 'request-1', 'run-1') is None
    assert gate.create(session, 'request-1', 'different-run') is None


def test_other_connection_cannot_approve_or_observe_ticket():
    gate, session, ticket = candidate()
    other = PostTurnSendGate(session)
    assert not other.approve(ticket)
    assert not other.start_send(ticket)
    assert not other.was_started(ticket)


def test_unknown_session_after_transition_fails_closed():
    gate, session, ticket = candidate()
    gate.begin_session_change()
    gate.end_session_change(None)
    assert gate.create(None, 'request-next', 'run-next') is None


def test_capacity_is_explicit_and_does_not_invalidate_prior_permission(monkeypatch):
    monkeypatch.setattr(PostTurnSendGate, '_MAX_TICKETS', 2)
    gate, session, first = candidate()
    gate.cancel()
    status, second = gate.create_checked(session, 'request-2', 'run-2')
    assert status == 'created' and second is not None
    assert gate.approve(second)
    status, rejected = gate.create_checked(session, 'request-3', 'run-3')
    assert status == 'capacity_exhausted' and rejected is None
    assert gate.start_send(second)
    assert gate.was_started(second)
    assert gate.create_checked(session, 'request-1', 'run-other') == ('replay', None)
    assert gate.create_checked(session, 'request-2', 'run-2') == ('replay', None)


def test_capacity_does_not_recover_on_cancel_or_switch(monkeypatch):
    monkeypatch.setattr(PostTurnSendGate, '_MAX_TICKETS', 1)
    gate, session, ticket = candidate()
    gate.cancel()
    assert gate.create_checked(session, 'another', 'run') == ('capacity_exhausted', None)
    next_session = object()
    gate.begin_session_change()
    gate.end_session_change(next_session)
    assert gate.create_checked(next_session, 'third', 'run') == ('capacity_exhausted', None)


def test_connection_close_denies_new_candidates_without_losing_started_fact():
    gate, session, ticket = candidate()
    assert gate.approve(ticket) and gate.start_send(ticket)
    gate.disconnect()
    assert gate.was_started(ticket)
    assert gate.create_checked(session, 'other', 'run') == ('closed', None)


def test_legacy_create_raises_instead_of_silently_dropping_capacity(monkeypatch):
    from work_record_post_turn_gate import PostTurnGateCapacityError
    monkeypatch.setattr(PostTurnSendGate, '_MAX_TICKETS', 1)
    gate, session, ticket = candidate()
    with pytest.raises(PostTurnGateCapacityError):
        gate.create(session, 'other-request', 'run')
