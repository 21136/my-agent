from __future__ import annotations

import concurrent.futures
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import work_record_post_turn_confirmation as confirmation
from paths import AgentPaths
from session import Session, SessionMeta


def _session(root: Path, conversation_id: str = "post-turn-session") -> Session:
    root.mkdir(parents=True, exist_ok=True)
    paths = AgentPaths(
        agent_root=root,
        workspace=root / "workspace",
        data=root / "data",
        evolve=root / "evolve",
    )
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return Session(conversation_id, session_dir, "", SessionMeta(), [], paths)


def _binding(**overrides):
    now = datetime(2026, 10, 31, 3, 0, tzinfo=UTC)
    value = {
        "confirmation_id": str(uuid.uuid4()),
        "request_id": str(uuid.uuid4()),
        "trusted_host_run_id": "host-run-verified-17",
        "purpose": "post_turn_record",
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
        "now": now,
    }
    value.update(overrides)
    return value


def test_reopen_invalidates_pending_and_persists_audit_only_state(tmp_path, monkeypatch):
    monkeypatch.setattr(confirmation, "_PROCESS_EPOCH", "epoch-one")
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    binding = _binding()
    created = store.create_pending(**binding)

    monkeypatch.setattr(confirmation, "_PROCESS_EPOCH", "epoch-two")
    reopened = confirmation.PostTurnConfirmationStore(session)
    rows = reopened.list_records()

    assert rows[0]["confirmation_id"] == created["confirmation_id"]
    assert rows[0]["status"] == "unknown"
    with pytest.raises(confirmation.PostTurnConfirmationReplayError):
        reopened.decide(binding["confirmation_id"], "accepted", now=binding["now"])
    raw = (session.session_dir / confirmation._STATE_FILENAME).read_text(encoding="utf-8")
    assert "candidate summary" not in raw
    assert "outbound body" not in raw
    assert "credential" not in raw
    assert set(rows[0]) == {
        "confirmation_id", "conversation_id", "request_id", "run_id", "purpose",
        "deadline_at", "status", "created_at", "decision", "decided_at",
    }


def test_same_binding_is_idempotent_but_same_id_different_binding_is_rejected(tmp_path):
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    binding = _binding()
    first = store.create_pending(**binding)
    second = store.create_pending(**binding)
    assert first == second

    changed = dict(binding, purpose="different_purpose")
    with pytest.raises(confirmation.PostTurnConfirmationConflict):
        store.create_pending(**changed)


def test_duplicate_clicks_apply_one_terminal_decision_under_concurrency(tmp_path):
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    binding = _binding()
    store.create_pending(**binding)

    def click(_):
        return store.decide(binding["confirmation_id"], "rejected", now=binding["now"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(click, range(24)))
    assert {row["status"] for row in results} == {"rejected"}
    assert len(store.list_records()) == 1

    with pytest.raises(confirmation.PostTurnConfirmationConflict):
        store.decide(binding["confirmation_id"], "accepted", now=binding["now"])


def test_cancel_and_reject_remain_terminal_audit_states(tmp_path, monkeypatch):
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    rejected = _binding()
    cancelled = _binding()
    store.create_pending(**rejected)
    store.create_pending(**cancelled)

    store.decide(rejected["confirmation_id"], "rejected", now=rejected["now"])
    store.decide(cancelled["confirmation_id"], "cancelled", now=cancelled["now"])
    assert {row["status"] for row in store.list_records()} == {"rejected", "cancelled"}
    monkeypatch.setattr(confirmation, "_PROCESS_EPOCH", "new-audit-process")
    reopened = confirmation.PostTurnConfirmationStore(session)
    assert {row["status"] for row in reopened.list_records()} == {"rejected", "cancelled"}


def test_expired_or_late_decision_is_never_accepted(tmp_path):
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    binding = _binding(deadline_at="2026-10-31T03:00:01+00:00")
    store.create_pending(**binding)

    late = binding["now"] + timedelta(seconds=1)
    with pytest.raises(confirmation.PostTurnConfirmationExpired):
        store.decide(binding["confirmation_id"], "accepted", now=late)
    assert store.list_records()[0]["status"] == "expired"

    with pytest.raises(confirmation.PostTurnConfirmationExpired):
        store.create_pending(**_binding(deadline_at="2026-10-31T02:59:59+00:00"))


def test_cross_session_store_cannot_reuse_another_sessions_confirmation(tmp_path):
    first = _session(tmp_path / "agent", "session-one")
    second = _session(tmp_path / "agent", "session-two")
    store_one = confirmation.PostTurnConfirmationStore(first)
    binding = _binding()
    store_one.create_pending(**binding)

    store_two = confirmation.PostTurnConfirmationStore(second)
    with pytest.raises(confirmation.PostTurnConfirmationNotFound):
        store_two.decide(binding["confirmation_id"], "accepted", now=binding["now"])


def test_disk_write_failure_does_not_claim_a_decision(tmp_path, monkeypatch):
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    binding = _binding()
    store.create_pending(**binding)

    def fail_write(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(confirmation, "atomic_write_text", fail_write)
    with pytest.raises(confirmation.PostTurnConfirmationWriteError):
        store.decide(binding["confirmation_id"], "accepted", now=binding["now"])
    monkeypatch.undo()
    assert store.list_records()[0]["status"] == "pending"


def test_sidecar_path_type_is_checked_before_any_write(tmp_path):
    session = _session(tmp_path / "agent")
    sidecar = session.session_dir / confirmation._STATE_FILENAME
    sidecar.mkdir()
    store = confirmation.PostTurnConfirmationStore(session)

    with pytest.raises(confirmation.PostTurnConfirmationCorrupt):
        store.create_pending(**_binding())
    assert sidecar.is_dir()
    assert not list(session.session_dir.glob(".post-turn-confirmation.*.tmp"))

def test_conflicting_concurrent_clicks_have_exactly_one_winner(tmp_path):
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    binding = _binding()
    store.create_pending(**binding)

    def click(decision):
        try:
            return store.decide(binding["confirmation_id"], decision, now=binding["now"])["status"]
        except confirmation.PostTurnConfirmationConflict:
            return "conflict"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(click, ("accepted", "rejected")))
    assert sorted(results) == ["conflict", "rejected"] or sorted(results) == ["accepted", "conflict"]
    assert store.list_records()[0]["status"] in {"accepted", "rejected"}

def test_repeating_create_without_fixed_clock_is_idempotent(tmp_path):
    session = _session(tmp_path / "agent")
    store = confirmation.PostTurnConfirmationStore(session)
    actual_now = datetime.now(UTC)
    binding = _binding(deadline_at=(actual_now + timedelta(minutes=5)).isoformat())
    binding.pop("now")
    first = store.create_pending(**binding)
    second = store.create_pending(**binding)
    assert first == second


def test_session_path_must_remain_inside_its_agent_root(tmp_path):
    agent = _session(tmp_path / "agent", "expected-session")
    outside = tmp_path / "outside" / "expected-session"
    outside.mkdir(parents=True)
    forged = Session(
        agent.conversation_id, outside, "", SessionMeta(), [], agent.paths
    )
    with pytest.raises(confirmation.PostTurnConfirmationScopeError):
        confirmation.PostTurnConfirmationStore(forged)
