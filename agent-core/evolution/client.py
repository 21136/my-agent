"""Fixed Node helper client for the private evolution control channel."""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    MAX_CONTROL_BYTES,
    ContractError,
    canonical_json,
    parse_control_request,
    parse_control_result,
    parse_json_bytes,
)

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_BINDING_EXPIRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$")
_MAX_TIMEOUT_SECONDS = 600.0


def _unknown(request: Mapping[str, Any], message: str = "control helper outcome is unknown") -> dict[str, Any]:
    checked = parse_control_request(dict(request))
    return {
        "operation_id": checked["operation_id"],
        "evolution_id": checked["evolution_id"],
        "state_revision": checked["expected_state_revision"],
        "status": "unknown",
        "result": None,
        "error": {
            "code": "outcome_unknown",
            "message": message[:256],
            "evidence_ids": [],
            "retryable": False,
        },
    }


def decode_helper_result(stdout: bytes, *, exit_code: int, request: dict[str, Any]) -> dict[str, Any]:
    """Accept only a complete, successful helper envelope for this exact request."""
    checked = parse_control_request(request)
    if type(exit_code) is not int or exit_code != 0 or not isinstance(stdout, bytes):
        return _unknown(checked)
    if not stdout or len(stdout) > MAX_CONTROL_BYTES:
        return _unknown(checked)
    try:
        result = parse_control_result(parse_json_bytes(stdout))
    except (ContractError, UnicodeError, ValueError, TypeError):
        return _unknown(checked)
    if result["operation_id"] != checked["operation_id"] or result["evolution_id"] != checked["evolution_id"]:
        return _unknown(checked)
    expected_revision = checked["expected_state_revision"]
    if checked["action"] == "inspect":
        if result["state_revision"] < expected_revision:
            return _unknown(checked)
    elif result["state_revision"] != expected_revision + 1:
        return _unknown(checked)
    return result


def _checked_binding_request(value: object) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"type", "conversation_id", "scope_id", "confirmation_id", "expires_at"}:
        raise ContractError("invalid_request", "trusted host binding request fields are invalid")
    if value.get("type") != "host.bind":
        raise ContractError("invalid_request", "trusted host binding request type is invalid")
    for key in ("conversation_id", "scope_id", "confirmation_id"):
        if not isinstance(value.get(key), str) or not _UUID_RE.fullmatch(value[key]):
            raise ContractError("invalid_request", f"{key} must be a canonical UUID")
    expiry = value.get("expires_at")
    if not isinstance(expiry, str) or not _BINDING_EXPIRY_RE.fullmatch(expiry):
        raise ContractError("invalid_request", "expires_at must be an ISO timestamp with zone")
    return {key: value[key] for key in ("type", "conversation_id", "scope_id", "confirmation_id", "expires_at")}


def _unknown_binding(request: Mapping[str, Any], message: str = "trusted binding outcome is unknown") -> dict[str, Any]:
    checked = _checked_binding_request(dict(request))
    return {
        "type": "host.bind.result",
        "confirmation_id": checked["confirmation_id"],
        "status": "unknown",
        "error": {"code": "outcome_unknown", "message": message[:512]},
    }


