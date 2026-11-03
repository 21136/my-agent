from __future__ import annotations
import sys,uuid,threading
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_work_records_service import make_session,FakeProvider
from work_records_service import WorkRecordCaptureService
from work_records import WorkRecordStore

@pytest.fixture
def service(tmp_path,monkeypatch):
    session=make_session(tmp_path);provider=FakeProvider();prompts=[]
    def confirm(preview):prompts.append(preview);return 'yes',str(uuid.uuid4())
    registry=lambda *a,**k:type('R',(),{'get':lambda self,key:type('M',(),{'id':'0x567-flash','vendor':'0x567','provider_model':'gpt-6-luna','max_input_tokens':96000})()})()
    monkeypatch.setattr('work_records_service.get_registry',registry);monkeypatch.setattr('work_records_luna_intent.get_registry',registry)
    svc=WorkRecordCaptureService(session=session,client=provider,confirm=confirm)
    return session,provider,prompts,svc

def test_split_is_memory_only_until_confirm_and_exactly_once(service):
    session,provider,prompts,svc=service
    result=svc.recognize_candidate('synthetic',request_id=str(uuid.uuid4()))
    assert result.status=='candidate' and result.candidate is not None
    assert len(provider.calls)==len(prompts)==1 and WorkRecordStore(session).list_items()==[]
    assert 'synthetic' not in repr(result.candidate)
    saved=svc.confirm_candidate(result.candidate)
    assert saved.status=='recorded' and len(prompts)==2 and len(WorkRecordStore(session).list_items())==1
    assert '合成原文：整理事项' in prompts[-1]
    assert svc.confirm_candidate(result.candidate).status=='invalid_candidate'

def test_foreign_service_rejects_and_does_not_consume(service):
    session,provider,prompts,svc=service
    candidate=svc.recognize_candidate('synthetic',request_id=str(uuid.uuid4())).candidate
    other=WorkRecordCaptureService(session=session,client=provider,confirm=lambda p:('y',str(uuid.uuid4())))
    assert other.confirm_candidate(candidate).status=='invalid_candidate'
    assert svc.confirm_candidate(candidate).status=='recorded'

def test_changed_snapshot_never_prompts_or_saves_candidate(service):
    session,provider,prompts,svc=service
    candidate=svc.recognize_candidate('synthetic',request_id=str(uuid.uuid4())).candidate
    WorkRecordStore(session).create_item(title='other',scope={'kind':'independent'})
    assert svc.confirm_candidate(candidate).status=='stale_context'
    assert len(prompts)==1 and len(WorkRecordStore(session).list_items())==1

def test_concurrent_replies_are_one_shot(service):
    session,provider,prompts,svc=service
    candidate=svc.recognize_candidate('synthetic',request_id=str(uuid.uuid4())).candidate
    results=[];threads=[threading.Thread(target=lambda:results.append(svc.confirm_candidate(candidate).status)) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join(3)
    assert sorted(results)==['invalid_candidate','recorded'] and len(prompts)==2


def test_second_recognition_continues_while_first_storage_confirm_waits(service):
    session,provider,prompts,svc=service
    first=svc.recognize_candidate('first synthetic',request_id=str(uuid.uuid4())).candidate
    entered=threading.Event();release=threading.Event();results=[]
    original=svc._confirm
    def confirm(preview):
        if preview.startswith('工作记录本地落盘确认'):
            entered.set();assert release.wait(3)
        return original(preview)
    svc._confirm=confirm
    thread=threading.Thread(target=lambda:results.append(svc.confirm_candidate(first).status));thread.start()
    try:
        assert entered.wait(2)
        second=svc.recognize_candidate('second synthetic',request_id=str(uuid.uuid4()))
        assert second.status=='candidate' and len(provider.calls)==2
    finally:
        release.set();thread.join(3)
    assert results==['recorded']
    assert svc.confirm_candidate(second.candidate).status=='stale_context'


def test_mutating_candidate_object_cannot_change_preview_or_write(service):
    session,provider,prompts,svc=service
    candidate=svc.recognize_candidate('synthetic',request_id=str(uuid.uuid4())).candidate
    object.__setattr__(candidate,'_title','forged title')
    object.__setattr__(candidate,'_requirement','forged requirement')
    assert svc.confirm_candidate(candidate).status=='recorded'
    assert 'forged title' not in prompts[-1] and 'forged requirement' not in prompts[-1]
    assert WorkRecordStore(session).list_items()[0]['title']=='合成原文：整理事项'


def test_storage_refusal_does_not_write_or_allow_replay(service):
    session,provider,prompts,svc=service
    candidate=svc.recognize_candidate('synthetic',request_id=str(uuid.uuid4())).candidate
    svc._confirm=lambda preview:('n',str(uuid.uuid4()))
    assert svc.confirm_candidate(candidate).status=='storage_not_authorized'
    assert svc.confirm_candidate(candidate).status=='invalid_candidate'
    assert WorkRecordStore(session).list_items()==[]


def test_split_deferred_commit_uses_same_host_guard_and_receipt(service):
    from work_records_service import WorkRecordTerminalRunReceipt
    session,provider,prompts,unused=service
    lock=threading.RLock(); valid={'yes':True}
    svc=WorkRecordCaptureService(session=session,client=provider,confirm=lambda p:(prompts.append(p) or 'y',str(uuid.uuid4())),
        host_binding_is_valid=lambda:valid['yes'],commit_critical_section=lambda:lock,write_policy='create_only')
    req=str(uuid.uuid4());run=uuid.uuid4().hex
    candidate=svc.recognize_candidate('synthetic',request_id=req,track_run=True,defer_commit_until_terminal=True)
    assert candidate.status=='candidate' and WorkRecordStore(session).list_items()==[]
    prepared=svc.confirm_candidate(candidate.candidate)
    assert prepared.status=='awaiting_host_run' and WorkRecordStore(session).list_items()==[]
    receipt=WorkRecordTerminalRunReceipt(session.conversation_id,req,run,'settled',17,'stop')
    result=svc.commit_prepared_capture(prepared.prepared_capture,receipt=receipt)
    assert result.status=='recorded'
    row=WorkRecordStore(session).get_item(result.item_id)['runs'][0]
    assert uuid.UUID(row['run_id']).hex==run and row['status']=='unknown' and row['verification']['status']=='not_verified'


def test_host_revoke_between_recognition_and_confirmation_is_fail_closed(service):
    session,provider,prompts,_=service
    valid={'yes':True}
    svc=WorkRecordCaptureService(session=session,client=provider,confirm=lambda p:(prompts.append(p) or 'y',str(uuid.uuid4())),
        host_binding_is_valid=lambda:valid['yes'],commit_critical_section=lambda:threading.RLock())
    candidate=svc.recognize_candidate('synthetic',request_id=str(uuid.uuid4()),track_run=True,defer_commit_until_terminal=True)
    valid['yes']=False
    assert svc.confirm_candidate(candidate.candidate).status=='stale_context'
    assert len(prompts)==1 and WorkRecordStore(session).list_items()==[]
