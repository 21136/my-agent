from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

import llm_client
from llm_client import LLMClient, LLMConfig
from llm_models import ModelEntry, ModelRegistry

_SECRET = "synthetic-api-secret-never-preview-this"
_MODEL_ID = "synthetic-confirmed-model"
_SESSION_ID = "20261104-abcdef01"
_REAL_HTTPX_CLIENT = httpx.Client


def make_entry(*, base_url: str = "https://provider.example.test", provider_model: str = "safe-json-1") -> ModelEntry:
    return ModelEntry(
        id=_MODEL_ID,
        name="Synthetic JSON model",
        vendor="synthetic-provider",
        base_url=base_url,
        provider_model=provider_model,
        api_key=_SECRET,
        supports_tool_call=True,
    )


def make_registry(entry: ModelEntry) -> ModelRegistry:
    return ModelRegistry(
        models=(entry,),
        default_flash_id=entry.id,
        default_pro_id=entry.id,
        alias_map={},
    )


def make_client() -> LLMClient:
    return LLMClient(LLMConfig(
        api_key=None,
        base_url="https://unused.example.test",
        model=_MODEL_ID,
        model_coding=_MODEL_ID,
        timeout_sec=1.0,
        context_limit_override=None,
    ))


def provider_payload(entry: ModelEntry, messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model": entry.provider_model,
        "messages": messages,
        "temperature": 0.0,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def preview_json(entry: ModelEntry, session_id: str, messages: list[dict[str, Any]]) -> str:
    return json.dumps({
        "model_id": entry.id,
        "vendor": entry.vendor,
        "endpoint": entry.chat_completions_url(),
        "session_id": session_id,
        "provider_payload": provider_payload(entry, messages),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def response_json() -> dict[str, Any]:
    return {
        "model": "safe-json-1",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"ok":true}'},
        }],
    }


def install_transport(monkeypatch, handler):
    captured_requests = []
    client_options = []
    real_client = _REAL_HTTPX_CLIENT
    transport = httpx.MockTransport(lambda request: (captured_requests.append(request), handler(request))[1])

    def create_client(**kwargs):
        client_options.append(dict(kwargs))
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(llm_client.httpx, "Client", create_client)
    return captured_requests, client_options


def invoke(client, messages, confirm_fn, expected_preview, *, session_id=_SESSION_ID):
    return client.chat_confirmed_json(
        messages,
        model_id=_MODEL_ID,
        session_id=session_id,
        expected_preview_json=expected_preview,
        confirm_fn=confirm_fn,
    )


