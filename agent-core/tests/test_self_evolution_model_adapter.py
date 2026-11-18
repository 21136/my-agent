from __future__ import annotations

import json
import sys
import threading
import secrets
from pathlib import Path
from typing import Any

import httpx
import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))

from llm_client import LLMClient
from paths import AgentPaths
from self_evolution_worker import HostTool, SelfEvolutionWorkerContext
from session import SessionMeta
from self_evolution_model_adapter import (
    build_trusted_worker_capabilities,
    create_self_evolution_model_adapter,
    run_self_evolution_worker_with_project_model,
)

MODEL_ID = "0x567-flash"
DIGEST = "sha256:offline-candidate"


def _paths(tmp_path: Path) -> AgentPaths:
    root = tmp_path / "agent"
    (root / "evolve").mkdir(parents=True)
    (root / "evolve" / "_index.toml").write_text("format = 1\n", encoding="utf-8")
    paths = AgentPaths.from_root(root)
    paths.data.mkdir(parents=True)
    (paths.data / "llm_models.json").write_text(json.dumps({
        "models": [{
            "registryId": MODEL_ID,
            "name": "Offline flash test profile",
            "vendor": "0x567",
            "baseUrl": "https://offline.invalid",
            "modelId": "offline-test-model",
            "apiKeyEnv": "OFFLINE_TEST_ONLY_KEY",
            "supportsToolCall": True,
            "tier": "flash",
        }],
    }), encoding="utf-8")
    (paths.data / "llm_role_models.json").write_text(json.dumps({
        "format": 1,
        "preferences": {"main": "0x567-pro", "subagent": MODEL_ID},
        "explicit_roles": ["subagent"],
    }), encoding="utf-8")
    return paths


def _context(cancel: threading.Event | None = None, confirm=None, after_each=None) -> SelfEvolutionWorkerContext:
    event = cancel or threading.Event()
    return SelfEvolutionWorkerContext(
        session_id="offline-session", task_id="offline-task", attempt_id="attempt-1",
        run_id="run-1", turn_epoch=1, candidate_id="candidate-1",
        candidate_digest=DIGEST, cancel_event=event, model_id="intentionally-ignored",
        outbound_request_confirm_fn=confirm or (lambda _preview, allow_all: "y" if allow_all is False else "n"),
        after_each=after_each or (lambda: True), current_candidate_digest=lambda: DIGEST,
    )


def _completion(*, tool_call: dict[str, Any] | None = None, content: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_call is not None:
        message["tool_calls"] = [tool_call]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if tool_call else "stop"}]}


def _call(name: str, args: dict[str, str], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _tools() -> tuple[HostTool, ...]:
    return (
        HostTool("observe_candidate", "Observe the bound candidate", lambda _ctx, _args: {"status": "observed"}),
        HostTool("read_candidate_file", "Read within the bound candidate", lambda _ctx, _args: {"status": "ok", "content": "source"}),
        HostTool("write_candidate_file", "Write within the bound candidate", lambda _ctx, _args: {"status": "written"}),
        HostTool("run_registered_check", "Run one host-registered check", lambda _ctx, args: {
            "status": "passed", "check_id": args["check_id"], "candidate_digest": DIGEST,
            "evidence_ref": "offline-evidence-1", "process_tree_state": "stopped",
            "requires_reconciliation": False, "exit_code": 0,
        }),
    )


class _FakeHttpClient:
    payloads: list[dict[str, Any]] = []
    status_code = 200
    cancel_on_post: threading.Event | None = None
    posts: list[dict[str, Any]] = []

    def __init__(self, **_kwargs: Any):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url: str, *, headers: dict[str, str], content: bytes, **_kwargs: Any):
        self.posts.append({"url": url, "headers": headers, "body": json.loads(content)})
        if self.cancel_on_post is not None:
            self.cancel_on_post.set()
        body = self.payloads.pop(0) if self.payloads else {"error": "unexpected extra request"}
        return httpx.Response(self.status_code, json=body, request=httpx.Request("POST", url))

    def close(self):
        pass

    @classmethod
    def reset(cls):
        cls.payloads = []
        cls.status_code = 200
        cls.cancel_on_post = None
        cls.posts = []


