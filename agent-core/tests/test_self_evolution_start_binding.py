from __future__ import annotations

import threading
import uuid

import pytest

from server import SelfEvolutionChatIntegration
from self_evolution_host import HostRunContext, HostRunContextProvider
from self_evolution_store import StoreOwnershipError
from self_evolution_types import EvolutionRequest
from tests.test_self_evolution_store import _store


_RESULT = {
    "status": "started",
    "task_id": "task-bound",
    "attempt_id": "attempt-bound",
    "run_id": "run-authoritative",
    "worker_bound": True,
    "os_sandbox": False,
    "merge_authorized": False,
}


class _Host:
    worker_bound = True

    def __init__(self, store, provider):
        self._store = store
        self._context_provider = provider
        self._startup_reconciliation_error = None
        self.starts = []

    def start(self, request, *, start_request_claim, **_kwargs):
        self.starts.append((request, start_request_claim))
        return dict(_RESULT)


def _context(session_id: str, run_id: str, epoch: int, request_id: str) -> HostRunContext:
    return HostRunContext(
        session_id, run_id, epoch, "agent", "execute", threading.Event(), request_id,
        "human", self_evolution_intent=True,
    )


def test_persistent_start_reservation_uses_authenticated_session_run_and_epoch(tmp_path, monkeypatch):
    store = _store(tmp_path / "bound-start.sqlite3")
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-authenticated", emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    context = _context("session-authenticated", "run-authoritative", 23, request_id)
    request = EvolutionRequest("preserve the current behavior")

    with provider.bind(context, lambda: True):
        first = integration.start(request, request_digest="a" * 64)

    assert first["status"] == "started"
    assert len(host.starts) == 1
    assert store.reserve_start_request(
        "session-authenticated", request_id, "a" * 64,
        run_id="run-authoritative", turn_epoch=23,
    ).result == _RESULT
    with pytest.raises(StoreOwnershipError):
        store.reserve_start_request(
            "session-authenticated", request_id, "a" * 64,
            run_id="renderer-forged-run", turn_epoch=23,
        )
    with pytest.raises(StoreOwnershipError):
        store.reserve_start_request(
            "session-authenticated", request_id, "a" * 64,
            run_id="run-authoritative", turn_epoch=24,
        )


def test_second_context_provider_failure_does_not_reject_a_concurrently_consumed_claim(tmp_path, monkeypatch):
    store = _store(tmp_path / "context-recheck-failure.sqlite3")
    original_reserve = store.reserve_start_request
    owned_claims = []

    def capture_owned_claim(*args, **kwargs):
        claim = original_reserve(*args, **kwargs)
        if claim.status == "claimed" and claim.claim_token:
            owned_claims.append(claim)
        return claim

    monkeypatch.setattr(store, "reserve_start_request", capture_owned_claim)
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    calls = 0

    def flaky_provider():
        nonlocal calls
        calls += 1
        if calls == 2:
            store.consume_start_request_claim(
                owned_claims[0], session_id="session-context-recheck",
                run_id="run-context-recheck", turn_epoch=7,
            )
            raise RuntimeError("context provider failed during reservation recheck")
        return provider()

    host._context_provider = flaky_provider
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-context-recheck", emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    context = _context("session-context-recheck", "run-context-recheck", 7, request_id)

    with provider.bind(context, lambda: True):
        result = integration.start(EvolutionRequest("do not dispatch without a rechecked context"),
                                   request_digest="c" * 64)

    assert result == {"status": "unknown", "error_code": "start_claim_already_consumed"}
    with store._connection() as db:
        persisted = db.execute(
            "SELECT state, result_json FROM start_requests WHERE session_id=? AND request_id=?",
            ("session-context-recheck", request_id),
        ).fetchone()
    assert persisted["state"] == "dispatching"
    assert persisted["result_json"] is None

    next_context = _context("session-context-recheck", "run-context-recheck-2", 8, str(uuid.uuid4()))
    with provider.bind(next_context, lambda: True):
        next_result = integration.start(EvolutionRequest("allow a fresh start after definite denial"),
                                        request_digest="f" * 64)
    assert next_result == {"status": "denied", "error_code": "start_busy"}
    assert host.starts == []


def test_context_recheck_failure_rejects_before_dispatch_and_allows_new_request(tmp_path, monkeypatch):
    store = _store(tmp_path / "context-recheck-denied.sqlite3")
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    calls = 0

    def flaky_provider():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("context provider failed during reservation recheck")
        return provider()

    host._context_provider = flaky_provider
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-context-denied", emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    context = _context("session-context-denied", "run-context-denied", 17, request_id)

    with provider.bind(context, lambda: True):
        result = integration.start(EvolutionRequest("deny before host dispatch"), request_digest="a" * 64)

    assert result == {"status": "denied", "error_code": "start_context_recheck_failed"}
    assert host.starts == []
    with store._connection() as db:
        persisted = db.execute(
            "SELECT state, result_json FROM start_requests WHERE session_id=? AND request_id=?",
            ("session-context-denied", request_id),
        ).fetchone()
    assert persisted["state"] == "rejected"

    next_context = _context("session-context-denied", "run-context-denied-2", 18, str(uuid.uuid4()))
    with provider.bind(next_context, lambda: True):
        next_result = integration.start(EvolutionRequest("new request after safe rejection"),
                                        request_digest="b" * 64)
    assert next_result == _RESULT
    assert len(host.starts) == 1


