from __future__ import annotations
import copy
import sys
import uuid
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_linked_project_session_delivery import setup_copy
from multi_project_work_record_bindings import ConversationWorkRecordBindings, _issue_authorized_project_record_source, WorkRecordWriteError


def test_refresh_101_remains_accessible_after_reopen(tmp_path, monkeypatch):
    parent, ns, rows, store, events, bridge, repl = setup_copy(tmp_path)
    from multi_project_work_record_bindings import ConversationWorkRecordBindings, ReadOnlyWorkRecordStore
    # Existing authorized source fixture is backed by the same isolated temporary root.
    from test_multi_project_work_record_bindings import _session
    source_session = _session(tmp_path, 'project-a-session', 'project-a')
    source = _issue_authorized_project_record_source('fresh-first', parent.conversation_id, 'project-a', ReadOnlyWorkRecordStore(source_session))
    opts = dict(project_id='project-a', binding_ids=[rows[0]['binding_id']], source=source,
                refresh_id=str(uuid.uuid4()), refresh_run_id=str(uuid.uuid4()), refresh_turn_epoch=5)
    preview=ns.prepare_project_session_refresh(**opts)
    first=ns.prepare_project_session_refresh(**opts, expected=preview, commit=True, is_current=lambda *a: True)
    # Seed valid legacy history efficiently without skipping its real schema validator.
    data=ns._read()
    data['project_refreshes'][0]['copy']['intent']['state'] = 'reference_queued'
    first = copy.deepcopy(data['project_refreshes'][0])
    for n in range(1,100):
        row=copy.deepcopy(first)
        row['refresh_id']=str(uuid.uuid4()); row['run_id']=str(uuid.uuid4()); row['authorization_id']=f'historical-{n}'
        data['project_refreshes'].append(row)
    data['revision']+=1
    ns._validate_payload(data); ns._publish(data)
    source2 = _issue_authorized_project_record_source('fresh-101', parent.conversation_id, 'project-a', ReadOnlyWorkRecordStore(source_session))
    opts2={**opts,'source':source2,'refresh_id':str(uuid.uuid4()),'refresh_run_id':str(uuid.uuid4())}
    preview2=ns.prepare_project_session_refresh(**opts2)
    original_v1 = copy.deepcopy(ns._read()['project_refreshes'])
    original_publish = ns._publish
    monkeypatch.setattr(ns, '_publish', lambda data: (_ for _ in ()).throw(OSError('synthetic index crash')))
    with pytest.raises(OSError):
        ns.prepare_project_session_refresh(**opts2,expected=preview2,commit=True,is_current=lambda *a: True)
    assert ns._read()['project_refreshes'] == original_v1
    assert len(ns.list_project_session_refreshes()) == 100
    monkeypatch.setattr(ns, '_publish', original_publish)
    added=ns.prepare_project_session_refresh(**opts2,expected=preview2,commit=True,is_current=lambda *a: True)
    reopened=ConversationWorkRecordBindings(parent)
    refreshes=reopened.list_project_session_refreshes()
    assert len(refreshes)==101 and refreshes[0]==first and refreshes[-1]==added
    assert ns.prepare_project_session_refresh(**opts2,expected=preview2,commit=True,is_current=lambda *a: True)==added

    # The confirmed 101st snapshot is atomically indexed after a durable prepared stage.
    current=ns._read()
    assert current['schema_version']==2 and current['project_refreshes']==original_v1
    assert current.get('refresh_prepared') is None
    assert len(current['refresh_segments'])==1
    # Actual server recovery validates the segmented row against the ledger.
    import server
    from server import WsBridge
    from work_card_ledger import WorkCardLedger
    from types import SimpleNamespace
    repl=SimpleNamespace(session=parent,agent=SimpleNamespace(session=parent))
    transport=WsBridge(emit=events.append,paths=parent.paths)
    original_class_publish=ConversationWorkRecordBindings._publish
    monkeypatch.setattr(ConversationWorkRecordBindings,'_publish',lambda *a,**k: (_ for _ in ()).throw(OSError('synthetic crash after queued segment')))
    assert server._resume_linked_project_session_refreshes(repl,transport,parent,store,emit_event=True)==0
    assert reopened.list_project_session_refreshes()[-1]['copy']['intent']['state']=='pending'
    assert len(WorkCardLedger(parent,store).list_cards())==1
    monkeypatch.setattr(ConversationWorkRecordBindings,'_publish',original_class_publish)
    assert server._resume_linked_project_session_refreshes(repl,transport,parent,store,emit_event=True)==1
    assert reopened.list_project_session_refreshes()[-1]['copy']['intent']['state']=='reference_queued'
    assert len(WorkCardLedger(parent,store).list_cards())==1
    assert server._resume_linked_project_session_refreshes(repl,transport,parent,store,emit_event=True)==0
    assert len(WorkCardLedger(parent,store).list_cards())==1
    # Queueing the refresh advances its segment without occupying the prepared slot.
    current=ns._read()
    assert current['schema_version']==2 and current.get('refresh_prepared') is None
    assert len(current['refresh_segments'])==1
    assert current['project_refreshes']==original_v1

    # A second post-cap refresh is also durable, recoverable and queueable.
    source3=_issue_authorized_project_record_source('fresh-102',parent.conversation_id,'project-a',ReadOnlyWorkRecordStore(source_session))
    opts3={**opts2,'source':source3,'refresh_id':str(uuid.uuid4()),'refresh_run_id':str(uuid.uuid4()),'refresh_turn_epoch':6}
    preview3=ns.prepare_project_session_refresh(**opts3)
    added2=ns.prepare_project_session_refresh(**opts3,expected=preview3,commit=True,is_current=lambda *a:True)
    assert len(reopened.list_project_session_refreshes())==102
    assert ns._read()['schema_version']==2 and ns._read().get('refresh_prepared') is None
    assert len(ns._read()['refresh_segments'])==2
    assert server._resume_linked_project_session_refreshes(repl,transport,parent,store,emit_event=True)==1
    current=ns._read()
    assert current['schema_version']==2 and current.get('refresh_prepared') is None
    assert len(current['refresh_segments'])==2 and current['project_refreshes']==original_v1
    assert len(reopened.list_project_session_refreshes())==102

    # Two concurrent retries of the same identity must reuse the durable row.
    from concurrent.futures import ThreadPoolExecutor
    def retry():
        return ConversationWorkRecordBindings(parent, lock_timeout=10).prepare_project_session_refresh(
            **opts3, expected=preview3, commit=True, is_current=lambda *a: True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: retry(), range(2)))
    latest = reopened.list_project_session_refreshes()[-1]
    assert a == b == latest
    assert latest["refresh_id"] == added2["refresh_id"]
    assert latest["copy"]["snapshot"] == added2["copy"]["snapshot"]
    assert latest["copy"]["intent"]["state"] == "reference_queued"
    assert len(reopened.list_project_session_refreshes()) == 102

    from work_records_overview_api import query_work_records_overview
    overview=query_work_records_overview(parent)
    linked=[g for g in overview['groups'] if g.get('kind')=='linked_source_snapshot']
    assert linked and linked[0]['source_snapshot']['copy_id']==added2['copy']['intent']['copy_id']
    assert linked[0]['source_snapshot']['intent_state']=='reference_queued'
    # The committed content-addressed segment cannot be silently missing or corrupted.
    from work_records import WorkRecordCorrupt
    manifest=reopened._read()['refresh_segments'][-1]
    segment=parent.session_dir / ('.project-refresh-'+manifest['sha256']+'.json')
    segment.write_bytes(b'corrupt')
    with pytest.raises(WorkRecordCorrupt):
        reopened.list_project_session_refreshes()


