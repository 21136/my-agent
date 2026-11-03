from __future__ import annotations

import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordConflict, WorkRecordStore
from work_records_service import (
    WorkRecordCaptureService,
    WorkRecordResultEvidence,
    WorkRecordTerminalRunReceipt,
)


class FakeProvider:
    paths = None

    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return (
            '{"action":"record","kind":"requirement","title":"Synthetic work item",'
            '"requirement":"Synthetic requirement","scope":"independent","item_id":null}'
        )


def make_session(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / "terminal-capture-session"
    session_dir.mkdir(parents=True)
    paths.workspace.mkdir()
    paths.evolve.mkdir()
    return Session("terminal-capture-session", session_dir, "", SessionMeta(), [], paths)


def install_model_registry(monkeypatch):
    monkeypatch.setattr(
        "work_records_service.get_registry",
        lambda *_a, **_k: type("Registry", (), {
            "get": lambda _self, _id: type("Model", (), {
                "id": "0x567-flash", "vendor": "0x567",
                "provider_model": "gpt-6-luna", "max_input_tokens": 96000,
            })()
        })(),
    )


def yes():
    return "yes", str(uuid.uuid4())


def result_evidence_preview(preview):
    return json.loads(preview.split("\n", 2)[2].rsplit("\n", 1)[0])


def make_receipt(session, request_id, run_id=None, **overrides):
    fields = {
        "session_id": session.conversation_id,
        "request_id": request_id,
        "run_id": run_id or str(uuid.uuid4()),
        "terminal_state": "settled",
        "sequence": 17,
        "finish_reason": "completed",
    }
    fields.update(overrides)
    return WorkRecordTerminalRunReceipt(**fields)


def prepare_capture(tmp_path, monkeypatch, *, guard_factory=None, valid=None):
    session = make_session(tmp_path)
    provider = FakeProvider()
    prompts = []
    valid = valid if valid is not None else {"value": True}
    service = WorkRecordCaptureService(
        session=session,
        client=provider,
        confirm=lambda preview: (prompts.append(preview) or yes()),
        host_binding_is_valid=lambda: valid["value"],
        commit_critical_section=guard_factory or (lambda: _empty_guard()),
    )
    install_model_registry(monkeypatch)
    request_id = str(uuid.uuid4())
    result = service.capture(
        "Record this synthetic requirement",
        request_id=request_id,
        track_run=True,
        defer_commit_until_terminal=True,
    )
    return session, provider, prompts, service, request_id, result


@contextmanager
def _empty_guard():
    yield


def test_deferred_capture_commits_item_requirement_and_terminal_unknown_run_together(tmp_path, monkeypatch):
    session, provider, prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    store = WorkRecordStore(session)

    assert prepared.status == "awaiting_host_run"
    assert prepared.prepared_capture is not None
    assert len(provider.calls) == 1 and len(prompts) == 2
    assert store.list_items() == []  # no half-created item before host termination

    receipt = make_receipt(session, request_id)
    committed = service.commit_prepared_capture(prepared.prepared_capture, receipt=receipt)

    assert committed.status == "recorded"
    item = WorkRecordStore(Session(
        session.conversation_id, session.session_dir, "", SessionMeta(), [], session.paths,
    )).get_item(committed.item_id)
    requirement = next(row for row in item["requirements"] if row["requirement_id"] == committed.requirement_id)
    assert requirement["current_run_id"] == receipt.run_id
    assert requirement["status"] == "unknown"
    assert len(item["runs"]) == 1
    run = item["runs"][0]
    assert run["run_id"] == receipt.run_id
    assert run["status"] == "unknown"
    assert run["result_summary"] == "Host terminal receipt recorded; requirement outcome remains unknown."
    assert run["verification"] == {
        "status": "not_verified",
        "evidence_refs": [],
        "host_execution": {"sequence": 17, "finish_reason": "completed"},
    }
    assert len(item["history"]) == 3


def test_deferred_commit_rechecks_binding_inside_host_critical_section(tmp_path, monkeypatch):
    binding = {"value": True, "inside": False}

    @contextmanager
    def guard():
        binding["inside"] = True
        binding["value"] = False
        try:
            yield
        finally:
            binding["inside"] = False

    session, _provider, _prompts, service, request_id, prepared = prepare_capture(
        tmp_path, monkeypatch, guard_factory=guard, valid=binding,
    )
    assert prepared.status == "awaiting_host_run"

    result = service.commit_prepared_capture(
        prepared.prepared_capture, receipt=make_receipt(session, request_id),
    )

    assert binding["inside"] is False
    assert result.status == "stale_context"
    assert WorkRecordStore(session).list_items() == []


def test_deferred_commit_rejects_nonterminal_or_mismatched_receipt_without_write(tmp_path, monkeypatch):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)

    wrong_session = make_receipt(session, request_id, session_id="another-session")
    result = service.commit_prepared_capture(prepared.prepared_capture, receipt=wrong_session)

    assert result.status == "stale_context"
    assert WorkRecordStore(session).list_items() == []