def decode_host_binding_result(stdout: bytes, *, exit_code: int, request: dict[str, Any]) -> dict[str, Any]:
    """Accept only a complete binding response correlated to the exact user confirmation."""
    checked = _checked_binding_request(request)
    if type(exit_code) is not int or exit_code != 0 or not isinstance(stdout, bytes) or not stdout or len(stdout) > MAX_CONTROL_BYTES:
        return _unknown_binding(checked)
    try:
        result = parse_json_bytes(stdout)
    except (ContractError, UnicodeError, ValueError, TypeError):
        return _unknown_binding(checked)
    if type(result) is not dict or result.get("type") != "host.bind.result" or result.get("confirmation_id") != checked["confirmation_id"]:
        return _unknown_binding(checked, "trusted binding response identity does not match the confirmation")
    status = result.get("status")
    if status == "succeeded":
        if set(result) != {"type", "confirmation_id", "status", "result", "error"} or result.get("error") is not None or type(result.get("result")) is not dict:
            return _unknown_binding(checked, "trusted binding success envelope is malformed")
        value = result["result"]
        if set(value) != {"evolution_id", "authorization_id", "scope_id", "workspace_revision"}:
            return _unknown_binding(checked, "trusted binding result fields are malformed")
        if not isinstance(value.get("evolution_id"), str) or not _UUID_RE.fullmatch(value["evolution_id"]):
            return _unknown_binding(checked, "trusted binding evolution identity is invalid")
        if not isinstance(value.get("authorization_id"), str) or not _UUID_RE.fullmatch(value["authorization_id"]):
            return _unknown_binding(checked, "trusted binding grant identity is invalid")
        revision = value.get("workspace_revision")
        if value.get("scope_id") != checked["scope_id"] or type(revision) is not int or not 0 <= revision <= 9_007_199_254_740_991:
            return _unknown_binding(checked, "trusted binding scope or revision does not match")
        return result
    if status == "failed":
        error = result.get("error")
        if set(result) != {"type", "confirmation_id", "status", "error"} or type(error) is not dict or set(error) != {"code", "message"}:
            return _unknown_binding(checked, "trusted binding failure envelope is malformed")
        if not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str) or len(error["message"]) > 512:
            return _unknown_binding(checked, "trusted binding failure details are malformed")
        return result
    if status == "unknown":
        error = result.get("error")
        if set(result) == {"type", "confirmation_id", "status", "error"} and type(error) is dict and set(error) == {"code", "message"} and error.get("code") == "outcome_unknown" and isinstance(error.get("message"), str) and len(error["message"]) <= 512:
            return result
    return _unknown_binding(checked, "trusted binding response status is invalid")


def _checked_host_authorization_request(value: object) -> dict[str, Any]:
    fields = {"type", "conversation_id", "evolution_id", "kind", "constraints", "confirmation_id", "expires_at"}
    if type(value) is not dict or set(value) != fields or value.get("type") != "host.authorize":
        raise ContractError("invalid_request", "trusted Host authorization fields are invalid")
    for key in ("conversation_id", "evolution_id", "confirmation_id"):
        if not isinstance(value.get(key), str) or not _UUID_RE.fullmatch(value[key]):
            raise ContractError("invalid_request", f"{key} must be a canonical UUID")
    expiry = value.get("expires_at")
    if not isinstance(expiry, str) or not _BINDING_EXPIRY_RE.fullmatch(expiry):
        raise ContractError("invalid_request", "expires_at must be an ISO timestamp with zone")
    constraints = value.get("constraints")
    if type(constraints) is not dict:
        raise ContractError("invalid_request", "grant constraints must be a plain object")
    kind = value.get("kind")
    if kind == "source.patch":
        if set(constraints) != {"owned_paths", "expected_before_hashes"}:
            raise ContractError("invalid_request", "source.patch constraints are invalid")
        paths = constraints["owned_paths"]
        hashes = constraints["expected_before_hashes"]
        if type(paths) is not list or not 1 <= len(paths) <= 256 or type(hashes) is not dict or set(hashes) != set(paths):
            raise ContractError("invalid_request", "source.patch paths or hashes are invalid")
        folded = set()
        for item in paths:
            if not isinstance(item, str) or not item or len(item) > 4096 or item.startswith("/") or "\\" in item or ":" in item or any(part in {"", ".", ".."} for part in item.split("/")):
                raise ContractError("invalid_request", "source.patch path is unsafe")
            if item.lower() in folded:
                raise ContractError("invalid_request", "source.patch paths contain a case collision")
            folded.add(item.lower())
            before = hashes[item]
            if before != "missing" and (not isinstance(before, str) or not _HASH_RE.fullmatch(before)):
                raise ContractError("invalid_request", "source.patch before hash is invalid")
    elif kind == "candidate.build":
        if set(constraints) != {"source_snapshot_id", "build_input_digest", "isolation_mode"} or not isinstance(constraints.get("source_snapshot_id"), str) or not _HASH_RE.fullmatch(constraints["source_snapshot_id"]) or not isinstance(constraints.get("build_input_digest"), str) or not _HASH_RE.fullmatch(constraints["build_input_digest"]) or constraints.get("isolation_mode") not in {"local_managed", "protected_guest"}:
            raise ContractError("invalid_request", "candidate.build constraints are invalid")
    elif kind == "candidate.launch":
        if set(constraints) != {"candidate_id", "profile_fixture_id", "isolation_mode"} or not isinstance(constraints.get("candidate_id"), str) or not _HASH_RE.fullmatch(constraints["candidate_id"]) or not isinstance(constraints.get("profile_fixture_id"), str) or not _UUID_RE.fullmatch(constraints["profile_fixture_id"]) or constraints.get("isolation_mode") not in {"local_managed", "protected_guest"}:
            raise ContractError("invalid_request", "candidate.launch constraints are invalid")
    else:
        raise ContractError("unsupported_capability", "trusted Host authorization kind is unsupported")
    checked = {key: value[key] for key in fields}
    if len(canonical_json(checked)) > MAX_CONTROL_BYTES:
        raise ContractError("budget_exceeded", "trusted Host authorization exceeds its byte limit")
    return checked