def test_adapter_uses_project_paths_role_preference_and_real_llmclient(tmp_path):
    paths = _paths(tmp_path)
    adapter = create_self_evolution_model_adapter(paths, SessionMeta())

    assert adapter.model_id == MODEL_ID
    assert isinstance(adapter.client, LLMClient)
    assert adapter.client.paths == paths
    second = create_self_evolution_model_adapter(paths, SessionMeta())
    assert second.client is not adapter.client
    worker_cancel = threading.Event()
    adapter.client.set_cancel_event(worker_cancel)
    assert second.client._cancel_event is None
    assert adapter.client._cancel_event is worker_cancel


def test_trusted_capability_builder_binds_backend_tools_without_opening_start_gate(tmp_path):
    paths = _paths(tmp_path)
    confirm = lambda _preview, allow_all: "y" if allow_all is False else "n"
    stopped = lambda _identity: True

    tools = _tools()
    caps = build_trusted_worker_capabilities(
        paths, SessionMeta(), tools=tools, outbound_request_confirm_fn=confirm,
        process_tree_stopped=stopped,
    )

    assert caps.model_id == MODEL_ID
    assert caps.tools is tools
    assert caps.outbound_request_confirm_fn is confirm
    assert caps.process_tree_stopped is stopped
    assert caps.enable_experimental_start is False
    assert caps.max_requests == 32
    assert isinstance(caps.client, LLMClient) and caps.client.paths == paths
    explicitly_limited = build_trusted_worker_capabilities(
        paths, SessionMeta(), tools=tools, outbound_request_confirm_fn=confirm,
        process_tree_stopped=stopped, max_requests=64,
    )
    assert explicitly_limited.max_requests == 64
    with pytest.raises(TypeError):
        build_trusted_worker_capabilities(
            paths, SessionMeta(), tools=list(_tools()), outbound_request_confirm_fn=confirm,
            process_tree_stopped=stopped,
        )


def test_adapter_rejects_non_tool_model_before_any_worker_request(tmp_path):
    paths = _paths(tmp_path)
    registry = json.loads((paths.data / "llm_models.json").read_text(encoding="utf-8"))
    registry["models"][0]["supportsToolCall"] = False
    (paths.data / "llm_models.json").write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(ValueError, match="tool"):
        create_self_evolution_model_adapter(paths, SessionMeta())


def test_real_client_contract_runs_tool_loop_with_fake_http_only(tmp_path, monkeypatch):
    import llm_client

    _FakeHttpClient.reset()
    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)
    fake_key = secrets.token_urlsafe(32)
    monkeypatch.setenv("OFFLINE_TEST_ONLY_KEY", fake_key)
    _FakeHttpClient.payloads = [
        _completion(tool_call=_call("observe_candidate", {}, "obs-1")),
        _completion(tool_call=_call("write_candidate_file", {"path": "module.py", "content": "x=1"}, "write-1")),
        _completion(tool_call=_call("run_registered_check", {"check_id": "syntax"}, "check-1")),
        _completion(content="Candidate updated and host check passed."),
    ]
    confirmations: list[tuple[str, bool]] = []
    context = _context(confirm=lambda preview, allow: confirmations.append((preview, allow)) or "y")
    # Only the adapter's real LLMClient runs; its transport is replaced by an in-process fake.
    result = run_self_evolution_worker_with_project_model(
        "Improve the isolated candidate", context=context, tools=_tools(), paths=_paths(tmp_path),
        session_meta=SessionMeta(),
    )

    assert result.status == "finished"
    assert result.candidate_digest == DIGEST
    assert result.check_status == "passed"
    assert result.check_evidence[0]["evidence_ref"] == "offline-evidence-1"
    assert result.requests == 4 and result.tool_calls == 3
    assert len(_FakeHttpClient.posts) == 4
    assert len(confirmations) == 4 and all(allow is False for _, allow in confirmations)
    assert all("[redacted]" in preview and fake_key not in preview for preview, _ in confirmations)
    assert all(post["body"]["model"] == "offline-test-model" for post in _FakeHttpClient.posts)
    assert all(post["url"] == "https://offline.invalid/v1/chat/completions" for post in _FakeHttpClient.posts)
    assert all(post["headers"]["Authorization"] == f"Bearer {fake_key}" for post in _FakeHttpClient.posts)


