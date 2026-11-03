from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_CORE=Path(__file__).resolve().parents[1]
_TESTS=Path(__file__).resolve().parent
sys.path.insert(0,str(_CORE));sys.path.insert(0,str(_TESTS))

import work_records_luna_recall as luna
from llm_client import LLMClient,LLMResponse
from test_work_card_ledger import make_independent_session
from work_card_result_reply_recognition import (
    ResultReplyCandidate,prepare_result_reply_recognition,recognize_result_reply,
)

CARD='wc-'+'a'*64

def candidate(session, *, suffix='one', summary='Synthetic blocked result'):
    return ResultReplyCandidate(
        session_id=session.conversation_id,
        presentation_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'presentation-'+suffix)),
        card_id=CARD,result_id='result-'+suffix,requirement_id='requirement-'+suffix,
        title='Synthetic '+suffix,requirement_text='Synthetic requirement '+suffix,
        result_summary=summary,result_status='blocked',verification_status='not_verified',
    )

def install_registry(monkeypatch,session):
    entry=SimpleNamespace(id=luna.MODEL_ID,vendor=luna.EXPECTED_VENDOR,
        provider_model=luna.EXPECTED_PROVIDER_MODEL,max_input_tokens=1_000_000,
        chat_completions_url=lambda:'https://api-cdn.0x567.com/v1/chat/completions')
    monkeypatch.setattr(luna,'get_registry',lambda *_args:SimpleNamespace(get=lambda _model:entry))

