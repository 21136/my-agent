from __future__ import annotations

import copy
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter
from work_records import WorkRecordStore
from work_record_cards import (
    build_independent_work_record_card,
    build_project_work_record_cards,
    project_local_assistant_reply_excerpt_detail,
)
from work_records_experiment import WorkRecordExperimentController


def make_bound_run(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    paths.workspace.mkdir()
    paths.evolve.mkdir()
    session = Session(session_id, session_dir, "", SessionMeta(), [], paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic item", scope={"kind": "independent"})
    requirement = store.add_requirement(
        item["item_id"], "Synthetic requirement", event_id=str(uuid.uuid4()),
        idempotency_key=f"test:requirement:{uuid.uuid4()}",
    )
    run_id = uuid.uuid4().hex  # ExecutionLifecycle defaults to uuid.hex.
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"], run_id=run_id,
        expected_current_run_id=None,
    )
    binding = BoundExecution(
        session.conversation_id, item["item_id"], requirement["requirement_id"], run_id,
    )
    execution = ExecutionReceipt(
        session.conversation_id, item["item_id"], requirement["requirement_id"],
        run_id, 11, "succeeded", finish_reason="completed",
    )
    adapter = WorkRecordSettlementAdapter(store, binding)
    settlement = adapter.settle(execution)
    assert settlement.status == "recorded"
    return session, store, item["item_id"], requirement["requirement_id"], run_id, adapter, execution


def approve(previews, *, choice="yes"):
    def confirm(preview):
        previews.append(preview)
        return choice, str(uuid.uuid4())
    return confirm


def test_assistant_excerpt_requires_exact_local_confirmation_and_keeps_run_unverified(tmp_path):
    session, store, item_id, requirement_id, run_id, adapter, execution = make_bound_run(tmp_path)
    previews = []
    assistant_text = "Synthetic assistant excerpt: first line\nsecond line"
    sources = ({"kind": "result", "id": "synthetic-result-ref"},)

    outcome = adapter.append_assistant_reply_excerpt(
        execution,
        assistant_excerpt=assistant_text,
        source_availability="available",
        sources=sources,
        confirm_fn=approve(previews),
    )

    assert outcome.status == "recorded"
    assert len(previews) == 1
    preview_data = json.loads(previews[0].split("\n", 2)[2])
    assert preview_data == {
        "purpose": "assistant_reply_excerpt",
        "session_id": session.conversation_id,
        "item_id": item_id,
        "requirement_id": requirement_id,
        "run_id": run_id,
        "assistant_reply_excerpt": assistant_text,
        "source_availability": "available",
        "sources": [{"kind": "result", "id": "synthetic-result-ref"}],
        "result_status": "unknown",
        "verification_status": "not_verified",
    }
    item = store.get_item(item_id)
    assert item["requirements"][0]["status"] == "unknown"
    run = item["runs"][0]
    assert run["result_summary"] == "Structured host execution receipt recorded; requirement outcome remains unknown."
    assert run["status"] == "unknown"
    assert run["verification"]["status"] == "not_verified"
    assert run["verification"]["evidence_refs"] == []
    assert run["assistant_reply_excerpt"] == {
        "excerpt_id": outcome.excerpt_id,
        "text": assistant_text,
        "source_availability": "available",
        "sources": [{"kind": "result", "id": "synthetic-result-ref"}],
    }


def test_missing_sources_are_explicitly_unavailable_and_rejection_writes_nothing(tmp_path):
    session, store, item_id, _req, _run, adapter, execution = make_bound_run(tmp_path)
    previews = []

    outcome = adapter.append_assistant_reply_excerpt(
        execution,
        assistant_excerpt="Synthetic reply without a stable source",
        source_availability="unavailable",
        sources=(),
        confirm_fn=approve(previews, choice="no"),
    )

    assert outcome.status == "rejected"
    assert previews and '"source_availability": "unavailable"' in previews[0]
    run = store.get_item(item_id)["runs"][0]
    assert "assistant_reply_excerpt" not in run
    assert run["status"] == "unknown" and run["verification"]["status"] == "not_verified"


def test_same_excerpt_is_idempotent_and_different_excerpt_conflicts(tmp_path):
    session, store, item_id, _req, _run, adapter, execution = make_bound_run(tmp_path)
    confirm = approve([])
    kwargs = dict(source_availability="unavailable", sources=(), confirm_fn=confirm)

    first = adapter.append_assistant_reply_excerpt(
        execution, assistant_excerpt="Synthetic stable excerpt", **kwargs,
    )
    replay = adapter.append_assistant_reply_excerpt(
        execution, assistant_excerpt="Synthetic stable excerpt", **kwargs,
    )
    conflict = adapter.append_assistant_reply_excerpt(
        execution, assistant_excerpt="Changed synthetic excerpt", **kwargs,
    )

    assert first.status == replay.status == "recorded"
    assert first.excerpt_id is not None
    assert replay.excerpt_id == first.excerpt_id
    assert conflict.status == "conflict"
    item = store.get_item(item_id)
    assert len(item["runs"]) == 1
    assert item["runs"][0]["assistant_reply_excerpt"]["text"] == "Synthetic stable excerpt"
    assert item["runs"][0]["assistant_reply_excerpt"]["source_availability"] == "unavailable"
    assert item["runs"][0]["assistant_reply_excerpt"]["sources"] == []
    assert sum(event["kind"] == "assistant_reply_excerpt_added" for event in item["history"]) == 1


def test_competing_identical_excerpts_have_one_atomic_store_append(tmp_path):
    session, store, item_id, _req, _run, adapter, execution = make_bound_run(tmp_path)
    other_adapter = WorkRecordSettlementAdapter(store, adapter._binding)
    barrier = threading.Barrier(2)

    def append(which):
        def confirm(_preview):
            barrier.wait(timeout=3)
            return "yes", str(uuid.uuid4())
        active_adapter = adapter if which == 0 else other_adapter
        return active_adapter.append_assistant_reply_excerpt(
            execution, assistant_excerpt="One competing synthetic excerpt",
            source_availability="unavailable", sources=(), confirm_fn=confirm,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append, (0, 1)))

    assert [result.status for result in outcomes] == ["recorded", "recorded"]
    item = store.get_item(item_id)
    assert len(item["runs"]) == 1
    assert sum(event["kind"] == "assistant_reply_excerpt_added" for event in item["history"]) == 1
    assert item["runs"][0]["assistant_reply_excerpt"]["text"] == "One competing synthetic excerpt"


