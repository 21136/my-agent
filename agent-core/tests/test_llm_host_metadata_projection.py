from __future__ import annotations

import copy
import json
from unittest.mock import patch

import httpx
import pytest
import llm_client
from llm_models import ModelRegistry
from tests.test_confirmed_llm_request import (make_entry, make_registry, make_client, install_transport,
                                              response_json, preview_json, invoke, _MODEL_ID, _SESSION_ID)
from tests.test_confirmed_llm_tools import (_entry, _registry, _client, _install_transport,
                                            _response, _call)
from tests.test_confirmed_llm_batch import preview as batch_preview, call as batch_call

SECRET = 'HOST_PRIVATE_SYNTHETIC_422'
RAW = [{'role':'user', 'content':'normal question', '_host_internal':{'request_id':SECRET}},
       {'role':'assistant', 'content':None, 'tool_calls':[{'id':'c1','type':'function','function':{'name':'read','arguments':'{"x":1}'}}], '_host_internal':SECRET},
       {'role':'tool','tool_call_id':'c1','content':'normal output','_host_internal':{'secret':SECRET}}]
CLEAN = [{k:v for k,v in row.items() if k != '_host_internal'} for row in RAW]


def test_direct_chat_strips_before_http_without_mutating_messages(monkeypatch):
    entry=make_entry(); original=copy.deepcopy(RAW); sent=[]
    monkeypatch.setattr(llm_client, 'get_registry', lambda *a: make_registry(entry))
    transport=httpx.MockTransport(lambda r:(sent.append(r),httpx.Response(200,json=response_json(),request=r))[1])
    monkeypatch.setattr(llm_client, 'make_httpx_client', lambda **kw:httpx.Client(transport=transport,**kw))
    make_client().chat(RAW, model=_MODEL_ID)
    payload=json.loads(sent[0].content)
    assert payload['messages']==CLEAN and SECRET not in sent[0].content.decode()
    assert RAW==original


def test_confirmed_tools_preview_and_post_are_identical_and_sanitized(monkeypatch):
    entry=_entry(); requests,_=_install_transport(monkeypatch,_response); seen=[]
    with patch.object(ModelRegistry,'load',return_value=_registry(entry)):
        _call(_client(), RAW, [{'type':'function','function':{'name':'read_project_file'}}],
              {'project_id':'synthetic'}, lambda p,a: seen.append(p) or 'y')
    doc=json.loads(seen[0]); assert doc['provider_payload']['messages']==CLEAN
    assert SECRET not in seen[0] and json.loads(requests[0].content)==doc['provider_payload']


def test_confirmed_json_requires_exact_sanitized_preview_and_refuses_unsanitized_preview(monkeypatch):
    entry=make_entry(); requests,_=install_transport(monkeypatch,lambda r:httpx.Response(200,json=response_json(),request=r)); seen=[]
    with patch.object(ModelRegistry,'load',return_value=make_registry(entry)):
        invoke(make_client(), RAW, lambda p,a: seen.append(p) or 'y', preview_json(entry,_SESSION_ID,CLEAN))
        with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
            invoke(make_client(), RAW, lambda *_: pytest.fail('must not confirm changed preview'), preview_json(entry,_SESSION_ID,RAW))
    assert len(requests)==1 and SECRET not in seen[0]
    assert json.loads(requests[0].content)==json.loads(seen[0])['provider_payload']


def test_batch_confirmation_matches_sanitized_payloads(monkeypatch):
    entry=make_entry(); requests,_=install_transport(monkeypatch,lambda r:httpx.Response(200,json=response_json(),request=r)); seen=[]
    with patch.object(ModelRegistry,'load',return_value=make_registry(entry)):
        batch_call([RAW],batch_preview(entry,[CLEAN]),confirm=lambda p,a:seen.append(p) or 'y')
    assert SECRET not in seen[0] and len(requests)==1
    assert json.loads(requests[0].content)==json.loads(seen[0])['provider_payloads'][0]


def test_streaming_chat_sends_sanitized_messages(monkeypatch):
    entry = make_entry(); sent = []
    monkeypatch.setattr(llm_client, 'get_registry', lambda *a: make_registry(entry))
    event = 'data: {"model":"safe-json-1","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: [DONE]\n\n'
    transport = httpx.MockTransport(lambda r: (sent.append(r), httpx.Response(200, text=event, headers={'content-type':'text/event-stream'}, request=r))[1])
    monkeypatch.setattr(llm_client, 'make_httpx_client', lambda **kw: httpx.Client(transport=transport, **kw))
    make_client().chat(RAW, model=_MODEL_ID, stream=llm_client.StreamHandlers())
    body = json.loads(sent[0].content)
    assert body['messages'] == CLEAN and body['stream'] is True
    assert SECRET not in sent[0].content.decode()


def test_namespace_text_in_user_content_and_tool_arguments_remains_unchanged(monkeypatch):
    entry=make_entry(); sent=[]
    monkeypatch.setattr(llm_client, 'get_registry', lambda *a: make_registry(entry))
    transport=httpx.MockTransport(lambda r:(sent.append(r),httpx.Response(200,json=response_json(),request=r))[1])
    monkeypatch.setattr(llm_client, 'make_httpx_client', lambda **kw:httpx.Client(transport=transport,**kw))
    messages=[{'role':'user','content':'literal _host_internal is user text', '_host_internal':{'id':SECRET}},
              {'role':'assistant','tool_calls':[{'id':'c','type':'function','function':{'name':'x','arguments':'{"_host_internal":"user-value"}'}}]}]
    make_client().chat(messages,model=_MODEL_ID)
    projected=json.loads(sent[0].content)['messages']
    assert projected[0]['content']=='literal _host_internal is user text'
    assert projected[1]['tool_calls']==messages[1]['tool_calls'] and SECRET not in sent[0].content.decode()


def test_batch_rejects_unsanitized_caller_preview_before_confirmation(monkeypatch):
    entry=make_entry(); requests,_=install_transport(monkeypatch,lambda r:httpx.Response(200,json=response_json(),request=r)); seen=[]
    with patch.object(ModelRegistry,'load',return_value=make_registry(entry)):
        with pytest.raises(llm_client.LLMConfirmedBatchError):
            batch_call([RAW],batch_preview(entry,[RAW]),confirm=lambda p,a:seen.append(p) or 'y')
    assert not seen and not requests
