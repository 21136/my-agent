"""Opt-in, single-request backend probe; all project and session data are synthetic."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

from paths import AgentPaths
from session import Session, SessionMeta
from llm_client import LLMClient, load_config
from llm_models import ModelRegistry
import llm_client as client_module
import project_worker_readonly as worker

OPT_IN = "MY_AGENT_LIVE_PROJECT_WORKER_READONLY_OPTIN"
SECRET_ENV = "MY_AGENT_SYNTHETIC_PROJECT_WORKER_SECRET"


@pytest.mark.skipif(os.getenv(OPT_IN) != "1", reason="explicit synthetic-only live opt-in")
@pytest.mark.skipif(os.name != "nt", reason="secure project text reader requires Windows")
def test_live_readonly_project_summary_single_synthetic_request(tmp_path, monkeypatch):
    import httpx

    provider_paths = AgentPaths.discover(start=CORE)
    configured = ModelRegistry.load(provider_paths).get("0x567-flash")
    if configured is None or (configured.id, configured.vendor, configured.provider_model) != ("0x567-flash", "0x567", "gpt-6-luna"):
        pytest.fail("configured provider profile unavailable; no request", pytrace=False)
    endpoint = configured.chat_completions_url()
    url = urlsplit(endpoint)
    if (url.scheme != "https" or url.hostname != "api-cdn.0x567.com" or url.username or url.password
            or url.query or url.fragment or url.port not in (None, 443)):
        pytest.fail("endpoint not permitted; no request", pytrace=False)
    credential = configured.resolve_api_key(provider_paths)
    if not credential:
        pytest.skip("credential unavailable; no request")

    root = tmp_path / "synthetic-project"
    root.mkdir()
    (root / "notes").mkdir()
    texts = {"overview.txt": "Synthetic project: Kestrel741 studies imaginary blue stars.\n",
             "notes/status.md": "Synthetic status: draft observations recorded; analysis remains pending.\n"}
    for name, text in texts.items():
        (root / name).write_bytes(text.encode("utf-8"))
    before = {name: (root / name).read_bytes() for name in texts}
    paths_root = tmp_path / "synthetic-agent"
    paths = AgentPaths(agent_root=paths_root, workspace=paths_root / "workspace", data=paths_root / "data", evolve=paths_root / "evolve")
    session_dir = paths.data / "sessions" / "20261101-aabbccdd"
    session_dir.mkdir(parents=True)
    session = Session("20261101-aabbccdd", session_dir, "synthetic only", SessionMeta(project_id="synthetic-project"), [], paths)
    session.save()
    settings = {"models": [{"registryId": "0x567-flash", "modelId": "gpt-6-luna", "vendor": "0x567",
                            "baseUrl": endpoint, "apiKeyEnv": SECRET_ENV, "maxInputTokens": 128000}]}
    (paths.data / "llm_models.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setenv(SECRET_ENV, credential)
    del credential
    client = LLMClient(load_config(), paths=paths)
    project_id = "synthetic-project"
    files = list(texts)
    def bound():
        return (client.paths is paths and session.paths is paths and session.session_dir == session_dir
                and session.conversation_id == "20261101-aabbccdd" and session.meta.project_id == project_id
                and root == tmp_path / "synthetic-project" and root.is_dir())
    prepared = worker.prepare_project_summary(project_id, root, files, session.conversation_id, client, bound)
    if prepared.status != "ready":
        pytest.fail(f"synthetic preparation failed: {prepared.reason}; no request", pytrace=False)
    if not isinstance(prepared._reader, worker.ProjectTextReader):
        pytest.fail("real project reader unavailable; no request", pytrace=False)
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    expected_content = canonical({"project_id": project_id, "files": files,
                                  "texts": [{"path": name, "text": texts[name]} for name in files]})
    expected_payload = {"model": "gpt-6-luna", "messages": [{"role": "system", "content": worker._SYSTEM},
                        {"role": "user", "content": expected_content}], "temperature": 0.0,
                        "stream": False, "response_format": {"type": "json_object"}}
    expected_preview = canonical({"model_id": "0x567-flash", "vendor": "0x567", "endpoint": endpoint,
                                  "session_id": session.conversation_id, "provider_payload": expected_payload})
    if prepared.preview_json != expected_preview:
        pytest.fail("full synthetic preview mismatch; no request", pytrace=False)

    confirmations, posts, metadata = [], [], []
    def confirm(actual, allow_all):
        if confirmations or actual != expected_preview or allow_all is not False or not bound():
            return "n"
        confirmations.append(True)
        return "y"
    original_post = httpx.Client.post
    def guarded_post(http_client, target, *args, **kwargs):
        if (posts or len(confirmations) != 1 or target != endpoint or not bound()
                or kwargs.get("content") != canonical(expected_payload).encode("utf-8")
                or kwargs.get("follow_redirects") is not False):
            raise RuntimeError("synthetic request rejected")
        posts.append(True)
        return original_post(http_client, target, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, "post", guarded_post)
    original_parse = client_module._parse_completion
    def observe_parse(data, *, fallback_model):
        response = original_parse(data, fallback_model=fallback_model)
        metadata.append((response.model if response.model == "gpt-6-luna" else "other",
                         response.finish_reason if response.finish_reason in {"stop", "length", "tool_calls"} else "other"))
        return response
    monkeypatch.setattr(client_module, "_parse_completion", observe_parse)
    result = worker.execute_project_summary(prepared, client, confirm, bound)
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    valid = (result.status == "ok" and result.project_id == project_id and result.files == tuple(files)
             and isinstance(result.summary, str) and bool(result.summary.strip()) and result.is_acceptance is False
             and len(confirmations) == len(posts) == 1 and metadata == [("gpt-6-luna", "stop")]
             and after == before)
    if not valid:
        pytest.fail(f"synthetic summary: status={result.status}, reason={result.reason}, "
                    f"project_match={result.project_id == project_id}, files_match={result.files == tuple(files)}, "
                    f"summary_present={bool(result.summary.strip())}, confirmations={len(confirmations)}, "
                    f"requests={len(posts)}, metadata={metadata!r}, project_unchanged={after == before}; "
                    "response withheld", pytrace=False)
