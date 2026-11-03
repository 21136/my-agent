from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_cards import build_independent_work_record_card
from work_records import WorkRecordStore
from work_card_visible_result_candidates import collect_displayed_result_candidates


def _session(root: Path, conversation_id: str) -> tuple[Session, WorkRecordStore]:
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / conversation_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(conversation_id, session_dir, "synthetic", SessionMeta(project_id="synthetic-project"), [], paths)
    return session, WorkRecordStore(session)


def _make_presented_card(root: Path, conversation_id: str, *, result_count: int = 1,
                          requirement_text_bytes: int = 32, receive: bool = True,
                          reference_card_id: str | None = None):
    session, store = _session(root, conversation_id)
    item = store.create_item(title="synthetic visible result card", scope={"kind": "independent"})
    for index in range(result_count):
        requirement = store.add_requirement(
            item["item_id"], "R" * requirement_text_bytes,
            event_id=f"candidate-requirement-{index}",
        )
        run_id = str(uuid.uuid4())
        store.begin_requirement_run(
            item["item_id"], requirement["requirement_id"], run_id=run_id,
            expected_current_run_id=None,
        )
        summary = json.dumps({
            "kind": "project_worker_host_lifecycle", "result_status": "blocked",
            "host_status": "blocked", "host_error_code": "private-internal-host-code",
            "worker_execution_started": False, "worker_result_claimed": False,
            "acceptance_status": "not_run",
        }, sort_keys=True, separators=(",", ":"))
        store.record_result_if_current_run(
            item["item_id"], requirement["requirement_id"], run_id=run_id,
            result_id=f"synthetic-result-{index}", status="blocked", summary=summary,
            verification={"status": "not_verified", "evidence_refs": []}, sources=[],
        )

    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot(str(uuid.uuid4()), build_independent_work_record_card(store, item["item_id"]))
    assert saved.status == "saved"
    presentations = WorkCardPresentations(session, store, ledger)
    presentation_id = str(uuid.uuid4())
    presentation_run_id = str(uuid.uuid4())
    assert presentations.create(presentation_id, saved.card_id, run_id=presentation_run_id).status == "created"
    session.append_sent_work_card_reference(
        presentation_id, reference_card_id or saved.card_id, run_id=presentation_run_id,
    )
    assert presentations.mark_transport_offer(presentation_id, saved.card_id).status == "offered"
    if receive:
        assert presentations.mark_received(
            presentation_id, saved.card_id, session.conversation_id, presentation_run_id,
        ).status == "received"
    return session, store, ledger, presentations, saved.card_id, presentation_id


def test_collects_only_persisted_received_results_and_never_exposes_host_lifecycle_json(tmp_path):
    session, _store, ledger, presentations, card_id, presentation_id = _make_presented_card(
        tmp_path, "20261031-caad0001",
    )
    before_messages = session.messages_path.read_bytes()
    before_ledger = ledger._ledger_store._path.read_bytes()
    before_presentations = presentations._presentations_store._path.read_bytes()

    collected = collect_displayed_result_candidates(session)

    assert collected.complete is True
    assert len(collected.candidates) == 1
    candidate = collected.candidates[0]
    assert (candidate.session_id, candidate.presentation_id, candidate.card_id) == (
        session.conversation_id, presentation_id, card_id,
    )
    assert candidate.result_id == "synthetic-result-0"
    assert candidate.result_status == "blocked"
    assert "project_worker_host_lifecycle" not in candidate.result_summary
    assert "private-internal-host-code" not in candidate.result_summary
    assert [view.result_id for view in presentations.get(presentation_id).fact.result_views] == []
    assert session.messages_path.read_bytes() == before_messages
    assert ledger._ledger_store._path.read_bytes() == before_ledger
    assert presentations._presentations_store._path.read_bytes() == before_presentations