def test_context_recheck_finish_and_unknown_failures_keep_claim_reserved(tmp_path, monkeypatch):
    store = _store(tmp_path / "context-recheck-persistence-failure.sqlite3")
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    calls = 0

    def flaky_provider():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("context provider failed during reservation recheck")
        return provider()

    host._context_provider = flaky_provider

    persistence_attempts = []

    def fail_finish(_claim, _result):
        persistence_attempts.append("finish")
        raise OSError("sqlite unavailable")

    def fail_unknown(_claim, _result):
        persistence_attempts.append("unknown")
        raise OSError("sqlite unavailable")

    monkeypatch.setattr(store, "reject_start_request_after_dispatch", fail_finish)
    monkeypatch.setattr(store, "mark_unconsumed_start_request_unknown", fail_unknown)
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-persist-failure", emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    context = _context("session-persist-failure", "run-persist-failure", 8, request_id)

    with provider.bind(context, lambda: True):
        result = integration.start(EvolutionRequest("preserve the unresolved claim"),
                                   request_digest="d" * 64)

    assert result == {"status": "unknown", "error_code": "reservation_finalize_unconfirmed"}
    assert persistence_attempts == ["finish", "unknown"]
    assert host.starts == []
    still_reserved = store.reserve_start_request(
        "session-persist-failure", request_id, "d" * 64,
        run_id="run-persist-failure", turn_epoch=8,
    )
    assert still_reserved.status == "in_progress"
    assert still_reserved.claim_token is None
    assert still_reserved.result is None

    next_context = _context("session-persist-failure", "run-persist-failure-2", 9, str(uuid.uuid4()))
    with provider.bind(next_context, lambda: True):
        next_result = integration.start(EvolutionRequest("do not release an uncertain reservation"),
                                        request_digest="9" * 64)
    assert next_result == {"status": "denied", "error_code": "start_busy"}
    assert host.starts == []


def test_context_recheck_finish_failure_persists_unknown_before_failing_closed(tmp_path, monkeypatch):
    store = _store(tmp_path / "context-recheck-unknown-fallback.sqlite3")
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    calls = 0

    def flaky_provider():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("context provider failed during reservation recheck")
        return provider()

    def fail_rejection(_claim, _result):
        raise OSError("rejection commit outcome is uncertain")

    host._context_provider = flaky_provider
    monkeypatch.setattr(store, "reject_start_request_after_dispatch", fail_rejection)
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-unknown-fallback", emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    context = _context("session-unknown-fallback", "run-unknown-fallback", 19, request_id)

    with provider.bind(context, lambda: True):
        result = integration.start(EvolutionRequest("persist an unknown pre-dispatch outcome"),
                                   request_digest="c" * 64)

    assert result == {"status": "unknown", "error_code": "reservation_finalize_unconfirmed"}
    assert host.starts == []
    replay = store.reserve_start_request(
        "session-unknown-fallback", request_id, "c" * 64,
        run_id="run-unknown-fallback", turn_epoch=19,
    )
    assert replay.status == "unknown"
    assert replay.result == result

    next_context = _context("session-unknown-fallback", "run-unknown-fallback-2", 20, str(uuid.uuid4()))
    with provider.bind(next_context, lambda: True):
        next_result = integration.start(EvolutionRequest("unknown still holds global admission"),
                                        request_digest="e" * 64)
    assert next_result == {"status": "denied", "error_code": "start_busy"}
    assert host.starts == []


def test_rejection_commit_response_loss_replays_persisted_rejection_without_overwrite(tmp_path, monkeypatch):
    store = _store(tmp_path / "rejection-response-lost.sqlite3")
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    calls = 0
    original_reject = store.reject_start_request_after_dispatch
    reject_calls = 0

    def flaky_provider():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("context provider failed during reservation recheck")
        return provider()

    def commit_then_lose_response(claim, result):
        nonlocal reject_calls
        reject_calls += 1
        original_reject(claim, result)
        raise OSError("rejection committed but acknowledgement was lost")

    host._context_provider = flaky_provider
    monkeypatch.setattr(store, "reject_start_request_after_dispatch", commit_then_lose_response)
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-rejection-response-lost", emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    request = EvolutionRequest("replay the committed pre-dispatch rejection")
    context = _context("session-rejection-response-lost", "run-rejection-response-lost", 21, request_id)

    with provider.bind(context, lambda: True):
        first = integration.start(request, request_digest="a" * 64)
        replay = integration.start(request, request_digest="a" * 64)

    denied = {"status": "denied", "error_code": "start_context_recheck_failed"}
    assert first == {"status": "unknown", "error_code": "reservation_finalize_unconfirmed"}
    assert replay == denied
    assert reject_calls == 1
    assert host.starts == []
    persisted = store.reserve_start_request(
        "session-rejection-response-lost", request_id, "a" * 64,
        run_id="run-rejection-response-lost", turn_epoch=21,
    )
    assert persisted.status == "rejected"
    assert persisted.result == denied

    next_context = _context("session-rejection-response-lost", "run-rejection-response-lost-2", 22,
                            str(uuid.uuid4()))
    with provider.bind(next_context, lambda: True):
        next_result = integration.start(EvolutionRequest("reserve after stable rejected replay"),
                                        request_digest="b" * 64)
    assert next_result == _RESULT
    assert len(host.starts) == 1


