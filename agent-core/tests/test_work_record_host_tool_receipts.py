from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController
from evolution.context import normalize_host_run_id


RAW_RUN_ID = "44444444444444448444444444444444"
CANONICAL_RUN_ID = normalize_host_run_id(RAW_RUN_ID)


class Fixture:
    def __init__(self, tmp_path, run_id=RAW_RUN_ID):
        self.session_id = "session-host-tool"
        session_dir = tmp_path / "data" / "sessions" / self.session_id
        session_dir.mkdir(parents=True)
        paths = AgentPaths(agent_root=tmp_path, workspace=tmp_path / "workspace",
                           data=tmp_path / "data", evolve=tmp_path / "evolve")
        self.session = Session(conversation_id=self.session_id, session_dir=session_dir,
                               goal="", meta=SessionMeta(), messages=[], paths=paths)
        self.store = WorkRecordStore(self.session)
        item = self.store.create_item(title="host tool run", scope={"kind": "independent"})
        requirement = self.store.add_requirement(item["item_id"], "inspect host facts", event_id="req")
        self.item_id = item["item_id"]
        self.requirement_id = requirement["requirement_id"]
        self.binding = BoundExecution(self.session_id, self.item_id, self.requirement_id, run_id)
        self.store.begin_requirement_run(self.item_id, self.requirement_id,
                                         run_id=self.binding.run_id, expected_current_run_id=None)

    def receipt(self, **extra):
        return ExecutionReceipt(
            session_id=self.binding.session_id,
            item_id=self.binding.item_id,
            requirement_id=self.binding.requirement_id,
            run_id=self.binding.run_id,
            sequence=1,
            outcome="succeeded",
            **extra,
        )


def fact(*, host_run_id=CANONICAL_RUN_ID, call_id=None, tool="read_file", ok=True):
    return {"host_run_id": host_run_id,
            "call_id": call_id or str(uuid.uuid4()),
            "tool": tool,
            "ok": ok}


def settle(fixture, **extra):
    return WorkRecordSettlementAdapter(fixture.store, fixture.binding).settle(fixture.receipt(**extra))


