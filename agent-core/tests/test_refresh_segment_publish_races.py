from pathlib import Path
import hashlib, json, os, sys, uuid
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_linked_project_session_delivery import setup_copy
from test_multi_project_work_record_bindings import _session
from multi_project_work_record_bindings import (
    ReadOnlyWorkRecordStore, _issue_authorized_project_record_source, WorkRecordCorrupt,
)
import multi_project_work_record_bindings as module


def test_segment_publisher_never_overwrites_existing_content_addressed_target(tmp_path, monkeypatch):
    parent, ns, _rows, *_ = setup_copy(tmp_path)
    row={"refresh_id":str(uuid.uuid4()),"synthetic":"complete row"}
    raw=(json.dumps(row,ensure_ascii=False,separators=(",",":"),sort_keys=True)+"\n").encode()
    digest=hashlib.sha256(raw).hexdigest()
    target=parent.session_dir/(".project-refresh-"+digest+".json")
    target.write_bytes(b"UNRELATED")
    before=target.stat()
    monkeypatch.setattr(module.os,"replace",lambda *_a,**_k: pytest.fail("path replacement is forbidden"))
    with pytest.raises(WorkRecordCorrupt,match="digest mismatch"):
        ns._write_refresh_segment_locked(row)
    after=target.stat()
    assert target.read_bytes()==b"UNRELATED"
    assert (after.st_dev,after.st_ino)==(before.st_dev,before.st_ino)


def test_segment_publisher_removes_its_stage_but_preserves_unrelated_temp(tmp_path,monkeypatch):
    parent, ns, _rows, *_ = setup_copy(tmp_path)
    unrelated=parent.session_dir/".project-refresh.other.tmp"
    unrelated.write_bytes(b"UNRELATED")
    before=unrelated.stat()
    monkeypatch.setattr(module.tempfile,"mkstemp",lambda *_a,**_k: pytest.fail("pathname tempfile API is forbidden"))
    row={"refresh_id":str(uuid.uuid4()),"synthetic":"complete row"}
    entry=ns._write_refresh_segment_locked(row)
    target=parent.session_dir/(".project-refresh-"+entry["sha256"]+".json")
    raw=target.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==entry["sha256"]
    assert json.loads(raw)==row
    assert list(parent.session_dir.glob(".project-refresh-stage.*.tmp"))==[]
    after=unrelated.stat()
    assert unrelated.read_bytes()==b"UNRELATED"
    assert (after.st_dev,after.st_ino)==(before.st_dev,before.st_ino)


def test_revoked_turn_cleans_only_its_unindexed_segment_and_retains_pending_intent(tmp_path,monkeypatch):
    from server import _work_card_presentation_id
    from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_refresh_request_id
    from work_card_presentations import WorkCardPresentations
    from work_records import WorkRecordStore
    parent, ns, rows, *_ = setup_copy(tmp_path)
    data=ns._read();data["schema_version"]=2;data["project_refreshes"]=[];data["refresh_segments"]=[];data["revision"]+=1
    ns._validate_payload(data);ns._publish(data)
    source_session=_session(tmp_path,"project-a-session","project-a")
    source=_issue_authorized_project_record_source("fresh-revoke",parent.conversation_id,"project-a",ReadOnlyWorkRecordStore(source_session))
    opts=dict(project_id="project-a",binding_ids=[rows[0]["binding_id"]],source=source,
        refresh_id=str(uuid.uuid4()),refresh_run_id=str(uuid.uuid4()),refresh_turn_epoch=2)
    preview=ns.prepare_project_session_refresh(**opts)
    pending=ns.prepare_project_session_refresh(**opts,expected=preview,commit=True,is_current=lambda *_:True)
    store=WorkRecordStore(parent);ledger=WorkCardLedger(parent,store)
    snapshot=build_linked_project_session_copy_snapshot(parent.conversation_id,pending["binding_id"],pending["copy"])
    saved=ledger.save_linked_project_session_copy_snapshot(linked_project_refresh_request_id(parent.conversation_id,pending["refresh_id"]),snapshot)
    presentation=_work_card_presentation_id(parent.conversation_id,pending["run_id"],saved.card_id)
    assert WorkCardPresentations(parent,store,ledger).create(presentation,saved.card_id,run_id=pending["run_id"]).status=="created"
    pending_manifest=ns._read()["refresh_segments"][0]
    pending_path=parent.session_dir/(".project-refresh-"+pending_manifest["sha256"]+".json")
    pending_bytes=pending_path.read_bytes()
    active=[True];original=ns._write_refresh_segment_locked
    def revoke_after_write(row):
        result=original(row);active[0]=False;return result
    monkeypatch.setattr(ns,"_write_refresh_segment_locked",revoke_after_write)
    result=ns.mark_project_refresh_reference_queued(refresh_id=pending["refresh_id"],card_id=saved.card_id,
        presentation_id=presentation,is_current=lambda:active[0])
    assert result=="rejected"
    assert ns.list_project_session_refreshes()[0]["copy"]["intent"]["state"]=="pending"
    assert ns._read()["refresh_segments"]==[pending_manifest]
    assert pending_path.read_bytes()==pending_bytes
    assert len(list(parent.session_dir.glob(".project-refresh-*.json")))==1


