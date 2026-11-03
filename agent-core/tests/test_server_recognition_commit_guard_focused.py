from __future__ import annotations
import sys, threading
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parent))
import server
from work_record_recognition_queue import RecognitionQueue

class Store:
    def __init__(self,session): self._session=session;self.calls=[]
    def create_item_with_requirement(self,*args,**kwargs): self.calls.append((args,kwargs));return 'saved'

def ticket():
    connection=object();session=object();queue=RecognitionQueue();queue.authorize(connection,session)
    admission=queue.enqueue(connection,session);lease=queue.claim_next()
    assert lease.request_token==admission.request_token
    pending=queue.finish_recognition(lease,connection,session,candidate=True)
    return connection,session,queue,pending.ticket

def test_guard_allows_exact_ticket_only_once():
    c,s,q,t=ticket();store=Store(s)
    guarded=server._guard_auto_record_atomic_store(store,q,c,s,t)
    assert guarded.create_item_with_requirement(title='synthetic')=='saved'
    with pytest.raises(Exception):guarded.create_item_with_requirement(title='synthetic')
    assert len(store.calls)==1

def test_revoked_ticket_cannot_write():
    c,s,q,t=ticket();store=Store(s);guarded=server._guard_auto_record_atomic_store(store,q,c,s,t)
    assert q.revoke(c,s)
    with pytest.raises(Exception):guarded.create_item_with_requirement(title='synthetic')
    assert store.calls==[]

def test_store_failure_not_replayed():
    c,s,q,t=ticket();store=Store(s)
    def fail(*a,**k):store.calls.append(1);raise OSError('synthetic')
    store.create_item_with_requirement=fail
    guarded=server._guard_auto_record_atomic_store(store,q,c,s,t)
    with pytest.raises(Exception):guarded.create_item_with_requirement(title='synthetic')
    with pytest.raises(Exception):guarded.create_item_with_requirement(title='synthetic')
    assert store.calls==[1]


def test_split_service_real_atomic_write_is_guarded(tmp_path,monkeypatch):
    import uuid
    from test_work_records_service import make_session,FakeProvider
    from work_records_service import WorkRecordCaptureService,WorkRecordTerminalRunReceipt
    from work_records import WorkRecordStore
    from work_record_recognition_queue import QueueStatus
    session=make_session(tmp_path);provider=FakeProvider();connection=object();queue=RecognitionQueue();queue.authorize(connection,session)
    registry=lambda *a,**k:type('R',(),{'get':lambda self,key:type('M',(),{'id':'0x567-flash','vendor':'0x567','provider_model':'gpt-6-luna','max_input_tokens':96000})()})()
    monkeypatch.setattr('work_records_service.get_registry',registry);monkeypatch.setattr('work_records_luna_intent.get_registry',registry)
    state={'ticket':None}
    def valid():return state['ticket'] is None or queue.ticket_status(state['ticket'],connection,session)==QueueStatus.CANDIDATE_READY
    lock=threading.RLock()
    service=WorkRecordCaptureService(session=session,client=provider,confirm=lambda _:('y',str(uuid.uuid4())),host_binding_is_valid=valid,commit_critical_section=lambda:lock,write_policy='create_only')
    request=str(uuid.uuid4());admission=queue.enqueue(connection,session);lease=queue.claim_next()
    candidate=service.recognize_candidate('synthetic',request_id=request,track_run=True,defer_commit_until_terminal=True)
    assert candidate.status=='candidate'
    state['ticket']=queue.finish_recognition(lease,connection,session,candidate=True).ticket
    prepared=service.confirm_candidate(candidate.candidate);assert prepared.status=='awaiting_host_run'
    service._store=server._guard_auto_record_atomic_store(service._store,queue,connection,session,state['ticket'])
    receipt=WorkRecordTerminalRunReceipt(session.conversation_id,request,uuid.uuid4().hex,'settled',5,'stop')
    result=service.commit_prepared_capture(prepared.prepared_capture,receipt=receipt)
    assert result.status=='recorded' and len(WorkRecordStore(session).list_items())==1
    assert queue.ticket_status(state['ticket'],connection,session)==QueueStatus.INVALID_TICKET