def test_store_terminal_create_is_idempotent_and_conflicting_run_proof_is_rejected(tmp_path):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    event_id = str(uuid.uuid4())
    idempotency_key = f"desktop:user.message:{event_id}:synthetic-digest"
    terminal_run = {
        "run_id": str(uuid.uuid4()), "sequence": 3,
        "terminal_state": "failed", "finish_reason": "error",
    }
    kwargs = dict(
        title="Synthetic item", scope={"kind": "independent"},
        requirement="Synthetic requirement", event_id=event_id,
        idempotency_key=idempotency_key, terminal_run=terminal_run,
        return_commit_status=True,
    )

    first, did_commit = store.create_item_with_requirement(**kwargs)
    replay, replay_committed = store.create_item_with_requirement(**kwargs)

    assert did_commit is True and replay_committed is False
    assert replay["item_id"] == first["item_id"]
    assert len(store.list_items()) == 1
    assert len(first["runs"]) == 1 and first["runs"][0]["status"] == "unknown"

    conflicting = dict(kwargs)
    conflicting["terminal_run"] = {**terminal_run, "run_id": str(uuid.uuid4())}
    with pytest.raises(WorkRecordConflict):
        store.create_item_with_requirement(**conflicting)
    assert len(store.list_items()) == 1


def test_store_publish_failure_leaves_no_item_requirement_or_run(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    monkeypatch.setattr(store, "_publish", lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic publish failure")))

    with pytest.raises(Exception):
        store.create_item_with_requirement(
            title="Synthetic item", scope={"kind": "independent"},
            requirement="Synthetic requirement", event_id=str(uuid.uuid4()),
            idempotency_key=f"desktop:user.message:{uuid.uuid4()}:digest",
            terminal_run={
                "run_id": str(uuid.uuid4()), "sequence": 1,
                "terminal_state": "settled", "finish_reason": "completed",
            },
        )

    assert store.list_items() == []

def test_missing_finish_reason_is_not_invented_in_persisted_host_execution(tmp_path, monkeypatch):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)

    result = service.commit_prepared_capture(
        prepared.prepared_capture, receipt=make_receipt(session, request_id, finish_reason=None),
    )

    assert result.status == "recorded"
    item = WorkRecordStore(session).get_item(result.item_id)
    run = item["runs"][0]
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"] == {"sequence": 17}


def test_consumed_preparation_cannot_duplicate_or_replace_the_terminal_run(tmp_path, monkeypatch):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    first_receipt = make_receipt(session, request_id)
    first = service.commit_prepared_capture(prepared.prepared_capture, receipt=first_receipt)
    replay = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id, run_id=str(uuid.uuid4()), sequence=99),
    )

    assert first.status == "recorded"
    assert replay.status == "already_recorded"
    assert replay.item_id == first.item_id
    item = WorkRecordStore(session).get_item(first.item_id)
    assert len(item["requirements"]) == len(item["runs"]) == 1
    assert item["runs"][0]["run_id"] == first_receipt.run_id


@pytest.mark.parametrize("overrides", [
    {"terminal_state": "running"},
    {"sequence": True},
    {"finish_reason": "invented-success"},
    {"terminal_state": []},
    {"finish_reason": []},
])
def test_nonterminal_or_invalid_host_receipt_is_rejected_without_write(tmp_path, monkeypatch, overrides):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)

    result = service.commit_prepared_capture(
        prepared.prepared_capture, receipt=make_receipt(session, request_id, **overrides),
    )

    assert result.status == "stale_context"
    assert WorkRecordStore(session).list_items() == []


