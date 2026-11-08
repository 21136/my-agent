from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation_helpers import temporary_agent_paths
from session import Session
from work_card_ledger import WorkCardLedger, linked_item_copy_request_id
from work_records import WorkRecordStore
from paths import AgentPaths

_CORE = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
_PARENT_ID = "parent-dialog"

_CHILD = r'''
import json, os, sys, uuid
from pathlib import Path
from types import SimpleNamespace
root, mode, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path.insert(0, str(root / "agent-core"))
sys.path.insert(0, str(root / "agent-core" / "tests"))
from paths import AgentPaths
from session import Session
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import ConversationWorkRecordBindings, ReadOnlyWorkRecordStore
from work_records import WorkRecordStore, _digest
from work_card_ledger import WorkCardLedger, build_linked_item_copy_snapshot, linked_item_copy_request_id
from server import WsBridge, _resume_linked_item_copy_cards, _emit_pending_work_card_references
paths = AgentPaths.from_root(root)
marker = root / "worker-was-invoked.synthetic"

def prohibit_source_read(*_args, **_kwargs):
    raise AssertionError("recovery attempted to read source WorkRecord")

def crash(code=73):
    sys.stdout.flush(); sys.stderr.flush(); os._exit(code)

if mode == "crash":
    parent, sources, tasks = _fixture(root)
    parent.meta.harness = "desktop"
    parent.meta.active_shell = "unified"
    parent.save()
    tasks = [type(task)(task.project_id, task.item_id, task.requirement_id, task.task_id,
                       task.task_input, task.worker_id, task.run_id,
                       source_item_digest=_digest(sources[task.project_id].store.get_item(task.item_id)))
             for task in tasks]
    namespace = ConversationWorkRecordBindings(parent)
    rows = namespace.bind_existing_tasks(batch_id="subprocess-copy-batch", tasks=tasks,
                                         authorized_sources=sources)
    parent_run_id = str(uuid.uuid4())
    for row in rows:
        namespace.record_worker_start(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], reservation_id="reservation-" + row["task_id"],
            parent_run_id=parent_run_id, turn_epoch=4, project_id=row["project_id"], task_id=row["task_id"])
        namespace.record_worker_lifecycle(binding_id=row["binding_id"], worker_id=row["worker_id"],
            run_id=row["run_id"], sequence=4, status="model_turn_finished", finish_reason="completed")
    proposal = namespace.prepare_item_copies(binding_ids=[rows[0]["binding_id"]],
        authorized_sources={"project-a": sources["project-a"]})
    namespace.commit_confirmed_item_copies(proposed=proposal,
        authorized_sources={"project-a": sources["project-a"]}, is_current=lambda: True)
    if phase == "after_copy_consent":
        crash()
    store = WorkRecordStore(parent)
    copy = namespace.get_binding(rows[0]["binding_id"])["card_copy"]
    snapshot = build_linked_item_copy_snapshot(parent.conversation_id, copy)
    request_id = linked_item_copy_request_id(parent.conversation_id, snapshot.copy_id)
    ledger = WorkCardLedger(parent, store)
    saved = ledger.save_linked_item_copy_snapshot(request_id, snapshot)
    assert saved.status in {"saved", "already_saved"}
    if phase == "after_card_save":
        crash()
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    if phase == "after_history_before_copy_outbox":
        original = ConversationWorkRecordBindings.mark_item_copy_reference_queued
        def crash_before_outbox(*_args, **_kwargs):
            raise OSError("synthetic process interruption after durable history reference")
        ConversationWorkRecordBindings.mark_item_copy_reference_queued = crash_before_outbox
        _resume_linked_item_copy_cards(repl, bridge, parent, store,
            binding_ids=[rows[0]["binding_id"]], emit_event=True)
        ConversationWorkRecordBindings.mark_item_copy_reference_queued = original
        crash()
    if phase == "after_outbox_before_client_receipt":
        _resume_linked_item_copy_cards(repl, bridge, parent, store,
            binding_ids=[rows[0]["binding_id"]], emit_event=True)
        crash()
    raise AssertionError("unknown synthetic crash phase")

if mode == "recover":
    parent = Session.load(paths, "parent-dialog", message_cap=None)
    store = WorkRecordStore(parent)
    events = []
    bridge = WsBridge(emit=events.append, paths=paths)
    repl = SimpleNamespace(session=parent, agent=SimpleNamespace(session=parent))
    ReadOnlyWorkRecordStore._read_items_locked = prohibit_source_read
    ReadOnlyWorkRecordStore.list_items = prohibit_source_read
    ReadOnlyWorkRecordStore.get_item = prohibit_source_read
    from project_worker_host import ProjectWorkerHost
    def forbidden_worker(*_args, **_kwargs):
        marker.write_text("unexpected", encoding="utf-8")
        raise AssertionError("recovery attempted worker execution")
    ProjectWorkerHost.run_many = forbidden_worker
    # Always exercise the real startup recovery chain, including queued-reference receipt replay.
    server_count = _emit_pending_work_card_references(repl, bridge)
    namespace = ConversationWorkRecordBindings(parent)
    bindings = namespace.list_bindings()
    rows = [row for row in bindings if "card_copy" in row]
    cards = WorkCardLedger(parent, store).list_cards()
    from session import _valid_work_card_ref
    refs = [row for row in parent.messages if _valid_work_card_ref(row)]
    data = {
      "server_count": server_count,
      "card_ids": [row["card_id"] for row in cards],
      "request_ids": [row["request_id"] for row in cards],
      "copy_states": [row["card_copy"]["intent"]["state"] for row in rows],
      "references": [{"presentation_id": row["presentation_id"], "card_id": row["card_id"], "run_id": row["run_id"]} for row in refs],
      "present_events": [row for row in events if row.get("type") == "work-card.present"],
      "worker_marker_exists": marker.exists(),
      "source_read_prohibited": True,
    }
    print(json.dumps(data, sort_keys=True))
    raise SystemExit(0)
raise AssertionError("unknown child mode")
'''


