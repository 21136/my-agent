from __future__ import annotations
import sys
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_record_cards import build_project_work_record_cards
from work_card_ledger import WorkCardLedger


def test_project_self_report_survives_existing_project_card_projection(tmp_path):
    project_id, session_id = "project-self-report", "20261101-abcdef12"
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, goal="synthetic projection", meta=SessionMeta(project_id=project_id), messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic item", scope={"kind": "project", "project_id": project_id})
    requirement = store.add_requirement(item["item_id"], "Synthetic requirement", event_id="self-report-requirement")
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id="run-self-report", expected_current_run_id=None)
    report = {"status": "passed", "source": "user_authorized_project_self_report",
              "pytest_target": "tests/test_target.py::test_case", "exit_code": 0}
    store.record_result_if_current_run(item["item_id"], requirement["requirement_id"], run_id="run-self-report",
        result_id="result-self-report", status="unknown", summary="synthetic lifecycle",
        verification={"status": "not_verified", "evidence_refs": [], "project_test": report}, sources=[])

    snapshot = build_project_work_record_cards(store, project_id)
    current = snapshot.cards[0].requirements[0]
    result = asdict(current.latest_result)
    assert result["project_test"] == report
    assert current.status == "unknown"
    assert result["status"] == "unknown" and result["verification_status"] == "not_verified"
    ledger = WorkCardLedger(session, store)
    saved = ledger.save_snapshot("2b7a8a6b-f7cd-4df4-8a42-22ae12c80123", snapshot)
    assert saved.status == "saved"
    persisted = ledger.get_card(saved.card_id)
    persisted_result = persisted["snapshot"]["cards"][0]["requirements"][0]["latest_result"]
    assert persisted_result["project_test"] == report
    assert persisted_result["status"] == "unknown" and persisted_result["verification_status"] == "not_verified"




def test_project_card_rejects_uncontrolled_project_test_fields(tmp_path):
    import pytest
    from work_record_cards import WorkRecordCardsError
    project_id, session_id = "project-self-report", "20261101-abcdef12"
    paths = AgentPaths(tmp_path, tmp_path / "workspace", tmp_path / "data", tmp_path / "evolve")
    session_dir = paths.data / "sessions" / session_id
    session_dir.mkdir(parents=True)
    session = Session(session_id, session_dir, goal="synthetic malformed projection", meta=SessionMeta(project_id=project_id), messages=[], paths=paths)
    store = WorkRecordStore(session)
    item = store.create_item(title="Synthetic item", scope={"kind": "project", "project_id": project_id})
    requirement = store.add_requirement(item["item_id"], "Synthetic requirement", event_id="self-report-requirement")
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id="run-self-report", expected_current_run_id=None)
    report = {"status": "passed", "source": "user_authorized_project_self_report",
              "pytest_target": "tests/test_target.py::test_case", "exit_code": 0, "uncontrolled": True}
    store.record_result_if_current_run(item["item_id"], requirement["requirement_id"], run_id="run-self-report",
        result_id="result-self-report", status="unknown", summary="synthetic lifecycle",
        verification={"status": "not_verified", "evidence_refs": [], "project_test": report}, sources=[])
    with pytest.raises(WorkRecordCardsError, match="project self-report test shape"):
        build_project_work_record_cards(store, project_id)