def test_deferred_capture_requires_host_binding_and_commit_guard(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    provider = FakeProvider()
    prompts = []
    service = WorkRecordCaptureService(
        session=session, client=provider,
        confirm=lambda preview: (prompts.append(preview) or yes()),
    )
    install_model_registry(monkeypatch)

    result = service.capture(
        "Record this synthetic requirement", request_id=str(uuid.uuid4()),
        track_run=True, defer_commit_until_terminal=True,
    )

    assert result.status == "unavailable"
    assert provider.calls == []
    assert prompts == []
    assert WorkRecordStore(session).list_items() == []

def test_store_normalizes_hex_and_canonical_run_ids_to_one_idempotent_commit(tmp_path):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    event_id = str(uuid.uuid4())
    run_id = uuid.uuid4()
    idempotency_key = f"desktop:user.message:{event_id}:normalized-run"
    base = dict(
        title="Synthetic item", scope={"kind": "independent"},
        requirement="Synthetic requirement", event_id=event_id,
        idempotency_key=idempotency_key, return_commit_status=True,
    )

    first, first_committed = store.create_item_with_requirement(
        **base, terminal_run={
            "run_id": run_id.hex, "sequence": 4,
            "terminal_state": "settled", "finish_reason": "completed",
        },
    )
    replay, replay_committed = store.create_item_with_requirement(
        **base, terminal_run={
            "run_id": str(run_id), "sequence": 4,
            "terminal_state": "settled", "finish_reason": "completed",
        },
    )

    assert first_committed is True and replay_committed is False
    assert replay["item_id"] == first["item_id"]
    assert first["runs"][0]["run_id"] == str(run_id)
    assert len(store.list_items()) == 1


def test_service_accepts_uuid_hex_receipt_and_persists_same_canonical_host_run(tmp_path, monkeypatch):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    run_id = uuid.uuid4()

    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id, run_id=run_id.hex),
    )

    assert result.status == "recorded"
    item = WorkRecordStore(session).get_item(result.item_id)
    assert item["runs"][0]["run_id"] == str(run_id)


@pytest.mark.parametrize("run_id", [
    "not-a-run-id",
    "FFFFFFFF-FFFF-4FFF-8FFF-FFFFFFFFFFFF",
    "f" * 32,
])
def test_service_rejects_invalid_or_noncanonical_host_run_ids_without_write(tmp_path, monkeypatch, run_id):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)

    result = service.commit_prepared_capture(
        prepared.prepared_capture, receipt=make_receipt(session, request_id, run_id=run_id),
    )

    assert result.status == "stale_context"
    assert WorkRecordStore(session).list_items() == []



def test_optional_host_result_evidence_is_previewed_exactly_and_stays_unverified(tmp_path, monkeypatch):
    session, _provider, prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    evidence = WorkRecordResultEvidence(
        summary_excerpt="Synthetic excerpt\nsecond line",
        source_availability="available",
        sources=({"kind": "result", "id": "synthetic-result-1"},),
    )
    first_two = len(prompts)
    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id),
        result_evidence=evidence,
    )

    assert result.status == "recorded"
    assert len(prompts) == first_two + 1
    previewed = result_evidence_preview(prompts[-1])
    assert previewed["run"]["result_summary"] == evidence.summary_excerpt
    assert previewed["run"]["verification"]["host_execution"]["source_availability"] == "available"
    assert previewed["run"]["sources"] == [{"kind": "result", "id": "synthetic-result-1"}]
    assert previewed["run"]["status"] == "unknown"
    assert previewed["run"]["verification"]["status"] == "not_verified"
    item = WorkRecordStore(session).get_item(result.item_id)
    run = item["runs"][0]
    assert run["result_summary"] == "Synthetic excerpt\nsecond line"
    assert run["sources"] == [{"kind": "result", "id": "synthetic-result-1"}]
    assert run["verification"] == {
        "status": "not_verified", "evidence_refs": [],
        "host_execution": {"sequence": 17, "finish_reason": "completed",
                            "source_availability": "available"},
    }
    assert run["status"] == item["requirements"][0]["status"] == "unknown"


def test_optional_host_result_evidence_rejection_writes_nothing(tmp_path, monkeypatch):
    session, _provider, prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    seen = []
    service._confirm = lambda preview: (seen.append(preview) or ("no", str(uuid.uuid4())))
    evidence = WorkRecordResultEvidence(
        summary_excerpt="Synthetic private excerpt",
        source_availability="unavailable",
        sources=(),
    )

    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id),
        result_evidence=evidence,
    )

    assert result.status == "storage_not_authorized"
    assert len(seen) == 1
    assert "Synthetic private excerpt" in seen[0]
    assert '"source_availability": "unavailable"' in seen[0]
    assert WorkRecordStore(session).list_items() == []


