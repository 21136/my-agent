"""Fail-closed HTTP transport to the trusted Electron native-confirmation host.

The caller must construct this client only with environment inherited from the
trusted sidecar. This module deliberately uses a direct ``HTTPConnection``:
there is no proxy lookup, redirect handling, or credential forwarding.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import re
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

from self_evolution_merge_integration import NativeClaim

URL_ENV = "MY_AGENT_SELF_EVOLUTION_NATIVE_URL"
TOKEN_ENV = "MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN"
ENDPOINT_PATH = "/self-evolution/confirm"
REQUEST_SCHEMA = "self_evolution_host_selected_apply_request_v1"
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 65_536
MAX_FILES = 256
DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_TIMEOUT_SECONDS = 120.0
CONNECT_TIMEOUT_SECONDS = 2.0

_REQUEST_KEYS = frozenset({
    "schema", "session_id", "task_id", "attempt_id", "candidate_id",
    "baseline_digest", "candidate_digest", "operation_id", "target_root",
    "target_head", "target_index_sha256", "target_worktree_digest", "files",
    "request_sha256",
})
_FILE_KEYS = frozenset({
    "path", "action", "before_sha256", "after_sha256", "before_size",
    "after_size", "before_mode", "after_mode",
})
_RESPONSE_KEYS = frozenset({"approved", "request_sha256", "operation_id", "selected_paths"})
_URL_RE = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})/self-evolution/confirm\Z")
_HEX_TOKEN_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
_B64_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}=?\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class NativeTransportError(RuntimeError):
    """Safe, non-secret-bearing failure from native confirmation transport."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeTransportError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _configuration(environ: Mapping[str, str] | None) -> tuple[str, int, str]:
    if environ is None:
        import os
        environ = os.environ
    url = environ.get(URL_ENV)
    token = environ.get(TOKEN_ENV)
    if not isinstance(url, str) or not isinstance(token, str):
        _fail("native_config_missing")
    match = _URL_RE.fullmatch(url)
    if not match:
        _fail("native_url_invalid")
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        _fail("native_url_invalid")
    if _HEX_TOKEN_RE.fullmatch(token):
        pass
    elif _B64_TOKEN_RE.fullmatch(token):
        try:
            decoded = base64.urlsafe_b64decode(token.rstrip("=") + "=")
        except (ValueError, base64.binascii.Error):
            _fail("native_token_invalid")
        if len(decoded) != 32:
            _fail("native_token_invalid")
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
        if token.rstrip("=") != canonical:
            _fail("native_token_invalid")
    else:
        _fail("native_token_invalid")
    return "127.0.0.1", port, token


def _prepare_request(request: Mapping[str, object]) -> tuple[bytes, str, str, tuple[str, ...]]:
    if not isinstance(request, Mapping):
        _fail("native_request_invalid")
    # HostSelectedApply adds selected_paths only to its in-process callback view;
    # that helper field is intentionally not part of the wire schema/digest.
    payload = dict(request)
    callback_selected = payload.pop("selected_paths", None)
    if frozenset(payload) != _REQUEST_KEYS or payload.get("schema") != REQUEST_SCHEMA:
        _fail("native_request_invalid")
    files = payload.get("files")
    if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_FILES:
        _fail("native_request_invalid")
    paths: list[str] = []
    clean_files: list[dict[str, object]] = []
    for row in files:
        if not isinstance(row, Mapping) or frozenset(row) != _FILE_KEYS:
            _fail("native_request_invalid")
        path = row.get("path")
        if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
            _fail("native_request_invalid")
        paths.append(path)
        clean_files.append(dict(row))
    selected_paths = tuple(paths)
    if (len(set(paths)) != len(paths) or paths != sorted(paths) or
            (callback_selected is not None and tuple(callback_selected) != selected_paths)):
        _fail("native_request_invalid")
    payload["files"] = clean_files
    digest = payload.get("request_sha256")
    operation_id = payload.get("operation_id")
    if (not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest) or
            not isinstance(operation_id, str) or not operation_id):
        _fail("native_request_invalid")
    unsigned = {key: value for key, value in payload.items() if key != "request_sha256"}
    try:
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail("native_request_invalid")
    if len(canonical) > MAX_REQUEST_BYTES:
        _fail("native_request_too_large")
    expected_digest = hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(digest, expected_digest):
        _fail("native_request_digest_invalid")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(body) > MAX_REQUEST_BYTES:
        _fail("native_request_too_large")
    return body, digest, operation_id, selected_paths


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("native_confirmation_timeout")
    return remaining


