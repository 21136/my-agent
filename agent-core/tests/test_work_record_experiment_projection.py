from __future__ import annotations

import multiprocessing
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_records_experiment import WorkRecordExperimentController


def _store(root: Path) -> WorkRecordStore:
    paths = AgentPaths(agent_root=root, workspace=root / "workspace", data=root / "data", evolve=root / "evolve")
    session_dir = paths.data / "sessions" / "projection-session"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = Session(
        conversation_id="projection-session", session_dir=session_dir, goal="",
        meta=SessionMeta(), messages=[], paths=paths,
    )
    return WorkRecordStore(session)


def _format_in_fresh_process(root: str, queue) -> None:
    fresh_store = _store(Path(root))
    queue.put(WorkRecordExperimentController._format_recall(fresh_store.list_items()))


def _cas_item(store: WorkRecordStore, status: str = "in_progress") -> tuple[str, str]:
    item = store.create_item(title="synthetic projection", scope={"kind": "independent"})
    requirement = store.add_requirement(item["item_id"], "synthetic requirement", event_id="projection-requirement")
    store.begin_requirement_run(item["item_id"], requirement["requirement_id"], run_id="run-projection", expected_current_run_id=None)
    if status != "in_progress":
        store.record_result_if_current_run(
            item["item_id"], requirement["requirement_id"], run_id="run-projection",
            result_id="projection-result", status=status,
            summary="synthetic terminal result", verification={"status": "not_verified"}, sources=[],
        )
    return item["item_id"], requirement["requirement_id"]


def test_reopened_cas_in_progress_projects_as_started_but_execution_unconfirmed(tmp_path):
    store = _store(tmp_path)
    item_id, _ = _cas_item(store)
    sidecar = store._path
    before = sidecar.read_bytes()

    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_format_in_fresh_process, args=(str(tmp_path), queue))
    process.start()
    process.join(15)
    assert process.exitcode == 0
    rendered = queue.get(timeout=2)

    assert "\u5df2\u8bb0\u5f55\u5f00\u59cb\uff0c\u5f53\u524d\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" in rendered
    assert "\u4e8b\u9879\u6c47\u603b\uff1a\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" in rendered
    assert "\u5f53\u524d\u8fd0\u884c\u4e2d" not in rendered
    assert "in_progress" not in rendered
    assert sidecar.read_bytes() == before


def test_reopened_confirmed_completed_run_keeps_terminal_projection(tmp_path):
    store = _store(tmp_path)
    _cas_item(store, status="completed")
    reopened = _store(tmp_path)

    rendered = WorkRecordExperimentController._format_recall(reopened.list_items())

    assert "completed" in rendered
    assert "synthetic requirement" in rendered and "completed" in rendered
    assert "\u5df2\u8bb0\u5f55\u5f00\u59cb\uff0c\u5f53\u524d\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" not in rendered


def test_legacy_in_progress_without_cas_identity_keeps_compatible_projection():
    legacy = [{
        "item_id": "legacy-item", "title": "legacy synthetic", "scope": {"kind": "independent"},
        "status": "in_progress", "requirements": [{"requirement_id": "legacy-req", "text": "old requirement", "status": "in_progress"}],
        "runs": [],
    }]

    rendered = WorkRecordExperimentController._format_recall(legacy)

    assert "\uff08\u8bb0\u5f55\u72b6\u6001\uff1ain_progress\uff09" in rendered
    assert "old requirement" in rendered and "in_progress" in rendered
    assert "\u5df2\u8bb0\u5f55\u5f00\u59cb\uff0c\u5f53\u524d\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" not in rendered


@pytest.mark.parametrize("terminal_status", ["stopped", "blocked", "unknown"])
def test_reopened_other_terminal_states_are_not_projected_as_active(tmp_path, terminal_status):
    store = _store(tmp_path)
    _cas_item(store, status=terminal_status)
    reopened = _store(tmp_path)

    rendered = WorkRecordExperimentController._format_recall(reopened.list_items())

    assert terminal_status in rendered
    assert "\u5df2\u8bb0\u5f55\u5f00\u59cb\uff0c\u5f53\u524d\u6267\u884c\u72b6\u6001\u5f85\u786e\u8ba4" not in rendered