def _unknown_host_authorization(request: Mapping[str, Any], message: str = "trusted authorization outcome is unknown") -> dict[str, Any]:
    checked = _checked_host_authorization_request(dict(request))
    return {"type": "host.authorize.result", "confirmation_id": checked["confirmation_id"], "status": "unknown", "error": {"code": "outcome_unknown", "message": message[:512]}}


def decode_host_authorization_result(stdout: bytes, *, exit_code: int, request: dict[str, Any]) -> dict[str, Any]:
    checked = _checked_host_authorization_request(request)
    if type(exit_code) is not int or exit_code != 0 or not isinstance(stdout, bytes) or not stdout or len(stdout) > MAX_CONTROL_BYTES:
        return _unknown_host_authorization(checked)
    try:
        result = parse_json_bytes(stdout)
    except (ContractError, UnicodeError, ValueError, TypeError):
        return _unknown_host_authorization(checked)
    if type(result) is not dict or result.get("type") != "host.authorize.result" or result.get("confirmation_id") != checked["confirmation_id"]:
        return _unknown_host_authorization(checked, "trusted authorization response identity does not match the confirmation")
    status = result.get("status")
    if status == "succeeded":
        if set(result) != {"type", "confirmation_id", "status", "result", "error"} or result.get("error") is not None or type(result.get("result")) is not dict:
            return _unknown_host_authorization(checked, "trusted authorization success envelope is malformed")
        value = result["result"]
        if set(value) != {"evolution_id", "authorization_id", "kind"} or value.get("evolution_id") != checked["evolution_id"] or value.get("kind") != checked["kind"] or not isinstance(value.get("authorization_id"), str) or not _UUID_RE.fullmatch(value["authorization_id"]):
            return _unknown_host_authorization(checked, "trusted authorization result identity is invalid")
        return result
    if status in {"failed", "unknown"}:
        error = result.get("error")
        if set(result) != {"type", "confirmation_id", "status", "error"} or type(error) is not dict or set(error) != {"code", "message"} or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str) or len(error["message"]) > 512:
            return _unknown_host_authorization(checked, "trusted authorization error envelope is malformed")
        if status == "unknown" and error.get("code") != "outcome_unknown":
            return _unknown_host_authorization(checked, "trusted authorization unknown status is malformed")
        return result
    return _unknown_host_authorization(checked, "trusted authorization response status is invalid")


