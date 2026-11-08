from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation_helpers import temporary_agent_paths
from paths import AgentPaths
from session import Session
from multi_project_work_record_bindings import ConversationWorkRecordBindings
from work_card_ledger import linked_project_refresh_request_id

_CORE = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
_PARENT_ID = "parent-dialog"

_CHILD = r'''
import copy, json, os, sys, uuid
from pathlib import Path
from types import SimpleNamespace
root, mode, phase, refresh_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, str(root / "agent-core")); sys.path.insert(0, str(root / "agent-core" / "tests"))
from paths import AgentPaths
from session import Session, _valid_work_card_ref
import session as session_module
assert Path(session_module.__file__).resolve(strict=True) == (root / "agent-core" / "session.py").resolve(strict=True)
assert AgentPaths.discover().agent_root == root.resolve(strict=True)
from test_multi_project_work_record_bindings import _fixture
from multi_project_work_record_bindings import (ConversationWorkRecordBindings, ReadOnlyWorkRecordStore,
    _issue_authorized_project_record_source, _reader_for)
from work_records import WorkRecordStore, _digest
from work_card_ledger import WorkCardLedger, linked_project_refresh_request_id
from work_card_presentations import WorkCardPresentations
from server import WsBridge, _emit_pending_work_card_references, _resume_linked_project_session_copies, _resume_linked_project_session_refreshes
paths=AgentPaths.from_root(root)
worker_marker=root/"worker-was-invoked.synthetic"
old_rows_path=root/"expected-old-v1-refresh-rows.json"
old_card_id_path=root/"expected-original-card-id.txt"
new_refresh_id=refresh_id

def fail(*_args,**_kwargs):
    raise AssertionError("restart attempted source WorkRecord read")

def hard_exit():
    sys.stdout.flush(); sys.stderr.flush(); os._exit(73)

def build_parent_state():
    parent,sources,tasks=_fixture(root)
    parent.meta.harness="desktop"; parent.meta.active_shell="unified"; parent.save()
    tasks=[type(task)(task.project_id,task.item_id,task.requirement_id,task.task_id,task.task_input,
                      task.worker_id,task.run_id,source_item_digest=_digest(sources[task.project_id].store.get_item(task.item_id))
                      ) for task in tasks]
    ns=ConversationWorkRecordBindings(parent)
    rows=ns.bind_existing_tasks(batch_id="refresh-segment-original",tasks=tasks,authorized_sources=sources)
    parent_run=str(uuid.uuid4())
    for row in rows:
        ns.record_worker_start(binding_id=row["binding_id"],worker_id=row["worker_id"],run_id=row["run_id"],
            reservation_id="original-res-"+row["task_id"],parent_run_id=parent_run,turn_epoch=3,
            project_id=row["project_id"],task_id=row["task_id"])
        ns.record_worker_lifecycle(binding_id=row["binding_id"],worker_id=row["worker_id"],run_id=row["run_id"],
            sequence=3,status="model_turn_finished",finish_reason="completed")
    anchor=next(row for row in rows if row["project_id"]=="project-a")
    preview=ns.prepare_project_session_copy(project_id="project-a",binding_ids=[anchor["binding_id"]],source=sources["project-a"])
    ns.prepare_project_session_copy(project_id="project-a",binding_ids=[anchor["binding_id"]],source=sources["project-a"],
        expected=preview,commit=True,is_current=lambda:True)
    store=WorkRecordStore(parent); events=[]; bridge=WsBridge(emit=events.append,paths=paths)
    repl=SimpleNamespace(session=parent,agent=SimpleNamespace(session=parent))
    assert _resume_linked_project_session_copies(repl,bridge,parent,store,binding_ids=[anchor["binding_id"]],emit_event=True)==1
    original_card=next(row for row in WorkCardLedger(parent,store).list_cards()
                       if row["snapshot"].get("kind")=="linked_project_session_copy")
    old_card_id_path.write_text(original_card["card_id"],encoding="ascii")

    # Build 100 validated legacy inline refresh rows so the next refresh uses a
    # content-addressed segment. Historical rows are identical valid v1 data.
    source_session=_reader_for(sources["project-a"].store)._session
    auth=_issue_authorized_project_record_source("seed-refresh-0",parent.conversation_id,"project-a",ReadOnlyWorkRecordStore(source_session))
    seed_id=str(uuid.uuid4()); seed_run=str(uuid.uuid4())
    seed_args=dict(project_id="project-a",binding_ids=[anchor["binding_id"]],source=auth,
        refresh_id=seed_id,refresh_run_id=seed_run,refresh_turn_epoch=4)
    seed_preview=ns.prepare_project_session_refresh(**seed_args)
    ns.prepare_project_session_refresh(**seed_args,expected=seed_preview,commit=True,is_current=lambda *_a:True)
    data=ns._read(); data["project_refreshes"][0]["copy"]["intent"]["state"]="reference_queued"
    first=copy.deepcopy(data["project_refreshes"][0])
    for index in range(1,100):
        row=copy.deepcopy(first); row["refresh_id"]=str(uuid.uuid4()); row["run_id"]=str(uuid.uuid4())
        row["authorization_id"]=f"synthetic-old-auth-{index}"
        data["project_refreshes"].append(row)
    data["revision"]+=1; ns._validate_payload(data); ns._publish(data)
    old_rows=data["project_refreshes"]
    old_rows_path.write_text(json.dumps(old_rows,ensure_ascii=False,sort_keys=True),encoding="utf-8")
    return parent,sources,ns,anchor,store,bridge,repl

if mode=="crash":
    parent,sources,ns,anchor,store,bridge,repl=build_parent_state()
    source_session=_reader_for(sources["project-a"].store)._session
    auth=_issue_authorized_project_record_source("fresh-segment-auth",parent.conversation_id,"project-a",ReadOnlyWorkRecordStore(source_session))
    run_id=str(uuid.uuid4())
    options=dict(project_id="project-a",binding_ids=[anchor["binding_id"]],source=auth,
        refresh_id=new_refresh_id,refresh_run_id=run_id,refresh_turn_epoch=8)
    preview=ns.prepare_project_session_refresh(**options)
    if phase=="segment_written_before_parent_index":
        original=ns._write_refresh_segment_locked
        def write_then_exit(row):
            result=original(row)
            if row["refresh_id"]==new_refresh_id: hard_exit()
            return result
        ns._write_refresh_segment_locked=write_then_exit
    if phase=="parent_index_committed_before_return":
        original=ns._publish
        def publish_then_exit(data):
            original(data)
            if any(entry.get("refresh_id")==new_refresh_id for entry in data.get("refresh_segments",[])):
                hard_exit()
        ns._publish=publish_then_exit
    ns.prepare_project_session_refresh(**options,expected=preview,commit=True,is_current=lambda *_a:True)
    if phase=="queued_segment_written_before_parent_index":
        original=ConversationWorkRecordBindings._write_refresh_segment_locked
        def queued_segment_then_exit(self,row):
            result=original(self,row)
            if row["refresh_id"]==new_refresh_id and row["copy"]["intent"]["state"]=="reference_queued":
                hard_exit()
            return result
        ConversationWorkRecordBindings._write_refresh_segment_locked=queued_segment_then_exit
    if phase=="queued_parent_index_committed_before_return":
        original=ConversationWorkRecordBindings.mark_project_refresh_reference_queued
        def queued_index_then_exit(self,**kwargs):
            result=original(self,**kwargs)
            if kwargs.get("refresh_id")==new_refresh_id and result in {"queued","already_queued"}: hard_exit()
            return result
        ConversationWorkRecordBindings.mark_project_refresh_reference_queued=queued_index_then_exit
    if phase in {"queued_segment_written_before_parent_index","queued_parent_index_committed_before_return"}:
        _resume_linked_project_session_refreshes(repl,bridge,parent,store,
            binding_ids=[anchor["binding_id"]],emit_event=True)
    raise AssertionError("selected synthetic crash hook did not fire")

if mode=="recover":
    parent=Session.load(paths,"parent-dialog",message_cap=None)
    store=WorkRecordStore(parent); events=[]; bridge=WsBridge(emit=events.append,paths=paths)
    repl=SimpleNamespace(session=parent,agent=SimpleNamespace(session=parent))
    original_read=WorkRecordStore._read
    def parent_only_read(self,*args,**kwargs):
        if self._conversation_id!=parent.conversation_id: return fail()
        return original_read(self,*args,**kwargs)
    WorkRecordStore._read=parent_only_read
    ReadOnlyWorkRecordStore._read_items_locked=fail
    ReadOnlyWorkRecordStore.list_items=fail
    ReadOnlyWorkRecordStore.get_item=fail
    from project_worker_host import ProjectWorkerHost
    def worker_forbidden(*_args,**_kwargs):
        worker_marker.write_text("unexpected",encoding="utf-8")
        raise AssertionError("refresh recovery executed worker")
    ProjectWorkerHost.run_many=worker_forbidden; ProjectWorkerHost.run=worker_forbidden
    emitted=_emit_pending_work_card_references(repl,bridge)
    ns=ConversationWorkRecordBindings(parent)
    data=ns._read(); refreshes=ns.list_project_session_refreshes()
    cards=WorkCardLedger(parent,store).list_cards()
    refs=[row for row in parent.messages if _valid_work_card_ref(row)]
    old_rows=json.loads(old_rows_path.read_text(encoding="utf-8"))
    old_card_id=old_card_id_path.read_text(encoding="ascii")
    presentations=WorkCardPresentations(parent,store,WorkCardLedger(parent,store))
    facts=[]
    for ref in refs:
        fact=presentations.get(ref["presentation_id"]).fact
        facts.append({"presentation_id":ref["presentation_id"],"card_id":ref["card_id"],"run_id":ref.get("run_id"),
            "receipt_state":fact.receipt_state if fact else None,"received_at":fact.received_at if fact else None,
            "acknowledged_at":fact.acknowledged_at if fact else None})
    data_out={"emitted":emitted,"schema_version":data["schema_version"],
        "inline_refresh_rows":data["project_refreshes"],"expected_old_inline_rows":old_rows,
        "segments":data.get("refresh_segments",[]),
        "refreshes":[{"refresh_id":r["refresh_id"],"run_id":r["run_id"],"copy_state":r["copy"]["intent"]["state"]} for r in refreshes],
        "cards":[{"card_id":r["card_id"],"request_id":r["request_id"],"snapshot":r["snapshot"]} for r in cards],
        "references":facts,"worker_marker_exists":worker_marker.exists()}
    print(json.dumps(data_out,sort_keys=True)); raise SystemExit(0)
raise AssertionError("unknown child mode")
'''