def test_same_result_redelivered_in_an_immutable_card_uses_latest_received_presentation(tmp_path, monkeypatch):
    import work_card_presentations as presentation_module

    session, store = _session(tmp_path, "20261031-caad0010")
    item = store.create_item(title="re-delivered result card", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "same result requirement", event_id="same-result-req")
    run_id = str(uuid.uuid4())
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id=run_id,
                               expected_current_run_id=None)
    store.record_result_if_current_run(
        item["item_id"], requirement["requirement_id"], run_id=run_id, result_id="same-result-id",
        status="unknown", summary="Result details remain unverified.",
        verification={"status": "not_verified", "evidence_refs": []}, sources=[],
    )
    ledger = WorkCardLedger(session, store)
    presentations = WorkCardPresentations(session, store, ledger)
    deliveries = []
    for index, received_at in enumerate(("2026-10-31T01:00:00.000000Z", "2026-10-31T01:01:00.000000Z")):
        snapshot = build_independent_work_record_card(store, item["item_id"])
        saved = ledger.save_snapshot(str(uuid.uuid4()), snapshot)
        assert saved.status == "saved"
        presentation_id = str(uuid.uuid4())
        assert presentations.create(presentation_id, saved.card_id, run_id=run_id).status == "created"
        session.append_sent_work_card_reference(presentation_id, saved.card_id, run_id=run_id)
        assert presentations.mark_transport_offer(presentation_id, saved.card_id).status == "offered"
        monkeypatch.setattr(presentation_module, "_server_timestamp", lambda value=received_at: value)
        assert presentations.mark_received(
            presentation_id, saved.card_id, session.conversation_id, run_id,
        ).status == "received"
        deliveries.append((presentation_id, saved.card_id))

    collected = collect_displayed_result_candidates(session)

    assert collected.complete is True
    assert len(collected.candidates) == 1
    candidate = collected.candidates[0]
    assert candidate.result_id == "same-result-id"
    assert (candidate.presentation_id, candidate.card_id) == deliveries[-1]


def test_pending_presentation_is_not_a_displayed_result_candidate(tmp_path):
    session, *_rest = _make_presented_card(tmp_path, "20261031-caad0002", receive=False)

    collected = collect_displayed_result_candidates(session)

    assert collected.complete is True
    assert collected.candidates == ()


def test_partial_session_history_fails_closed_without_loading_older_messages(tmp_path, monkeypatch):
    session, *_rest = _make_presented_card(tmp_path, "20261031-caad0006")
    session._messages_fully_loaded = False

    def unexpected_load():
        pytest.fail("candidate collection must not load historical Session messages")

    monkeypatch.setattr(session, "ensure_messages_loaded", unexpected_load)
    collected = collect_displayed_result_candidates(session)

    assert collected.complete is False
    assert collected.candidates == ()
    assert collected.reason == "session_history_unavailable"


def test_mismatched_persistent_history_card_reference_makes_set_incomplete(tmp_path):
    session, *_rest = _make_presented_card(
        tmp_path, "20261031-caad0003", reference_card_id="wc-" + "0" * 64,
    )

    collected = collect_displayed_result_candidates(session)

    assert collected.complete is False
    assert collected.candidates == ()


@pytest.mark.parametrize(
    ("result_count", "requirement_text_bytes", "reason"),
    [
        (25, 32, "candidate_limit_exceeded"),
        (13, 4096, "candidate_payload_limit_exceeded"),
    ],
)
def test_candidate_limits_fail_closed_without_truncating(tmp_path, result_count, requirement_text_bytes, reason):
    session, *_rest = _make_presented_card(
        tmp_path, f"20261031-{uuid.uuid4().hex[:8]}", result_count=result_count,
        requirement_text_bytes=requirement_text_bytes,
    )

    collected = collect_displayed_result_candidates(session)

    assert collected.complete is False
    assert collected.reason == reason
    assert collected.candidates == ()


def test_foreign_session_displayed_results_are_never_candidates(tmp_path):
    local, *_local = _make_presented_card(tmp_path, "20261031-caad0004")
    _foreign, *_foreign = _make_presented_card(tmp_path, "20261031-caad0005")

    collected = collect_displayed_result_candidates(local)

    assert collected.complete is True
    assert len(collected.candidates) == 1
    assert collected.candidates[0].session_id == local.conversation_id
    assert "synthetic-result-0" in collected.candidates[0].result_id
