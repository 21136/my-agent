"""Strict JSON, request, result and length-prefixed frame contracts.

This module intentionally has no desktop, filesystem or candidate imports.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, Final, Literal, TypeAlias

MAX_CONTROL_BYTES: Final = 65_536
MAX_FRAME_BYTES: Final = 65_536
MAX_PUSH_BYTES: Final = 1_048_576
MAX_JSON_DEPTH: Final = 64
MAX_COLLECTION_ITEMS: Final = 4_096
MAX_STRING_BYTES: Final = 65_536
Action: TypeAlias = Literal[
    "inspect", "patch", "snapshot", "build", "launch", "stop", "run_case",
    "capture_scene", "restore_scene", "prepare_adoption", "execute_adoption_step",
]
Hash: TypeAlias = str
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_LEGACY_CONVERSATION_ID_RE = re.compile(r"^([0-9]{4})([0-9]{2})([0-9]{2})-([0-9a-f]{8})$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACTIONS = frozenset((
    "inspect", "patch", "snapshot", "build", "launch", "stop", "run_case",
    "capture_scene", "restore_scene", "prepare_adoption", "execute_adoption_step",
))
_ERROR_CODES = frozenset((
    "invalid_request", "permission_denied", "scope_conflict", "stale_revision",
    "identity_mismatch", "protocol_incompatible", "unsupported_capability",
    "isolation_unavailable", "environment_not_ready", "budget_exceeded", "build_failed",
    "start_failed", "case_failed", "capture_unavailable", "scene_stale", "restore_ambiguous",
    "timeout", "cancelled", "outcome_unknown", "adoption_blocked", "data_incompatible",
    "recovery_required",
))


class ContractError(ValueError):
    """A bounded protocol validation failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def _invalid(message: str, field: str | None = None) -> None:
    raise ContractError("invalid_request", message, field)


def _check_string(value: Any, field: str, limit: int = MAX_STRING_BYTES) -> str:
    if not isinstance(value, str):
        _invalid("expected string", field)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _invalid("string contains an unpaired surrogate", field)
    if len(encoded) > limit:
        _invalid("string exceeds byte limit", field)
    return value


def _check_uuid(value: Any, field: str) -> str:
    text = _check_string(value, field, 36)
    if not _UUID_RE.fullmatch(text):
        _invalid("expected canonical lowercase UUID", field)
    return text


def is_conversation_id(value: Any) -> bool:
    """Accept canonical UUIDs and the exact date-prefixed ID emitted by session.py."""
    if not isinstance(value, str):
        return False
    if len(value) == 36 and _UUID_RE.fullmatch(value):
        return True
    if len(value) != 17:
        return False
    match = _LEGACY_CONVERSATION_ID_RE.fullmatch(value)
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


def _check_conversation_id(value: Any, field: str) -> str:
    text = _check_string(value, field, 36)
    if not is_conversation_id(text):
        _invalid("expected canonical UUID or native conversation id", field)
    return text


def _check_hash(value: Any, field: str) -> str:
    text = _check_string(value, field, 71)
    if not _HASH_RE.fullmatch(text):
        _invalid("expected sha256 lowercase hash", field)
    return text