def _checked_host_resolve_stop_request(value: object) -> dict[str, str]:
    fields = {"type", "conversation_id", "evolution_id", "instance_id"}
    if type(value) is not dict or set(value) != fields or value.get("type") != "host.resolve-stop":
        raise ContractError("invalid_request", "trusted stop-grant recovery fields are invalid")
    for key in ("conversation_id", "evolution_id", "instance_id"):
        if not isinstance(value.get(key), str) or not _UUID_RE.fullmatch(value[key]):
            raise ContractError("invalid_request", f"{key} must be a canonical UUID")
    return {key: value[key] for key in fields}


def _unknown_host_resolve_stop(message: str = "stop-grant recovery outcome is unknown") -> dict[str, Any]:
    return {"type": "host.resolve-stop.result", "status": "unknown", "error": {"code": "outcome_unknown", "message": message[:512]}}


def decode_host_resolve_stop_result(stdout: bytes, *, exit_code: int, request: dict[str, Any]) -> dict[str, Any]:
    checked = _checked_host_resolve_stop_request(request)
    if type(exit_code) is not int or exit_code != 0 or not isinstance(stdout, bytes) or not stdout or len(stdout) > MAX_CONTROL_BYTES:
        return _unknown_host_resolve_stop()
    try:
        result = parse_json_bytes(stdout)
    except (ContractError, UnicodeError, ValueError, TypeError):
        return _unknown_host_resolve_stop()
    if type(result) is not dict or result.get("type") != "host.resolve-stop.result":
        return _unknown_host_resolve_stop("stop-grant recovery response type is invalid")
    if result.get("status") == "succeeded":
        if set(result) != {"type", "status", "result", "error"} or result.get("error") is not None or type(result.get("result")) is not dict:
            return _unknown_host_resolve_stop("stop-grant recovery success envelope is malformed")
        value = result["result"]
        if set(value) != {"evolution_id", "instance_id", "authorization_id"} or value.get("evolution_id") != checked["evolution_id"] or value.get("instance_id") != checked["instance_id"] or not isinstance(value.get("authorization_id"), str) or not _UUID_RE.fullmatch(value["authorization_id"]):
            return _unknown_host_resolve_stop("stop-grant recovery identity does not match the exact instance")
        return result
    if result.get("status") in {"failed", "unknown"}:
        error = result.get("error")
        if set(result) != {"type", "status", "error"} or type(error) is not dict or set(error) != {"code", "message"} or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str) or len(error["message"]) > 512:
            return _unknown_host_resolve_stop("stop-grant recovery error envelope is malformed")
        if result["status"] == "unknown" and error.get("code") != "outcome_unknown":
            return _unknown_host_resolve_stop("stop-grant recovery unknown status is malformed")
        return result
    return _unknown_host_resolve_stop("stop-grant recovery status is invalid")


def _checked_t9_late_ready_request(value: object) -> dict[str, Any]:
    fields = {"type", "conversation_id", "evolution_id", "candidate_id", "instance_id", "instance_sequence"}
    if type(value) is not dict or set(value) != fields or value.get("type") != "host.t9-late-ready":
        raise ContractError("invalid_request", "T9 late-ready request fields are invalid")
    for key in ("conversation_id", "evolution_id", "instance_id"):
        if not isinstance(value.get(key), str) or not _UUID_RE.fullmatch(value[key]):
            raise ContractError("invalid_request", f"{key} must be a canonical UUID")
    if not isinstance(value.get("candidate_id"), str) or not _HASH_RE.fullmatch(value["candidate_id"]):
        raise ContractError("invalid_request", "candidate_id must be a content hash")
    sequence = value.get("instance_sequence")
    if type(sequence) is not int or not 1 <= sequence <= 9_007_199_254_740_991:
        raise ContractError("invalid_request", "instance_sequence is outside its bound")
    return {key: value[key] for key in fields}


def _unknown_t9_late_ready(message: str = "T9 late-ready outcome is unknown") -> dict[str, Any]:
    return {"type": "host.t9-late-ready.result", "status": "unknown", "accepted": None, "error": {"code": "outcome_unknown", "message": message[:512]}}