@pytest.mark.parametrize("evidence", [
    {"summary_excerpt": "too large", "source_availability": "available", "sources": ()},
    {"summary_excerpt": "x" * 5000, "source_availability": "unavailable", "sources": ()},
    {"summary_excerpt": "ok", "source_availability": "made-up", "sources": ()},
    {"summary_excerpt": "ok", "source_availability": "available", "sources": ()},
    {"summary_excerpt": "ok", "source_availability": "not_applicable", "sources": ({"kind": "result", "id": "s1"},)},
    {"summary_excerpt": "ok", "source_availability": "available",
     "sources": tuple({"kind": "result", "id": f"s{i}"} for i in range(17))},
])
def test_invalid_or_unbounded_optional_result_evidence_fails_closed(tmp_path, monkeypatch, evidence):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    confirm_calls = []
    service._confirm = lambda preview: (confirm_calls.append(preview) or yes())
    evidence_value = WorkRecordResultEvidence(**evidence)

    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id),
        result_evidence=evidence_value,
    )

    assert result.status == "invalid_request"
    assert confirm_calls == []
    assert WorkRecordStore(session).list_items() == []


def test_store_result_evidence_changes_idempotency_digest_and_validates_size(tmp_path):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    event_id = str(uuid.uuid4())
    key = f"desktop:user.message:{event_id}:evidence"
    terminal_run = {"run_id": str(uuid.uuid4()), "sequence": 2,
                    "terminal_state": "settled", "finish_reason": "completed"}
    evidence = {"summary_excerpt": "Synthetic source-backed excerpt",
                "source_availability": "available",
                "sources": [{"kind": "result", "id": "synthetic-evidence"}]}
    base = dict(title="Synthetic", scope={"kind": "independent"}, requirement="Synthetic req",
                event_id=event_id, idempotency_key=key, terminal_run=terminal_run,
                result_evidence=evidence, return_commit_status=True)
    first, committed = store.create_item_with_requirement(**base)
    replay, replayed = store.create_item_with_requirement(**base)

    assert committed is True and replayed is False
    assert replay["item_id"] == first["item_id"]
    run = first["runs"][0]
    assert run["result_summary"] == evidence["summary_excerpt"]
    assert run["sources"] == evidence["sources"]
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"

    changed = {**base, "result_evidence": {**evidence, "summary_excerpt": "different excerpt"}}
    with pytest.raises(WorkRecordConflict):
        store.create_item_with_requirement(**changed)
    assert len(store.list_items()) == 1



def test_optional_result_excerpt_and_source_availability_get_exact_extra_storage_consent(tmp_path, monkeypatch):
    session, _provider, prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    evidence = WorkRecordResultEvidence(
        summary_excerpt="Synthetic host excerpt\nsecond line",
        source_availability="available",
        sources=({"kind": "result", "id": "synthetic-result-ref"},),
    )
    before = len(prompts)

    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id),
        result_evidence=evidence,
    )

    assert result.status == "recorded"
    assert len(prompts) == before + 1
    previewed = result_evidence_preview(prompts[-1])
    assert previewed["run"]["result_summary"] == evidence.summary_excerpt
    assert previewed["run"]["verification"]["host_execution"]["source_availability"] == "available"
    assert previewed["run"]["sources"] == [{"kind": "result", "id": "synthetic-result-ref"}]
    assert previewed["run"]["status"] == "unknown"
    assert previewed["run"]["verification"]["status"] == "not_verified"
    item = WorkRecordStore(session).get_item(result.item_id)
    run = item["runs"][0]
    assert run["result_summary"] == evidence.summary_excerpt
    assert run["sources"] == [{"kind": "result", "id": "synthetic-result-ref"}]
    assert run["status"] == item["requirements"][0]["status"] == "unknown"
    assert run["verification"] == {
        "status": "not_verified", "evidence_refs": [],
        "host_execution": {"sequence": 17, "finish_reason": "completed",
                            "source_availability": "available"},
    }


def test_declining_optional_result_evidence_confirms_nothing_and_writes_no_item(tmp_path, monkeypatch):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    result_prompts = []
    service._confirm = lambda preview: (result_prompts.append(preview) or ("no", str(uuid.uuid4())))

    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id),
        result_evidence=WorkRecordResultEvidence(
            summary_excerpt="Synthetic private result excerpt",
            source_availability="unavailable",
            sources=(),
        ),
    )

    assert result.status == "storage_not_authorized"
    assert len(result_prompts) == 1
    assert "Synthetic private result excerpt" in result_prompts[0]
    assert '"source_availability": "unavailable"' in result_prompts[0]
    assert WorkRecordStore(session).list_items() == []


