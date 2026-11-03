from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_record_settlement import (
    BoundExecution, ExecutionReceipt, VerificationReceipt, WorkRecordSettlementAdapter,
)
from work_records import WorkRecordConflict, WorkRecordStore


class StoreFixture:
    def __init__(self, tmp_path, session_id="session-1"):
        session_dir = tmp_path / "data" / "sessions" / session_id
        session_dir.mkdir(parents=True)
        paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                           data=tmp_path / "data", evolve=tmp_path / "evolve")
        self.session = Session(conversation_id=session_id, session_dir=session_dir,
                               goal="", meta=SessionMeta(), messages=[], paths=paths)
        self.store = WorkRecordStore(self.session)
        item = self.store.create_item(title="local run", scope={"kind": "independent"})
        req = self.store.add_requirement(item["item_id"], "check result", event_id="req-event")
        self.item_id, self.requirement_id = item["item_id"], req["requirement_id"]
        self.store.begin_requirement_run(self.item_id, self.requirement_id,
                                         run_id="run-1", expected_current_run_id=None)


def binding(fixture, run="run-1"):
    return BoundExecution(fixture.session.conversation_id, fixture.item_id,
                          fixture.requirement_id, run)


def receipt(fixture, *, sequence=2, outcome="failed", finish_reason=None, run="run-1"):
    b = binding(fixture, run)
    return ExecutionReceipt(
        session_id=b.session_id, item_id=b.item_id, requirement_id=b.requirement_id,
        run_id=b.run_id, sequence=sequence, outcome=outcome,
        **({} if finish_reason is None else {"finish_reason": finish_reason}),
    )


def verification(fixture, status="passed"):
    b = binding(fixture)
    return VerificationReceipt(
        session_id=b.session_id, item_id=b.item_id, requirement_id=b.requirement_id,
        run_id=b.run_id, status=status,
        evidence_refs=("private://must-not-persist",),
    )


@pytest.mark.parametrize("finish_reason", [
    "completed", "cancelled", "timeout", "error", "paused", "unknown",
])
def test_safe_host_finish_reason_and_sequence_persist_without_claiming_requirement_success(tmp_path, finish_reason):
    f = StoreFixture(tmp_path)
    result = WorkRecordSettlementAdapter(f.store, binding(f)).settle(
        receipt(f, finish_reason=finish_reason), verification(f, "passed"),
    )
    assert result.status == "recorded"
    loaded = WorkRecordStore(f.session).get_item(f.item_id)
    req = loaded["requirements"][0]
    run = loaded["runs"][0]
    assert req["status"] == "unknown"
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"] == {
        "sequence": 2, "finish_reason": finish_reason,
    }
    assert "private://must-not-persist" not in str(loaded)
    assert run["result_summary"] == "Structured host execution receipt recorded; requirement outcome remains unknown."


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled", "timeout", "exception"])
def test_legacy_outcome_labels_never_become_requirement_or_failure_facts(tmp_path, outcome):
    f = StoreFixture(tmp_path)
    result = WorkRecordSettlementAdapter(f.store, binding(f)).settle(
        receipt(f, outcome=outcome, finish_reason="unknown"),
    )
    assert result.status == "recorded"
    run = f.store.get_item(f.item_id)["runs"][0]
    assert run["status"] == "unknown"
    assert run["verification"]["host_execution"] == {
        "sequence": 2, "finish_reason": "unknown",
    }
    assert "outcome" not in run["verification"]["host_execution"]
    assert run["verification"]["status"] == "not_verified"


def test_legacy_receipt_without_finish_reason_persists_sequence_only(tmp_path):
    f = StoreFixture(tmp_path)
    result = WorkRecordSettlementAdapter(f.store, binding(f)).settle(receipt(f, outcome="succeeded"))
    assert result.status == "recorded"
    run = f.store.get_item(f.item_id)["runs"][0]
    assert run["verification"]["host_execution"] == {"sequence": 2}
    assert run["status"] == "unknown"


def test_bool_sequence_and_unsafe_finish_reason_are_rejected(tmp_path):
    f = StoreFixture(tmp_path)
    adapter = WorkRecordSettlementAdapter(f.store, binding(f))
    assert adapter.settle(receipt(f, sequence=True, finish_reason="completed")).status == "rejected"
    assert adapter.settle(receipt(f, finish_reason="model said done; private text")).status == "rejected"
    assert f.store.get_item(f.item_id)["runs"] == []


def test_same_receipt_is_idempotent_but_different_sequence_or_reason_conflicts(tmp_path):
    f = StoreFixture(tmp_path)
    adapter = WorkRecordSettlementAdapter(f.store, binding(f))
    first = adapter.settle(receipt(f, finish_reason="paused"))
    same = adapter.settle(receipt(f, finish_reason="paused"))
    different_sequence = adapter.settle(receipt(f, sequence=3, finish_reason="paused"))
    different_reason = adapter.settle(receipt(f, finish_reason="unknown"))
    assert first.status == same.status == "recorded"
    assert first.result_id == same.result_id
    assert different_sequence.status == different_reason.status == "conflict"
    assert len(f.store.get_item(f.item_id)["runs"]) == 1


def test_mismatched_run_and_invalid_outcome_do_not_write(tmp_path):
    f = StoreFixture(tmp_path)
    adapter = WorkRecordSettlementAdapter(f.store, binding(f))
    assert adapter.settle(receipt(f, run="other-run", finish_reason="completed")).status == "rejected"
    assert adapter.settle(receipt(f, outcome="arbitrary text", finish_reason="completed")).status == "rejected"
    assert f.store.get_item(f.item_id)["runs"] == []


def test_storage_error_does_not_retry_execution(tmp_path, monkeypatch):
    f = StoreFixture(tmp_path)
    calls = 0
    original = f.store.record_result_if_current_run
    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OSError("storage unavailable")
    monkeypatch.setattr(f.store, "record_result_if_current_run", fail_once)
    result = WorkRecordSettlementAdapter(f.store, binding(f)).settle(
        receipt(f, finish_reason="error"), verification(f),
    )
    assert result.status == "executed_not_recorded"
    assert result.retry_execution is False
    assert calls == 1
    monkeypatch.setattr(f.store, "record_result_if_current_run", original)
    assert f.store.get_item(f.item_id)["runs"] == []
