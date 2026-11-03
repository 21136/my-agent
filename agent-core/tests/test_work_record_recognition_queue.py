from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from work_record_recognition_queue import RecognitionQueue, QueueStatus


class Identity:
    def __init__(self, label):
        self.label = label

    def __eq__(self, other):
        return isinstance(other, Identity) and self.label == other.label



def authorized_queue(*, capacity=4):
    connection = Identity("connection")
    session = Identity("session")
    queue = RecognitionQueue(capacity=capacity)
    assert queue.authorize(connection, session).status == QueueStatus.AUTHORIZED
    return queue, connection, session


def test_authorization_defaults_closed_and_uses_binding_object_identity():
    queue = RecognitionQueue(capacity=2)
    conn = Identity("same")
    session = Identity("same")

    assert queue.enqueue(conn, session).status == QueueStatus.UNAUTHORIZED
    assert queue.authorize(conn, session).status == QueueStatus.AUTHORIZED
    # Equality is deliberately insufficient; each host binding is an exact object identity.
    assert queue.enqueue(Identity("same"), session).status == QueueStatus.BINDING_MISMATCH
    assert queue.enqueue(conn, Identity("same")).status == QueueStatus.BINDING_MISMATCH
    assert queue.pending_count == 0


def test_finished_candidate_releases_serial_slot_while_confirmation_is_pending():
    queue, conn, session = authorized_queue(capacity=2)
    first = queue.enqueue(conn, session)
    assert first.status == QueueStatus.ENQUEUED
    lease1 = queue.claim_next()
    assert lease1 is not None
    candidate1 = queue.finish_recognition(lease1, conn, session, candidate=True)
    assert candidate1.status == QueueStatus.CANDIDATE_READY
    assert candidate1.ticket is not None

    # The first candidate may wait for confirmation, but a later message can be recognized.
    second = queue.enqueue(conn, session)
    assert second.status == QueueStatus.ENQUEUED
    lease2 = queue.claim_next()
    assert lease2 is not None and lease2.request_token != lease1.request_token
    candidate2 = queue.finish_recognition(lease2, conn, session, candidate=True)
    assert candidate2.status == QueueStatus.CANDIDATE_READY
    assert queue.pending_candidate_count == 2
    assert queue.claim_next() is None



def test_only_one_recognition_lease_is_active_and_queue_order_is_fifo():
    queue, conn, session = authorized_queue(capacity=3)
    first = queue.enqueue(conn, session)
    second = queue.enqueue(conn, session)
    assert first.status == second.status == QueueStatus.ENQUEUED

    lease1 = queue.claim_next()
    assert lease1 is not None and lease1.request_token == first.request_token
    assert queue.claim_next() is None
    assert queue.finish_recognition(lease1, conn, session, candidate=False).status == QueueStatus.NO_CANDIDATE

    lease2 = queue.claim_next()
    assert lease2 is not None and lease2.request_token == second.request_token
    assert queue.finish_recognition(lease2, conn, session, candidate=False).status == QueueStatus.NO_CANDIDATE
    assert queue.pending_count == 0

def test_full_queue_is_explicit_and_does_not_evict_existing_candidate_or_request():
    queue, conn, session = authorized_queue(capacity=2)
    first = queue.enqueue(conn, session)
    lease = queue.claim_next()
    candidate = queue.finish_recognition(lease, conn, session, candidate=True)
    second = queue.enqueue(conn, session)
    assert first.status == second.status == QueueStatus.ENQUEUED

    rejected = queue.enqueue(conn, session)
    assert rejected.status == QueueStatus.FULL
    assert rejected.request_token is None
    assert queue.pending_count == 2
    assert queue.ticket_status(candidate.ticket, conn, session) == QueueStatus.CANDIDATE_READY
    next_lease = queue.claim_next()
    assert next_lease is not None and next_lease.request_token == second.request_token


def test_revocation_and_rebinding_invalidate_all_waiting_candidates_and_queued_work():
    queue, conn, session = authorized_queue(capacity=4)
    queue.enqueue(conn, session)
    lease = queue.claim_next()
    candidate = queue.finish_recognition(lease, conn, session, candidate=True)
    queued = queue.enqueue(conn, session)
    assert candidate.ticket is not None and queued.status == QueueStatus.ENQUEUED

    assert queue.revoke(conn, session) is True
    assert queue.ticket_status(candidate.ticket, conn, session) == QueueStatus.REVOKED
    assert queue.enqueue(conn, session).status == QueueStatus.UNAUTHORIZED
    assert queue.claim_next() is None

    new_session = Identity("session")  # equal-valued replacement is a distinct binding
    assert queue.authorize(conn, new_session).status == QueueStatus.AUTHORIZED
    assert queue.ticket_status(candidate.ticket, conn, new_session) == QueueStatus.REVOKED
    assert queue.pending_count == 0


def test_revocation_before_commit_rejects_without_invoking_local_write():
    queue, conn, session = authorized_queue()
    queue.enqueue(conn, session)
    lease = queue.claim_next()
    ticket = queue.finish_recognition(lease, conn, session, candidate=True).ticket
    assert ticket is not None
    assert queue.revoke(conn, session)
    writes = []

    outcome = queue.commit_if_current(ticket, conn, session, lambda: writes.append("write"))

    assert outcome.status == QueueStatus.REVOKED
    assert writes == []