def decode_t9_late_ready_result(stdout: bytes, *, exit_code: int, request: dict[str, Any]) -> dict[str, Any]:
    checked = _checked_t9_late_ready_request(request)
    if type(exit_code) is not int or exit_code != 0 or not isinstance(stdout, bytes) or not stdout or len(stdout) > MAX_CONTROL_BYTES:
        return _unknown_t9_late_ready()
    try:
        result = parse_json_bytes(stdout)
    except (ContractError, UnicodeError, ValueError, TypeError):
        return _unknown_t9_late_ready()
    if type(result) is not dict or result.get("type") != "host.t9-late-ready.result":
        return _unknown_t9_late_ready("T9 late-ready response type is invalid")
    if result.get("status") == "observed":
        if set(result) != {"type", "status", "accepted", "projection"} or result.get("accepted") is not False or type(result.get("projection")) is not dict:
            return _unknown_t9_late_ready("T9 late-ready observation is malformed")
        projection = result["projection"]
        if set(projection) != {"conversation_id", "current_candidate_id", "current_instance_id", "late_event_count"} or projection.get("conversation_id") != checked["conversation_id"]:
            return _unknown_t9_late_ready("T9 projection conversation identity does not match")
        if projection.get("current_candidate_id") is not None and (not isinstance(projection["current_candidate_id"], str) or not _HASH_RE.fullmatch(projection["current_candidate_id"])):
            return _unknown_t9_late_ready("T9 current candidate identity is malformed")
        if projection.get("current_instance_id") is not None and (not isinstance(projection["current_instance_id"], str) or not _UUID_RE.fullmatch(projection["current_instance_id"])):
            return _unknown_t9_late_ready("T9 current instance identity is malformed")
        if type(projection.get("late_event_count")) is not int or projection["late_event_count"] < 1:
            return _unknown_t9_late_ready("T9 late event was not observed")
        return result
    if result.get("status") == "unknown" and set(result) == {"type", "status", "accepted", "error"} and result.get("accepted") is None:
        error = result.get("error")
        if type(error) is dict and set(error) == {"code", "message"} and error.get("code") == "outcome_unknown" and isinstance(error.get("message"), str) and len(error["message"]) <= 512:
            return result
    return _unknown_t9_late_ready("T9 late-ready status is invalid")