def provider_response(content):
    raw={'model':'gpt-6-luna','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content,'tool_calls':[]}}]}
    return LLMResponse(model='gpt-6-luna',content=content,tool_calls=[],finish_reason='stop',usage=None,raw=raw)

def install_synthetic_confirmed_call(monkeypatch,*,answer=None,after=None,send_counter=None):
    def fake(self,messages,*,model_id,session_id,expected_preview_json,confirm_fn):
        choice=confirm_fn(expected_preview_json,False)
        if choice!='y':
            raise RuntimeError('synthetic confirmation declined')
        if after: after()
        if send_counter is not None: send_counter.append(1)
        payload=json.loads(messages[1]['content'])
        candidate_id=answer if answer is not None else payload['candidates'][0]['id']
        content=json.dumps({'candidate_id':candidate_id},separators=(',',':'))
        return provider_response(content)
    monkeypatch.setattr(LLMClient,'chat_confirmed_json',fake)

def prepare(session,candidates,*,complete=True,question='What happened to this blocked result?'):
    return prepare_result_reply_recognition(session,question,candidates,candidates_complete=complete)

def test_prepare_only_uses_complete_same_session_candidates_and_opaque_provider_ids(tmp_path,monkeypatch):
    session,*_=make_independent_session(tmp_path,False);install_registry(monkeypatch,session)
    item=candidate(session,suffix='opaque-991',summary='Synthetic result text')
    prepared=prepare(session,[item])
    assert prepared.status=='ready'
    preview=json.loads(prepared.preview_json)
    assert preview['model_id']=='0x567-flash' and preview['vendor']=='0x567'
    assert preview['provider_payload']['model']=='gpt-6-luna'
    provider=json.loads(preview['provider_payload']['messages'][1]['content'])
    assert provider['question']=='What happened to this blocked result?'
    assert len(provider['candidates'])==1
    row=provider['candidates'][0]
    assert set(row)=={'id','title','requirement_text','result_summary','result_status','verification_status'}
    assert row['id'] in prepared.candidate_ids and row['id'] not in {
        item.presentation_id,item.card_id,item.result_id,item.requirement_id,
    }
    assert all(hidden not in preview['provider_payload']['messages'][1]['content'] for hidden in
               (item.presentation_id,item.card_id,item.result_id,item.requirement_id))
    assert prepare(session,[item],complete=False).status=='uncertain'
    assert prepare(session,[candidate(session,suffix='dup'),candidate(session,suffix='dup')]).status=='uncertain'
    assert prepare(session,[candidate(SimpleNamespace(conversation_id='other-session'),suffix='foreign')]).status=='uncertain'

def test_confirmed_match_returns_only_opaque_candidate_and_never_acknowledges(tmp_path,monkeypatch):
    import work_card_result_reply_recognition as recognition_module
    session,store,*_=make_independent_session(tmp_path,False);install_registry(monkeypatch,session)
    item=candidate(session,suffix='alpha');prepared=prepare(session,[item]);client=LLMClient(paths=session.paths)
    before=list(session.messages);calls=[];install_synthetic_confirmed_call(monkeypatch,send_counter=calls)
    confirmations=[]
    result=recognize_result_reply(prepared,client,confirm_fn=lambda preview,allow_all:confirmations.append((preview,allow_all)) or 'y',
        current_session=lambda:session,current_candidates=lambda:[item],is_cancelled=lambda:False)
    assert result.status=='match' and result.candidate_id in prepared.candidate_ids
    assert not hasattr(result,'target')
    assert calls==[1] and len(confirmations)==1 and confirmations[0]==(prepared.preview_json,False)
    assert session.messages==before, "recognition must not append or rebind a user message"
    assert not hasattr(recognition_module,'bind_result_reply_to_persisted_message')


def test_cancel_decline_candidate_change_and_untrusted_model_ids_never_yield_match(tmp_path,monkeypatch):
    session,*_=make_independent_session(tmp_path,False);install_registry(monkeypatch,session)
    first=candidate(session,suffix='first');second=candidate(session,suffix='second')
    prepared=prepare(session,[first,second]);client=LLMClient(paths=session.paths);calls=[]
    install_synthetic_confirmed_call(monkeypatch,answer='forged-result-id',send_counter=calls)
    denied=recognize_result_reply(prepared,client,confirm_fn=lambda *_args:'n',current_session=lambda:session,
        current_candidates=lambda:[first,second],is_cancelled=lambda:False)
    assert denied.status=='uncertain' and calls==[]
    cancelled=recognize_result_reply(prepared,client,confirm_fn=lambda *_args:'y',current_session=lambda:session,
        current_candidates=lambda:[first,second],is_cancelled=lambda:True)
    assert cancelled.status=='cancelled' and calls==[]
    changed=recognize_result_reply(prepared,client,confirm_fn=lambda *_args:'y',current_session=lambda:session,
        current_candidates=lambda:[first],is_cancelled=lambda:False)
    assert changed.status=='uncertain' and calls==[]
    forged=recognize_result_reply(prepared,client,confirm_fn=lambda *_args:'y',current_session=lambda:session,
        current_candidates=lambda:[first,second],is_cancelled=lambda:False)
    assert forged.status=='uncertain' and forged.candidate_id is None and calls==[1]

def test_recognition_module_has_no_ack_or_host_binding_side_effects():
    import work_card_result_reply_recognition as recognition_module
    assert not hasattr(recognition_module,"bind_result_reply_to_persisted_message")
    assert not hasattr(recognition_module,"acknowledge_result")

def test_cancel_state_must_be_exact_false_before_during_and_after_confirmed_call(tmp_path,monkeypatch):
    session,*_=make_independent_session(tmp_path,False);install_registry(monkeypatch,session)
    item=candidate(session,suffix='strict-cancel')
    prepared=prepare(session,[item]);client=LLMClient(paths=session.paths)

    for stage in ('before','during','after'):
        for cancel_value in (True,None,1):
            state={'cancelled':False};sent=[];confirmations=[]
            if stage=='before': state['cancelled']=cancel_value
            def confirm(_preview,_allow_all):
                confirmations.append(1)
                if stage=='during': state['cancelled']=cancel_value
                return 'y'
            after=(lambda:state.__setitem__('cancelled',cancel_value)) if stage=='after' else None
            install_synthetic_confirmed_call(monkeypatch,answer=None,after=after,send_counter=sent)
            result=recognize_result_reply(prepared,client,confirm_fn=confirm,current_session=lambda:session,
                current_candidates=lambda:[item],is_cancelled=lambda:state['cancelled'])
            assert result.status!='match', (stage,cancel_value)
            if stage in ('before','during'):
                assert sent==[], (stage,cancel_value)
            else:
                assert sent==[1], (stage,cancel_value)
            if stage=='before': assert confirmations==[], (stage,cancel_value)
            elif stage in ('during','after'): assert confirmations==[1], (stage,cancel_value)