def _child(root: Path, mode: str, phase: str, refresh_id: str, expected: int = 0):
    keep={"PATH","SYSTEMROOT","WINDIR","TEMP","TMP","USERPROFILE","HOMEDRIVE","HOMEPATH"}
    env={key:value for key,value in os.environ.items() if key.upper() in keep}
    env["PYTHONDONTWRITEBYTECODE"]="1"
    env["PYTHONUTF8"]="1"
    env["PYTHONIOENCODING"]="utf-8"
    env["MY_AGENT_ROOT"]=str(root.resolve(strict=True))
    result=subprocess.run([sys.executable,"-c",_CHILD,str(root),mode,phase,refresh_id],
        cwd=_CORE,env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,
        encoding="utf-8",errors="replace",timeout=60,check=False)
    assert result.returncode==expected, f"{mode}/{phase} exit={result.returncode}; stdout={result.stdout[-1500:]!r}; stderr={result.stderr[-2000:]!r}"
    assert "OX567_API_KEY" not in result.stderr and "OPENAI_API_KEY" not in result.stderr
    return None if expected else json.loads(result.stdout)


@pytest.mark.parametrize("phase",[
    "segment_written_before_parent_index",
    "parent_index_committed_before_return",
    "queued_segment_written_before_parent_index",
    "queued_parent_index_committed_before_return",
])
def test_refresh_segment_process_crash_recovery_is_atomic_idempotent_and_parent_only(phase, monkeypatch):
    # The helper discovers its code source from MY_AGENT_ROOT. Bind that source
    # to this test's checkout before it creates its isolated temporary root.
    monkeypatch.setenv("MY_AGENT_ROOT", str(_CORE.parent.resolve(strict=True)))
    with temporary_agent_paths(prefix="refresh-segment-process-crash-") as paths:
        # The temporary root must link the exact checkout containing this test,
        # regardless of the directory from which pytest was launched.
        assert (paths.agent_root / "agent-core").resolve(strict=True) == _CORE.resolve(strict=True)
        refresh_id=str(uuid.uuid4())
        _child(paths.agent_root,"crash",phase,refresh_id,expected=73)
        # Inspect the exact durable parent index and every content-addressed segment
        # before restart recovery consumes or advances it.
        staged_session=Session.load(AgentPaths.from_root(paths.agent_root),_PARENT_ID,message_cap=None)
        staged_ns=ConversationWorkRecordBindings(staged_session)
        staged_data=staged_ns._read()
        expected_old_bytes=(paths.agent_root/"expected-old-v1-refresh-rows.json").read_bytes()
        assert json.dumps(staged_data["project_refreshes"],ensure_ascii=False,sort_keys=True).encode("utf-8")==expected_old_bytes
        session_dir=staged_session.session_dir
        segment_files=list(session_dir.glob(".project-refresh-*.json"))
        indexed=staged_data.get("refresh_segments",[])
        indexed_ids={entry["refresh_id"] for entry in indexed}
        parsed_segments=[]
        for segment_path in segment_files:
            raw=segment_path.read_bytes()
            digest=segment_path.name.removeprefix(".project-refresh-").removesuffix(".json")
            assert hashlib.sha256(raw).hexdigest()==digest
            parsed_segments.append(json.loads(raw.decode("utf-8")))
        for entry in indexed:
            segment_path=session_dir/(".project-refresh-"+entry["sha256"]+".json")
            assert segment_path.is_file(), "parent index may reference only a complete existing segment"
            assert hashlib.sha256(segment_path.read_bytes()).hexdigest()==entry["sha256"]
        indexed_refreshes=staged_ns._refresh_rows_locked(staged_data)
        indexed_new=next((row for row in indexed_refreshes if row["refresh_id"]==refresh_id),None)
        if phase=="segment_written_before_parent_index":
            # Confirmed pending intent is durable in the prepared slot before
            # segment publication; the complete orphan is not yet index-visible.
            assert staged_data["schema_version"]==3 and not indexed
            prepared=staged_data["refresh_prepared"]
            assert prepared["refresh_id"]==refresh_id
            assert prepared["copy"]["intent"]["state"]=="pending"
            assert indexed_new==prepared
            orphan=next(row for row in parsed_segments if row["refresh_id"]==refresh_id)
            assert orphan["copy"]["intent"]["state"]=="pending"
        elif phase=="parent_index_committed_before_return":
            assert staged_data["schema_version"]==2 and len(indexed)==1
            assert indexed_new is not None and indexed_new["copy"]["intent"]["state"]=="pending"
            assert refresh_id in indexed_ids
        elif phase=="queued_segment_written_before_parent_index":
            assert indexed_new is not None and indexed_new["copy"]["intent"]["state"]=="pending"
            queued_orphan=next(row for row in parsed_segments if row["refresh_id"]==refresh_id
                               and row["copy"]["intent"]["state"]=="reference_queued")
            assert queued_orphan["refresh_id"]==refresh_id and refresh_id in indexed_ids
        else:
            assert indexed_new is not None and indexed_new["copy"]["intent"]["state"]=="reference_queued"
            assert refresh_id in indexed_ids
        first=_child(paths.agent_root,"recover",phase,refresh_id)
        assert first["worker_marker_exists"] is False
        assert first["inline_refresh_rows"] == first["expected_old_inline_rows"]
        assert len(first["inline_refresh_rows"]) == 100
        old_card_id=(paths.agent_root/"expected-original-card-id.txt").read_text(encoding="ascii")
        old_cards=[row for row in first["cards"] if row["card_id"]==old_card_id]
        assert len(old_cards)==1
        assert old_cards[0]["snapshot"]["source"]["project_id"]=="project-a"
        rows=[row for row in first["refreshes"] if row["refresh_id"]==refresh_id]
        cards=[row for row in first["cards"] if row["request_id"]==linked_project_refresh_request_id(_PARENT_ID,refresh_id)]
        refs=[row for row in first["references"] if row["card_id"] in {card["card_id"] for card in cards}]

        assert first["schema_version"]==2
        assert len(first["segments"])==1
        assert len(rows)==1 and len(cards)==1 and len(refs)==1
        assert rows[0]["run_id"]!=old_cards[0]["snapshot"]["source"]["worker_facts"][0]["host_start"]["parent_run_id"]
        assert rows[0]["copy_state"]=="reference_queued"
        assert refs[0]["run_id"]==rows[0]["run_id"]
        assert refs[0]["receipt_state"]!="received" and refs[0]["received_at"] is None and refs[0]["acknowledged_at"] is None
        assert len(first["cards"])==2 and len(first["references"])==2
        second=_child(paths.agent_root,"recover",phase,refresh_id)
        assert second["inline_refresh_rows"]==first["inline_refresh_rows"]
        assert len(second["cards"])==len(first["cards"])
        assert len(second["references"])==len(first["references"])
        assert len([row for row in second["cards"] if row["card_id"]==old_card_id])==1
        assert second["worker_marker_exists"] is False
        if phase!="segment_written_before_parent_index":
            assert second["refreshes"]==first["refreshes"]
            assert second["segments"]==first["segments"]
            assert len([row for row in second["cards"] if row["request_id"]==linked_project_refresh_request_id(_PARENT_ID,refresh_id)])==1
            assert len([row for row in second["references"] if row["run_id"]==rows[0]["run_id"]])==1