def test_context_recheck_does_not_swallow_process_interrupt(tmp_path, monkeypatch):
    store = _store(tmp_path / "context-recheck-interrupt.sqlite3")
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    calls = 0

    def interrupting_provider():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return provider()

    host._context_provider = interrupting_provider
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: "session-interrupt", emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    context = _context("session-interrupt", "run-interrupt", 9, request_id)

    with provider.bind(context, lambda: True), pytest.raises(KeyboardInterrupt):
        integration.start(EvolutionRequest("preserve process interrupts"), request_digest="e" * 64)

    assert host.starts == []
    still_reserved = store.reserve_start_request(
        "session-interrupt", request_id, "e" * 64,
        run_id="run-interrupt", turn_epoch=9,
    )
    assert still_reserved.status == "in_progress"


def test_concurrent_consume_and_pre_dispatch_reject_preserve_winner_state(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    for index in range(4):
        store = _store(tmp_path / f"consume-reject-race-{index}.sqlite3")
        session_id = f"session-race-{index}"
        run_id = f"run-race-{index}"
        request_id = str(uuid.uuid4())
        claim = store.reserve_start_request(
            session_id, request_id, f"{index:064x}", run_id=run_id, turn_epoch=index,
        )
        barrier = threading.Barrier(3)

        def consume():
            barrier.wait()
            try:
                store.consume_start_request_claim(
                    claim, session_id=session_id, run_id=run_id, turn_epoch=index,
                )
                return "consumed"
            except StoreOwnershipError:
                return "lost"

        def reject():
            barrier.wait()
            try:
                store.reject_start_request_after_dispatch(
                    claim, {"status": "denied", "error_code": "context_recheck_failed"},
                )
                return "rejected"
            except StoreOwnershipError:
                return "lost"

        with ThreadPoolExecutor(max_workers=2) as pool:
            consume_future = pool.submit(consume)
            reject_future = pool.submit(reject)
            barrier.wait()
            outcomes = {consume_future.result(timeout=5), reject_future.result(timeout=5)}

        assert len(outcomes) == 2
        assert "lost" in outcomes
        with store._connection() as db:
            row = db.execute(
                "SELECT state, result_json FROM start_requests WHERE session_id=? AND request_id=?",
                (session_id, request_id),
            ).fetchone()
        if "consumed" in outcomes:
            assert row["state"] == "dispatching"
            assert row["result_json"] is None
        else:
            assert "rejected" in outcomes
            assert row["state"] == "rejected"


def test_bound_start_replay_is_idempotent_but_cannot_cross_epoch_or_session(tmp_path, monkeypatch):
    store = _store(tmp_path / "replay-bound-start.sqlite3")
    provider = HostRunContextProvider()
    host = _Host(store, provider)
    current_session = {"id": "session-one"}
    integration = SelfEvolutionChatIntegration(
        host=host, session_provider=lambda: current_session["id"], emit=lambda _: None,
    )
    monkeypatch.setattr(integration, "_trusted_production_host", lambda: host)
    request_id = str(uuid.uuid4())
    request = EvolutionRequest("keep the request replay-safe")
    original = _context("session-one", "run-one", 31, request_id)

    with provider.bind(original, lambda: True):
        first = integration.start(request, request_digest="b" * 64)
        same_turn_replay = integration.start(request, request_digest="b" * 64)

    assert first == _RESULT
    assert same_turn_replay == first
    assert len(host.starts) == 1

    stale_epoch = _context("session-one", "run-one", 32, request_id)
    with provider.bind(stale_epoch, lambda: True):
        cross_epoch_replay = integration.start(request, request_digest="b" * 64)
    assert cross_epoch_replay["status"] == "unavailable"
    assert len(host.starts) == 1

    # Request UUIDs are scoped by authenticated session, so another session's
    # identical request ID is independent rather than a false replay collision.
    current_session["id"] = "session-two"
    other_session = _context("session-two", "run-two", 1, request_id)
    with provider.bind(other_session, lambda: True):
        independent_session_start = integration.start(request, request_digest="b" * 64)
    assert independent_session_start == _RESULT
    assert len(host.starts) == 2
    assert host.starts[1][1].session_id == "session-two"
