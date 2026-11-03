"""Two synthetic projects, explicit opt-in real provider; no GUI or scheduler."""
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
from paths import AgentPaths
from session import Session, SessionMeta
from llm_client import LLMClient, load_config
from llm_models import ModelRegistry
from project_worker_readonly import prepare_project_summary, _SYSTEM
from project_workers_readonly import ProjectWorkerSpec, run_two_project_summaries

OPT_IN = 'MY_AGENT_LIVE_TWO_PROJECT_WORKERS_OPTIN'
SECRET_ENV = 'MY_AGENT_SYNTHETIC_TWO_PROJECT_SECRET'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@pytest.mark.skipif(os.getenv(OPT_IN) != '1', reason='explicit synthetic-only live opt-in')
@pytest.mark.skipif(os.name != 'nt', reason='secure project reader requires Windows')
def test_two_synthetic_project_summaries_real_provider(tmp_path, monkeypatch):
    import httpx
    provider_paths = AgentPaths.discover(start=CORE)
    configured = ModelRegistry.load(provider_paths).get('0x567-flash')
    if configured is None or (configured.id, configured.vendor, configured.provider_model) != ('0x567-flash', '0x567', 'gpt-6-luna'):
        pytest.fail('configured provider unavailable; no request', pytrace=False)
    endpoint = configured.chat_completions_url()
    url = urlsplit(endpoint)
    if (url.scheme != 'https' or url.hostname != 'api-cdn.0x567.com' or url.username or url.password
            or url.query or url.fragment or url.port not in (None, 443)):
        pytest.fail('endpoint not permitted; no request', pytrace=False)
    secret = configured.resolve_api_key(provider_paths)
    if not secret:
        pytest.skip('credential unavailable; no request')
    monkeypatch.setenv(SECRET_ENV, secret)
    del secret

    definitions = [
        ('project-kestrel', '20261101-aabbcc01', 'star.txt', 'Synthetic Kestrel741 astronomy: imaginary blue stars and draft observations.\n'),
        ('project-orion', '20261101-aabbcc02', 'garden.txt', 'Synthetic Orion852 garden: fictional purple flowers and planting notes.\n'),
    ]
    specs, expected_previews, expected_bodies, before = [], {}, {}, {}
    for project, sid, filename, text in definitions:
        root = tmp_path / project
        root.mkdir()
        (root / filename).write_bytes(text.encode('utf-8'))
        before[project] = {filename: text.encode('utf-8')}
        agent_root = tmp_path / ('agent-' + project)
        paths = AgentPaths(agent_root=agent_root, workspace=agent_root / 'workspace', data=agent_root / 'data', evolve=agent_root / 'evolve')
        session_dir = paths.data / 'sessions' / sid
        session_dir.mkdir(parents=True)
        session = Session(sid, session_dir, 'synthetic only', SessionMeta(project_id=project), [], paths)
        session.save()
        settings = {'models': [{'registryId': '0x567-flash', 'modelId': 'gpt-6-luna', 'vendor': '0x567',
                                'baseUrl': endpoint, 'apiKeyEnv': SECRET_ENV, 'maxInputTokens': 128000}]}
        (paths.data / 'llm_models.json').write_text(json.dumps(settings), encoding='utf-8')
        client = LLMClient(load_config(), paths=paths)
        def bound(c=client, p=paths, s=session, d=session_dir, pid=project, r=root, expected_sid=sid):
            return (c.paths is p and s.paths is p and s.session_dir == d and s.conversation_id == expected_sid
                    and s.meta.project_id == pid and r == tmp_path / pid and r.is_dir())
        spec = ProjectWorkerSpec(project, root, (filename,), sid, client, bound)
        specs.append(spec)
        content = canonical({'project_id': project, 'files': [filename], 'texts': [{'path': filename, 'text': text}]})
        body = {'model': 'gpt-6-luna', 'messages': [{'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': content}], 'temperature': 0.0, 'stream': False,
                'response_format': {'type': 'json_object'}}
        expected_bodies[project] = canonical(body).encode('utf-8')
        expected_previews[project] = canonical({'model_id': '0x567-flash', 'vendor': '0x567', 'endpoint': endpoint,
                                                'session_id': sid, 'provider_payload': body})
        preflight = prepare_project_summary(project, root, [filename], sid, client, bound)
        if preflight.status != 'ready' or preflight.preview_json != expected_previews[project]:
            pytest.fail('both synthetic scopes must pass exact preview preflight; no request', pytrace=False)
    if specs[0].client is specs[1].client or specs[0].session_id == specs[1].session_id:
        pytest.fail('scope isolation invalid; no request', pytrace=False)

    confirmations, timings, attempted = [], {}, set()
    guard = threading.Lock()
    def confirm(project, preview, allow):
        if allow is not False or project in confirmations or preview != expected_previews.get(project):
            return 'n'
        confirmations.append(project)
        return 'y'
    original_post = httpx.Client.post
    def guarded_post(http_client, target, *args, **kwargs):
        content = kwargs.get('content')
        matching = [project for project, body in expected_bodies.items() if content == body]
        with guard:
            if (len(matching) != 1 or target != endpoint or kwargs.get('follow_redirects') is not False
                    or matching[0] not in confirmations or matching[0] in attempted or len(attempted) >= 2):
                raise RuntimeError('synthetic scope or request rejected')
            project = matching[0]
            attempted.add(project)
            timings[project] = [time.monotonic(), None]
        try:
            return original_post(http_client, target, *args, **kwargs)
        finally:
            with guard:
                timings[project][1] = time.monotonic()
    monkeypatch.setattr(httpx.Client, 'post', guarded_post)
    outcomes = run_two_project_summaries(specs, confirm)
    unchanged = all({p.relative_to(spec.project_root).as_posix(): p.read_bytes()
                     for p in spec.project_root.rglob('*') if p.is_file()} == before[spec.project_id] for spec in specs)
    complete_timing = len(timings) == 2 and all(t[1] is not None for t in timings.values())
    overlap = (complete_timing and max(t[0] for t in timings.values()) < min(t[1] for t in timings.values()))
    print(f'SYNTHETIC_NETWORK post_count={len(attempted)} complete_timing={complete_timing} overlap={overlap}')
    valid = (confirmations == [s.project_id for s in specs] and len(attempted) == 2 and complete_timing and unchanged
             and len(outcomes) == 2 and all(out.status == 'ok' and out.project_id == spec.project_id
             and out.files == spec.files and bool(out.summary.strip()) and out.is_acceptance is False
             for out, spec in zip(outcomes, specs)))
    if not valid:
        pytest.fail(f'synthetic outcomes: statuses={[r.status for r in outcomes]}, confirmations={len(confirmations)}, '
                    f'posts={len(attempted)}, project_unchanged={unchanged}, overlap={overlap}; response withheld', pytrace=False)
