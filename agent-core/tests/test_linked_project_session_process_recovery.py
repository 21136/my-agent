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
from work_card_ledger import WorkCardLedger, linked_project_session_copy_request_id
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
from multi_project_work_record_bindings import ConversationWorkRecordBindings, ReadOnlyWorkRecordStore, _reader_for
from work_records import WorkRecordStore, _digest
from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_session_copy_request_id
from server import WsBridge, _resume_linked_project_session_copies, _emit_pending_work_card_references
paths = AgentPaths.from_root(root)
marker = root / "worker-was-invoked.synthetic"

def crash(code=73):
    sys.stdout.flush(); sys.stderr.flush(); os._exit(code)

def prohibit(*_args, **_kwargs):
    raise AssertionError("restart attempted source-session read")

if mode == "crash":
    parent, sources, tasks = _fixture(root)
    parent.meta.harness = "desktop"
    parent.meta.active_shell = "unified"
    parent.save()
    source_a = _reader_for(sources["project-a"].store)
    source_a.add_requirement(tasks[0].item_id, "SYNTHETIC second requirement A1", event_id="synthetic-extra-req")
    item_a2 = source_a.create_item(title="SYNTHETIC project A second item",
        scope={"kind": "project", "project_id": "project-a"})
    source_a.add_requirement(item_a2["item_id"], "SYNTHETIC requirement A2", event_id="synthetic-a2-req")
    tasks = [replace(task, source_item_digest=_digest(sources[task.project_id].store.get_item(task.item_id)))
             for task in tasks]
    namespace = ConversationWorkRecordBindings(parent)
    rows = namespace.bind_existing_tasks(batch_id="synthetic-project-copy-batch", tasks=tasks,
                                         authorized_sources=sources)
    parent_run_id = str(uuid.uuid4())
    for row in rows:
        namespace.record_worker_start(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], reservation_id="synthetic-reservation-" + row["task_id"],
            parent_run_id=parent_run_id, turn_epoch=5, project_id=row["project_id"], task_id=row["task_id"])
        namespace.record_worker_lifecycle(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], sequence=5, status="model_turn_finished", finish_reason="completed")
    anchor = next(row for row in rows if row["project_id"] == "project-a")
    proposal = namespace.prepare_project_session_copy(project_id="project-a",
        binding_ids=[anchor["binding_id"]], source=sources["project-a"])
    namespace.prepare_project_session_copy(project_id="project-a", binding_ids=[anchor["binding_id"]],
        source=sources["project-a"], expected=proposal, is_current=lambda: True, commit=True)
    if phase == "after_copy_confirmation":
        crash()
    store = WorkRecordStore(parent)
    copy = namespace.get_binding(anchor["binding_id"])["project_session_copy"]
    snapshot = build_linked_project_session_copy_snapshot(parent.conversation_id, anchor["binding_id"], copy)
    request_id = linked_project_session_copy_request_id(parent.conversation_id, snapshot.copy_id)
    ledger = WorkCardLedger(parent, store)
    saved = ledger.save_linked_project_session_copy_snapshot(request_id, snapshot)
    assert saved.status in {"saved", "already_saved"}
    if phase == "after_card_save":
        crash()
    bridge = WsBridge(emit=lambda _event: None, paths=paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    if phase == "after_history_before_copy_outbox":
        original = ConversationWorkRecordBindings.mark_project_session_copy_reference_queued
        def fail_copy_outbox(*_args, **_kwargs):
            raise OSError("synthetic process crash before copy intent advancement")
        ConversationWorkRecordBindings.mark_project_session_copy_reference_queued = fail_copy_outbox
        _resume_linked_project_session_copies(repl, bridge, parent, store,
            binding_ids=[anchor["binding_id"]], emit_event=True)
        ConversationWorkRecordBindings.mark_project_session_copy_reference_queued = original
        crash()
    if phase == "after_outbox_before_client_receipt":
        _resume_linked_project_session_copies(repl, bridge, parent, store,
            binding_ids=[anchor["binding_id"]], emit_event=True)
        crash()
    raise AssertionError("unknown synthetic crash phase")

if mode == "recover":
    parent = Session.load(paths, "parent-dialog", message_cap=None)
    store = WorkRecordStore(parent)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    # Source reads are forbidden in this restarted process. A failure is fatal to recovery.
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
        raise AssertionError("restart attempted worker execution")
    ProjectWorkerHost.run_many = forbidden_worker
    emitted = _emit_pending_work_card_references(repl, bridge)
    namespace = ConversationWorkRecordBindings(parent)
    copied = [row for row in namespace.list_bindings() if "project_session_copy" in row]
    cards = WorkCardLedger(parent, store).list_cards()
    refs = [row for row in parent.messages if _valid_work_card_ref(row)]
    facts = []
    for ref in refs:
        from work_card_presentations import WorkCardPresentations
        fact = WorkCardPresentations(parent, store, WorkCardLedger(parent, store)).get(ref["presentation_id"]).fact
        facts.append(None if fact is None else {"receipt_state": fact.receipt_state,
            "received_at": fact.received_at, "acknowledged_at": fact.acknowledged_at})
    data = {
        "emitted": emitted,
        "cards": [{"card_id": row["card_id"], "request_id": row["request_id"],
                   "snapshot": row["snapshot"]} for row in cards],
        "copy_states": [row["project_session_copy"]["intent"]["state"] for row in copied],
        "references": [{"presentation_id": row["presentation_id"], "card_id": row["card_id"],
                        "run_id": row["run_id"]} for row in refs],
        "present_events": [{"presentation_id": row["presentation_id"], "card_id": row["card_id"]}
                           for row in events if row.get("type") == "work-card.present"],
        "receipt_facts": facts,
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
    "after_copy_confirmation",
    "after_card_save",
    "after_history_before_copy_outbox",
    "after_outbox_before_client_receipt",
])
def test_linked_project_session_copy_process_restart_replays_parent_snapshot_only(phase):
    with temporary_agent_paths(prefix="linked-project-copy-process-recovery-") as paths:
        root = paths.agent_root
        assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        _run_child(root, "crash", phase, expected_code=73)
        source_a = root / "data" / "sessions" / "project-a-session" / "work-records.v1.json"
        source_b = root / "data" / "sessions" / "project-b-session" / "work-records.v1.json"
        source_before = {source_a: source_a.read_bytes(), source_b: source_b.read_bytes()}

        first = _run_child(root, "recover", phase)
        assert first["source_reads_blocked"] is True
        assert first["worker_marker_exists"] is False
        assert source_a.read_bytes() == source_before[source_a]
        assert source_b.read_bytes() == source_before[source_b]
        assert len(first["cards"]) == 1
        card = first["cards"][0]
        snapshot = card["snapshot"]
        assert snapshot["kind"] == "linked_project_session_copy"
        assert snapshot["source"]["project_id"] == "project-a"
        assert snapshot["source"]["source_session_id"] == "project-a-session"
        assert snapshot["source"]["scope"] == "authorized_source_session_only"
        assert snapshot["source"]["mapping_claim"] == "not_asserted"
        assert len(snapshot["source"]["items"]) == 2
        assert sum(len(item["requirements"]) for item in snapshot["source"]["items"]) == 3
        assert len(snapshot["source"]["worker_facts"]) == 1
        assert all(fact["operation_result"] == "unknown" and fact["verification"] == "not_verified"
                   for fact in snapshot["source"]["worker_facts"])
        assert first["copy_states"] == ["reference_queued"]
        assert len(first["references"]) == 1
        assert len(first["present_events"]) == 1
        assert first["receipt_facts"] == [{"receipt_state": "pending", "received_at": None, "acknowledged_at": None}]
        expected_request = linked_project_session_copy_request_id(_PARENT_ID, snapshot["copy_id"])
        assert card["request_id"] == expected_request
        card_id = card["card_id"]
        presentation_id = first["references"][0]["presentation_id"]
        assert first["references"][0]["card_id"] == card_id

        second = _run_child(root, "recover", phase)
        assert len(second["cards"]) == 1
        assert second["cards"][0]["card_id"] == card_id
        assert second["cards"][0]["request_id"] == expected_request
        assert len(second["references"]) == 1
        assert second["references"][0]["presentation_id"] == presentation_id
        assert second["references"][0]["card_id"] == card_id
        assert len(second["present_events"]) == 1
        assert second["receipt_facts"] == [{"receipt_state": "pending", "received_at": None, "acknowledged_at": None}]
        assert second["worker_marker_exists"] is False
        assert source_a.read_bytes() == source_before[source_a]
        assert source_b.read_bytes() == source_before[source_b]
