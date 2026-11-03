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
from project_workers_readonly import ProjectWorkerSpec, run_two_project_summaries

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows safe project reader only')


def setup(tmp_path, monkeypatch):
    specs = []
    for name in ('alpha', 'beta'):
        root = tmp_path / name
        root.mkdir()
        (root / 'source.txt').write_text('synthetic ' + name, encoding='utf-8')
        specs.append(ProjectWorkerSpec('project-' + name, root, ('source.txt',),
                                       'session-' + name, LLMClient(), lambda: True))
    entry = ModelEntry(id='0x567-flash', name='Synthetic', vendor='0x567',
        base_url='https://example.com', provider_model='gpt-6-luna',
        max_input_tokens=128000, api_key='synthetic-private')
    return specs, make_registry(entry)


def test_concurrent_requests_sequential_confirmations_exact_payload_and_binding(tmp_path, monkeypatch):
    specs, registry = setup(tmp_path, monkeypatch)
    host_thread = threading.get_ident()
    seen, concurrent = [], threading.Barrier(2)
    before = [s.project_root.joinpath('source.txt').read_bytes() for s in specs]
    def confirm(project, preview, allow):
        assert threading.get_ident() == host_thread and allow is False
        seen.append((project, preview))
        return 'y'
    def transport(request):
        payload = json.loads(request.content)
        data = json.loads(payload['messages'][1]['content'])
        concurrent.wait(timeout=5)
        content = json.dumps({'project_id': data['project_id'], 'files': data['files'],
                              'summary': 'summary for ' + data['project_id']})
        return httpx.Response(200, json={'model':'gpt-6-luna', 'choices':[{'finish_reason':'stop',
            'message':{'role':'assistant', 'content':content}}]})
    requests, _ = install_transport(monkeypatch, transport)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        results = run_two_project_summaries(specs, confirm)
    assert [p for p, _ in seen] == ['project-alpha', 'project-beta']
    assert len(requests) == 2
    bodies = {json.loads(json.loads(r.content)['messages'][1]['content'])['project_id']: json.loads(r.content) for r in requests}
    assert all(json.loads(preview)['provider_payload'] == bodies[project] for project, preview in seen)
    assert [r.status for r in results] == ['ok', 'ok']
    assert [r.project_id for r in results] == ['project-alpha', 'project-beta']
    assert [r.summary for r in results] == ['summary for project-alpha', 'summary for project-beta']
    assert all(not r.is_acceptance for r in results)
    assert before == [s.project_root.joinpath('source.txt').read_bytes() for s in specs]


@pytest.mark.parametrize('mode', ['deny_first', 'change_second', 'invalid_first', 'cancel_first'])
def test_partial_failures_and_cancel_never_mix_results(tmp_path, monkeypatch, mode):
    specs, registry = setup(tmp_path, monkeypatch)
    cancel = threading.Event()
    seen = []
    def confirm(project, preview, allow):
        seen.append(project)
        if mode == 'deny_first' and project.endswith('alpha'): return 'n'
        if mode == 'cancel_first' and project.endswith('alpha'):
            cancel.set()
        if mode == 'change_second' and project.endswith('beta'):
            (specs[1].project_root / 'source.txt').write_text('changed', encoding='utf-8')
        return 'y'
    def transport(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        project = data['project_id']
        if mode == 'invalid_first' and project.endswith('alpha'): project = 'project-beta'
        body = json.dumps({'project_id':project,'files':data['files'],'summary':'summary ' + project})
        return httpx.Response(200,json={'model':'gpt-6-luna','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':body}}]})
    requests, _ = install_transport(monkeypatch, transport)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        results = run_two_project_summaries(specs, confirm, cancel_event=cancel)
    assert [r.project_id for r in results] == ['project-alpha','project-beta']
    if mode in ('deny_first', 'cancel_first'):
        assert not requests and seen == ['project-alpha'] and all(r.status != 'ok' for r in results)
    elif mode == 'change_second':
        assert results[1].status != 'ok' and len(requests) == 1 and results[0].status == 'ok'
    else:
        assert results[0].status != 'ok' and results[1].status == 'ok' and len(requests) == 2
    assert all(not r.is_acceptance for r in results)


def test_duplicate_bindings_rejected_without_confirmation_or_send(tmp_path, monkeypatch):
    from dataclasses import replace
    specs, registry = setup(tmp_path, monkeypatch)
    for changed in (replace(specs[1], project_id=specs[0].project_id),
                    replace(specs[1], session_id=specs[0].session_id),
                    replace(specs[1], client=specs[0].client),
                    replace(specs[1], project_root=specs[0].project_root)):
        calls=[]
        requests,_ = install_transport(monkeypatch, lambda _: pytest.fail('must not send'))
        with patch.object(ModelRegistry, 'load', return_value=registry):
            results = run_two_project_summaries([specs[0],changed], lambda *a: calls.append(a) or 'y')
        assert all(r.status != 'ok' for r in results) and not calls and not requests


def test_first_scope_invalid_other_scope_can_complete(tmp_path, monkeypatch):
    from dataclasses import replace
    specs, registry = setup(tmp_path, monkeypatch)
    specs[0] = replace(specs[0], files=('../outside.txt',))
    seen = []
    def transport(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        content = json.dumps({'project_id': data['project_id'], 'files': data['files'], 'summary': 'synthetic beta'})
        return httpx.Response(200, json={'model': 'gpt-6-luna', 'choices': [{'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': content}}]})
    requests, _ = install_transport(monkeypatch, transport)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        results = run_two_project_summaries(specs, lambda project, preview, allow: seen.append(project) or 'y')
    assert seen == ['project-beta'] and len(requests) == 1
    assert [r.status for r in results] == ['incomplete', 'ok']
    assert results[0].summary == '' and results[1].summary == 'synthetic beta'


def test_confirm_exception_stops_later_confirmation_without_exposing_error(tmp_path, monkeypatch):
    specs, registry = setup(tmp_path, monkeypatch)
    calls = []
    def confirm(*args):
        calls.append(args[0])
        raise RuntimeError('SECRET FROM HOST')
    requests, _ = install_transport(monkeypatch, lambda _: pytest.fail('must not send'))
    with patch.object(ModelRegistry, 'load', return_value=registry):
        results = run_two_project_summaries(specs, confirm)
    assert calls == ['project-alpha'] and not requests
    assert all(r.status == 'incomplete' and 'SECRET' not in repr(r) for r in results)


def test_second_denial_preserves_first_approved_inflight_result(tmp_path, monkeypatch):
    specs, registry = setup(tmp_path, monkeypatch)
    second_denied = threading.Event()
    calls = []
    def confirm(project, preview, allow):
        calls.append(project)
        if project == 'project-beta':
            second_denied.set()
            return 'n'
        return 'y'
    def transport(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert data['project_id'] == 'project-alpha'
        assert second_denied.wait(5)
        content = json.dumps({'project_id': data['project_id'], 'files': data['files'], 'summary': 'alpha result'})
        return httpx.Response(200, json={'model': 'gpt-6-luna', 'choices': [{'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': content}}]})
    requests, _ = install_transport(monkeypatch, transport)
    with patch.object(ModelRegistry, 'load', return_value=registry):
        results = run_two_project_summaries(specs, confirm)
    assert calls == ['project-alpha', 'project-beta'] and len(requests) == 1
    assert [r.status for r in results] == ['ok', 'incomplete']
    assert results[0].summary == 'alpha result' and results[1].summary == ''