def test_legacy_receipt_defaults_to_empty_host_tool_sequence_and_persists(tmp_path):
    f = Fixture(tmp_path)
    result = settle(f)
    loaded = WorkRecordStore(f.session).get_item(f.item_id)
    run = loaded["runs"][0]
    assert result.status == "recorded"
    assert run["verification"]["host_execution"] == {"sequence": 1}
    assert loaded["requirements"][0]["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"


def test_valid_host_tool_receipts_are_projected_without_extra_data_and_read_back(tmp_path):
    f = Fixture(tmp_path)
    call_id = str(uuid.uuid4())
    privateish = {"host_run_id": CANONICAL_RUN_ID, "call_id": call_id,
                  "tool": "read_file", "ok": False,
                  "path": "C:/private", "arguments": {"secret": "x"}, "output": "hidden"}
    # Extra keys are invalid, even when they could otherwise be silently stripped.
    assert settle(f, host_tool_receipts=(privateish,), omitted_count=2).status == "rejected"
    assert f.store.get_item(f.item_id)["runs"] == []

    result = settle(f, host_tool_receipts=(fact(call_id=call_id, ok=False),), omitted_count=2)
    loaded = WorkRecordStore(f.session).get_item(f.item_id)
    run = loaded["runs"][0]
    assert result.status == "recorded"
    assert run["verification"]["host_execution"] == {
        "sequence": 1,
        "host_tool_receipts": [{"host_run_id": CANONICAL_RUN_ID, "call_id": call_id,
                                 "tool": "read_file", "ok": False}],
        "omitted_count": 2,
    }
    assert loaded["requirements"][0]["status"] == "unknown"
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert "C:/private" not in str(loaded) and "hidden" not in str(loaded)


@pytest.mark.parametrize("bad_fact", [
    {"host_run_id": "other-run", "call_id": "123e4567-e89b-12d3-a456-426614174000", "tool": "read_file", "ok": True},
    {"host_run_id": CANONICAL_RUN_ID, "call_id": "not-a-uuid", "tool": "read_file", "ok": True},
    {"host_run_id": CANONICAL_RUN_ID, "call_id": "123e4567e89b12d3a456426614174000", "tool": "read_file", "ok": True},
    {"host_run_id": CANONICAL_RUN_ID, "call_id": "123e4567-e89b-12d3-a456-426614174000", "tool": "../read_file", "ok": True},
    {"host_run_id": CANONICAL_RUN_ID, "call_id": "123e4567-e89b-12d3-a456-426614174000", "tool": "ReadFile", "ok": True},
    {"host_run_id": CANONICAL_RUN_ID, "call_id": "123e4567-e89b-12d3-a456-426614174000", "tool": "read_file", "ok": 1},
    {"host_run_id": CANONICAL_RUN_ID, "call_id": "123e4567-e89b-12d3-a456-426614174000", "tool": "read_file", "ok": True, "output": "model text"},
])
def test_any_invalid_host_tool_fact_fails_closed_without_writing(tmp_path, bad_fact):
    f = Fixture(tmp_path)
    result = settle(f, host_tool_receipts=(fact(), bad_fact), omitted_count=0)
    assert result.status == "rejected"
    assert result.retry_execution is False
    assert f.store.get_item(f.item_id)["runs"] == []


@pytest.mark.parametrize("omitted_count", [-1, True, 1.5, "2"])
def test_omitted_count_must_be_a_nonnegative_exact_integer(tmp_path, omitted_count):
    f = Fixture(tmp_path)
    result = settle(f, host_tool_receipts=(), omitted_count=omitted_count)
    assert result.status == "rejected"
    assert f.store.get_item(f.item_id)["runs"] == []


def test_host_tool_sequence_is_limited_to_twenty_and_accepts_twenty(tmp_path):
    f = Fixture(tmp_path)
    twenty = tuple(fact() for _ in range(20))
    assert settle(f, host_tool_receipts=twenty, omitted_count=7).status == "recorded"
    stored = f.store.get_item(f.item_id)["runs"][0]["verification"]["host_execution"]
    assert len(stored["host_tool_receipts"]) == 20
    assert stored["omitted_count"] == 7

    g = Fixture(tmp_path / "second")
    too_many = tuple(fact() for _ in range(21))
    result = settle(g, host_tool_receipts=too_many, omitted_count=0)
    assert result.status == "rejected"
    assert g.store.get_item(g.item_id)["runs"] == []


def test_duplicate_call_ids_reject_entire_receipt(tmp_path):
    f = Fixture(tmp_path)
    duplicate = "123e4567-e89b-12d3-a456-426614174000"
    upper_duplicate = duplicate.upper()
    result = settle(f, host_tool_receipts=(fact(call_id=duplicate), fact(call_id=upper_duplicate)), omitted_count=0)
    assert result.status == "rejected"
    assert f.store.get_item(f.item_id)["runs"] == []


def test_same_host_tool_facts_are_idempotent_and_changed_facts_conflict(tmp_path):
    f = Fixture(tmp_path)
    fact1 = fact(call_id="123e4567-e89b-12d3-a456-426614174000")
    adapter = WorkRecordSettlementAdapter(f.store, f.binding)
    first = adapter.settle(f.receipt(host_tool_receipts=(fact1,), omitted_count=1))
    same = adapter.settle(f.receipt(host_tool_receipts=(fact1,), omitted_count=1))
    changed = adapter.settle(f.receipt(host_tool_receipts=(fact1,), omitted_count=2))
    assert first.status == same.status == "recorded"
    assert first.result_id == same.result_id
    assert changed.status == "conflict"
    assert changed.retry_execution is False
    assert len(f.store.get_item(f.item_id)["runs"]) == 1


def test_host_tool_receipt_container_must_be_a_sequence(tmp_path):
    f = Fixture(tmp_path)
    for invalid in (None, "not-a-sequence", {"call": fact()}):
        result = settle(f, host_tool_receipts=invalid, omitted_count=0)
        assert result.status == "rejected"
        assert f.store.get_item(f.item_id)["runs"] == []


def test_uppercase_canonical_uuid_is_normalized_for_storage(tmp_path):
    f = Fixture(tmp_path)
    call_id = "123e4567-e89b-12d3-a456-426614174000"
    result = settle(f, host_tool_receipts=(fact(call_id=call_id.upper()),), omitted_count=0)
    assert result.status == "recorded"
    stored = f.store.get_item(f.item_id)["runs"][0]["verification"]["host_execution"]
    assert stored["host_tool_receipts"][0]["call_id"] == call_id


def test_hex_lifecycle_run_id_accepts_equivalent_executor_uuid_and_keeps_raw_cas_identity(tmp_path):
    raw_run_id = RAW_RUN_ID
    canonical_run_id = CANONICAL_RUN_ID
    assert canonical_run_id and canonical_run_id != raw_run_id
    f = Fixture(tmp_path, run_id=raw_run_id)
    call_id = "123e4567-e89b-12d3-a456-426614174000"

    result = settle(f, host_tool_receipts=(fact(host_run_id=canonical_run_id, call_id=call_id),), omitted_count=0)

    loaded = WorkRecordStore(f.session).get_item(f.item_id)
    run = loaded["runs"][0]
    host_execution = run["verification"]["host_execution"]
    assert result.status == "recorded"
    assert run["run_id"] == raw_run_id
    assert host_execution["host_tool_receipts"] == [{
        "host_run_id": canonical_run_id,
        "call_id": call_id,
        "tool": "read_file",
        "ok": True,
    }]
    projection = WorkRecordExperimentController._format_recall([loaded])
    assert call_id in projection and "read_file" in projection


@pytest.mark.parametrize("host_run_id", [
    "not-a-run-id",
    "4444444444444444844444444444444A",
    "55555555-5555-4555-8555-555555555555",
])
def test_hex_lifecycle_run_id_rejects_invalid_or_non_equivalent_host_identity(tmp_path, host_run_id):
    raw_run_id = RAW_RUN_ID
    f = Fixture(tmp_path, run_id=raw_run_id)
    result = settle(f, host_tool_receipts=(fact(host_run_id=host_run_id),), omitted_count=0)
    assert result.status == "rejected"
    assert result.retry_execution is False
    assert f.store.get_item(f.item_id)["runs"] == []


def test_omitted_tool_receipts_require_a_normalizable_bound_run_identity(tmp_path):
    f = Fixture(tmp_path, run_id="run-host-1")
    result = settle(f, host_tool_receipts=(), omitted_count=1)
    assert result.status == "rejected"
    assert f.store.get_item(f.item_id)["runs"] == []
