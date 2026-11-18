from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import sys
import threading
import unittest
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from self_evolution_merge_integration import NativeClaim
import self_evolution_native_transport as transport


class _Handler(http.server.BaseHTTPRequestHandler):
    response_status = 200
    response_body: bytes = b"{}"
    response_headers: dict[str, str] = {}
    captured: list[tuple[dict[str, str], bytes]] = []
    sleep_seconds = 0.0

    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).captured.append((dict(self.headers.items()), body))
        if type(self).sleep_seconds:
            import time
            time.sleep(type(self).sleep_seconds)
        self.send_response(type(self).response_status)
        for key, value in type(self).response_headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(type(self).response_body)))
        self.end_headers()
        try:
            self.wfile.write(type(self).response_body)
        except OSError:
            # Expected when the client enforces its deadline and closes early.
            pass

    def log_message(self, *_args):
        pass


@contextmanager
def server():
    _Handler.response_status = 200
    _Handler.response_headers = {"Content-Type": "application/json"}
    _Handler.response_body = b"{}"
    _Handler.captured = []
    _Handler.sleep_seconds = 0.0
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=1)


def valid_request():
    request = {
        "schema": "self_evolution_host_selected_apply_request_v1",
        "session_id": "session-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "candidate_id": "candidate-1",
        "baseline_digest": "a" * 64,
        "candidate_digest": "b" * 64,
        "operation_id": "c" * 32,
        "target_root": "C:/temporary-target",
        "target_head": "d" * 40,
        "target_index_sha256": "e" * 64,
        "target_worktree_digest": "f" * 64,
        "files": [{"path": "agent-core/example.py", "action": "modify",
                   "before_sha256": "1" * 64, "after_sha256": "2" * 64,
                   "before_size": 1, "after_size": 2, "before_mode": "100644", "after_mode": "100644"}],
        "request_sha256": "",
    }
    canonical = json.dumps({key: value for key, value in request.items() if key != "request_sha256"},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    request["request_sha256"] = hashlib.sha256(canonical).hexdigest()
    return request


def response_for(request, **overrides):
    value = {"approved": True, "request_sha256": request["request_sha256"],
             "operation_id": request["operation_id"], "selected_paths": ["agent-core/example.py"]}
    value.update(overrides)
    return json.dumps(value, separators=(",", ":")).encode()


class NativeTransportTests(unittest.TestCase):
    def env(self, port, token=None, url=None):
        raw_token = token or base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
        return {"MY_AGENT_SELF_EVOLUTION_NATIVE_URL": url or f"http://127.0.0.1:{port}/self-evolution/confirm",
                "MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN": raw_token}

    def test_posts_exact_request_and_returns_existing_typed_claim(self):
        request = valid_request()
        with server() as port:
            _Handler.response_body = response_for(request)
            config = self.env(port)
            client = transport.take_native_claim_from_environment(environ=config)
            claim = client.claim(request)
            self.assertNotIn(transport.URL_ENV, config)
            self.assertNotIn(transport.TOKEN_ENV, config)
        self.assertIs(type(claim), NativeClaim)
        self.assertEqual(claim, NativeClaim(True, request["request_sha256"], "c" * 32, ("agent-core/example.py",)))
        headers, body = _Handler.captured[0]
        self.assertEqual(json.loads(body), request)
        self.assertEqual(headers["Authorization"], "Bearer " + self.env(port)["MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN"])
        self.assertEqual(headers["Content-Type"], "application/json")
        with self.assertRaises(FrozenInstanceError):
            claim.approved = False

    def test_accepts_hex_token_and_sends_32_decoded_bytes_as_bearer(self):
        request = valid_request()
        token = bytes(range(32)).hex()
        with server() as port:
            _Handler.response_body = response_for(request)
            config = self.env(port, token=token)
            transport.take_native_claim_from_environment(environ=config).claim(request)
            self.assertNotIn(transport.URL_ENV, config)
            self.assertNotIn(transport.TOKEN_ENV, config)
        self.assertEqual(_Handler.captured[0][0]["Authorization"], "Bearer " + token)

    def test_rejects_nonmatching_or_nonexact_approval_payloads(self):
        request = valid_request()
        cases = [
            response_for(request, approved=0),
            response_for(request, request_sha256="0" * 64),
            response_for(request, operation_id="0" * 32),
            response_for(request, selected_paths=[]),
            response_for(request, unexpected=True),
        ]
        for body in cases:
            with self.subTest(body=body), server() as port:
                _Handler.response_body = body
                with self.assertRaises(transport.NativeTransportError):
                    transport.take_native_claim_from_environment(environ=self.env(port)).claim(request)

    def test_rejects_wrong_schema_and_duplicate_json_fields_before_network(self):
        request = valid_request()
        for malformed in (b'{"approved":true,"approved":false}',):
            with server() as port:
                _Handler.response_body = malformed
                with self.assertRaises(transport.NativeTransportError):
                    transport.take_native_claim_from_environment(environ=self.env(port)).claim(request)
        request["schema"] = "wrong"
        with self.assertRaises(transport.NativeTransportError):
            transport.take_native_claim_from_environment(environ=self.env(1)).claim(request)

    def test_rejects_untrusted_urls_and_malformed_or_weak_credentials(self):
        request = valid_request()
        bad_urls = ["https://127.0.0.1:1234/self-evolution/confirm",
                    "http://localhost:1234/self-evolution/confirm",
                    "http://127.0.0.2:1234/self-evolution/confirm",
                    "http://127.0.0.1:1234/other", "http://u:p@127.0.0.1:1234/self-evolution/confirm",
                    "http://127.0.0.1:1234/self-evolution/confirm?x=1"]
        for url in bad_urls:
            with self.subTest(url=url):
                with self.assertRaises(transport.NativeTransportError):
                    transport.take_native_claim_from_environment(environ=self.env(1234, url=url)).claim(request)
        for token in ("short", "g" * 64, base64.urlsafe_b64encode(b"short").decode().rstrip("=")):
            with self.subTest(token=token[:8]):
                with self.assertRaises(transport.NativeTransportError):
                    transport.take_native_claim_from_environment(environ=self.env(1234, token=token)).claim(request)

    def test_exact_200_decline_returns_typed_negative_claim(self):
        request = valid_request()
        with server() as port:
            _Handler.response_status = 200
            _Handler.response_body = response_for(request, approved=False)
            claim = transport.take_native_claim_from_environment(environ=self.env(port)).claim(request)
        self.assertIs(type(claim), NativeClaim)
        self.assertIs(claim.approved, False)
        self.assertEqual(claim.request_sha256, request["request_sha256"])
        self.assertEqual(claim.operation_id, request["operation_id"])
        self.assertEqual(claim.selected_paths, ("agent-core/example.py",))

    def test_exact_409_decline_remains_a_typed_negative_claim(self):
        request = valid_request()
        with server() as port:
            _Handler.response_status = 409
            _Handler.response_body = response_for(request, approved=False)
            claim = transport.take_native_claim_from_environment(environ=self.env(port)).claim(request)
        self.assertIs(type(claim), NativeClaim)
        self.assertIs(claim.approved, False)
        self.assertEqual(claim.request_sha256, request["request_sha256"])
        self.assertEqual(claim.operation_id, request["operation_id"])
        self.assertEqual(claim.selected_paths, ("agent-core/example.py",))

    def test_409_true_or_mismatched_decline_is_rejected(self):
        request = valid_request()
        replies = [
            response_for(request, approved=True),
            response_for(request, approved=False, request_sha256="0" * 64),
            response_for(request, approved=False, operation_id="0" * 32),
            response_for(request, approved=False, selected_paths=[]),
            b'{"approved":false,"request_sha256":"' + request["request_sha256"].encode()
            + b'","operation_id":"' + request["operation_id"].encode()
            + b'","selected_paths":["agent-core/example.py"],"extra":true}',
        ]
        for body in replies:
            with self.subTest(body=body), server() as port:
                _Handler.response_status = 409
                _Handler.response_body = body
                with self.assertRaises(transport.NativeTransportError):
                    transport.take_native_claim_from_environment(environ=self.env(port)).claim(request)

    def test_rejects_redirect_http_error_and_oversized_response(self):
        request = valid_request()
        variants = [(302, {"Location": "http://127.0.0.1:9/"}, response_for(request)),
                    (500, {}, response_for(request)),
                    (200, {}, b"x" * (transport.MAX_RESPONSE_BYTES + 1))]
        for status, headers, body in variants:
            with self.subTest(status=status, size=len(body)), server() as port:
                _Handler.response_status = status
                _Handler.response_headers.update(headers)
                _Handler.response_body = body
                with self.assertRaises(transport.NativeTransportError):
                    transport.take_native_claim_from_environment(environ=self.env(port)).claim(request)

    def test_human_confirmation_timeout_defaults_to_90_and_allows_120_without_waiting(self):
        self.assertEqual(transport.DEFAULT_TIMEOUT_SECONDS, 90.0)
        self.assertEqual(transport.MAX_TIMEOUT_SECONDS, 120.0)
        default_client = transport.take_native_claim_from_environment(environ=self.env(1234))
        max_client = transport.take_native_claim_from_environment(
            environ=self.env(1234), timeout_seconds=120.0,
        )
        self.assertEqual(default_client._timeout, 90.0)
        self.assertEqual(max_client._timeout, 120.0)

    def test_timeout_fails_closed(self):
        request = valid_request()
        with server() as port:
            _Handler.sleep_seconds = 0.2
            client = transport.take_native_claim_from_environment(environ=self.env(port), timeout_seconds=0.03)
            with self.assertRaises(transport.NativeTransportError):
                client.claim(request)

    def test_default_startup_pops_real_process_environment_and_redacts_client(self):
        request = valid_request()
        with server() as port:
            config = self.env(port)
            with patch.dict(os.environ, config, clear=False):
                client = transport.take_native_claim_from_environment()
                self.assertNotIn(transport.URL_ENV, os.environ)
                self.assertNotIn(transport.TOKEN_ENV, os.environ)
                self.assertNotIn(config[transport.TOKEN_ENV], repr(client))
                _Handler.response_body = response_for(request)
                claim = client.claim(request)
        self.assertTrue(claim.approved)

    def test_missing_and_invalid_environment_is_consumed_before_failure(self):
        with self.assertRaises(transport.NativeTransportError):
            transport.take_native_claim_from_environment(environ={})
        env = {transport.URL_ENV: "bad", transport.TOKEN_ENV: "secret-invalid-value"}
        with self.assertRaises(transport.NativeTransportError):
            transport.take_native_claim_from_environment(environ=env)
        self.assertEqual(env, {})


if __name__ == "__main__":
    unittest.main()