def test_project_model_adapter_default_budget_allows_long_sequential_tool_loop(tmp_path, monkeypatch):
    import llm_client

    _FakeHttpClient.reset()
    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)
    monkeypatch.setenv("OFFLINE_TEST_ONLY_KEY", secrets.token_urlsafe(32))
    payloads = [_completion(tool_call=_call("observe_candidate", {}, "obs"))]
    payloads.extend(
        _completion(tool_call=_call("read_candidate_file", {"path": f"module-{index}.py"}, f"read-{index}"))
        for index in range(17)
    )
    payloads.extend([
        _completion(tool_call=_call("write_candidate_file", {"path": "module.py", "content": "updated"}, "write")),
        _completion(tool_call=_call("run_registered_check", {"check_id": "syntax"}, "check")),
        _completion(content="The host check passed."),
    ])
    _FakeHttpClient.payloads = payloads

    result = run_self_evolution_worker_with_project_model(
        "Inspect and update the isolated candidate", context=_context(confirm=lambda _preview, _allow: "y"),
        tools=_tools(), paths=_paths(tmp_path), session_meta=SessionMeta(),
    )

    assert result.status == "finished", result.error_code
    assert result.requests == 21 and result.tool_calls == 20
    assert len(_FakeHttpClient.posts) == 21


def test_candidate_binding_revalidated_immediately_before_post(tmp_path, monkeypatch):
    import llm_client

    _FakeHttpClient.reset()
    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)
    fake_key = secrets.token_urlsafe(32)
    monkeypatch.setenv("OFFLINE_TEST_ONLY_KEY", fake_key)
    checks = iter([True, False])
    context = _context(
        confirm=lambda _preview, _allow: "y",
        after_each=lambda: next(checks),
    )

    result = run_self_evolution_worker_with_project_model(
        "Do not send if the candidate lease changes", context=context,
        tools=_tools(), paths=_paths(tmp_path), session_meta=SessionMeta(),
    )

    assert result.status == "blocked"
    assert result.error_code == "candidate_binding_changed"
    assert result.requests == 0
    assert _FakeHttpClient.posts == []


def test_outbound_denial_never_reaches_fake_transport(tmp_path, monkeypatch):
    import llm_client

    _FakeHttpClient.reset()
    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)
    fake_key = secrets.token_urlsafe(32)
    monkeypatch.setenv("OFFLINE_TEST_ONLY_KEY", fake_key)
    context = _context(confirm=lambda _preview, _allow: "n")
    result = run_self_evolution_worker_with_project_model(
        "Do not send", context=context, tools=_tools(), paths=_paths(tmp_path),
        session_meta=SessionMeta(),
    )

    assert result.status == "blocked" and result.error_code == "outbound_request_denied"
    assert result.requests == 0 and _FakeHttpClient.posts == []


def test_cancel_during_fake_transport_returns_cancelled_without_retry(tmp_path, monkeypatch):
    import llm_client

    _FakeHttpClient.reset()
    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)
    fake_key = secrets.token_urlsafe(32)
    monkeypatch.setenv("OFFLINE_TEST_ONLY_KEY", fake_key)
    _FakeHttpClient.payloads = [_completion(content="late response")]
    cancel = threading.Event()
    _FakeHttpClient.cancel_on_post = cancel
    result = run_self_evolution_worker_with_project_model(
        "Cancel safely", context=_context(cancel, confirm=lambda _preview, _allow: "y"), tools=_tools(), paths=_paths(tmp_path),
        session_meta=SessionMeta(),
    )

    assert result.status == "cancelled" and result.error_code == "cancelled"
    assert result.requests == 1 and len(_FakeHttpClient.posts) == 1


def test_provider_http_failure_is_structured_and_not_retried(tmp_path, monkeypatch):
    import llm_client

    _FakeHttpClient.reset()
    monkeypatch.setattr(llm_client.httpx, "Client", _FakeHttpClient)
    fake_key = secrets.token_urlsafe(32)
    monkeypatch.setenv("OFFLINE_TEST_ONLY_KEY", fake_key)
    _FakeHttpClient.status_code = 503
    _FakeHttpClient.payloads = [{"error": "offline synthetic failure"}]
    result = run_self_evolution_worker_with_project_model(
        "Exercise failure handling", context=_context(confirm=lambda _preview, _allow: "y"), tools=_tools(), paths=_paths(tmp_path),
        session_meta=SessionMeta(),
    )

    assert result.status == "failed" and result.error_code == "provider_request_failed"
    assert result.requests == 1 and len(_FakeHttpClient.posts) == 1
