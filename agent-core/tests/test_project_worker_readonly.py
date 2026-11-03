from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_client import LLMClient
from llm_models import ModelEntry, ModelRegistry
from test_confirmed_llm_request import make_registry, install_transport
from project_worker_readonly import prepare_project_summary, execute_project_summary

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows safe project reader only')


def setup_worker(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    (root / 'alpha.txt').write_text('Synthetic alpha 内容', encoding='utf-8')
    (root / 'sub').mkdir(); (root / 'sub' / 'beta.md').write_text('Synthetic beta', encoding='utf-8')
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
                       base_url='https://example.com', provider_model='gpt-6-luna',
                       max_input_tokens=128_000, api_key='synthetic-private-key')
    return root, LLMClient(), make_registry(entry)


def model_response(project='project-alpha', files=None, summary='Synthetic model summary'):
    content = json.dumps({'project_id':project,'files':files or ['alpha.txt','sub/beta.md'], 'summary':summary})
    return {'model':'gpt-6-luna','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]}


def test_real_reads_exact_preview_mock_transport_and_no_writes(tmp_path, monkeypatch):
    root, client, registry = setup_worker(tmp_path)
    before = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with patch.object(ModelRegistry,'load',return_value=registry):
        prepared = prepare_project_summary('project-alpha', root, ['alpha.txt','sub/beta.md'],
                                           'session-alpha', client, lambda: True)
        assert prepared.status == 'ready'
        preview = json.loads(prepared.preview_json)
        payload = preview['provider_payload']
        assert 'Synthetic alpha 内容' in prepared.preview_json and 'Synthetic beta' in prepared.preview_json
        assert preview['model_id']=='0x567-flash' and payload['model']=='gpt-6-luna'
        confirmations = []
        requests, _ = install_transport(monkeypatch, lambda _: httpx.Response(200,json=model_response()))
        result = execute_project_summary(prepared, client, lambda p,a: confirmations.append((p,a)) or 'y', lambda: True)
    assert confirmations == [(prepared.preview_json, False)] and len(requests) == 1
    assert requests[0].content == json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    assert result.status == 'ok' and result.project_id == 'project-alpha'
    assert result.files == ('alpha.txt','sub/beta.md') and result.summary == 'Synthetic model summary'
    assert result.is_acceptance is False
    assert before == {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('paths', [['../outside.txt'], ['/outside.txt'], ['host/secret'], ['alpha.txt','ALPHA.TXT']])
def test_invalid_paths_never_request_consent_or_send(tmp_path, monkeypatch, paths):
    root, client, registry = setup_worker(tmp_path)
    with patch.object(ModelRegistry,'load',return_value=registry):
        p = prepare_project_summary('project-alpha', root, paths, 'session-alpha', client, lambda: True)
    assert p.status != 'ready' and not p.preview_json


@pytest.mark.parametrize('mode', ['deny','exception','content_change','same_content_replacement','binding_change'])
def test_rejection_or_post_confirmation_changes_block_send(tmp_path, monkeypatch, mode):
    root, client, registry = setup_worker(tmp_path)
    alive=[True]
    with patch.object(ModelRegistry,'load',return_value=registry):
        prepared = prepare_project_summary('project-alpha',root,['alpha.txt','sub/beta.md'],'session-alpha',client,lambda:alive[0])
        requests,_ = install_transport(monkeypatch, lambda _: pytest.fail('must not post'))
        def confirm(*_):
            if mode == 'deny': return 'n'
            if mode == 'exception': raise RuntimeError('PRIVATE')
            if mode == 'content_change': (root/'alpha.txt').write_text('changed',encoding='utf-8')
            if mode == 'same_content_replacement':
                (root/'alpha.txt').rename(root/'old.txt')
                (root/'alpha.txt').write_text('Synthetic alpha 内容',encoding='utf-8')
            if mode == 'binding_change': alive[0]=False
            return 'y'
        result = execute_project_summary(prepared,client,confirm,lambda:alive[0])
    assert result.status != 'ok' and not requests and result.summary == ''
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('mode', ['missing_model','wrong_model','length','tools','bad_json','foreign_project','foreign_files'])
def test_invalid_model_results_are_not_returned(tmp_path,monkeypatch,mode):
    root, client, registry = setup_worker(tmp_path)
    raw = model_response()
    if mode=='missing_model': del raw['model']
    elif mode=='wrong_model': raw['model']='other'
    elif mode=='length': raw['choices'][0]['finish_reason']='length'
    elif mode=='tools': raw['choices'][0]['message']['tool_calls']=['invalid']
    elif mode=='bad_json': raw['choices'][0]['message']['content']='bad'
    elif mode=='foreign_project': raw=model_response(project='project-other')
    else: raw=model_response(files=['other.txt'])
    with patch.object(ModelRegistry,'load',return_value=registry):
        prepared=prepare_project_summary('project-alpha',root,['alpha.txt','sub/beta.md'],'session-alpha',client,lambda:True)
        requests,_=install_transport(monkeypatch,lambda _:httpx.Response(200,json=raw))
        result=execute_project_summary(prepared,client,lambda *_:'y',lambda:True)
    assert len(requests)==1 and result.status != 'ok' and result.summary == ''


def test_cancel_and_provider_exception_never_retry(tmp_path,monkeypatch):
    root, client, registry=setup_worker(tmp_path)
    with patch.object(ModelRegistry,'load',return_value=registry):
        prepared=prepare_project_summary('project-alpha',root,['alpha.txt','sub/beta.md'],'session-alpha',client,lambda:True)
        event=threading.Event();event.set();client.set_cancel_event(event)
        requests,_=install_transport(monkeypatch,lambda _:pytest.fail('cancelled'))
        result=execute_project_summary(prepared,client,lambda *_:'y',lambda:True)
        assert result.status != 'ok' and not requests
        event.clear()
        requests,_=install_transport(monkeypatch,lambda r: (_ for _ in ()).throw(httpx.ConnectError('PRIVATE',request=r)))
        result=execute_project_summary(prepared,client,lambda *_:'y',lambda:True)
    assert result.status != 'ok' and len(requests)==1 and 'PRIVATE' not in repr(result)


def test_tampered_preview_is_not_sent(tmp_path, monkeypatch):
    from dataclasses import replace
    root, client, registry = setup_worker(tmp_path)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        prepared = prepare_project_summary('project-alpha', root, ['alpha.txt', 'sub/beta.md'],
                                           'session-alpha', client, lambda: True)
        preview = json.loads(prepared.preview_json)
        preview['provider_payload']['messages'][1]['content'] = 'different private content'
        forged = replace(prepared, preview_json=json.dumps(preview, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        requests, _ = install_transport(monkeypatch, lambda _: pytest.fail('must not send'))
        confirms = []
        result = execute_project_summary(forged, client, lambda *a: confirms.append(a) or 'y', lambda: True)
    assert result.status == 'incomplete' and not requests and not confirms


def test_oversized_input_fails_closed_without_preview(tmp_path):
    root, client, registry = setup_worker(tmp_path)
    (root / 'alpha.txt').write_text('x' * 100_000, encoding='utf-8')
    with patch.object(ModelRegistry, 'load', return_value=registry):
        prepared = prepare_project_summary('project-alpha', root, ['alpha.txt'], 'session-alpha', client, lambda: True)
    assert prepared.status == 'incomplete' and not prepared.preview_json


def test_binding_changed_during_transport_discards_result(tmp_path, monkeypatch):
    root, client, registry = setup_worker(tmp_path)
    alive = [True]
    with patch.object(ModelRegistry, 'load', return_value=registry):
        prepared = prepare_project_summary('project-alpha', root, ['alpha.txt', 'sub/beta.md'],
                                           'session-alpha', client, lambda: alive[0])
        def transport(_):
            alive[0] = False
            return httpx.Response(200, json=model_response())
        requests, _ = install_transport(monkeypatch, transport)
        result = execute_project_summary(prepared, client, lambda *_: 'y', lambda: alive[0])
    assert len(requests) == 1 and result.status == 'incomplete' and result.summary == ''


def test_profile_change_after_approval_blocks_transport(tmp_path, monkeypatch):
    root, client, registry = setup_worker(tmp_path)
    changed_entry = ModelEntry(id='0x567-flash', name='Changed', vendor='0x567',
                               base_url='https://other.example.com', provider_model='gpt-6-luna',
                               max_input_tokens=128_000, api_key='synthetic-private-key')
    current = [registry]
    with patch.object(ModelRegistry, 'load', side_effect=lambda *_: current[0]):
        prepared = prepare_project_summary('project-alpha', root, ['alpha.txt', 'sub/beta.md'],
                                           'session-alpha', client, lambda: True)
        requests, _ = install_transport(monkeypatch, lambda _: pytest.fail('must not send'))
        def approve(*_):
            current[0] = make_registry(changed_entry)
            return 'y'
        result = execute_project_summary(prepared, client, approve, lambda: True)
    assert result.status == 'incomplete' and not requests