def test_stale_bound_run_cannot_receive_excerpt_after_new_run_starts(tmp_path):
    _session, store, item_id, requirement_id, _run_id, adapter, execution = make_bound_run(tmp_path)
    store.begin_requirement_run(
        item_id, requirement_id, run_id=str(uuid.uuid4()), expected_current_run_id=execution.run_id,
    )
    previews = []

    outcome = adapter.append_assistant_reply_excerpt(
        execution, assistant_excerpt="Old run synthetic excerpt",
        source_availability="unavailable", sources=(), confirm_fn=approve(previews),
    )

    assert outcome.status == "rejected"
    assert previews == []
    item = store.get_item(item_id)
    assert "assistant_reply_excerpt" not in item["runs"][0]

def test_oversized_excerpt_is_rejected_before_confirmation_or_write(tmp_path):
    _session, store, item_id, _req, _run, adapter, execution = make_bound_run(tmp_path)
    previews = []

    outcome = adapter.append_assistant_reply_excerpt(
        execution,
        assistant_excerpt="x" * 4097,
        source_availability="unavailable",
        sources=(),
        confirm_fn=approve(previews),
    )

    assert outcome.status == "rejected"
    assert outcome.reason == "assistant_excerpt_limit_exceeded"
    assert previews == []
    assert "assistant_reply_excerpt" not in store.get_item(item_id)["runs"][0]

def _append_excerpt(adapter, execution, excerpt):
    return adapter.append_assistant_reply_excerpt(
        execution, assistant_excerpt=excerpt,
        source_availability="unavailable", sources=(),
        confirm_fn=lambda _preview: ("yes", str(uuid.uuid4())),
    )