def _json_value(value: Any, field: str = "$", depth: int = 0, ancestors: set[int] | None = None) -> None:
    if depth > MAX_JSON_DEPTH:
        _invalid("JSON nesting exceeds limit", field)
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > 9_007_199_254_740_991:
            _invalid("integer is not a safe cross-language number", field)
        return
    if type(value) is str:
        _check_string(value, field)
        return
    if ancestors is None:
        ancestors = set()
    if type(value) not in (dict, list):
        _invalid("value is not JSON", field)
    identity = id(value)
    if identity in ancestors:
        _invalid("cyclic value is not JSON", field)
    ancestors.add(identity)
    if type(value) is list:
        if len(value) > MAX_COLLECTION_ITEMS:
            _invalid("array exceeds item limit", field)
        for index, item in enumerate(value):
            _json_value(item, f"{field}[{index}]", depth + 1, ancestors)
    else:
        if len(value) > MAX_COLLECTION_ITEMS:
            _invalid("object exceeds field limit", field)
        for key, item in value.items():
            _check_string(key, f"{field} key")
            _json_value(item, f"{field}.{key}", depth + 1, ancestors)
    ancestors.remove(identity)


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _canonical(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is str:
        return _quote(value)
    if type(value) is list:
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    # UTF-16 code-unit order matches JavaScript's default Array.sort order.
    keys = sorted(value, key=lambda key: key.encode("utf-16-be", errors="strict"))
    return "{" + ",".join(_quote(key) + ":" + _canonical(value[key]) for key in keys) + "}"


def canonical_json(value: Any) -> bytes:
    _json_value(value)
    try:
        encoded = _canonical(value).encode("utf-8", errors="strict")
    except (UnicodeEncodeError, ValueError):
        _invalid("value cannot be represented as canonical UTF-8")
    return encoded


def content_hash(value: Any) -> Hash:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _exact_object(value: Any, allowed: set[str], required: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict:
        _invalid("expected object", field)
    unknown = value.keys() - allowed
    if unknown:
        _invalid("unknown field", f"{field}.{sorted(unknown)[0]}")
    missing = required - value.keys()
    if missing:
        _invalid("missing field", f"{field}.{sorted(missing)[0]}")
    return value


def _safe_path(value: Any, field: str) -> str:
    path = _check_string(value, field, 4_096)
    if not path or path.startswith("/") or "\\" in path or ":" in path or "\0" in path:
        _invalid("expected safe relative POSIX path", field)
    if any(part in ("", ".", "..") for part in path.split("/")):
        _invalid("path traversal or ambiguous component", field)
    return path


def _payload(value: Any, action: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
    result = _exact_object(value, allowed, required, f"payload({action})")
    _json_value(result, f"payload({action})")
    canonical_json(result)
    return result


def validate_action_payload(action: str, payload: Any) -> dict[str, Any]:
    if action not in _ACTIONS:
        _invalid("unknown action", "action")
    if type(payload) is not dict:
        _invalid("expected object", "payload")
    _json_value(payload, "payload")
    encoded_payload = canonical_json(payload)
    if len(encoded_payload) > MAX_CONTROL_BYTES:
        raise ContractError("budget_exceeded", "payload exceeds 64 KiB")
    if action == "inspect":
        query = payload.get("query")
        if query in ("summary", "source_list"):
            return _payload(payload, action, {"query"}, {"query"})
        if query == "operation":
            p = _payload(payload, action, {"query", "operation_id"}, {"query", "operation_id"})
            _check_uuid(p["operation_id"], "payload.operation_id")
            return p
        if query == "source_text":
            p = _payload(payload, action, {"query", "path"}, {"query", "path"})
            _safe_path(p["path"], "payload.path")
            return p
        _invalid("unsupported inspect query", "payload.query")
    if action == "patch":
        p = _payload(payload, action, {"path", "expected_before_hash", "content_utf8"}, {"path", "expected_before_hash", "content_utf8"})
        _safe_path(p["path"], "payload.path")
        if p["expected_before_hash"] != "missing":
            _check_hash(p["expected_before_hash"], "payload.expected_before_hash")
        _check_string(p["content_utf8"], "payload.content_utf8", 32_768)
        return p
    if action == "snapshot":
        p = _payload(payload, action, {"workspace_revision", "include_paths"}, {"workspace_revision", "include_paths"})
        revision = p["workspace_revision"]
        if type(revision) is not int or revision < 0 or revision > 9_007_199_254_740_991:
            _invalid("invalid workspace revision", "payload.workspace_revision")
        paths = p["include_paths"]
        if type(paths) is not list or not 1 <= len(paths) <= 256:
            _invalid("invalid include_paths", "payload.include_paths")
        seen: set[str] = set()
        for index, item in enumerate(paths):
            path = _safe_path(item, f"payload.include_paths[{index}]")
            folded = path.lower()
            if folded in seen:
                _invalid("duplicate or case-colliding include path", f"payload.include_paths[{index}]")
            seen.add(folded)
        return p
    if action == "build":
        p = _payload(payload, action, {"source_snapshot_id", "descriptor_artifact_id", "build_input_digest", "isolation_mode"}, {"source_snapshot_id", "descriptor_artifact_id", "build_input_digest", "isolation_mode"})
        _check_hash(p["source_snapshot_id"], "payload.source_snapshot_id")
        _check_uuid(p["descriptor_artifact_id"], "payload.descriptor_artifact_id")
        _check_hash(p["build_input_digest"], "payload.build_input_digest")
        if p["isolation_mode"] not in ("local_managed", "protected_guest"):
            _invalid("invalid isolation mode", "payload.isolation_mode")
        return p
    if action == "launch":
        p = _payload(payload, action, {"candidate_id", "profile_fixture_id"}, {"candidate_id", "profile_fixture_id"})
        _check_hash(p["candidate_id"], "payload.candidate_id")
        _check_uuid(p["profile_fixture_id"], "payload.profile_fixture_id")
        return p
    if action == "stop":
        p = _payload(payload, action, {"instance_id"}, {"instance_id"})
        _check_uuid(p["instance_id"], "payload.instance_id")
        return p
    if action == "run_case":
        p = _payload(payload, action, {"instance_id", "case_spec_artifact_id"}, {"instance_id", "case_spec_artifact_id"})
        _check_uuid(p["instance_id"], "payload.instance_id")
        _check_uuid(p["case_spec_artifact_id"], "payload.case_spec_artifact_id")
        return p
    # Future actions are parsed as bounded JSON, then rejected by the A dispatcher.
    return payload


def parse_control_request(value: Any) -> dict[str, Any]:
    _json_value(value)
    encoded = canonical_json(value)
    if len(encoded) > MAX_CONTROL_BYTES:
        raise ContractError("budget_exceeded", "control request exceeds 64 KiB")
    keys = {"protocol", "operation_id", "evolution_id", "conversation_id", "host_run_id", "expected_state_revision", "authorization_id", "action", "payload"}
    request = _exact_object(value, keys, keys, "request")
    if request["protocol"] != "evolution/1.0":
        raise ContractError("protocol_incompatible", "unsupported protocol version", "protocol")
    for field in ("operation_id", "evolution_id", "host_run_id", "authorization_id"):
        _check_uuid(request[field], field)
    _check_conversation_id(request["conversation_id"], "conversation_id")
    revision = request["expected_state_revision"]
    if type(revision) is not int or revision < 0 or revision > 9_007_199_254_740_991:
        _invalid("invalid state revision", "expected_state_revision")
    action = request["action"]
    if type(action) is not str or action not in _ACTIONS:
        _invalid("unknown action", "action")
    if action in {"inspect", "patch", "snapshot", "build", "launch", "stop", "run_case"}:
        validate_action_payload(action, request["payload"])
    elif type(request["payload"]) is not dict:
        _invalid("expected object", "payload")
    return request


def parse_control_result(value: Any) -> dict[str, Any]:
    _json_value(value)
    encoded = canonical_json(value)
    if len(encoded) > MAX_CONTROL_BYTES:
        raise ContractError("budget_exceeded", "control result exceeds 64 KiB")
    keys = {"operation_id", "evolution_id", "state_revision", "status", "result", "error"}
    result = _exact_object(value, keys, keys, "result")
    _check_uuid(result["operation_id"], "operation_id")
    _check_uuid(result["evolution_id"], "evolution_id")
    revision = result["state_revision"]
    if type(revision) is not int or revision < 0 or revision > 9_007_199_254_740_991:
        _invalid("invalid state revision", "state_revision")
    if result["status"] not in ("running", "succeeded", "failed", "cancelled", "unknown"):
        _invalid("invalid result status", "status")
    error = result["error"]
    if error is not None:
        fault = _exact_object(error, {"code", "message", "evidence_ids", "retryable"}, {"code", "message", "evidence_ids", "retryable"}, "error")
        if type(fault["code"]) is not str or fault["code"] not in _ERROR_CODES:
            _invalid("unknown error code", "error.code")
        _check_string(fault["message"], "error.message", 4_096)
        evidence = fault["evidence_ids"]
        if type(evidence) is not list or len(evidence) > 256:
            _invalid("invalid evidence_ids", "error.evidence_ids")
        for index, item in enumerate(evidence):
            _check_uuid(item, f"error.evidence_ids[{index}]")
        if type(fault["retryable"]) is not bool:
            _invalid("retryable must be boolean", "error.retryable")
    return result


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _invalid("duplicate JSON object key", key)
        result[key] = value
    return result


def _parse_float(_: str) -> Any:
    _invalid("A protocol numbers must be integers")


def _parse_constant(_: str) -> Any:
    _invalid("non-JSON numeric constant")


def parse_json_bytes(raw: bytes | bytearray | memoryview) -> Any:
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        _invalid("expected byte input")
    input_size = raw.nbytes if isinstance(raw, memoryview) else len(raw)
    if input_size > MAX_CONTROL_BYTES:
        raise ContractError("budget_exceeded", "JSON message exceeds 64 KiB")
    data = bytes(raw)
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_pairs_without_duplicates, parse_float=_parse_float, parse_constant=_parse_constant)
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("invalid_request", "malformed UTF-8 JSON") from exc
    _json_value(value)
    return value


def encode_frame(value: Any) -> bytes:
    body = canonical_json(value)
    if not 1 <= len(body) <= MAX_FRAME_BYTES:
        raise ContractError("budget_exceeded", "frame payload exceeds 64 KiB")
    return len(body).to_bytes(4, "big") + body


class FrameDecoder:
    """Incremental bounded decoder for 4-byte big-endian length-prefixed JSON."""

    def __init__(self) -> None:
        self._pending = bytearray()
        self._finished = False

    def push(self, chunk: bytes | bytearray | memoryview) -> list[Any]:
        if self._finished:
            raise ContractError("invalid_request", "frame decoder is already finished")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            _invalid("frame chunk must be bytes")
        input_size = chunk.nbytes if isinstance(chunk, memoryview) else len(chunk)
        if input_size > MAX_PUSH_BYTES:
            raise ContractError("budget_exceeded", "single decoder input exceeds 1 MiB")
        data_in = bytes(chunk)
        data = self._pending + data_in
        values: list[Any] = []
        offset = 0
        while len(data) - offset >= 4:
            size = int.from_bytes(data[offset:offset+4], "big")
            if size == 0:
                _invalid("empty frame is not allowed")
            if size > MAX_FRAME_BYTES:
                raise ContractError("budget_exceeded", "declared frame exceeds 64 KiB")
            end = offset + 4 + size
            if len(data) < end:
                break
            values.append(parse_json_bytes(data[offset+4:end]))
            offset = end
        self._pending = bytearray(data[offset:])
        if len(self._pending) > 4 + MAX_FRAME_BYTES:
            raise ContractError("budget_exceeded", "decoder buffer exceeds one frame")
        return values

    def finish(self) -> None:
        self._finished = True
        if self._pending:
            _invalid("stream ended inside a frame")
