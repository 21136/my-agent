import json
from unittest.mock import patch
import httpx
import pytest
import llm_client
from llm_models import ModelRegistry
from tests.test_confirmed_llm_request import (make_entry, make_registry, make_client, install_transport, response_json, provider_payload, _MODEL_ID, _SESSION_ID)


def preview(entry, batches):
    return json.dumps({'model_id': entry.id, 'vendor': entry.vendor, 'endpoint': entry.chat_completions_url(), 'session_id': _SESSION_ID, 'provider_payloads': [provider_payload(entry, b) for b in batches]}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def call(batches, p, confirm=lambda p, a: 'y', before=lambda: True, validator=lambda i, r: True):
    return make_client().chat_confirmed_json_batch(batches, model_id=_MODEL_ID, session_id=_SESSION_ID, expected_preview_json=p, confirm_fn=confirm, before_each=before, response_validator=validator)


def test_exact_bytes_one_confirmation_and_freeze(monkeypatch):
    e = make_entry(); batches = [[{'role':'user','content':'one'}], [{'role':'user','content':'二'}]]; p = preview(e,batches); confirmations=[]; checks=[]
    requests, opts = install_transport(monkeypatch, lambda r: httpx.Response(200,json=response_json()))
    def confirm(value, allow):
        confirmations.append((value, allow)); batches[1][0]['content']='changed'; return 'y'
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
        result = call(batches,p,confirm,lambda: checks.append(1) or True)
    assert len(result)==2 and confirmations==[(p,False)] and len(checks)>=3
    payloads=json.loads(p)['provider_payloads']
    assert [r.content for r in requests]==[json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode() for x in payloads]
    assert all(x['trust_env'] is False and x['follow_redirects'] is False for x in opts)


def test_deny_tamper_limits_never_send(monkeypatch):
    e=make_entry(); b=[[{'role':'user','content':'x'}]]; p=preview(e,b)
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)), patch.object(llm_client.httpx,'Client') as transport:
        for confirm, expected in [(lambda p,a:'n',p),(lambda p,a:(_ for _ in ()).throw(ValueError('secret')),p),(lambda p,a:'y',p+' ')]:
            with pytest.raises(Exception): call(b,expected,confirm)
        with pytest.raises(Exception): call(b*33,preview(e,b*33))
        huge=[[{'role':'user','content':'x'*(2*1024*1024)}]]
        with pytest.raises(Exception): call(huge,preview(e,huge))
        transport.assert_not_called()


def test_failure_stops_and_reports_attempts(monkeypatch):
    e=make_entry(); b=[[{'role':'user','content':str(i)}] for i in range(3)]; p=preview(e,b)
    requests,_=install_transport(monkeypatch,lambda r: httpx.Response(302,headers={'location':'https://other.example/'}))
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
        with pytest.raises(llm_client.LLMConfirmedBatchError) as caught: call(b,p)
    assert len(requests)==1 and caught.value.attempted==1 and caught.value.remaining==2


def test_changed_before_each_and_configuration_stop(monkeypatch):
    e=make_entry(); b=[[{'role':'user','content':str(i)}] for i in range(2)]; p=preview(e,b); count=[]
    requests,_=install_transport(monkeypatch,lambda r: httpx.Response(200,json=response_json()))
    def before():
        count.append(1); return len(count)<3
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
        with pytest.raises(llm_client.LLMConfirmedBatchError) as caught: call(b,p,before=before)
    assert len(requests)==1 and caught.value.attempted==1 and caught.value.remaining==1
    requests.clear()
    with patch.object(ModelRegistry,'load',side_effect=[make_registry(e),make_registry(make_entry(provider_model='changed'))]):
        with pytest.raises(llm_client.LLMConfirmedBatchError) as caught: call(b,p)
    assert not requests and caught.value.attempted==0



def test_cancel_and_transport_error_do_not_retry(monkeypatch):
    import threading
    e=make_entry(); b=[[{'role':'user','content':str(i)}] for i in range(2)]; p=preview(e,b)
    requests,_=install_transport(monkeypatch,lambda r: (_ for _ in ()).throw(httpx.ConnectError('secret',request=r)))
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
        with pytest.raises(llm_client.LLMConfirmedBatchError) as caught: call(b,p)
    assert len(requests)==1 and caught.value.attempted==1 and caught.value.remaining==1
    assert 'secret' not in str(caught.value)
    requests.clear(); event=threading.Event(); client=make_client(); client.set_cancel_event(event)
    def confirm(value, allow): event.set(); return 'y'
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
        with pytest.raises(llm_client.LLMConfirmedBatchError) as caught:
            client.chat_confirmed_json_batch(b,model_id=_MODEL_ID,session_id=_SESSION_ID,expected_preview_json=p,confirm_fn=confirm,before_each=lambda:True,response_validator=lambda i,r:True)
    assert not requests and caught.value.attempted==0


@pytest.mark.parametrize("validator", [lambda i,r: False, lambda i,r: 1,
    lambda i,r: (_ for _ in ()).throw(ValueError("sensitive"))])
def test_bad_first_batch_stops_before_next_post(monkeypatch, validator):
    e=make_entry(); batches=[[{'role':'user','content':str(i)}] for i in range(3)]
    p=preview(e,batches)
    requests,_=install_transport(monkeypatch, lambda r: httpx.Response(200,json=response_json()))
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
        with pytest.raises(llm_client.LLMConfirmedBatchError) as error:
            call(batches,p,validator=validator)
    assert len(requests)==1 and error.value.attempted==1 and error.value.remaining==2
    assert 'sensitive' not in str(error.value)


def test_bad_response_identity_json_or_id_stops(monkeypatch):
    e=make_entry(); batches=[[{'role':'user','content':str(i)}] for i in range(2)]; p=preview(e,batches)
    for bad in [
        {'model':'wrong','choices':[{'message':{'role':'assistant','content':'{\"id\":\"ok\"}'},'finish_reason':'stop'}]},
        {'model':'safe-json-1','choices':[{'message':{'role':'assistant','content':'not json'},'finish_reason':'stop'}]},
        {'model':'safe-json-1','choices':[{'message':{'role':'assistant','content':'{\"id\":\"outside\"}'},'finish_reason':'stop'}]},
    ]:
        requests,_=install_transport(monkeypatch,lambda r: httpx.Response(200,json=bad))
        def validator(index, response):
            return response.model == 'safe-json-1' and json.loads(response.content)['id'] == 'ok'
        with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
            with pytest.raises(llm_client.LLMConfirmedBatchError): call(batches,p,validator=validator)
        assert len(requests)==1


def test_validator_cannot_change_frozen_later_body(monkeypatch):
    e=make_entry(); batches=[[{'role':'user','content':'first'}],[{'role':'user','content':'original'}]]; p=preview(e,batches)
    requests,_=install_transport(monkeypatch,lambda r:httpx.Response(200,json=response_json()))
    def validate(index, response):
        batches[1][0]['content']='changed'
        return True
    with patch.object(ModelRegistry,'load',return_value=make_registry(e)):
        call(batches,p,validator=validate)
    assert len(requests)==2
    assert json.loads(requests[1].content)==json.loads(p)['provider_payloads'][1]