def test_existing_segment_is_append_only_and_queued_update_uses_new_content_address(tmp_path):
    from server import _work_card_presentation_id
    from work_card_ledger import WorkCardLedger, build_linked_project_session_copy_snapshot, linked_project_refresh_request_id
    from work_card_presentations import WorkCardPresentations
    from work_records import WorkRecordStore
    parent,ns,rows,*_=setup_copy(tmp_path)
    source_session=_session(tmp_path,"project-a-session","project-a")
    source=_issue_authorized_project_record_source("prior-segment",parent.conversation_id,"project-a",ReadOnlyWorkRecordStore(source_session))
    opts=dict(project_id="project-a",binding_ids=[rows[0]["binding_id"]],source=source,refresh_id=str(uuid.uuid4()),refresh_run_id=str(uuid.uuid4()),refresh_turn_epoch=2)
    preview=ns.prepare_project_session_refresh(**opts)
    old=ns.prepare_project_session_refresh(**opts,expected=preview,commit=True,is_current=lambda *_:True)
    data=ns._read();data["project_refreshes"]=[];data["schema_version"]=2
    raw=(json.dumps(old,ensure_ascii=False,separators=(",",":"),sort_keys=True)+"\n").encode();digest=hashlib.sha256(raw).hexdigest()
    old_path=parent.session_dir/(".project-refresh-"+digest+".json");old_path.write_bytes(raw)
    data["refresh_segments"]=[{"refresh_id":old["refresh_id"],"sha256":digest}];data["revision"]+=1
    ns._validate_payload(data);ns._publish(data);old_bytes=old_path.read_bytes()
    assert ns.list_project_session_refreshes()==[old]
    fresh=_issue_authorized_project_record_source("next-segment",parent.conversation_id,"project-a",ReadOnlyWorkRecordStore(source_session))
    opts2={**opts,"source":fresh,"refresh_id":str(uuid.uuid4()),"refresh_run_id":str(uuid.uuid4()),"refresh_turn_epoch":3}
    preview2=ns.prepare_project_session_refresh(**opts2)
    second=ns.prepare_project_session_refresh(**opts2,expected=preview2,commit=True,is_current=lambda *_:True)
    current=ns._read()
    assert current["schema_version"]==2 and len(current["refresh_segments"])==2
    assert old_path.read_bytes()==old_bytes
    assert {r["refresh_id"] for r in ns.list_project_session_refreshes()}=={old["refresh_id"],second["refresh_id"]}

    store=WorkRecordStore(parent);ledger=WorkCardLedger(parent,store)
    snapshot=build_linked_project_session_copy_snapshot(parent.conversation_id,old["binding_id"],old["copy"])
    saved=ledger.save_linked_project_session_copy_snapshot(linked_project_refresh_request_id(parent.conversation_id,old["refresh_id"]),snapshot)
    presentation=_work_card_presentation_id(parent.conversation_id,old["run_id"],saved.card_id)
    assert WorkCardPresentations(parent,store,ledger).create(presentation,saved.card_id,run_id=old["run_id"]).status=="created"
    assert ns.mark_project_refresh_reference_queued(refresh_id=old["refresh_id"],card_id=saved.card_id,
        presentation_id=presentation,is_current=lambda:True)=="queued"
    after=ns._read();manifests=after["refresh_segments"]
    old_entry=next(entry for entry in manifests if entry["refresh_id"]==old["refresh_id"])
    assert old_entry["sha256"]!=digest and old_path.read_bytes()==old_bytes
    listed=ns.list_project_session_refreshes()
    assert next(r for r in listed if r["refresh_id"]==old["refresh_id"])["copy"]["intent"]["state"]=="reference_queued"
    assert next(r for r in listed if r["refresh_id"]==second["refresh_id"])["copy"]["intent"]["state"]=="pending"
    assert ns.mark_project_refresh_reference_queued(refresh_id=old["refresh_id"],card_id=saved.card_id,
        presentation_id=presentation,is_current=lambda:True)=="already_queued"
    assert old_path.read_bytes()==old_bytes


