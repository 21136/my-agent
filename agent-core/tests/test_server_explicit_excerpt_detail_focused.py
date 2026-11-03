from __future__ import annotations
import asyncio, copy, sys, uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_record_cards import build_independent_work_record_card
from work_card_ledger import WorkCardLedger
from work_card_presentations import WorkCardPresentations
from work_record_settlement import BoundExecution, ExecutionReceipt, WorkRecordSettlementAdapter


def setup(tmp_path, with_excerpt=True):
    paths=AgentPaths(tmp_path,tmp_path/"workspace",tmp_path/"data",tmp_path/"evolve")
    paths.workspace.mkdir(); sid=str(uuid.uuid4()); d=paths.data/"sessions"/sid;d.mkdir(parents=True)
    session=Session(sid,d,"",SessionMeta(),[],paths);session.save()
    store=WorkRecordStore(session)
    item=store.create_item(title="synthetic",scope={"kind":"independent"})
    req=store.add_requirement(item["item_id"],"synthetic",event_id=str(uuid.uuid4()),idempotency_key=f"synthetic:{uuid.uuid4()}")
    run=uuid.uuid4().hex;store.begin_requirement_run(item["item_id"],req["requirement_id"],run_id=run,expected_current_run_id=None)
    adapter=WorkRecordSettlementAdapter(store,BoundExecution(session.conversation_id,item["item_id"],req["requirement_id"],run))
    execution=ExecutionReceipt(session.conversation_id,item["item_id"],req["requirement_id"],run,11,"succeeded",finish_reason="completed")
    assert adapter.settle(execution).status=="recorded"
    if with_excerpt:
        assert adapter.append_assistant_reply_excerpt(execution,assistant_excerpt="synthetic <script>& text",source_availability="unavailable",sources=(),confirm_fn=lambda _: ("y",str(uuid.uuid4()))).status=="recorded"
    result=store.get_item(item["item_id"])["runs"][0]["result_id"]
    ledger=WorkCardLedger(session,store)
    saved=ledger.save_snapshot(str(uuid.uuid4()),build_independent_work_record_card(store,item["item_id"]))
    presentation=str(uuid.uuid4()); session.append_sent_work_card_reference(presentation,saved.card_id,run_id=str(uuid.uuid4()))
    assert WorkCardPresentations(session,store,ledger).create(presentation,saved.card_id).status=="created"
    events=[]; bridge=server.WsBridge(emit=events.append,paths=paths);repl=server._build_repl(session,paths,bridge)
    request={"type":"work-card.result.view","session_id":session.conversation_id,"presentation_id":presentation,"card_id":saved.card_id,"result_id":result,"requirement_id":req["requirement_id"]}
    return session,store,ledger,bridge,repl,request,events,item


@pytest.mark.parametrize("has_excerpt",[True,False])
def test_result_view_returns_only_local_detail_after_authorized_identity(tmp_path,has_excerpt):
    session,store,ledger,bridge,repl,request,events,item=setup(tmp_path,has_excerpt)
    before=copy.deepcopy(ledger.get_card(request["card_id"])["snapshot"])
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming(request,repl,bridge))
    result=events[-1];assert result["status"]=="acknowledged"
    detail=result.get("local_assistant_reply_excerpt_detail")
    if has_excerpt:
        assert detail["session_id"]==session.conversation_id and detail["item_id"]==item["item_id"]
        assert detail["result_id"]==request["result_id"] and detail["requirement_id"]==request["requirement_id"]
        assert detail["escaped_excerpt_text"]=="synthetic &lt;script&gt;&amp; text"
        assert detail["source_availability"]=="unavailable" and detail["verification_status"]=="not_verified"
    else: assert detail is None
    assert ledger.get_card(request["card_id"])["snapshot"]==before


@pytest.mark.parametrize("field,value",[("session_id","foreign"),("result_id","forged"),("requirement_id","forged"),("presentation_id",str(uuid.uuid4()))])
def test_forged_identity_never_returns_detail(tmp_path,field,value):
    session,store,ledger,bridge,repl,request,events,item=setup(tmp_path)
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming({**request,field:value},repl,bridge))
    assert events[-1]["status"] not in {"acknowledged","already_acknowledged"}
    assert "local_assistant_reply_excerpt_detail" not in events[-1]


