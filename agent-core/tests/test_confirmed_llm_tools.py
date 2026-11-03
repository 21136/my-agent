"""Exact-confirmation tests for the tools-enabled LLM transport."""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

import llm_client
from llm_client import LLMClient, LLMConfig
from llm_models import ModelEntry, ModelRegistry

MODEL_ID = "0x567-flash"
SESSION_ID = "synthetic-project-worker-session"
SECRET = "never-preview-this-test-secret"
_REAL_HTTPX_CLIENT = httpx.Client


def _entry(*, provider_model="gpt-6-luna", supports_tool_call=True):
    return ModelEntry(
        id=MODEL_ID, name="Synthetic Luna", vendor="0x567",
        base_url="https://api-cdn.0x567.com", provider_model=provider_model,
        api_key=SECRET, supports_tool_call=supports_tool_call,
    )


def _registry(entry):
    return ModelRegistry(models=(entry,), default_flash_id=entry.id,
                         default_pro_id=entry.id, alias_map={})


def _client():
    return LLMClient(LLMConfig(
        api_key=None, base_url="https://unused.example", model=MODEL_ID,
        model_coding=MODEL_ID, timeout_sec=2, context_limit_override=None,
    ))


def _response(request):
    body = {
        "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "read_project_file", "arguments": "{\\\"path\\\":\\\"README.md\\\"}"},
            }],
        }}],
    }
    return httpx.Response(200, json=body, request=request)


def _preview(entry, messages, tools, scope):
    payload = {"model": entry.provider_model, "messages": messages,
               "temperature": 0.0, "stream": False, "tools": tools}
    return json.dumps({
        "method": "POST", "model_id": entry.id, "vendor": entry.vendor,
        "endpoint": entry.chat_completions_url(),
        "headers": {"Authorization": "[redacted]", "Content-Type": "application/json"},
        "session_id": SESSION_ID, "scope": scope, "provider_payload": payload,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _install_transport(monkeypatch, handler):
    requests = []
    options = []
    transport = httpx.MockTransport(lambda req: (requests.append(req), handler(req))[1])

    def create_client(**kwargs):
        options.append(dict(kwargs))
        return _REAL_HTTPX_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(llm_client.httpx, "Client", create_client)
    return requests, options


def _call(client, messages, tools, scope, confirm, before_each=lambda: True):
    return client.chat_confirmed_tools(
        messages, model_id=MODEL_ID, session_id=SESSION_ID, tools=tools,
        scope=scope, confirm_fn=confirm, before_each=before_each,
    )


def test_confirmed_tools_sends_the_frozen_payload_bytes_shown_in_scope_preview(monkeypatch):
    entry = _entry()
    messages = [{"role": "system", "content": "synthetic worker"},
                {"role": "user", "content": "synthetic task"}]
    tools = [{"type": "function", "function": {"name": "read_project_file"}}]
    scope = {"project_id": "probe", "read_targets": ["README.md"], "write_targets": ["result.txt"]}
    expected = _preview(entry, messages, tools, scope)
    confirmations = []
    checks = []
    requests, options = _install_transport(monkeypatch, _response)

    def confirm(preview, allow_all):
        confirmations.append((preview, allow_all))
        messages[1]["content"] = "changed after the user approved"
        tools[0]["function"]["name"] = "run_command"
        return "y"

    with patch.object(ModelRegistry, "load", side_effect=[_registry(entry), _registry(entry), _registry(entry)]):
        result = _call(_client(), messages, tools, scope, confirm,
                       before_each=lambda: checks.append(True) or True)

    assert len(confirmations) == 1
    assert confirmations[0] == (expected, False)
    assert len(checks) >= 1
    assert len(requests) == 1
    preview_doc = json.loads(expected)
    assert json.loads(requests[0].content) == preview_doc["provider_payload"]
    assert requests[0].content == json.dumps(
        preview_doc["provider_payload"], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert "never-preview-this-test-secret" not in expected
    assert requests[0].url.host == "api-cdn.0x567.com"
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
    assert result.tool_calls[0]["function"]["name"] == "read_project_file"


def test_rejection_or_failed_scope_recheck_posts_zero(monkeypatch):
    entry = _entry()
    messages = [{"role": "user", "content": "synthetic"}]
    tools = [{"type": "function", "function": {"name": "read_project_file"}}]
    scope = {"project_id": "probe", "read_targets": ["README.md"]}
    requests, _options = _install_transport(monkeypatch, _response)
    with patch.object(ModelRegistry, "load", return_value=_registry(entry)):
        with pytest.raises(llm_client.LLMOutboundConfirmationError):
            _call(_client(), messages, tools, scope, lambda _preview, _allow: "n")
        with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
            _call(_client(), messages, tools, scope, lambda _preview, _allow: "y",
                  before_each=lambda: False)
    assert requests == []


def test_confirmation_callback_exception_posts_zero_and_is_sanitized(monkeypatch):
    entry = _entry()
    requests, _options = _install_transport(monkeypatch, _response)
    with patch.object(ModelRegistry, "load", return_value=_registry(entry)):
        with pytest.raises(llm_client.LLMOutboundConfirmationError) as error:
            _call(_client(), [{"role": "user", "content": "synthetic"}],
                  [{"type": "function", "function": {"name": "read_project_file"}}],
                  {"project_id": "probe"},
                  lambda _preview, _allow: (_ for _ in ()).throw(RuntimeError(SECRET)))
    assert requests == []
    assert SECRET not in str(error.value)




def test_model_profile_changed_after_approval_posts_zero(monkeypatch):
    entry = _entry()
    changed = _entry(provider_model="different-gpt-6-luna")
    requests, _options = _install_transport(monkeypatch, _response)
    with patch.object(ModelRegistry, "load", side_effect=[
        _registry(entry), _registry(entry), _registry(changed),
    ]):
        with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
            _call(
                _client(), [{"role": "user", "content": "synthetic"}],
                [{"type": "function", "function": {"name": "read_project_file"}}],
                {"project_id": "probe"}, lambda _preview, _allow: "y",
            )
    assert requests == []