@dataclass(frozen=True, slots=True, repr=False)
class NativeConfirmationClient:
    """Closed, immutable transport capability consumed at trusted startup."""

    _host: str = field(repr=False)
    _port: int = field(repr=False)
    _token: str = field(repr=False)
    _timeout: float = field(repr=False)

    def __repr__(self) -> str:
        return "<NativeConfirmationClient configured>"

    def claim(self, request: Mapping[str, object]) -> NativeClaim:
        return _post_native_claim(self, request)


def take_native_claim_from_environment(
    *,
    environ: MutableMapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> NativeConfirmationClient:
    """Consume trusted sidecar config, removing both variables even on error.

    Production callers omit ``environ`` so values are taken from and popped
    from ``os.environ`` before ordinary tool subprocesses can inherit them.
    The explicit mapping hook exists for isolated tests only.
    """
    if environ is None:
        import os
        environ = os.environ
    config_error = False
    try:
        url = environ.pop(URL_ENV, None)
    except Exception:
        url = None
        config_error = True
    try:
        token = environ.pop(TOKEN_ENV, None)
    except Exception:
        token = None
        config_error = True
    if config_error:
        _fail("native_config_invalid")
    consumed = {URL_ENV: url, TOKEN_ENV: token}
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        _fail("native_timeout_invalid")
    timeout = float(timeout_seconds)
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        _fail("native_timeout_invalid")
    host, port, token = _configuration(consumed)
    return NativeConfirmationClient(host, port, token, timeout)


def _post_native_claim(
    client: NativeConfirmationClient,
    request: Mapping[str, object],
) -> NativeClaim:
    """POST an exact HostSelectedApply request and return a typed approval."""
    host, port, token = client._host, client._port, client._token
    body, request_sha256, operation_id, selected_paths = _prepare_request(request)
    deadline = time.monotonic() + client._timeout
    connection = http.client.HTTPConnection(host, port, timeout=min(CONNECT_TIMEOUT_SECONDS, client._timeout))
    try:
        connection.request("POST", ENDPOINT_PATH, body=body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
        })
        _remaining(deadline)
        if connection.sock is not None:
            connection.sock.settimeout(_remaining(deadline))
        response = connection.getresponse()
        _remaining(deadline)
        if response.status not in (200, 409):
            _fail("native_http_rejected")
        response_status = response.status
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            _fail("native_response_invalid")
        transfer_encoding = response.getheader("Transfer-Encoding")
        if transfer_encoding:
            _fail("native_response_invalid")
        raw_length = response.getheader("Content-Length")
        if raw_length is None or not raw_length.isascii() or not raw_length.isdecimal():
            _fail("native_response_invalid")
        length = int(raw_length)
        if length > MAX_RESPONSE_BYTES:
            _fail("native_response_too_large")
        if connection.sock is not None:
            connection.sock.settimeout(_remaining(deadline))
        raw = response.read(length + 1)
        _remaining(deadline)
        if len(raw) != length:
            _fail("native_response_invalid")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            _fail("native_response_invalid")
        if not isinstance(value, dict) or frozenset(value) != _RESPONSE_KEYS:
            _fail("native_response_invalid")
        approved = value["approved"]
        if type(approved) is not bool:
            _fail("native_response_invalid")
        returned_digest = value["request_sha256"]
        returned_operation = value["operation_id"]
        returned_paths = value["selected_paths"]
        if (not isinstance(returned_digest, str) or
                not hmac.compare_digest(returned_digest, request_sha256) or
                not isinstance(returned_operation, str) or returned_operation != operation_id or
                not isinstance(returned_paths, list) or
                any(not isinstance(path, str) for path in returned_paths) or
                tuple(returned_paths) != selected_paths):
            _fail("native_response_mismatch")
        if approved is True and response_status != 200:
            _fail("native_http_response_mismatch")
        _remaining(deadline)
        return NativeClaim(approved, returned_digest, returned_operation, selected_paths)
    except NativeTransportError:
        raise
    except (OSError, http.client.HTTPException, TimeoutError, ValueError):
        _fail("native_transport_failed")
    finally:
        connection.close()


__all__ = ["NativeClaim", "NativeTransportError", "NativeConfirmationClient", "take_native_claim_from_environment"]