class EvolutionClient:
    """Invokes only the helper sealed into a previously verified control release."""

    def __init__(
        self,
        control_release: Mapping[str, Any],
        pipe_name: str,
        host_instance_id: str,
        host_token: str,
    ) -> None:
        if not isinstance(control_release, Mapping):
            raise ContractError("invalid_request", "control_release must be an object")
        required = {"release_id", "root", "node_executable", "python_executable", "manifest_path", "manifest_digest"}
        if set(control_release) != required:
            raise ContractError("invalid_request", "control_release fields are invalid")
        root = control_release["root"]
        node = control_release["node_executable"]
        release_id = control_release["release_id"]
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ContractError("invalid_request", "control release root must be absolute")
        if not isinstance(node, str) or not Path(node).is_absolute():
            raise ContractError("invalid_request", "fixed Node executable must be absolute")
        if not isinstance(release_id, str) or not _HASH_RE.fullmatch(release_id):
            raise ContractError("invalid_request", "control release id is invalid")
        if not isinstance(pipe_name, str) or not pipe_name.startswith("\\\\.\\pipe\\") or len(pipe_name) > 240:
            raise ContractError("invalid_request", "control pipe name is invalid")
        try:
            parsed_host = str(uuid.UUID(host_instance_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ContractError("invalid_request", "host_instance_id must be a UUID") from exc
        if parsed_host != host_instance_id:
            raise ContractError("invalid_request", "host_instance_id must be canonical lowercase UUID")
        if not isinstance(host_token, str) or not _TOKEN_RE.fullmatch(host_token):
            raise ContractError("invalid_request", "host token must contain 32 random bytes as lowercase hex")
        self.control_release = dict(control_release)
        self.pipe_name = pipe_name
        self.host_instance_id = host_instance_id
        self.host_token = host_token
        self._instance_authorizations: dict[tuple[str, str, str], str] = {}
        self._release_root = Path(root).resolve(strict=True)
        self._node = Path(node).resolve(strict=True)
        self._helper = (self._release_root / "node" / "control-client-entry.js").resolve(strict=True)
        if not self._helper.is_file() or self._release_root not in self._helper.parents:
            raise ContractError("permission_denied", "fixed helper is not inside the control release")
        if not self._node.is_file():
            raise ContractError("permission_denied", "fixed Node executable is not a regular file")

    def _run_helper(self, message: dict[str, Any], timeout_s: float) -> tuple[bytes, int]:
        bootstrap = {
            "pipe_name": self.pipe_name,
            "host_instance_id": self.host_instance_id,
            "host_token": self.host_token,
            "control_release": self.control_release,
        }
        payload = canonical_json({"bootstrap": bootstrap, "request": message, "timeout_ms": int(timeout_s * 1000)})
        if len(payload) > MAX_CONTROL_BYTES:
            raise ContractError("budget_exceeded", "control helper input exceeds 64 KiB")
        env: dict[str, str] = {}
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        try:
            completed = subprocess.run(
                [str(self._node), str(self._helper)],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self._release_root),
                env=env,
                timeout=float(timeout_s),
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, bytes) else b""
            return partial, -1
        except OSError:
            return b"", -1
        return completed.stdout, completed.returncode

    def request(self, envelope: dict[str, Any], timeout_s: float = 10.0) -> dict[str, Any]:
        checked = parse_control_request(envelope)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= timeout_s <= _MAX_TIMEOUT_SECONDS:
            raise ContractError("invalid_request", "timeout_s is outside the supported range")
        stdout, exit_code = self._run_helper(checked, float(timeout_s))
        response = decode_helper_result(stdout, exit_code=exit_code, request=checked)
        if response.get("status") in {"succeeded", "failed"} and checked["action"] == "launch":
            result = response.get("result")
            if isinstance(result, dict):
                instance_id = result.get("instance_id")
                stop_authorization_id = result.get("stop_authorization_id")
                if isinstance(instance_id, str) and _UUID_RE.fullmatch(instance_id):
                    if isinstance(stop_authorization_id, str) and _UUID_RE.fullmatch(stop_authorization_id):
                        self._instance_authorizations[(checked["evolution_id"], checked["conversation_id"], instance_id)] = stop_authorization_id
                    elif result.get("status") in {"ready", "failed", "quarantined"}:
                        return _unknown(checked, "owned launch receipt omitted its exact-instance stop grant")
                result.pop("stop_authorization_id", None)
        elif response.get("status") == "succeeded" and checked["action"] == "stop":
            result = response.get("result")
            if isinstance(result, dict) and result.get("status") == "stopped":
                instance_id = checked["payload"]["instance_id"]
                self._instance_authorizations.pop((checked["evolution_id"], checked["conversation_id"], instance_id), None)
        return response

    def authorization_for_instance(self, evolution_id: str, conversation_id: str, instance_id: str) -> str:
        if not all(isinstance(value, str) and _UUID_RE.fullmatch(value) for value in (evolution_id, conversation_id, instance_id)):
            raise ContractError("invalid_request", "instance authorization identity is invalid")
        try:
            return self._instance_authorizations[(evolution_id, conversation_id, instance_id)]
        except KeyError:
            return self.resolve_stop_authorization(conversation_id, evolution_id, instance_id)

    def resolve_stop_authorization(self, conversation_id: str, evolution_id: str, instance_id: str, timeout_s: float = 30.0) -> str:
        request = _checked_host_resolve_stop_request({"type": "host.resolve-stop", "conversation_id": conversation_id, "evolution_id": evolution_id, "instance_id": instance_id})
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= timeout_s <= _MAX_TIMEOUT_SECONDS:
            raise ContractError("invalid_request", "timeout_s is outside the supported range")
        stdout, exit_code = self._run_helper(request, float(timeout_s))
        response = decode_host_resolve_stop_result(stdout, exit_code=exit_code, request=request)
        if response["status"] == "failed":
            error = response["error"]
            raise ContractError(error["code"], error["message"])
        if response["status"] != "succeeded":
            raise ContractError("outcome_unknown", response["error"]["message"])
        authorization_id = response["result"]["authorization_id"]
        self._instance_authorizations[(evolution_id, conversation_id, instance_id)] = authorization_id
        return authorization_id

    def deliver_t9_late_ready(self, conversation_id: str, evolution_id: str, candidate_id: str, instance_id: str, instance_sequence: int = 1, timeout_s: float = 30.0) -> dict[str, Any]:
        request = _checked_t9_late_ready_request({"type": "host.t9-late-ready", "conversation_id": conversation_id, "evolution_id": evolution_id, "candidate_id": candidate_id, "instance_id": instance_id, "instance_sequence": instance_sequence})
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= timeout_s <= _MAX_TIMEOUT_SECONDS:
            raise ContractError("invalid_request", "timeout_s is outside the supported range")
        stdout, exit_code = self._run_helper(request, float(timeout_s))
        response = decode_t9_late_ready_result(stdout, exit_code=exit_code, request=request)
        if response.get("status") != "observed":
            error = response.get("error", {})
            raise ContractError(error.get("code", "outcome_unknown"), error.get("message", "T9 late-ready outcome is unknown"))
        return response
    def authorize_grant(self, conversation_id: str, evolution_id: str, kind: str, constraints: dict[str, Any], confirmation_id: str, expires_at: str, timeout_s: float = 30.0) -> str:
        request = _checked_host_authorization_request({
            "type": "host.authorize", "conversation_id": conversation_id, "evolution_id": evolution_id,
            "kind": kind, "constraints": constraints, "confirmation_id": confirmation_id, "expires_at": expires_at,
        })
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= timeout_s <= _MAX_TIMEOUT_SECONDS:
            raise ContractError("invalid_request", "timeout_s is outside the supported range")
        stdout, exit_code = self._run_helper(request, float(timeout_s))
        response = decode_host_authorization_result(stdout, exit_code=exit_code, request=request)
        if response["status"] == "failed":
            error = response["error"]
            raise ContractError(error["code"], error["message"])
        if response["status"] != "succeeded":
            raise ContractError("outcome_unknown", response["error"]["message"])
        return response["result"]["authorization_id"]

    def begin_binding(
        self,
        conversation_id: str,
        scope_id: str,
        confirmation_id: str,
        expires_at: str,
        timeout_s: float = 30.0,
    ) -> dict[str, str]:
        request = _checked_binding_request({
            "type": "host.bind",
            "conversation_id": conversation_id,
            "scope_id": scope_id,
            "confirmation_id": confirmation_id,
            "expires_at": expires_at,
        })
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= timeout_s <= _MAX_TIMEOUT_SECONDS:
            raise ContractError("invalid_request", "timeout_s is outside the supported range")
        stdout, exit_code = self._run_helper(request, float(timeout_s))
        response = decode_host_binding_result(stdout, exit_code=exit_code, request=request)
        if response["status"] == "failed":
            error = response["error"]
            raise ContractError(error["code"], error["message"])
        if response["status"] != "succeeded":
            raise ContractError("outcome_unknown", response["error"]["message"])
        result = response["result"]
        return {"evolution_id": result["evolution_id"], "authorization_id": result["authorization_id"]}

    def inspect(self, evolution_id: str, conversation_id: str, host_run_id: str) -> dict[str, Any]:
        envelope = {
            "protocol": "evolution/1.0",
            "operation_id": str(uuid.uuid4()),
            "evolution_id": evolution_id,
            "conversation_id": conversation_id,
            "host_run_id": host_run_id,
            "expected_state_revision": 0,
            "authorization_id": str(uuid.uuid4()),
            "action": "inspect",
            "payload": {"query": "summary"},
        }
        return self.request(envelope)