@pytest.mark.parametrize("evidence", [
    {"summary_excerpt": "", "source_availability": "available", "sources": ({"kind": "result", "id": "s1"},)},
    {"summary_excerpt": "x" * 5000, "source_availability": "unavailable", "sources": ()},
    {"summary_excerpt": "ok", "source_availability": "invented", "sources": ()},
    {"summary_excerpt": "ok", "source_availability": "available", "sources": ()},
    {"summary_excerpt": "ok", "source_availability": "not_applicable", "sources": ({"kind": "result", "id": "s1"},)},
])
def test_invalid_or_unbounded_optional_result_evidence_fails_closed_without_confirmation(tmp_path, monkeypatch, evidence):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)
    confirm_calls = []
    service._confirm = lambda preview: (confirm_calls.append(preview) or yes())

    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id),
        result_evidence=WorkRecordResultEvidence(**evidence),
    )

    assert result.status == "invalid_request"
    assert confirm_calls == []
    assert WorkRecordStore(session).list_items() == []


def test_store_result_evidence_is_bounded_and_part_of_idempotency_payload(tmp_path):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)
    event_id = str(uuid.uuid4())
    key = f"desktop:user.message:{event_id}:result-evidence"
    terminal_run = {"run_id": str(uuid.uuid4()), "sequence": 4,
                    "terminal_state": "settled", "finish_reason": "completed"}
    result_evidence = {
        "summary_excerpt": "Synthetic host-provided excerpt",
        "source_availability": "available",
        "sources": [{"kind": "result", "id": "synthetic-source"}],
    }
    base = dict(title="Synthetic", scope={"kind": "independent"}, requirement="Synthetic requirement",
                event_id=event_id, idempotency_key=key, terminal_run=terminal_run,
                result_evidence=result_evidence, return_commit_status=True)

    first, committed = store.create_item_with_requirement(**base)
    replay, replayed = store.create_item_with_requirement(**base)
    assert committed is True and replayed is False and replay["item_id"] == first["item_id"]
    run = first["runs"][0]
    assert run["result_summary"] == result_evidence["summary_excerpt"]
    assert run["sources"] == result_evidence["sources"]
    assert run["status"] == "unknown" and run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"]["source_availability"] == "available"

    changed = {**base, "result_evidence": {**result_evidence, "summary_excerpt": "different"}}
    with pytest.raises(WorkRecordConflict):
        store.create_item_with_requirement(**changed)
    assert len(store.list_items()) == 1

    bad = {**base, "event_id": str(uuid.uuid4()), "idempotency_key": f"desktop:user.message:{uuid.uuid4()}:oversized",
           "result_evidence": {**result_evidence, "summary_excerpt": "x" * 5000}}
    with pytest.raises(ValueError):
        store.create_item_with_requirement(**bad)
    assert len(store.list_items()) == 1



def test_provider_stop_finish_reason_is_saved_verbatim_without_success_inference(tmp_path, monkeypatch):
    session, _provider, _prompts, service, request_id, prepared = prepare_capture(tmp_path, monkeypatch)

    result = service.commit_prepared_capture(
        prepared.prepared_capture,
        receipt=make_receipt(session, request_id, terminal_state="settled", finish_reason="stop"),
    )

    assert result.status == "recorded"
    item = WorkRecordStore(session).get_item(result.item_id)
    requirement = item["requirements"][0]
    run = item["runs"][0]
    assert run["run_id"] == requirement["current_run_id"]
    assert run["status"] == requirement["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["host_execution"]["finish_reason"] == "stop"
    assert run["status"] != "completed"


def test_store_rejects_unknown_finish_reason_without_publishing(tmp_path):
    session = make_session(tmp_path)
    store = WorkRecordStore(session)

    with pytest.raises(ValueError):
        store.create_item_with_requirement(
            title="Synthetic item", scope={"kind": "independent"},
            requirement="Synthetic requirement", event_id=str(uuid.uuid4()),
            idempotency_key=f"desktop:user.message:{uuid.uuid4()}:invalid-finish",
            terminal_run={
                "run_id": str(uuid.uuid4()), "sequence": 1,
                "terminal_state": "settled", "finish_reason": "invented_success",
            },
        )

    assert store.list_items() == []