def _safe_child_env() -> dict[str, str]:
    # Do not pass provider credentials or user-selected agent storage locations to children.
    allow = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allow}
    env["PYTHONPATH"] = os.pathsep.join((str(_CORE), str(_TESTS)))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_child(root: Path, mode: str, phase: str, *, expected_code: int = 0) -> dict | None:
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), mode, phase],
        cwd=_CORE,
        env=_safe_child_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
    )
    assert result.returncode == expected_code, (
        f"synthetic child {mode}/{phase} exit={result.returncode}, expected={expected_code}; "
        f"stdout={result.stdout[-2000:]!r}, stderr={result.stderr[-2000:]!r}"
    )
    assert "OX567_API_KEY" not in result.stderr and "OPENAI_API_KEY" not in result.stderr
    if expected_code != 0:
        return None
    return json.loads(result.stdout)


@pytest.mark.parametrize("phase", [
    "after_copy_consent",
    "after_card_save",
    "after_history_before_copy_outbox",
    "after_outbox_before_client_receipt",
])
def test_linked_item_copy_subprocess_crash_recovery_is_parent_only_idempotent(phase):
    with temporary_agent_paths(prefix="linked-copy-process-recovery-") as paths:
        root = paths.agent_root
        assert root.is_relative_to(Path(__import__("tempfile").gettempdir()).resolve())
        expected_crash = 73
        _run_child(root, "crash", phase, expected_code=expected_crash)

        source_sidecar = root / "data" / "sessions" / "project-a-session" / "work-records.v1.json"
        before_source = source_sidecar.read_bytes()
        first = _run_child(root, "recover", phase)
        assert first["source_read_prohibited"] is True
        assert first["worker_marker_exists"] is False
        assert source_sidecar.read_bytes() == before_source, "source sidecar must remain byte-for-byte unchanged"
        assert len(first["card_ids"]) == 1
        assert len(first["request_ids"]) == 1
        assert first["copy_states"] == ["reference_queued"]
        assert len(first["references"]) == 1
        assert len(first["present_events"]) == 1

        expected_request = None
        reloaded = Session.load(AgentPaths.from_root(root), _PARENT_ID, message_cap=None)
        from multi_project_work_record_bindings import ConversationWorkRecordBindings
        namespace = ConversationWorkRecordBindings(reloaded)
        row = next(item for item in namespace.list_bindings() if item.get("card_copy"))
        expected_request = linked_item_copy_request_id(_PARENT_ID, row["card_copy"]["intent"]["copy_id"])
        assert first["request_ids"] == [expected_request]
        card_id = first["card_ids"][0]
        presentation_id = first["references"][0]["presentation_id"]
        assert first["references"][0]["card_id"] == card_id

        # A second fresh process must reuse the same immutable card/reference identity.
        second = _run_child(root, "recover", phase)
        assert second["card_ids"] == [card_id]
        assert second["request_ids"] == [expected_request]
        assert len(second["references"]) == 1
        assert second["references"][0]["presentation_id"] == presentation_id
        assert second["references"][0]["card_id"] == card_id
        assert len(second["present_events"]) == 1
        assert second["worker_marker_exists"] is False
        assert source_sidecar.read_bytes() == before_source