def test_commit_and_revoke_are_linearized_by_the_same_short_lock():
    queue, conn, session = authorized_queue()
    queue.enqueue(conn, session)
    lease = queue.claim_next()
    ticket = queue.finish_recognition(lease, conn, session, candidate=True).ticket
    entered = threading.Event()
    allow_write_to_finish = threading.Event()
    revoked = threading.Event()
    writes = []

    revoke_entered = threading.Event()

    def revoke_after_enter():
        revoke_entered.set()
        return queue.revoke(conn, session)

    def local_commit():
        entered.set()
        assert allow_write_to_finish.wait(3)
        writes.append("committed")
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as executor:
        commit_future = executor.submit(queue.commit_if_current, ticket, conn, session, local_commit)
        assert entered.wait(3)
        revoke_future = executor.submit(revoke_after_enter)
        assert revoke_entered.wait(3)
        time.sleep(0.05)
        assert not revoke_future.done(), "revocation must linearize after the in-lock local commit"
        allow_write_to_finish.set()
        commit = commit_future.result(timeout=3)
        assert revoke_future.result(timeout=3) is True
        revoked.set()

    assert revoked.is_set()
    assert commit.status == QueueStatus.COMMITTED and commit.value == "ok"
    assert writes == ["committed"]
    assert queue.ticket_status(ticket, conn, session) == QueueStatus.REVOKED


def test_recognition_external_call_runs_outside_queue_lock_and_slot_releases_on_completion():
    queue, conn, session = authorized_queue(capacity=3)
    queued = queue.enqueue(conn, session)
    lease = queue.claim_next()
    external_entered = threading.Event()
    external_release = threading.Event()

    def fake_external_recognizer():
        external_entered.set()
        assert external_release.wait(3)
        return "synthetic decision"

    with ThreadPoolExecutor(max_workers=2) as executor:
        external = executor.submit(fake_external_recognizer)
        assert external_entered.wait(3)
        # Queue operations and revocation remain responsive during the caller's external I/O.
        later = queue.enqueue(conn, session)
        assert later.status == QueueStatus.ENQUEUED
        external_release.set()
        assert external.result(timeout=3) == "synthetic decision"

    candidate = queue.finish_recognition(lease, conn, session, candidate=True)
    assert candidate.status == QueueStatus.CANDIDATE_READY
    assert queue.claim_next().request_token == later.request_token
    assert queued.status == QueueStatus.ENQUEUED



def test_concurrent_admissions_never_exceed_capacity_or_silently_drop_accepted_work():
    queue, conn, session = authorized_queue(capacity=3)
    barrier = threading.Barrier(24)

    def submit(_index):
        barrier.wait(timeout=3)
        return queue.enqueue(conn, session)

    with ThreadPoolExecutor(max_workers=24) as executor:
        results = list(executor.map(submit, range(24)))

    assert sum(result.status == QueueStatus.ENQUEUED for result in results) == 3
    assert sum(result.status == QueueStatus.FULL for result in results) == 21
    accepted_tokens = {result.request_token for result in results if result.status == QueueStatus.ENQUEUED}
    claimed_tokens = set()
    for _ in range(3):
        lease = queue.claim_next()
        assert lease is not None
        claimed_tokens.add(lease.request_token)
        assert queue.finish_recognition(lease, conn, session, candidate=False).status == QueueStatus.NO_CANDIDATE
    assert claimed_tokens == accepted_tokens
    assert queue.pending_count == 0


def test_binding_switch_keeps_stale_external_call_serialized_until_it_returns():
    queue, old_conn, old_session = authorized_queue(capacity=3)
    old = queue.enqueue(old_conn, old_session)
    stale_lease = queue.claim_next()
    assert old.status == QueueStatus.ENQUEUED and stale_lease is not None

    new_conn, new_session = Identity("new-connection"), Identity("new-session")
    assert queue.authorize(new_conn, new_session).status == QueueStatus.AUTHORIZED
    new = queue.enqueue(new_conn, new_session)
    assert new.status == QueueStatus.ENQUEUED
    assert queue.claim_next() is None  # previous outbound call may still be active

    stale = queue.finish_recognition(stale_lease, old_conn, old_session, candidate=True)
    assert stale.status == QueueStatus.REVOKED and stale.ticket is None
    current = queue.claim_next()
    assert current is not None and current.request_token == new.request_token
    assert queue.finish_recognition(current, new_conn, new_session, candidate=False).status == QueueStatus.NO_CANDIDATE

def test_queue_never_retains_message_text_candidate_content_or_provider_payload():
    queue, conn, session = authorized_queue()
    private_text = "SYNTHETIC_SECRET_MESSAGE_BODY"
    private_candidate = "SYNTHETIC_PRIVATE_CANDIDATE_REQUIREMENT"
    with pytest.raises(TypeError):
        queue.enqueue(conn, session, body=private_text, candidate=private_candidate)
    assert private_text not in repr(queue)
    assert private_candidate not in repr(queue)
    assert queue.pending_count == 0


def test_stale_recognition_completion_cannot_mint_ticket_after_revoke_or_switch():
    queue, conn, session = authorized_queue()
    queue.enqueue(conn, session)
    lease = queue.claim_next()
    assert queue.revoke(conn, session)

    stale = queue.finish_recognition(lease, conn, session, candidate=True)

    assert stale.status == QueueStatus.REVOKED
    assert stale.ticket is None
    assert queue.pending_candidate_count == 0


def test_commit_ticket_is_one_shot_even_when_local_callback_fails():
    queue, conn, session = authorized_queue()
    queue.enqueue(conn, session)
    lease = queue.claim_next()
    ticket = queue.finish_recognition(lease, conn, session, candidate=True).ticket
    assert ticket is not None
    calls = []

    def failed_local_write():
        calls.append(1)
        raise OSError("synthetic local failure")

    first = queue.commit_if_current(ticket, conn, session, failed_local_write)
    replay = queue.commit_if_current(ticket, conn, session, lambda: calls.append(2))
    assert first.status == QueueStatus.COMMIT_FAILED
    assert replay.status == QueueStatus.INVALID_TICKET
    assert calls == [1]
