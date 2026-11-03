from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation_helpers import temporary_agent_paths
from paths import AgentPaths
from session import Session, _valid_work_card_ref
from work_card_ledger import (
    WorkCardLedger,
    linked_project_refresh_request_id,
    linked_project_session_copy_request_id,
)
from work_records import WorkRecordStore

_CORE = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
_PARENT_ID = "parent-dialog"

_CHILD = r'''
import json, os, sys, uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
root, mode, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, str(root / "agent-core"))
sys.path.insert(0, str(root / "agent-core" / "tests"))
from paths import AgentPaths
from session import Session, _valid_work_card_ref
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import (ConversationWorkRecordBindings, ReadOnlyWorkRecordStore,
    _issue_authorized_project_record_source, _reader_for)
from work_records import WorkRecordStore, _digest
from work_card_ledger import (WorkCardLedger, build_linked_project_session_copy_snapshot,
    linked_project_session_copy_request_id, linked_project_refresh_request_id)
from work_card_presentations import WorkCardPresentations
from server import (WsBridge, _resume_linked_project_session_copies,
    _resume_linked_project_session_refreshes, _emit_pending_work_card_references)
paths = AgentPaths.from_root(root)
marker = root / "worker-was-invoked.synthetic"

def crash(code=73):
    sys.stdout.flush(); sys.stderr.flush(); os._exit(code)

def prohibit(*_args, **_kwargs):
    raise AssertionError("refresh recovery attempted source-session read")

if mode == "crash":
    parent, sources, tasks = _fixture(root)
    parent.meta.harness = "desktop"
    parent.meta.active_shell = "unified"
    parent.save()
    source_a = _reader_for(sources["project-a"].store)
    # The original confirmed copy is a synthetic multi-item parent snapshot.
    source_a.add_requirement(tasks[0].item_id, "SYNTHETIC original second requirement", event_id="synthetic-original-req")
    original_item_two = source_a.create_item(title="SYNTHETIC original item two",
        scope={"kind": "project", "project_id": "project-a"})
    source_a.add_requirement(original_item_two["item_id"], "SYNTHETIC original item-two requirement",
        event_id="synthetic-original-item-two-req")
    tasks = [replace(task, source_item_digest=_digest(sources[task.project_id].store.get_item(task.item_id)))
             for task in tasks]
    namespace = ConversationWorkRecordBindings(parent)
    rows = namespace.bind_existing_tasks(batch_id="synthetic-project-refresh-batch", tasks=tasks,
                                         authorized_sources=sources)
    original_run_id = str(uuid.uuid4())
    for row in rows:
        namespace.record_worker_start(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], reservation_id="synthetic-reservation-" + row["task_id"],
            parent_run_id=original_run_id, turn_epoch=5, project_id=row["project_id"], task_id=row["task_id"])
        namespace.record_worker_lifecycle(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], sequence=5, status="model_turn_finished", finish_reason="completed")
    anchor = next(row for row in rows if row["project_id"] == "project-a")
    original_preview = namespace.prepare_project_session_copy(project_id="project-a",
        binding_ids=[anchor["binding_id"]], source=sources["project-a"])
    namespace.prepare_project_session_copy(project_id="project-a", binding_ids=[anchor["binding_id"]],
        source=sources["project-a"], expected=original_preview, is_current=lambda: True, commit=True)
    store = WorkRecordStore(parent)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    assert _resume_linked_project_session_copies(repl, bridge, parent, store,
        binding_ids=[anchor["binding_id"]], emit_event=True) == 1
    original_cards = [row for row in WorkCardLedger(parent, store).list_cards()
                      if row["snapshot"].get("kind") == "linked_project_session_copy"]
    assert len(original_cards) == 1
    original_card = original_cards[0]
    original_reference = next(row for row in parent.messages if _valid_work_card_ref(row)
                              and row["card_id"] == original_card["card_id"])
    original_copy_before = namespace.get_binding(anchor["binding_id"])["project_session_copy"]
    assert original_copy_before["intent"]["state"] == "reference_queued"

    # Change only the synthetic source snapshot, then use a distinct fresh source authorization
    # and refresh run. Recovery below must not use either source handle or execute a worker.
    source_a.add_requirement(tasks[0].item_id, "SYNTHETIC refreshed requirement", event_id="synthetic-refresh-req")
    source_session = _reader_for(sources["project-a"].store)._session
    refresh_source = _issue_authorized_project_record_source(
        "fresh-project-a-refresh-auth", parent.conversation_id, "project-a", ReadOnlyWorkRecordStore(source_session))
    refresh_run_id = str(uuid.uuid4())
    refresh_epoch = 9
    refresh_id = str(uuid.uuid4())
    options = dict(project_id="project-a", binding_ids=[anchor["binding_id"]], source=refresh_source,
        refresh_id=refresh_id, refresh_run_id=refresh_run_id, refresh_turn_epoch=refresh_epoch)
    refresh_preview = namespace.prepare_project_session_refresh(**options)
    namespace.prepare_project_session_refresh(**options, expected=refresh_preview,
        is_current=lambda *_args: True, commit=True)
    if phase == "after_refresh_pending":
        crash()

    refresh_row = next(row for row in namespace.list_project_session_refreshes() if row["refresh_id"] == refresh_id)
    refresh_snapshot = build_linked_project_session_copy_snapshot(parent.conversation_id,
        anchor["binding_id"], refresh_row["copy"])
    request_id = linked_project_refresh_request_id(parent.conversation_id, refresh_id)
    ledger = WorkCardLedger(parent, store)
    saved = ledger.save_linked_project_session_copy_snapshot(request_id, refresh_snapshot)
    assert saved.status in {"saved", "already_saved"}
    if phase == "after_refresh_card_save":
        crash()
    if phase == "after_refresh_history_before_intent_mark":
        original = ConversationWorkRecordBindings.mark_project_refresh_reference_queued
        def crash_before_refresh_mark(*_args, **_kwargs):
            (root / "refresh-mark-called.synthetic").write_text("called", encoding="utf-8")
            raise OSError("synthetic interruption after history reference persistence")
        ConversationWorkRecordBindings.mark_project_refresh_reference_queued = crash_before_refresh_mark
        _resume_linked_project_session_refreshes(repl, bridge, parent, store,
            binding_ids=[anchor["binding_id"]], emit_event=True)
        debug_rows = ConversationWorkRecordBindings(parent).list_project_session_refreshes()
        debug_refs = [message for message in parent.messages if _valid_work_card_ref(message)]
        (root / "refresh-stage-debug.synthetic").write_text(json.dumps({
            "copy_state": debug_rows[0]["copy"]["intent"]["state"],
            "refs": [{"card_id": ref["card_id"], "presentation_id": ref["presentation_id"]} for ref in debug_refs],
            "patched_method_marker": (root / "refresh-mark-called.synthetic").exists(),
        }), encoding="utf-8")
        ConversationWorkRecordBindings.mark_project_refresh_reference_queued = original
        crash()
    if phase == "after_refresh_intent_mark_before_client_receipt":
        _resume_linked_project_session_refreshes(repl, bridge, parent, store,
            binding_ids=[anchor["binding_id"]], emit_event=True)
        crash()
    raise AssertionError("unknown synthetic refresh crash phase")

if mode == "recover":
    parent = Session.load(paths, "parent-dialog", message_cap=None)
    store = WorkRecordStore(parent)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    # Guard both direct capability reads and any source-backed WorkRecordStore access.
    original_read = WorkRecordStore._read
    def parent_only_read(self, *args, **kwargs):
        if self._conversation_id != parent.conversation_id:
            return prohibit()
        return original_read(self, *args, **kwargs)
    WorkRecordStore._read = parent_only_read
    ReadOnlyWorkRecordStore._read_items_locked = prohibit
    ReadOnlyWorkRecordStore.list_items = prohibit
    ReadOnlyWorkRecordStore.get_item = prohibit
    from project_worker_host import ProjectWorkerHost
    def forbidden_worker(*_args, **_kwargs):
        marker.write_text("unexpected", encoding="utf-8")
        raise AssertionError("refresh recovery attempted worker execution")
    ProjectWorkerHost.run_many = forbidden_worker
    ProjectWorkerHost.run = forbidden_worker
    emitted = _emit_pending_work_card_references(repl, bridge)
    namespace = ConversationWorkRecordBindings(parent)
    binding_rows = namespace.list_bindings()
    anchor = next(row for row in binding_rows if "project_session_copy" in row)
    refreshes = namespace.list_project_session_refreshes()
    cards = WorkCardLedger(parent, store).list_cards()
    refs = [row for row in parent.messages if _valid_work_card_ref(row)]
    presentations = WorkCardPresentations(parent, store, WorkCardLedger(parent, store))
    facts = []
    for ref in refs:
        fact = presentations.get(ref["presentation_id"]).fact
        facts.append(None if fact is None else {"presentation_id": fact.presentation_id, "card_id": fact.card_id,
            "run_id": fact.run_id, "receipt_state": fact.receipt_state,
            "received_at": fact.received_at, "acknowledged_at": fact.acknowledged_at})
    data = {
        "emitted": emitted,
        "cards": [{"card_id": row["card_id"], "request_id": row["request_id"], "snapshot": row["snapshot"]}
                  for row in cards],
        "original_copy_state": anchor["project_session_copy"]["intent"]["state"],
        "refreshes": [{"refresh_id": row["refresh_id"], "run_id": row["run_id"],
                       "turn_epoch": row["turn_epoch"], "binding_id": row["binding_id"],
                       "copy_state": row["copy"]["intent"]["state"]} for row in refreshes],
        "references": [{"presentation_id": row["presentation_id"], "card_id": row["card_id"],
                        "run_id": row["run_id"]} for row in refs],
        "presentation_facts": facts,
        "present_events": [{"presentation_id": event["presentation_id"], "card_id": event["card_id"]}
                           for event in events if event.get("type") == "work-card.present"],
        "worker_marker_exists": marker.exists(),
        "source_reads_blocked": True,
    }
    print(json.dumps(data, sort_keys=True))
    raise SystemExit(0)
raise AssertionError("unknown child mode")
'''