def test_new_confirmed_refresh_promotes_prior_prepared_without_dropping_intent(tmp_path, monkeypatch):
    parent,ns,rows,_store,_events,_bridge,_repl=setup_copy(tmp_path)
    from test_multi_project_work_record_bindings import _session
    from multi_project_work_record_bindings import ReadOnlyWorkRecordStore
    source_session=_session(tmp_path,"project-a-session","project-a")
    def authorized(name):
        return _issue_authorized_project_record_source(name,parent.conversation_id,"project-a",ReadOnlyWorkRecordStore(source_session))
    base=dict(project_id="project-a",binding_ids=[rows[0]["binding_id"]],refresh_run_id=str(uuid.uuid4()),refresh_turn_epoch=5)
    first_source=authorized("seed-inline")
    first_opts={**base,"source":first_source,"refresh_id":str(uuid.uuid4())}
    first_preview=ns.prepare_project_session_refresh(**first_opts)
    first=ns.prepare_project_session_refresh(**first_opts,expected=first_preview,commit=True,is_current=lambda *_:True)
    data=ns._read();data["project_refreshes"][0]["copy"]["intent"]["state"]="reference_queued";seed=copy.deepcopy(data["project_refreshes"][0])
    for i in range(1,100):
        old=copy.deepcopy(seed);old["refresh_id"]=str(uuid.uuid4());old["run_id"]=str(uuid.uuid4());old["authorization_id"]=f"prepared-seed-{i}"
        data["project_refreshes"].append(old)
    data["revision"]+=1;ns._validate_payload(data);ns._publish(data)
    source101=authorized("fresh-101");opts101={**base,"source":source101,"refresh_id":str(uuid.uuid4()),"refresh_run_id":str(uuid.uuid4())}
    preview101=ns.prepare_project_session_refresh(**opts101)
    original_writer=ns._write_refresh_segment_locked
    def fail_101(row):
        if row["refresh_id"]==opts101["refresh_id"]:
            raise OSError("synthetic segment failure after prepared commit")
        return original_writer(row)
    monkeypatch.setattr(ns,"_write_refresh_segment_locked",fail_101)
    with pytest.raises(OSError,match="synthetic segment failure"):
        ns.prepare_project_session_refresh(**opts101,expected=preview101,commit=True,is_current=lambda *_:True)
    prepared=ns._read()["refresh_prepared"]
    assert prepared["refresh_id"]==opts101["refresh_id"]
    assert prepared["copy"]["intent"]["state"]=="pending"
    assert ns.list_project_session_refreshes()[-1]==prepared
    assert ns._read()["project_refreshes"]==data["project_refreshes"]

    # A new preview must remain read-only and must not release the pending intent.
    source102=authorized("fresh-102");opts102={**base,"source":source102,"refresh_id":str(uuid.uuid4()),"refresh_run_id":str(uuid.uuid4()),"refresh_turn_epoch":6}
    preview102=ns.prepare_project_session_refresh(**opts102)
    assert ns._read()["refresh_prepared"]==prepared
    assert ns._read()["project_refreshes"]==data["project_refreshes"]

    monkeypatch.setattr(ns,"_write_refresh_segment_locked",original_writer)
    second=ns.prepare_project_session_refresh(**opts102,expected=preview102,commit=True,is_current=lambda *_:True)
    current=ns._read()
    assert current["schema_version"]==2 and current.get("refresh_prepared") is None
    assert current["project_refreshes"]==data["project_refreshes"]
    assert len(current["refresh_segments"])==2
    listed=ns.list_project_session_refreshes()
    assert len(listed)==102
    first_pending=next(row for row in listed if row["refresh_id"]==prepared["refresh_id"])
    second_pending=next(row for row in listed if row["refresh_id"]==second["refresh_id"])
    assert first_pending["copy"]["intent"]["state"]=="pending"
    assert second_pending["copy"]["intent"]["state"]=="pending"
    assert ns.prepare_project_session_refresh(**opts102,expected=preview102,commit=True,is_current=lambda *_:True)==second
    assert len(ns.list_project_session_refreshes())==102