def test_segment_writer_preserves_unowned_file_with_reserved_stage_name(tmp_path):
    parent,ns,_rows,*_=setup_copy(tmp_path)
    unrelated=parent.session_dir/(".project-refresh-stage."+uuid.uuid4().hex+".tmp")
    unrelated.write_bytes(b"USER-OWNED-SYNTHETIC")
    before=unrelated.stat()
    entry=ns._write_refresh_segment_locked({"refresh_id":str(uuid.uuid4()),"synthetic":"row"})
    after=unrelated.stat()
    assert unrelated.read_bytes()==b"USER-OWNED-SYNTHETIC"
    assert (after.st_dev,after.st_ino)==(before.st_dev,before.st_ino)
    assert (parent.session_dir/(".project-refresh-"+entry["sha256"]+".json")).is_file()


@pytest.mark.skipif(sys.platform!="win32",reason="Windows handle lifecycle regression")
def test_segment_writer_closes_handle_when_pre_rename_cleanup_fails(tmp_path,monkeypatch):
    import ctypes
    from ctypes import wintypes
    parent,ns,_rows,*_=setup_copy(tmp_path)
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    get_count=kernel.GetProcessHandleCount
    get_count.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    get_count.restype=wintypes.BOOL
    current=kernel.GetCurrentProcess
    current.restype=wintypes.HANDLE
    def handle_count():
        value=wintypes.DWORD()
        assert get_count(current(),ctypes.byref(value))
        return value.value
    original_scope=ns._validate_scope
    calls=0
    def fail_after_open():
        nonlocal calls
        calls+=1
        if calls%2==0:
            raise PermissionError("synthetic revoke before rename")
        return original_scope()
    def delete_fails(_kernel,_handle):
        raise PermissionError("synthetic identity cleanup failure")
    monkeypatch.setattr(ns,"_validate_scope",fail_after_open)
    monkeypatch.setattr(module,"_delete_open_file_handle",delete_fails)
    before=handle_count()
    for _ in range(25):
        with pytest.raises(PermissionError,match="synthetic identity cleanup failure"):
            ns._write_refresh_segment_locked({"refresh_id":str(uuid.uuid4()),"synthetic":"row"})
    after=handle_count()
    assert after<=before, f"native handle leak: {after-before} handles after failed cleanup"
    assert len(list(parent.session_dir.glob(".project-refresh-stage.*.tmp")))==25


@pytest.mark.skipif(sys.platform!='win32',reason='Windows no-replace primitive probe')
def test_windows_rename_refuses_existing_destination(tmp_path):
    src=tmp_path/'source';dst=tmp_path/'existing'
    src.write_bytes(b'OWNED');dst.write_bytes(b'UNRELATED')
    with pytest.raises(FileExistsError): os.rename(src,dst)
    assert src.read_bytes()==b'OWNED' and dst.read_bytes()==b'UNRELATED'