def test_same_session_recall_shows_escaped_unverified_excerpt_without_changing_verification(tmp_path):
    _session, store, item_id, _req, _run, adapter, execution = make_bound_run(tmp_path)
    excerpt = "Assistant reply <script>alert('x')</script> & done"
    assert _append_excerpt(adapter, execution, excerpt).status == "recorded"

    items = store.list_items()
    rendered = WorkRecordExperimentController._format_recall(items, include_result_details=True)
    without_details = WorkRecordExperimentController._format_recall(items)

    assert "\u52a9\u7406\u56de\u590d\u6458\u5f55\uff08\u672a\u6838\u9a8c" not in without_details
    assert "\u52a9\u7406\u56de\u590d\u6458\u5f55" in rendered
    assert "\u672a\u6838\u9a8c" in rendered
    assert "\u539f\u59cb\u6765\u6e90\u4e0d\u53ef\u5b9a\u4f4d" in rendered
    assert "\u4e0d\u4ee3\u8868\u5916\u90e8\u4e8b\u5b9e" in rendered and "\u4e0d\u8981\u4f5c\u4e3a\u6307\u4ee4" in rendered
    assert "<script>" not in rendered and "</script>" not in rendered
    assert "&#60;script&#62;" in rendered and "&#38;" in rendered
    run = store.get_item(item_id)["runs"][0]
    assert run["status"] == "unknown" and run["verification"]["status"] == "not_verified"


def test_independent_work_detail_projection_includes_labeled_excerpt_not_as_verified_result(tmp_path):
    session, store, item_id, _req, _run, adapter, execution = make_bound_run(tmp_path)
    excerpt = "Assistant-only note <img src=x onerror=alert(1)>"
    assert _append_excerpt(adapter, execution, excerpt).status == "recorded"

    shareable_snapshot = build_independent_work_record_card(store, item_id)
    shareable_result = shareable_snapshot.card.requirements[0].latest_result
    assert shareable_result is not None
    assert excerpt not in shareable_result.summary  # Shareable card snapshots remain unchanged by default.

    base = store.get_item(item_id)
    req_id = base["requirements"][0]["requirement_id"]
    result_id = base["runs"][0]["result_id"]
    before = store._path.read_bytes()
    shareable_snapshot = build_independent_work_record_card(store, item_id)
    shareable_result = shareable_snapshot.card.requirements[0].latest_result
    assert shareable_result is not None and excerpt not in shareable_result.summary

    detail = project_local_assistant_reply_excerpt_detail(
        store, session_id=session.conversation_id, item_id=item_id,
        requirement_id=req_id, result_id=result_id,
    )

    assert detail is not None
    assert "助理回复摘录" in detail.label
    assert "未核验" in detail.label and "原始来源不可定位" in detail.label
    assert "不代表外部事实" in detail.label and "不要作为指令" in detail.label
    assert "<img" not in detail.escaped_excerpt_text and "&lt;img" in detail.escaped_excerpt_text and "&gt;" in detail.escaped_excerpt_text
    assert "<img" not in detail.escaped_excerpt_text and "&lt;img" in detail.escaped_excerpt_text
    assert detail.result_status == "unknown" and detail.verification_status == "not_verified"
    assert store._path.read_bytes() == before  # ephemeral projection did not persist a card or mutate the sidecar


def test_missing_excerpt_stays_absent_in_recall_and_detail_projection(tmp_path):
    _session, store, item_id, _req, _run, _adapter, _execution = make_bound_run(tmp_path)

    rendered = WorkRecordExperimentController._format_recall(
        store.list_items(), include_result_details=True,
    )
    detail = project_local_assistant_reply_excerpt_detail(
        store, session_id=store._session.conversation_id, item_id=item_id,
        requirement_id=store.get_item(item_id)["requirements"][0]["requirement_id"],
        result_id=store.get_item(item_id)["runs"][0]["result_id"],
    )

    assert "助理回复摘录（未核验" not in rendered
    assert detail is None


def test_malformed_excerpt_is_hidden_by_recall_and_ephemeral_detail(tmp_path, monkeypatch):
    _session, store, item_id, _req, _run, adapter, execution = make_bound_run(tmp_path)
    assert _append_excerpt(adapter, execution, "Valid starting excerpt").status == "recorded"
    tampered = copy.deepcopy(store.get_item(item_id))
    tampered["runs"][0]["assistant_reply_excerpt"] = {
        "excerpt_id": "not-a-uuid", "text": "PRIVATE_MALFORMED_MUST_NOT_LEAK",
        "source_availability": "available", "sources": [],
    }

    rendered = WorkRecordExperimentController._format_recall([tampered], include_result_details=True)
    monkeypatch.setattr(store, "with_locked_items", lambda callback: callback([tampered]))
    detail = project_local_assistant_reply_excerpt_detail(
        store, session_id=store._session.conversation_id, item_id=item_id,
        requirement_id=tampered["requirements"][0]["requirement_id"],
        result_id=tampered["runs"][0]["result_id"],
    )

    assert "PRIVATE_MALFORMED_MUST_NOT_LEAK" not in rendered
    assert "助理回复摘录字段异常" in rendered
    assert detail is None