def test_refresh_segment_capacity_refuses_before_publishing_unreadable_index(tmp_path, monkeypatch):
    import multi_project_work_record_bindings as bindings_module
    from multi_project_work_record_bindings import ReadOnlyWorkRecordStore
    from test_multi_project_work_record_bindings import _session

    parent, ns, rows, _store, _events, _bridge, _repl = setup_copy(tmp_path)
    source_session = _session(tmp_path, "project-a-session", "project-a")
    anchor = rows[0]

    def authorized(label):
        return _issue_authorized_project_record_source(
            label, parent.conversation_id, "project-a", ReadOnlyWorkRecordStore(source_session),
        )

    def prepare(source, refresh_id):
        options = {
            "project_id": "project-a", "binding_ids": [anchor["binding_id"]], "source": source,
            "refresh_id": refresh_id, "refresh_run_id": str(uuid.uuid4()), "refresh_turn_epoch": 5,
        }
        preview = ns.prepare_project_session_refresh(**options)
        return ns.prepare_project_session_refresh(
            **options, expected=preview, commit=True, is_current=lambda *_args: True,
        )

    first = prepare(authorized("segment-cap-first"), str(uuid.uuid4()))
    data = ns._read()
    first = copy.deepcopy(data["project_refreshes"][0])
    for index in range(1, 100):
        old = copy.deepcopy(first)
        old["refresh_id"] = str(uuid.uuid4())
        old["run_id"] = str(uuid.uuid4())
        old["authorization_id"] = f"legacy-refresh-{index}"
        data["project_refreshes"].append(old)
    data["revision"] += 1
    ns._validate_payload(data)
    ns._publish(data)

    monkeypatch.setattr(bindings_module, "_MAX_REFRESH_SEGMENTS", 1, raising=False)
    second = prepare(authorized("segment-cap-second"), str(uuid.uuid4()))
    assert ns._read()["schema_version"] == 2
    before = ns._path.read_bytes()
    with pytest.raises(WorkRecordWriteError):
        prepare(authorized("segment-cap-third"), str(uuid.uuid4()))

    assert ns._path.read_bytes() == before
    refreshed = ConversationWorkRecordBindings(parent)
    assert len(refreshed.list_project_session_refreshes()) == 101
    assert refreshed._read()["schema_version"] == 2
    assert refreshed.list_project_session_refreshes()[-1]["refresh_id"] == second["refresh_id"]