def test_duplicate_snapshot_result_is_not_uniquely_bound():
    result={"session_id":"s","item_id":"i","requirement_id":"r","result_id":"x"}
    req={"session_id":"s","item_id":"i","requirement_id":"r","latest_result":result}
    card={"session_id":"s","item_id":"i","requirements":[req,copy.deepcopy(req)]}
    snapshot={"session_id":"s","kind":"independent","item_id":"i","card":card}
    assert server._work_card_snapshot_item_for_result(snapshot,session_id="s",requirement_id="r",result_id="x") is None


def test_missing_history_reference_never_projects(tmp_path,monkeypatch):
    import work_record_cards
    session,store,ledger,bridge,repl,request,events,item=setup(tmp_path)
    other=str(uuid.uuid4())
    assert WorkCardPresentations(session,store,ledger).create(other,request['card_id']).status=='created'
    calls=[]
    monkeypatch.setattr(work_record_cards,'project_local_assistant_reply_excerpt_detail',lambda *a,**k:calls.append((a,k)))
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming({**request,'presentation_id':other},repl,bridge))
    assert events[-1]['status']=='rejected' and not calls
    assert 'local_assistant_reply_excerpt_detail' not in events[-1]


def test_failed_projection_preserves_existing_view_ack(tmp_path,monkeypatch):
    import work_record_cards
    session,store,ledger,bridge,repl,request,events,item=setup(tmp_path)
    def fail(*a,**k): raise OSError('synthetic')
    monkeypatch.setattr(work_record_cards,'project_local_assistant_reply_excerpt_detail',fail)
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming(request,repl,bridge))
    assert events[-1]['status']=='acknowledged'
    assert 'local_assistant_reply_excerpt_detail' not in events[-1]


def test_repeated_same_result_out_of_order_response_keeps_request_ids(tmp_path,monkeypatch):
    import threading
    import work_record_cards
    session,store,ledger,bridge,repl,request,events,item=setup(tmp_path)
    first_id,second_id=str(uuid.uuid4()),str(uuid.uuid4())
    entered=threading.Event(); release=threading.Event(); original=work_record_cards.project_local_assistant_reply_excerpt_detail
    calls=[]
    def delayed(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:
            entered.set()
            assert release.wait(4)
        return original(*args,**kwargs)
    monkeypatch.setattr(work_record_cards,'project_local_assistant_reply_excerpt_detail',delayed)
    async def run():
        handler=server.WsSessionHandler(session.paths)
        first=asyncio.create_task(handler._handle_incoming({**request,'request_id':first_id},repl,bridge))
        try:
            assert await asyncio.to_thread(entered.wait,2)
            await asyncio.wait_for(handler._handle_incoming({**request,'request_id':second_id},repl,bridge),3)
            assert events[-1]['request_id']==second_id
        finally:
            release.set()
            await asyncio.wait_for(first,3)
    asyncio.run(run())
    results=[e for e in events if e.get('type')=='work-card.result.view.done']
    assert [e['request_id'] for e in results]==[second_id,first_id]
    assert all(e.get('local_assistant_reply_excerpt_detail',{}).get('result_id')==request['result_id'] for e in results)
    assert {e['status'] for e in results}=={'acknowledged','already_acknowledged'}


@pytest.mark.parametrize('bad',[None,True,123,'', 'A'*36, 'x'*4096, '{12345678-1234-1234-1234-123456789abc}', '12345678123412341234123456789abc'])
def test_invalid_request_id_never_returns_excerpt_or_reflects_bad_value(tmp_path,bad):
    session,store,ledger,bridge,repl,request,events,item=setup(tmp_path)
    asyncio.run(server.WsSessionHandler(session.paths)._handle_incoming({**request,'request_id':bad},repl,bridge))
    result=events[-1]
    assert result['status']=='invalid_request' and 'request_id' not in result
    assert 'local_assistant_reply_excerpt_detail' not in result


def test_valid_request_id_echoed_on_rejection_and_legacy_omits_id(tmp_path):
    session,store,ledger,bridge,repl,request,events,item=setup(tmp_path)
    handler=server.WsSessionHandler(session.paths); ident=str(uuid.uuid4())
    async def run():
        await handler._handle_incoming({**request,'request_id':ident,'result_id':'forged'},repl,bridge)
        assert events[-1]['status']=='rejected' and events[-1]['request_id']==ident
        await handler._handle_incoming(request,repl,bridge)
        assert events[-1]['status']=='acknowledged' and 'request_id' not in events[-1]
    asyncio.run(run())
