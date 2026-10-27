"""Safety parsing for the fixed Node control-client helper response."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_AGENT_CORE = Path(__file__).resolve().parents[1]
if str(_AGENT_CORE) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE))
try:
    from evolution.client import (
        EvolutionClient,
        _checked_binding_request,
        _checked_host_authorization_request,
        _checked_host_resolve_stop_request,
        _checked_t9_late_ready_request,
        decode_helper_result,
        decode_host_authorization_result,
        decode_host_resolve_stop_result,
        decode_t9_late_ready_result,
    )
    from evolution.contracts import ContractError
except ImportError:
    decode_helper_result = None
    decode_t9_late_ready_result = None
    ContractError = ValueError

REQUEST = {
    "protocol": "evolution/1.0",
    "operation_id": "11111111-1111-4111-8111-111111111111",
    "evolution_id": "22222222-2222-4222-8222-222222222222",
    "conversation_id": "33333333-3333-4333-8333-333333333333",
    "host_run_id": "44444444-4444-4444-8444-444444444444",
    "expected_state_revision": 7,
    "authorization_id": "55555555-5555-4555-8555-555555555555",
    "action": "inspect",
    "payload": {"query": "summary"},
}


def decoder():
    assert callable(decode_helper_result), "evolution.client.decode_helper_result must be importable"
    return decode_helper_result


def test_non_json_helper_stdout_is_unknown_even_with_zero_exit():
    result = decoder()(b"candidate says PASS\n", exit_code=0, request=REQUEST)
    assert result["status"] == "unknown"
    assert result["error"]["code"] == "outcome_unknown"
    assert result["result"] is None


def test_unknown_helper_result_preserves_original_operation_identity():
    partial = {"operation_id": REQUEST["operation_id"], "status": "unknown"}
    result = decoder()(json.dumps(partial).encode(), exit_code=1, request=REQUEST)
    assert result["operation_id"] == REQUEST["operation_id"]
    assert result["evolution_id"] == REQUEST["evolution_id"]
    assert result["state_revision"] == REQUEST["expected_state_revision"]
    assert result["status"] == "unknown"


def test_wrong_identity_and_empty_stdout_cannot_be_accepted_as_success():
    wrong = {
        "operation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "evolution_id": REQUEST["evolution_id"],
        "state_revision": 8,
        "status": "succeeded",
        "result": {},
        "error": None,
    }
    for stdout, code in ((json.dumps(wrong).encode(), 0), (b"", -1)):
        result = decoder()(stdout, exit_code=code, request=REQUEST)
        assert result["status"] == "unknown"
        assert result["operation_id"] == REQUEST["operation_id"]
        assert result["error"]["code"] == "outcome_unknown"


def test_invalid_original_request_is_not_rewritten_as_unknown():
    if not callable(decode_helper_result):
        assert callable(decode_helper_result), "evolution.client.decode_helper_result must be importable"
        return
    bad_request = {**REQUEST, "operation_id": "not-a-uuid"}
    with pytest.raises(ContractError) as captured:
        decoder()(b"", exit_code=-1, request=bad_request)
    assert captured.value.code == "invalid_request"


def test_binding_request_accepts_native_legacy_conversation_id_but_keeps_scope_uuid_strict():
    legacy = {"type": "host.bind", "conversation_id": "20261026-1a2b3c4d", "scope_id": "55555555-5555-4555-8555-555555555555", "confirmation_id": "77777777-7777-4777-8777-777777777777", "expires_at": "2099-01-01T00:00:00.000Z"}
    assert _checked_binding_request(legacy) == legacy
    for invalid_id in ("20260230-1a2b3c4d", "２０２６１０２６-1a2b3c4d", "٢٠٢٦١٠٢٦-1a2b3c4d", "20261026-1a2b3c4d\n", "20261026-1a2b3c4d\r\n", "33333333-3333-4333-8333-333333333333\n", "33333333-3333-4333-8333-333333333333\r\n"):
        with pytest.raises(ContractError) as bad_id:
            _checked_binding_request({**legacy, "conversation_id": invalid_id})
        assert bad_id.value.code == "invalid_request"
    with pytest.raises(ContractError) as bad_scope:
        _checked_binding_request({**legacy, "scope_id": "20261026-1a2b3c4d"})
    assert bad_scope.value.code == "invalid_request"


@pytest.mark.parametrize("request_kind", ["authorize", "resolve_stop", "late_ready"])
def test_host_followup_requests_accept_legacy_conversation_id_and_keep_uuid_fields_strict(request_kind):
    legacy = "20261026-1a2b3c4d"
    evolution_id = REQUEST["evolution_id"]
    instance_id = "88888888-8888-4888-8888-888888888888"
    if request_kind == "authorize":
        request = {
            "type": "host.authorize", "conversation_id": legacy, "evolution_id": evolution_id,
            "kind": "candidate.build", "constraints": {
                "source_snapshot_id": "sha256:" + "a" * 64,
                "build_input_digest": "sha256:" + "b" * 64, "isolation_mode": "local_managed",
            }, "confirmation_id": "77777777-7777-4777-8777-777777777777",
            "expires_at": "2099-01-01T00:00:00.000Z",
        }
        checker = _checked_host_authorization_request
        decoder = decode_host_authorization_result
        strict_uuid_fields = ("evolution_id", "confirmation_id")
        response = {
            "type": "host.authorize.result", "confirmation_id": request["confirmation_id"], "status": "succeeded",
            "result": {"evolution_id": evolution_id, "authorization_id": "99999999-9999-4999-8999-999999999999", "kind": "candidate.build"},
            "error": None,
        }
    elif request_kind == "resolve_stop":
        request = {"type": "host.resolve-stop", "conversation_id": legacy, "evolution_id": evolution_id, "instance_id": instance_id}
        checker = _checked_host_resolve_stop_request
        decoder = decode_host_resolve_stop_result
        strict_uuid_fields = ("evolution_id", "instance_id")
        response = {
            "type": "host.resolve-stop.result", "status": "succeeded",
            "result": {"evolution_id": evolution_id, "instance_id": instance_id, "authorization_id": "99999999-9999-4999-8999-999999999999"},
            "error": None,
        }
    else:
        request = {
            "type": "host.t9-late-ready", "conversation_id": legacy, "evolution_id": evolution_id,
            "candidate_id": "sha256:" + "a" * 64, "instance_id": instance_id, "instance_sequence": 1,
        }
        checker = _checked_t9_late_ready_request
        decoder = decode_t9_late_ready_result
        strict_uuid_fields = ("evolution_id", "instance_id")
        response = {
            "type": "host.t9-late-ready.result", "status": "observed", "accepted": False,
            "projection": {"conversation_id": legacy, "current_candidate_id": None, "current_instance_id": None, "late_event_count": 1},
        }

    checked = checker(request)
    assert checked["conversation_id"] == legacy
    assert decoder(json.dumps(response).encode(), exit_code=0, request=checked) == response
    for field in strict_uuid_fields:
        with pytest.raises(ContractError):
            checker({**request, field: legacy})


def test_complete_matching_success_envelope_is_accepted():
    envelope = {
        "operation_id": REQUEST["operation_id"],
        "evolution_id": REQUEST["evolution_id"],
        "state_revision": REQUEST["expected_state_revision"],
        "status": "succeeded",
        "result": {"conversation_id": REQUEST["conversation_id"]},
        "error": None,
    }
    result = decoder()(json.dumps(envelope).encode(), exit_code=0, request=REQUEST)
    assert result == envelope


def _client_fixture(tmp_path: Path) -> tuple[EvolutionClient, str]:
    root = tmp_path / "release"
    helper = root / "node" / "control-client-entry.js"
    helper.parent.mkdir(parents=True)
    helper.write_text("// fixed helper fixture\n", encoding="utf-8")
    token = "a" * 64
    release = {
        "release_id": "sha256:" + "6" * 64,
        "root": str(root),
        "node_executable": os.environ["EVOLUTION_TRUSTED_NODE"],
        "python_executable": sys.executable,
        "manifest_path": str(root / "manifest.json"),
        "manifest_digest": "sha256:" + "7" * 64,
    }
    return EvolutionClient(
        release,
        r"\\.\pipe\evolution-test",
        "66666666-6666-4666-8666-666666666666",
        token,
    ), token


def test_client_keeps_bootstrap_secret_out_of_command_line_and_environment(tmp_path, monkeypatch):
    client, token = _client_fixture(tmp_path)
    result_envelope = {
        "operation_id": REQUEST["operation_id"],
        "evolution_id": REQUEST["evolution_id"],
        "state_revision": REQUEST["expected_state_revision"],
        "status": "succeeded",
        "result": {"conversation_id": REQUEST["conversation_id"]},
        "error": None,
    }
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(result_envelope).encode(), b"")

    monkeypatch.setattr("evolution.client.subprocess.run", fake_run)
    result = client.request(REQUEST, timeout_s=3)
    assert result == result_envelope
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert token not in repr(args)
    assert token not in repr(kwargs["env"])
    assert token.encode() in kwargs["input"]
    assert args[0] == os.environ["EVOLUTION_TRUSTED_NODE"]
    assert args[1].endswith("control-client-entry.js")
    assert kwargs["shell"] is False


def test_client_timeout_returns_unknown_without_replaying(tmp_path, monkeypatch):
    client, _token = _client_fixture(tmp_path)
    calls = []

    def fake_timeout(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=b"partial")

    monkeypatch.setattr("evolution.client.subprocess.run", fake_timeout)
    result = client.request(REQUEST, timeout_s=1)
    assert len(calls) == 1
    assert result["status"] == "unknown"
    assert result["operation_id"] == REQUEST["operation_id"]
    assert result["error"]["code"] == "outcome_unknown"

BINDING_REQUEST = {
    "type": "host.bind",
    "conversation_id": "33333333-3333-4333-8333-333333333333",
    "scope_id": "55555555-5555-4555-8555-555555555555",
    "confirmation_id": "77777777-7777-4777-8777-777777777777",
    "expires_at": "2099-01-01T00:00:00.000Z",
}
BINDING_RESULT = {
    "type": "host.bind.result",
    "confirmation_id": BINDING_REQUEST["confirmation_id"],
    "status": "succeeded",
    "result": {
        "evolution_id": "66666666-6666-4666-8666-666666666666",
        "authorization_id": "88888888-8888-4888-8888-888888888888",
        "scope_id": BINDING_REQUEST["scope_id"],
        "workspace_revision": 7,
    },
    "error": None,
}


def test_host_binding_result_requires_exact_confirmation_and_scope_identity():
    from evolution.client import decode_host_binding_result

    result = decode_host_binding_result(
        json.dumps(BINDING_RESULT).encode(), exit_code=0, request=BINDING_REQUEST
    )
    assert result == BINDING_RESULT
    wrong_confirmation = {**BINDING_RESULT, "confirmation_id": "99999999-9999-4999-8999-999999999999"}
    unknown = decode_host_binding_result(
        json.dumps(wrong_confirmation).encode(), exit_code=0, request=BINDING_REQUEST
    )
    assert unknown["status"] == "unknown"
    assert unknown["confirmation_id"] == BINDING_REQUEST["confirmation_id"]
    assert unknown["error"]["code"] == "outcome_unknown"


def test_begin_binding_uses_fixed_helper_and_returns_only_binding_and_patch_grant(tmp_path, monkeypatch):
    client, token = _client_fixture(tmp_path)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(BINDING_RESULT).encode(), b"")

    monkeypatch.setattr("evolution.client.subprocess.run", fake_run)
    result = client.begin_binding(
        BINDING_REQUEST["conversation_id"],
        BINDING_REQUEST["scope_id"],
        BINDING_REQUEST["confirmation_id"],
        BINDING_REQUEST["expires_at"],
    )
    assert result == {
        "evolution_id": BINDING_RESULT["result"]["evolution_id"],
        "authorization_id": BINDING_RESULT["result"]["authorization_id"],
    }
    args, kwargs = calls[0]
    assert token not in repr(args) and token not in repr(kwargs["env"])
    payload = json.loads(kwargs["input"])
    assert payload["request"] == BINDING_REQUEST
    assert payload["bootstrap"]["host_token"] == token


def test_host_authorize_uses_fixed_helper_and_correlates_exact_confirmation(tmp_path, monkeypatch):
    client, _token = _client_fixture(tmp_path)
    request = {
        "type": "host.authorize",
        "conversation_id": "33333333-3333-4333-8333-333333333333",
        "evolution_id": "22222222-2222-4222-8222-222222222222",
        "kind": "candidate.build",
        "constraints": {
            "source_snapshot_id": "sha256:" + "a" * 64,
            "build_input_digest": "sha256:" + "b" * 64,
            "isolation_mode": "local_managed",
        },
        "confirmation_id": "77777777-7777-4777-8777-777777777777",
        "expires_at": "2099-01-01T00:00:00.000Z",
    }
    calls = []
    authorization_id = "88888888-8888-4888-8888-888888888888"
    response = {"type": "host.authorize.result", "confirmation_id": request["confirmation_id"], "status": "succeeded", "result": {"evolution_id": "22222222-2222-4222-8222-222222222222", "authorization_id": authorization_id, "kind": "candidate.build"}, "error": None}
    monkeypatch.setattr(client, "_run_helper", lambda message, timeout: (calls.append((message, timeout)) or (json.dumps(response).encode(), 0)))

    result = client.authorize_grant(**{key: request[key] for key in ("conversation_id", "evolution_id", "kind", "constraints", "confirmation_id", "expires_at")})

    assert result == authorization_id
    assert calls[0][0] == request
    assert calls[0][1] == 30.0


def test_host_authorize_rejects_malformed_kind_and_confirmation_identity(tmp_path, monkeypatch):
    from evolution.client import decode_host_authorization_result
    from evolution.contracts import ContractError
    client, _token = _client_fixture(tmp_path)
    with pytest.raises(ContractError):
        client.authorize_grant("33333333-3333-4333-8333-333333333333", "22222222-2222-4222-8222-222222222222", "model.infer", {}, "77777777-7777-4777-8777-777777777777", "2099-01-01T00:00:00.000Z")
    request = {"type": "host.authorize", "conversation_id": "33333333-3333-4333-8333-333333333333", "evolution_id": "22222222-2222-4222-8222-222222222222", "kind": "candidate.build", "constraints": {"source_snapshot_id": "sha256:" + "a"*64, "build_input_digest": "sha256:" + "b"*64, "isolation_mode": "local_managed"}, "confirmation_id": "77777777-7777-4777-8777-777777777777", "expires_at": "2099-01-01T00:00:00.000Z"}
    malformed = {"type": "host.authorize.result", "confirmation_id": "99999999-9999-4999-8999-999999999999", "status": "succeeded", "result": {"evolution_id": "22222222-2222-4222-8222-222222222222", "authorization_id": "88888888-8888-4888-8888-888888888888", "kind": "candidate.build"}, "error": None}
    assert decode_host_authorization_result(json.dumps(malformed).encode(), exit_code=0, request=request)["status"] == "unknown"



def test_launch_keeps_derived_stop_grant_private_and_bound_to_instance(tmp_path, monkeypatch):
    client, _token = _client_fixture(tmp_path)
    instance_id = "99999999-9999-4999-8999-999999999999"
    child_grant = "88888888-8888-4888-8888-888888888888"
    request = {**REQUEST, "action": "launch", "payload": {"candidate_id": "sha256:" + "a" * 64, "profile_fixture_id": "77777777-7777-4777-8777-777777777777"}}
    response = {"operation_id": request["operation_id"], "evolution_id": request["evolution_id"], "state_revision": request["expected_state_revision"] + 1, "status": "succeeded", "result": {"candidate_id": request["payload"]["candidate_id"], "instance_id": instance_id, "status": "ready", "stop_authorization_id": child_grant}, "error": None}
    monkeypatch.setattr(client, "_run_helper", lambda _message, _timeout: (json.dumps(response).encode(), 0))

    visible = client.request(request)

    assert visible["result"]["instance_id"] == instance_id
    assert "stop_authorization_id" not in visible["result"]
    assert client.authorization_for_instance(request["evolution_id"], request["conversation_id"], instance_id) == child_grant
    with pytest.raises(ContractError):
        client.authorization_for_instance(request["evolution_id"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", instance_id)


def test_successful_stop_discards_the_exact_instance_grant(tmp_path, monkeypatch):
    client, _token = _client_fixture(tmp_path)
    instance_id = "99999999-9999-4999-8999-999999999999"
    child_grant = "88888888-8888-4888-8888-888888888888"
    launch = {**REQUEST, "action": "launch", "payload": {"candidate_id": "sha256:" + "a" * 64, "profile_fixture_id": "77777777-7777-4777-8777-777777777777"}}
    launch_result = {"operation_id": launch["operation_id"], "evolution_id": launch["evolution_id"], "state_revision": 1, "status": "succeeded", "result": {"instance_id": instance_id, "stop_authorization_id": child_grant}, "error": None}
    monkeypatch.setattr(client, "_run_helper", lambda _message, _timeout: (json.dumps(launch_result).encode(), 0))
    client.request(launch)
    stop = {**REQUEST, "operation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "expected_state_revision": 1, "authorization_id": child_grant, "action": "stop", "payload": {"instance_id": instance_id}}
    stop_result = {"operation_id": stop["operation_id"], "evolution_id": stop["evolution_id"], "state_revision": 2, "status": "succeeded", "result": {"instance_id": instance_id, "status": "stopped"}, "error": None}
    monkeypatch.setattr(client, "_run_helper", lambda _message, _timeout: (json.dumps(stop_result).encode(), 0))
    client.request(stop)
    with pytest.raises(ContractError):
        client.authorization_for_instance(stop["evolution_id"], stop["conversation_id"], instance_id)


def test_stop_grant_recovery_uses_fixed_trusted_host_lookup(tmp_path, monkeypatch):
    client, _token = _client_fixture(tmp_path)
    instance_id = "99999999-9999-4999-8999-999999999999"
    grant_id = "88888888-8888-4888-8888-888888888888"
    calls = []
    response = {"type": "host.resolve-stop.result", "status": "succeeded", "result": {"evolution_id": REQUEST["evolution_id"], "instance_id": instance_id, "authorization_id": grant_id}, "error": None}
    monkeypatch.setattr(client, "_run_helper", lambda message, timeout: (calls.append((message, timeout)) or (json.dumps(response).encode(), 0)))
    resolved = client.resolve_stop_authorization(REQUEST["conversation_id"], REQUEST["evolution_id"], instance_id)
    assert resolved == grant_id
    assert calls[0][0] == {"type": "host.resolve-stop", "conversation_id": REQUEST["conversation_id"], "evolution_id": REQUEST["evolution_id"], "instance_id": instance_id}


def test_failed_launch_also_keeps_owned_partial_instance_stop_grant_private(tmp_path, monkeypatch):
    client, _token = _client_fixture(tmp_path)
    instance_id = "99999999-9999-4999-8999-999999999999"
    child_grant = "88888888-8888-4888-8888-888888888888"
    request = {**REQUEST, "action": "launch", "payload": {"candidate_id": "sha256:" + "a" * 64, "profile_fixture_id": "77777777-7777-4777-8777-777777777777"}}
    response = {"operation_id": request["operation_id"], "evolution_id": request["evolution_id"], "state_revision": request["expected_state_revision"] + 1, "status": "failed", "result": {"candidate_id": request["payload"]["candidate_id"], "instance_id": instance_id, "status": "failed", "stop_authorization_id": child_grant}, "error": {"code": "start_failed", "message": "partial launch", "evidence_ids": [], "retryable": False}}
    monkeypatch.setattr(client, "_run_helper", lambda _message, _timeout: (json.dumps(response).encode(), 0))
    visible=client.request(request)
    assert visible["status"]=="failed"
    assert "stop_authorization_id" not in visible["result"]
    assert client.authorization_for_instance(request["evolution_id"],request["conversation_id"],instance_id)==child_grant



def test_t9_late_ready_requires_observed_rejection_and_same_projection_identity():
    assert callable(decode_t9_late_ready_result)
    request = {
        "type": "host.t9-late-ready",
        "conversation_id": REQUEST["conversation_id"],
        "evolution_id": REQUEST["evolution_id"],
        "candidate_id": "sha256:" + "a" * 64,
        "instance_id": "88888888-8888-4888-8888-888888888888",
        "instance_sequence": 1,
    }
    projection = {
        "conversation_id": request["conversation_id"],
        "current_candidate_id": "sha256:" + "b" * 64,
        "current_instance_id": "99999999-9999-4999-8999-999999999999",
        "late_event_count": 1,
    }
    response = {"type": "host.t9-late-ready.result", "status": "observed", "accepted": False, "projection": projection}
    assert decode_t9_late_ready_result(json.dumps(response).encode(), exit_code=0, request=request) == response
    forged = {**response, "accepted": True}
    unknown = decode_t9_late_ready_result(json.dumps(forged).encode(), exit_code=0, request=request)
    assert unknown["status"] == "unknown"
    assert unknown["error"]["code"] == "outcome_unknown"
