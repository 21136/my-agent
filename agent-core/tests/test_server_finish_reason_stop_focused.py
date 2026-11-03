from __future__ import annotations
import asyncio, sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import server
from work_records import WorkRecordStore
from llm_client import LLMResponse
from test_server_explicit_run_excerpt_focused import fixture, FakeLLM, test_explicit_record_synthetic_ws_post_turn_excerpt as exercise_synthetic_ws


def test_real_ws_provider_stop_preserves_exact_host_reason_and_excerpt(tmp_path,monkeypatch):
    facts=[]
    original=WorkRecordStore.record_independent_server_settlement_if_current_run
    def record(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        facts.append(kwargs['verification'])
        return result
    monkeypatch.setattr(WorkRecordStore,'record_independent_server_settlement_if_current_run',record)
    exercise_synthetic_ws(tmp_path,monkeypatch,'approve')
    assert len(facts)==1
    assert facts[0]['host_execution']['finish_reason']=='stop'
    assert facts[0]['status']=='not_verified'


@pytest.mark.parametrize('reason,expected', [('stop','stop'),('unexpected-provider-label','unknown'),('cancelled','cancelled')])
def test_terminal_bound_finish_reason_is_only_fact_not_success(tmp_path,monkeypatch,reason,expected):
    monkeypatch.setattr(server,'TURN_LOCK',asyncio.Lock())
    def chat(self,*args,**kwargs):
        return LLMResponse(model='synthetic',content='synthetic reply',tool_calls=[],finish_reason=reason,usage=None,raw={})
    monkeypatch.setattr(FakeLLM,'chat',chat)
    session,store,item,req,run_id,request,bridge,events,run=fixture(tmp_path,monkeypatch)
    asyncio.run(run(False))
    row=store.get_item(item['item_id'])['runs'][0]
    assert row['run_id']==run_id and row['status']=='unknown'
    assert row['verification']['status']=='not_verified'
    assert row['verification']['host_execution']['finish_reason']==expected


@pytest.mark.parametrize('kind',['cancelled_stop','missing_reason','wrong_run'])
def test_untrusted_or_inconsistent_terminal_facts_do_not_become_stop(tmp_path,monkeypatch,kind):
    import dataclasses, uuid
    monkeypatch.setattr(server,'TURN_LOCK',asyncio.Lock())
    session,store,item,req,run_id,request,bridge,events,run=fixture(tmp_path,monkeypatch)
    original_settle=server._settle_work_record_run
    original_snapshot=bridge.lifecycle.snapshot
    def settle_with_synthetic_snapshot(**kwargs):
        snapshot=original_snapshot()
        if kind=='cancelled_stop': changed=dataclasses.replace(snapshot,finish_reason='stop',cancel_requested=True)
        elif kind=='missing_reason': changed=dataclasses.replace(snapshot,finish_reason=None)
        else: changed=dataclasses.replace(snapshot,run_id=uuid.uuid4().hex)
        monkeypatch.setattr(bridge.lifecycle,'snapshot',lambda:changed)
        try:return original_settle(**kwargs)
        finally:monkeypatch.setattr(bridge.lifecycle,'snapshot',original_snapshot)
    monkeypatch.setattr(server,'_settle_work_record_run',settle_with_synthetic_snapshot)
    asyncio.run(run(False))
    rows=store.get_item(item['item_id'])['runs']
    if kind=='wrong_run':
        assert not rows or not rows[0].get('result_id')
    else:
        row=rows[0]
        assert row['verification']['host_execution']['finish_reason']=='unknown'
        assert row['status']=='unknown' and row['verification']['status']=='not_verified'