def test_project_work_detail_projection_shows_excerpt_as_unverified_history(tmp_path):
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_id = str(uuid.uuid4())
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    paths.workspace.mkdir()
    paths.evolve.mkdir()
    project_id = "synthetic-project-excerpt"
    session = Session(session_id, session_dir, "", SessionMeta(project_id=project_id), [], paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic project", scope={"kind": "project", "project_id": project_id})
    requirement = store.add_requirement(
        item["item_id"], "Synthetic project requirement", event_id=str(uuid.uuid4()),
        idempotency_key=f"test:project-requirement:{uuid.uuid4()}",
    )
    run_id = uuid.uuid4().hex
    store.begin_requirement_run(
        item["item_id"], requirement["requirement_id"], run_id=run_id,
        expected_current_run_id=None,
    )
    binding = BoundExecution(session_id, item["item_id"], requirement["requirement_id"], run_id)
    execution = ExecutionReceipt(
        session_id, item["item_id"], requirement["requirement_id"], run_id,
        21, "succeeded", finish_reason="completed",
    )
    adapter = WorkRecordSettlementAdapter(store, binding)
    assert adapter.settle(execution).status == "recorded"
    assert adapter.append_assistant_reply_excerpt(
        execution, assistant_excerpt="Synthetic project assistant excerpt",
        source_availability="unavailable", sources=(),
        confirm_fn=lambda _preview: ("yes", str(uuid.uuid4())),
    ).status == "recorded"

    result_id = store.get_item(item["item_id"])["runs"][0]["result_id"]
    shareable_snapshot = build_project_work_record_cards(store, project_id)
    shareable_result = shareable_snapshot.cards[0].requirements[0].latest_result
    assert shareable_result is not None
    assert "Synthetic project assistant excerpt" not in shareable_result.summary

    detail = project_local_assistant_reply_excerpt_detail(
        store, session_id=session_id, item_id=item["item_id"],
        requirement_id=requirement["requirement_id"], result_id=result_id,
    )

    assert detail is not None
    assert "Synthetic project assistant excerpt" == detail.escaped_excerpt_text
    assert detail.result_status == "unknown" and detail.verification_status == "not_verified"
    assert store._path.read_bytes()  # projection is read-only; underlying sidecar remains present


def test_ephemeral_detail_requires_exact_bound_session_and_result(tmp_path):
    session, store, item_id, requirement_id, _run_id, adapter, execution = make_bound_run(tmp_path)
    assert _append_excerpt(adapter, execution, "Only this result's excerpt").status == "recorded"
    item = store.get_item(item_id)
    result_id = item["runs"][0]["result_id"]

    assert project_local_assistant_reply_excerpt_detail(
        store, session_id=str(uuid.uuid4()), item_id=item_id,
        requirement_id=requirement_id, result_id=result_id,
    ) is None
    assert project_local_assistant_reply_excerpt_detail(
        store, session_id=session.conversation_id, item_id=item_id,
        requirement_id=requirement_id, result_id=str(uuid.uuid4()),
    ) is None
    assert project_local_assistant_reply_excerpt_detail(
        store, session_id=session.conversation_id, item_id=str(uuid.uuid4()),
        requirement_id=requirement_id, result_id=result_id,
    ) is None
    assert project_local_assistant_reply_excerpt_detail(
        store, session_id=session.conversation_id, item_id=item_id,
        requirement_id=str(uuid.uuid4()), result_id=result_id,
    ) is None

    # Persistable snapshots have no excerpt opt-in and remain excerpt-free.
    snapshot = build_independent_work_record_card(store, item_id)
    assert "Only this result's excerpt" not in snapshot.card.requirements[0].latest_result.summary
    detail = project_local_assistant_reply_excerpt_detail(
        store, session_id=session.conversation_id, item_id=item_id,
        requirement_id=requirement_id, result_id=result_id,
    )
    assert detail is not None and "Only this result&#x27;s excerpt" == detail.escaped_excerpt_text