def _safe_child_env() -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env["PYTHONPATH"] = os.pathsep.join((str(_CORE), str(_TESTS)))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_child(root: Path, mode: str, phase: str, *, expected_code: int = 0) -> dict | None:
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), mode, phase], cwd=_CORE,
        env=_safe_child_env(), stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=45, check=False,
    )
    assert result.returncode == expected_code, (
        f"synthetic child {mode}/{phase} exit={result.returncode}; "
        f"stdout={result.stdout[-2000:]!r}, stderr={result.stderr[-2000:]!r}"
    )
    assert "OX567_API_KEY" not in result.stderr and "OPENAI_API_KEY" not in result.stderr
    return None if expected_code != 0 else json.loads(result.stdout)


@pytest.mark.parametrize("phase", [
    "after_refresh_pending",
    "after_refresh_card_save",
    "after_refresh_history_before_intent_mark",
    "after_refresh_intent_mark_before_client_receipt",
])
def test_linked_project_refresh_process_restart_preserves_old_card_and_deduplicates(phase):
    with temporary_agent_paths(prefix="linked-project-refresh-process-") as paths:
        root = paths.agent_root
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        _run_child(root, "crash", phase, expected_code=73)
        # Verify the named crash point before asking startup recovery to consume it.
        staged_parent = Session.load(AgentPaths.from_root(root), _PARENT_ID, message_cap=None)
        from multi_project_work_record_bindings import ConversationWorkRecordBindings
        staged_namespace = ConversationWorkRecordBindings(staged_parent)
        staged_binding = next(row for row in staged_namespace.list_bindings() if "project_session_copy" in row)
        staged_refreshes = staged_namespace.list_project_session_refreshes()
        assert len(staged_refreshes) == 1
        staged_refresh = staged_refreshes[0]
        if phase == "after_refresh_history_before_intent_mark":
            assert (root / "refresh-mark-called.synthetic").exists()
            debug = json.loads((root / "refresh-stage-debug.synthetic").read_text(encoding="utf-8"))
            assert debug["copy_state"] == "pending", debug
        assert staged_binding["project_session_copy"]["intent"]["state"] == "reference_queued"
        assert staged_refresh["copy"]["intent"]["state"] == (
            "reference_queued" if phase == "after_refresh_intent_mark_before_client_receipt" else "pending")
        staged_store = WorkRecordStore(staged_parent)
        staged_cards = WorkCardLedger(staged_parent, staged_store).list_cards()
        staged_refs = [message for message in staged_parent.messages if _valid_work_card_ref(message)]
        assert len(staged_cards) == (1 if phase == "after_refresh_pending" else 2)
        assert len(staged_refs) == (2 if phase in {
            "after_refresh_history_before_intent_mark", "after_refresh_intent_mark_before_client_receipt",
        } else 1)
        source_a = root / "data" / "sessions" / "project-a-session" / "work-records.v1.json"
        source_b = root / "data" / "sessions" / "project-b-session" / "work-records.v1.json"
        source_before = {source_a: source_a.read_bytes(), source_b: source_b.read_bytes()}

        first = _run_child(root, "recover", phase)
        assert first["source_reads_blocked"] is True
        assert first["worker_marker_exists"] is False
        assert source_a.read_bytes() == source_before[source_a]
        assert source_b.read_bytes() == source_before[source_b]
        assert first["original_copy_state"] == "reference_queued"
        assert len(first["refreshes"]) == 1
        refresh = first["refreshes"][0]
        assert refresh["binding_id"] != ""
        assert refresh["copy_state"] == "reference_queued"
        assert len(first["cards"]) == 2
        old_cards = [row for row in first["cards"] if row["snapshot"]["kind"] == "linked_project_session_copy"
                     and row["snapshot"]["source"]["source_revision"] < max(
                         card["snapshot"]["source"]["source_revision"] for card in first["cards"])]
        assert len(old_cards) == 1
        new_cards = [row for row in first["cards"] if row["snapshot"]["source"]["source_revision"]
                     == max(card["snapshot"]["source"]["source_revision"] for card in first["cards"])]
        assert len(new_cards) == 1
        old_card, new_card = old_cards[0], new_cards[0]
        assert old_card["card_id"] != new_card["card_id"]
        assert new_card["snapshot"]["source"]["source_revision"] > old_card["snapshot"]["source"]["source_revision"]
        assert "SYNTHETIC refreshed requirement" in [req["text"] for item in new_card["snapshot"]["source"]["items"] for req in item["requirements"]]
        assert "SYNTHETIC refreshed requirement" not in [req["text"] for item in old_card["snapshot"]["source"]["items"] for req in item["requirements"]]
        assert len(first["references"]) == 2
        old_ref = next(ref for ref in first["references"] if ref["card_id"] == old_card["card_id"])
        new_ref = next(ref for ref in first["references"] if ref["card_id"] == new_card["card_id"])
        assert old_ref["run_id"] != refresh["run_id"]
        assert new_ref["run_id"] == refresh["run_id"]
        assert new_ref["presentation_id"] != old_ref["presentation_id"]
        assert len(first["present_events"]) == 2
        assert len(first["presentation_facts"]) == 2
        assert all(fact["receipt_state"] != "received" and fact["received_at"] is None
                   and fact["acknowledged_at"] is None for fact in first["presentation_facts"])
        refresh_request = linked_project_refresh_request_id(_PARENT_ID, refresh["refresh_id"])
        assert new_card["request_id"] == refresh_request
        refresh_card_id = new_card["card_id"]
        old_card_id = old_card["card_id"]
        refresh_presentation_id = new_ref["presentation_id"]

        second = _run_child(root, "recover", phase)
        assert second["original_copy_state"] == "reference_queued"
        assert len(second["refreshes"]) == 1 and second["refreshes"][0] == refresh
        assert len(second["cards"]) == 2
        assert {row["card_id"] for row in second["cards"]} == {old_card_id, refresh_card_id}
        assert next(row for row in second["cards"] if row["card_id"] == refresh_card_id)["request_id"] == refresh_request
        assert len(second["references"]) == 2
        assert len([row for row in second["references"] if row["card_id"] == refresh_card_id]) == 1
        assert next(row for row in second["references"] if row["card_id"] == refresh_card_id)["presentation_id"] == refresh_presentation_id
        assert next(row for row in second["references"] if row["card_id"] == refresh_card_id)["run_id"] == refresh["run_id"]
        assert len(second["present_events"]) == 2
        assert all(fact["receipt_state"] != "received" and fact["received_at"] is None
                   and fact["acknowledged_at"] is None for fact in second["presentation_facts"])
        assert second["worker_marker_exists"] is False
        assert source_a.read_bytes() == source_before[source_a]
        assert source_b.read_bytes() == source_before[source_b]