def test_preview_matches_exact_provider_payload_bytes_sent_and_never_contains_auth(monkeypatch):
    messages = [
        {"role": "system", "content": "Synthetic system prompt"},
        {"role": "user", "content": "Synthetic request with unicode: \\u96ea"},
    ]
    entry = make_entry()
    expected = preview_json(entry, _SESSION_ID, messages)
    confirm_calls = []
    def confirm(preview, allow_approve_all):
        confirm_calls.append((preview, allow_approve_all))
        return "y"

    captured, options = install_transport(
        monkeypatch, lambda _request: httpx.Response(200, json=response_json()),
    )
    with patch.object(ModelRegistry, "load", side_effect=[make_registry(entry), make_registry(entry)]):
        result = invoke(make_client(), messages, confirm, expected)

    assert result.content == '{"ok":true}'
    assert confirm_calls == [(expected, False)]
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://provider.example.test/v1/chat/completions"
    preview_value = json.loads(confirm_calls[0][0])
    sent_payload = json.loads(request.content)
    assert sent_payload == preview_value["provider_payload"]
    assert request.content == json.dumps(
        preview_value["provider_payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert preview_value["model_id"] == _MODEL_ID
    assert preview_value["vendor"] == entry.vendor
    assert preview_value["session_id"] == _SESSION_ID
    assert sent_payload["stream"] is False
    assert sent_payload["response_format"] == {"type": "json_object"}
    assert "tools" not in sent_payload
    assert _SECRET not in confirm_calls[0][0]
    assert request.headers["authorization"] == f"Bearer {_SECRET}"
    assert options == [{"timeout": 1.0, "trust_env": False, "follow_redirects": False}]


def test_confirm_rejection_or_exception_never_constructs_transport(monkeypatch):
    entry = make_entry()
    messages = [{"role": "user", "content": "synthetic"}]
    expected = preview_json(entry, _SESSION_ID, messages)
    with patch.object(ModelRegistry, "load", return_value=make_registry(entry)):
        with patch.object(llm_client.httpx, "Client") as http_client:
            with pytest.raises(llm_client.LLMOutboundConfirmationError):
                invoke(make_client(), messages, lambda preview, allow: "n", expected)
            http_client.assert_not_called()

    def broken_confirm(_preview, _allow):
        raise RuntimeError(f"callback failure must be suppressed: {_SECRET}")
    with patch.object(ModelRegistry, "load", return_value=make_registry(entry)):
        with patch.object(llm_client.httpx, "Client") as http_client:
            with pytest.raises(llm_client.LLMOutboundConfirmationError) as exc:
                invoke(make_client(), messages, broken_confirm, expected)
            assert _SECRET not in str(exc.value)
            http_client.assert_not_called()


def test_preview_mismatch_and_registry_endpoint_or_model_change_fail_before_http(monkeypatch):
    original = make_entry()
    messages = [{"role": "user", "content": "synthetic"}]
    with patch.object(ModelRegistry, "load", return_value=make_registry(original)):
        with patch.object(llm_client.httpx, "Client") as http_client:
            with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
                invoke(make_client(), messages, lambda *_: pytest.fail("must not confirm mismatched preview"), "{}")
            http_client.assert_not_called()

    for changed in (
        make_entry(base_url="https://other.example.test"),
        make_entry(provider_model="safe-json-2"),
    ):
        with patch.object(ModelRegistry, "load", side_effect=[make_registry(original), make_registry(changed)]):
            with patch.object(llm_client.httpx, "Client") as http_client:
                with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
                    invoke(make_client(), messages, lambda *_: "y", preview_json(original, _SESSION_ID, messages))
                http_client.assert_not_called()


def test_confirmed_payload_is_immutable_during_confirmation(monkeypatch):
    messages = [{"role": "user", "content": "approved exact text"}]
    entry = make_entry()
    expected = preview_json(entry, _SESSION_ID, messages)
    captured, _options = install_transport(
        monkeypatch, lambda _request: httpx.Response(200, json=response_json()),
    )

    def confirm(_preview, _allow):
        messages[0]["content"] = "mutated after preview"
        messages.append({"role": "user", "content": "also injected"})
        return "y"

    with patch.object(ModelRegistry, "load", side_effect=[make_registry(entry), make_registry(entry)]):
        invoke(make_client(), messages, confirm, expected)
    assert json.loads(captured[0].content)["messages"] == [
        {"role": "user", "content": "approved exact text"},
    ]


def test_non_https_endpoint_and_changed_credential_fail_closed(monkeypatch):
    messages = [{"role": "user", "content": "synthetic"}]
    insecure = make_entry(base_url="http://provider.example.test")
    with patch.object(ModelRegistry, "load", return_value=make_registry(insecure)):
        with patch.object(llm_client.httpx, "Client") as http_client:
            with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
                invoke(make_client(), messages, lambda *_: pytest.fail("insecure target must not prompt"), "")
            http_client.assert_not_called()

    entry = make_entry()
    secret_values = iter((_SECRET, "rotated-secret"))
    with patch.object(ModelRegistry, "load", side_effect=[make_registry(entry), make_registry(entry)]):
        with patch.object(ModelEntry, "resolve_api_key", autospec=True, side_effect=lambda *_args: next(secret_values)):
            with patch.object(llm_client.httpx, "Client") as http_client:
                with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
                    invoke(make_client(), messages, lambda *_: "y", preview_json(entry, _SESSION_ID, messages))
                http_client.assert_not_called()


def test_redirect_and_transport_failure_are_not_followed_or_retried(monkeypatch):
    entry = make_entry()
    messages = [{"role": "user", "content": "synthetic"}]
    expected = preview_json(entry, _SESSION_ID, messages)
    redirect_requests, _ = install_transport(
        monkeypatch,
        lambda _request: httpx.Response(307, headers={"Location": "https://attacker.example.test/collect"}),
    )
    with patch.object(ModelRegistry, "load", side_effect=[make_registry(entry), make_registry(entry)]):
        with pytest.raises(llm_client.LLMOutboundRequestError):
            invoke(make_client(), messages, lambda *_: "y", expected)
    assert len(redirect_requests) == 1
    assert str(redirect_requests[0].url) == "https://provider.example.test/v1/chat/completions"

    failure_requests, _ = install_transport(
        monkeypatch, lambda _request: (_ for _ in ()).throw(httpx.ConnectError("synthetic network failure")),
    )
    with patch.object(ModelRegistry, "load", side_effect=[make_registry(entry), make_registry(entry)]):
        with pytest.raises(llm_client.LLMOutboundRequestError) as exc:
            invoke(make_client(), messages, lambda *_: "y", expected)
    assert len(failure_requests) == 1
    assert _SECRET not in str(exc.value)
    assert not isinstance(exc.value, (llm_client.LLMNetworkError, llm_client.LLMApiError, llm_client.LLMTimeoutError))


def test_only_exact_y_and_plain_json_messages_are_accepted(monkeypatch):
    entry = make_entry()
    messages = [{"role": "user", "content": "synthetic"}]
    expected = preview_json(entry, _SESSION_ID, messages)
    for choice in ("Y", "yes", True, ("y", "receipt"), None):
        with patch.object(ModelRegistry, "load", return_value=make_registry(entry)):
            with patch.object(llm_client.httpx, "Client") as http_client:
                with pytest.raises(llm_client.LLMOutboundConfirmationError):
                    invoke(make_client(), messages, lambda *_args, _choice=choice: _choice, expected)
                http_client.assert_not_called()

    with patch.object(ModelRegistry, "load", return_value=make_registry(entry)):
        with patch.object(llm_client.httpx, "Client") as http_client:
            with pytest.raises(llm_client.LLMOutboundConfigurationChangedError):
                invoke(make_client(), [{"role": "user", "content": object()}], lambda *_: "y", expected)
            http_client.assert_not_called()
